# SPDX-FileCopyrightText: PyPSA Contributors
#
# SPDX-License-Identifier: MIT

"""Stage-0 proof-of-concept for the Effect system (demonstration only).

Shows, without changing PyPSA, that the built-in ``cost`` + ``co2``
"effects" reproduce existing results exactly:

- E1: an unbounded, unpriced effect never enters the model — solves with
  and without declared co2 coefficients are identical; the effect value is
  recomputed post-solve from stored coefficients.
- E2: a bound on the co2 effect (built via ``extra_functionality`` from one
  reusable expression builder) is *identical* to the stock
  ``primary_energy`` GlobalConstraint — objective, dispatch, and dual —
  including non-cyclic storage depletion terms and multi-investment-period
  ``years`` weighting.
- E3: pricing the co2 effect into the objective is identical to the
  PyPSA-Eur ``add_emission_prices`` folding
  (``marginal_cost += price * co2 / efficiency``), including when
  ``snapshot_weightings.objective`` differs from ``.generators``.
- E4: the objective can *be* an effect: minimize co2 subject to a cost cap.

Run:  python effects_proposal/poc_effects_shim.py
"""

import numpy as np
import pandas as pd
from linopy.expressions import merge
from numpy.testing import assert_allclose

import pypsa

RTOL = 1e-9
SOLVER = "highs"
SOLVER_OPTS = {"output_flag": False}


# ---------------------------------------------------------------------------
# The PoC core: one reusable effect-expression builder.
# This replicates the accumulation semantics of
# pypsa/optimization/global_constraints.py::define_primary_energy_limit
# (generators: p * coeff / efficiency * w; non-cyclic storage: depletion)
# and is what `build_effect_expression` would look like in core.
# ---------------------------------------------------------------------------


def _named(df: pd.DataFrame) -> pd.DataFrame:
    # linopy aligns pandas objects by axis name (works for the multi-invest
    # MultiIndex snapshot axis too, exactly as in define_primary_energy_limit)
    return df.rename_axis(columns="name")


def effect_expression(n, sns, carrier_attribute="co2_emissions", weighting="physical"):
    """Build the linopy expression of a carrier-derived effect over ``sns``.

    weighting="physical": snapshot_weightings.generators x period years
    (the primary_energy accounting); weighting="objective": use
    snapshot_weightings.objective (the aggregation a priced contribution
    inherits from the cost effect it feeds).
    """
    m = n.model
    coeffs = n.carriers[carrier_attribute]
    coeffs = coeffs[coeffs != 0]
    weightings = n.snapshot_weightings.loc[sns]
    multi = bool(n._multi_invest)
    if multi:
        period_weighting = n.investment_period_weightings.years[sns.unique("period")]
        weightings = weightings.mul(period_weighting, level=0, axis=0)

    w_col = "generators" if weighting == "physical" else "objective"
    terms = []

    # generators: p * coeff / efficiency * w
    gens = n.generators[n.generators.carrier.isin(coeffs.index)]
    if not gens.empty:
        eff = n.get_switchable_as_dense("Generator", "efficiency").loc[sns, gens.index]
        em_pu = coeffs[gens.carrier].set_axis(gens.index) / eff
        em_pu = em_pu.multiply(weightings[w_col], axis=0)
        p = m["Generator-p"].sel(name=gens.index, snapshot=sns)
        terms.append((p * _named(em_pu)).sum())

    # non-cyclic storage units: depletion (initial - final SoC) * coeff
    sus = n.storage_units.query(
        "carrier in @coeffs.index and not cyclic_state_of_charge"
    )
    if not sus.empty:
        if multi:
            raise NotImplementedError(
                "shim: non-cyclic storage depletion not implemented for "
                "multi-invest (mirrors the guarded branches in "
                "define_primary_energy_limit)"
            )
        em_pu = coeffs[sus.carrier].set_axis(sus.index)
        soc = m["StorageUnit-state_of_charge"].sel(name=sus.index, snapshot=sns)
        soc_final = soc.ffill("snapshot").isel(snapshot=-1)
        terms.append((soc_final * -em_pu).sum() + em_pu @ sus.state_of_charge_initial)

    # non-cyclic stores: depletion (initial - final energy) * coeff
    stores = n.stores.query("carrier in @coeffs.index and not e_cyclic")
    if not stores.empty:
        if multi:
            raise NotImplementedError("shim: see storage unit note")
        em_pu = coeffs[stores.carrier].set_axis(stores.index)
        e = m["Store-e"].sel(name=stores.index, snapshot=sns)
        e_final = e.ffill("snapshot").isel(snapshot=-1)
        terms.append((e_final * -em_pu).sum() + em_pu @ stores.e_initial)

    return merge(terms) if len(terms) > 1 else terms[0]


def effect_value_post_solve(n, carrier_attribute="co2_emissions"):
    """Results-side recomputation from stored coefficients (no model needed).

    This is what `n.statistics.effect(...)` computes in Stage 1.
    """
    coeffs = n.carriers[carrier_attribute]
    coeffs = coeffs[coeffs != 0]
    weightings = n.snapshot_weightings
    if n._multi_invest:
        period_weighting = n.investment_period_weightings.years
        weightings = weightings.mul(period_weighting, level=0, axis=0)
    total = 0.0
    gens = n.generators[n.generators.carrier.isin(coeffs.index)]
    if not gens.empty:
        eff = n.get_switchable_as_dense("Generator", "efficiency")[gens.index]
        em_pu = (coeffs[gens.carrier].set_axis(gens.index) / eff).multiply(
            weightings.generators, axis=0
        )
        total += float((n.generators_t.p[gens.index] * em_pu).sum().sum())
    sus = n.storage_units.query(
        "carrier in @coeffs.index and not cyclic_state_of_charge"
    )
    if not sus.empty:
        em_pu = coeffs[sus.carrier].set_axis(sus.index)
        soc_final = n.storage_units_t.state_of_charge[sus.index].iloc[-1]
        total += float((em_pu * (sus.state_of_charge_initial - soc_final)).sum())
    stores = n.stores.query("carrier in @coeffs.index and not e_cyclic")
    if not stores.empty:
        em_pu = coeffs[stores.carrier].set_axis(stores.index)
        e_final = n.stores_t.e[stores.index].iloc[-1]
        total += float((em_pu * (stores.e_initial - e_final)).sum())
    return total


# ---------------------------------------------------------------------------
# Fixtures (self-contained, no downloads)
# ---------------------------------------------------------------------------


def fixture_basic(objective_weight=3.0, energy_weight=3.0):
    n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=24, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "coal", co2_emissions=0.34)
    n.add("Carrier", "wind", co2_emissions=0.0)
    n.add("Bus", "b")
    n.add("Generator", "gas", bus="b", carrier="gas", p_nom=100, marginal_cost=50,
          efficiency=0.55)
    n.add("Generator", "coal", bus="b", carrier="coal", p_nom=80, marginal_cost=30,
          efficiency=0.40)
    wind = 0.3 + 0.5 * np.sin(np.linspace(0, 3 * np.pi, 24)) ** 2
    n.add("Generator", "wind", bus="b", carrier="wind", p_nom=120, marginal_cost=0.1,
          p_max_pu=wind)
    load = 90 + 30 * np.cos(np.linspace(0, 2 * np.pi, 24))
    n.add("Load", "load", bus="b", p_set=load)
    n.snapshot_weightings["objective"] = objective_weight
    n.snapshot_weightings["generators"] = energy_weight
    n.snapshot_weightings["stores"] = energy_weight
    return n


def fixture_storage():
    n = fixture_basic()
    n.add("Carrier", "emitting_storage", co2_emissions=0.15)
    n.add(
        "StorageUnit", "gas storage", bus="b", carrier="emitting_storage",
        p_nom=40, max_hours=6, state_of_charge_initial=200,
        cyclic_state_of_charge=False, marginal_cost=1.0,
    )
    return n


def fixture_multiinvest():
    n = pypsa.Network(snapshots=range(8))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [10.0, 10.0]
    n.investment_period_weightings["objective"] = [10.0, 7.0]
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "wind", co2_emissions=0.0)
    n.add("Bus", "b")
    for period in n.investment_periods:
        n.add("Generator", f"gas-{period}", bus="b", carrier="gas", build_year=period,
              lifetime=30, p_nom_extendable=True, capital_cost=1000,
              marginal_cost=50, efficiency=0.55)
        n.add("Generator", f"wind-{period}", bus="b", carrier="wind",
              build_year=period, lifetime=30, p_nom_extendable=True,
              capital_cost=1500, marginal_cost=0.1,
              p_max_pu=pd.Series(
                  np.tile([0.2, 0.9, 0.5, 0.0, 0.7, 0.3, 0.8, 0.1], 2),
                  index=n.snapshots,
              ))
    load = pd.Series(np.tile([80.0, 90, 100, 110, 105, 95, 85, 100], 2),
                     index=n.snapshots)
    n.add("Load", "load", bus="b", p_set=load)
    return n


# ---------------------------------------------------------------------------
# Equivalence checks
# ---------------------------------------------------------------------------


def solve(n, **kwargs):
    status, condition = n.optimize(
        solver_name=SOLVER, solver_options=SOLVER_OPTS, **kwargs
    )
    assert status == "ok", (status, condition)
    return n


def e1_unbounded_stays_out():
    """Declared but unbounded coefficients leave the model untouched."""
    a = solve(fixture_basic())
    b = fixture_basic()
    b.carriers["co2_emissions"] = 0.0  # undeclared
    solve(b)
    assert not any(
        name.startswith("GlobalConstraint") for name in a.model.constraints
    ), "unbounded effect leaked a constraint into the model"
    assert_allclose(a.objective, b.objective, rtol=RTOL)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-6)
    co2 = effect_value_post_solve(a)
    assert co2 > 0
    return {"objective": a.objective, "co2 (post-solve)": co2}


def _binding_cap(n_fixture, frac=0.7, **opt_kwargs):
    n = solve(n_fixture(), **opt_kwargs)
    return frac * effect_value_post_solve(n)


def e2_bound_equivalence(n_fixture, label, **opt_kwargs):
    """effect_limit via shim expression == stock primary_energy GC."""
    cap = _binding_cap(n_fixture, **opt_kwargs)

    a = n_fixture()
    a.add("GlobalConstraint", "co2_cap", type="primary_energy",
          carrier_attribute="co2_emissions", sense="<=", constant=cap)
    solve(a, **opt_kwargs)
    mu_a = float(a.global_constraints.loc["co2_cap", "mu"])

    b = n_fixture()

    def add_effect_limit(n, sns):
        expr = effect_expression(n, sns)
        n.model.add_constraints(expr, "<=", cap, name="GlobalConstraint-co2_shim")

    solve(b, extra_functionality=add_effect_limit, **opt_kwargs)
    mu_b = float(b.model.constraints["GlobalConstraint-co2_shim"].dual)

    assert_allclose(a.objective, b.objective, rtol=RTOL, err_msg=label)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-5,
                    err_msg=label)
    assert_allclose(mu_a, mu_b, rtol=1e-6, err_msg=label)
    assert_allclose(effect_value_post_solve(a), cap, rtol=1e-6, err_msg=label)
    return {"objective A": a.objective, "objective B": b.objective,
            "dual A": mu_a, "dual B": mu_b}


def e3_price_equivalence(price=60.0):
    """co2.price == PyPSA-Eur marginal-cost folding, with objective- and
    energy-weighting deliberately different (3.0 vs 2.0)."""
    a = fixture_basic(objective_weight=3.0, energy_weight=2.0)
    ep = a.carriers.co2_emissions[a.generators.carrier].set_axis(a.generators.index)
    a.generators["marginal_cost"] += price * ep / a.generators.efficiency
    solve(a)

    b = fixture_basic(objective_weight=3.0, energy_weight=2.0)

    def price_effect(n, sns):
        m = n.model
        # priced contribution inherits the *objective* weighting from the
        # cost effect it feeds (per-snapshot chaining, cost-side rollup)
        expr = effect_expression(n, sns, weighting="objective")
        m.objective = m.objective.expression + price * expr

    solve(b, extra_functionality=price_effect)

    assert_allclose(a.objective, b.objective, rtol=RTOL)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-5)
    return {"objective A": a.objective, "objective B": b.objective}


def e4_objective_swap(slack=1.05):
    """objective_effect='co2' with a cost cap."""
    base = solve(fixture_basic())
    cost_opt = base.objective
    co2_costmin = effect_value_post_solve(base)

    n = fixture_basic()

    def swap(n, sns):
        m = n.model
        cost_expr = m.objective.expression
        m.add_constraints(cost_expr, "<=", slack * cost_opt,
                          name="GlobalConstraint-cost_cap")
        m.objective = effect_expression(n, sns)

    solve(n, extra_functionality=swap)
    co2_min = n.objective  # objective *is* the co2 effect now
    cost_b = float(n.model.constraints["GlobalConstraint-cost_cap"].lhs.solution)

    assert co2_min <= co2_costmin * (1 + 1e-9)
    assert cost_b <= slack * cost_opt * (1 + 1e-9)
    assert_allclose(co2_min, effect_value_post_solve(n), rtol=1e-6)
    return {"co2 @ cost-optimum": co2_costmin, "co2 minimized": co2_min,
            "cost cap": slack * cost_opt, "cost used": cost_b}


def main():
    results = {}
    results["E1 unbounded-stays-out"] = e1_unbounded_stays_out()
    results["E2 basic"] = e2_bound_equivalence(fixture_basic, "basic")
    results["E2 non-cyclic storage"] = e2_bound_equivalence(
        fixture_storage, "storage"
    )
    results["E2 multi-invest (.years weighting)"] = e2_bound_equivalence(
        fixture_multiinvest, "multiinvest", multi_investment_periods=True
    )
    results["E3 price == folding"] = e3_price_equivalence()
    results["E4 min co2 s.t. cost cap"] = e4_objective_swap()

    print("\n" + "=" * 72)
    for name, vals in results.items():
        print(f"\n{name}: PASS")
        for k, v in vals.items():
            print(f"    {k:>24}: {v:,.4f}")
    print("\nAll equivalence checks passed.")


if __name__ == "__main__":
    main()
