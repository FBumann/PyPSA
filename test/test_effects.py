# SPDX-FileCopyrightText: PyPSA Contributors
#
# SPDX-License-Identifier: MIT

"""Tests for the Effect component and n.statistics.effect."""

import contextlib

import numpy as np
import pandas as pd
import pytest
from numpy.testing import assert_allclose

import pypsa

with contextlib.suppress(ImportError), contextlib.suppress(Warning):
    # import outside the per-test warning filters: netCDF4 emits a
    # binary-compat RuntimeWarning on first import in some environments
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import netCDF4  # noqa: F401


@pytest.fixture
def n_basic():
    n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=12, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "coal", co2_emissions=0.34)
    n.add("Carrier", "wind")
    n.add("Bus", "b")
    n.add(
        "Generator",
        "gas",
        bus="b",
        carrier="gas",
        p_nom=100,
        marginal_cost=50,
        efficiency=0.55,
    )
    n.add(
        "Generator",
        "coal",
        bus="b",
        carrier="coal",
        p_nom=80,
        marginal_cost=30,
        efficiency=0.40,
    )
    n.add(
        "Generator",
        "wind",
        bus="b",
        carrier="wind",
        p_nom=120,
        marginal_cost=0.1,
        p_max_pu=0.3 + 0.5 * np.sin(np.linspace(0, 3 * np.pi, 12)) ** 2,
    )
    n.add(
        "Load", "load", bus="b", p_set=90 + 30 * np.cos(np.linspace(0, 2 * np.pi, 12))
    )
    n.snapshot_weightings.loc[:, :] = 2.0
    n.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions")
    return n


def manual_co2(n):
    """Reference computation of generator emissions + storage depletion."""
    coeffs = n.carriers.co2_emissions[lambda s: s != 0]
    gens = n.generators[n.generators.carrier.isin(coeffs.index)]
    eff = n.get_switchable_as_dense("Generator", "efficiency")[gens.index]
    em = coeffs[gens.carrier].set_axis(gens.index) / eff
    total = float(
        (n.generators_t.p[gens.index] * em)
        .multiply(n.snapshot_weightings.generators, axis=0)
        .sum()
        .sum()
    )
    sus = n.storage_units.query(
        "carrier in @coeffs.index and not cyclic_state_of_charge"
    )
    if not sus.empty:
        em = coeffs[sus.carrier].set_axis(sus.index)
        soc_final = n.storage_units_t.state_of_charge[sus.index].iloc[-1]
        total += float((em * (sus.state_of_charge_initial - soc_final)).sum())
    return total


def test_effect_component_registration():
    n = pypsa.Network()
    n.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions")
    n.add("Effect", "land_use", unit="ha")
    assert list(n.effects.index) == ["co2", "land_use"]
    assert n.effects.at["co2", "accounting"] == "physical"
    assert "price" in n.effects_t


def test_effect_io_roundtrip(tmp_path):
    n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions")
    n.effects_t.price["co2"] = pd.Series([10.0, 20.0, 30.0], index=n.snapshots)

    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    pd.testing.assert_frame_equal(m.effects, n.effects)
    pd.testing.assert_frame_equal(
        m.effects_t.price, n.effects_t.price, check_freq=False
    )

    csv_path = tmp_path / "csvdir"
    n.export_to_csv_folder(csv_path)
    m = pypsa.Network()
    m.import_from_csv_folder(csv_path)
    pd.testing.assert_frame_equal(m.effects, n.effects)
    pd.testing.assert_frame_equal(
        m.effects_t.price, n.effects_t.price, check_freq=False
    )


def test_effect_statistic_matches_manual(n_basic):
    n = n_basic
    n.optimize()
    ref = manual_co2(n)
    assert ref > 0
    assert_allclose(float(n.statistics.effect("co2").sum()), ref, rtol=1e-9)


def test_effect_statistic_per_asset(n_basic):
    n = n_basic
    n.optimize()
    per_asset = n.statistics.effect("co2", groupby=False)
    assert set(per_asset.index.get_level_values(-1)) == {"gas", "coal"}
    assert_allclose(float(per_asset.sum()), manual_co2(n), rtol=1e-9)
    ts = n.statistics.effect("co2", groupby=False, groupby_time=False)
    assert ts.shape[1] == len(n.snapshots)


def test_effect_statistic_storage_depletion(n_basic):
    n = n_basic
    n.add("Carrier", "emitting_storage", co2_emissions=0.15)
    n.add(
        "StorageUnit",
        "gs",
        bus="b",
        carrier="emitting_storage",
        p_nom=40,
        max_hours=6,
        state_of_charge_initial=100,
        cyclic_state_of_charge=False,
        marginal_cost=1.0,
    )
    n.optimize()
    res = n.statistics.effect("co2")
    assert ("StorageUnit", "emitting_storage") in res.index
    assert_allclose(float(res.sum()), manual_co2(n), rtol=1e-9)


def test_effect_statistic_multiinvest():
    n = pypsa.Network(snapshots=range(8))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [10.0, 10.0]
    n.investment_period_weightings["objective"] = [10.0, 7.0]
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Bus", "b")
    for period in n.investment_periods:
        n.add(
            "Generator",
            f"gas-{period}",
            bus="b",
            carrier="gas",
            build_year=period,
            lifetime=30,
            p_nom_extendable=True,
            capital_cost=1000,
            marginal_cost=50,
            efficiency=0.55,
        )
    n.add("Load", "load", bus="b", p_set=pd.Series(100.0, index=n.snapshots))
    n.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions")
    n.optimize(multi_investment_periods=True)

    res = n.statistics.effect("co2")
    # physical accounting: contributions weighted with `years`, not `objective`
    manual = float(
        (n.generators_t.p / 0.55 * 0.2)
        .multiply(n.snapshot_weightings.generators, axis=0)
        .groupby(level="period")
        .sum()
        .sum(axis=1)
        .mul(n.investment_period_weightings.years)
        .sum()
    )
    # rtol reflects the statistics module's default rounding (round=2),
    # applied before the period weighting
    assert_allclose(float(res.sum().sum()), manual, rtol=1e-6)


def _coeffs_by_var(expr):
    flat = expr.flat
    return flat.groupby("vars").coeffs.sum().sort_index()


def test_build_effect_expression_cost_matches_objective(n_basic):
    from linopy import merge

    from pypsa.optimization.effects import build_effect_expression

    n = n_basic
    n.optimize.create_model()
    capex, opex = build_effect_expression(n, "cost", n.snapshots)
    expr = merge(capex + opex) if capex else merge(opex)
    pd.testing.assert_series_equal(
        _coeffs_by_var(expr),
        _coeffs_by_var(n.model.objective.expression),
    )


@pytest.mark.parametrize("with_storage", [False, True])
def test_build_effect_expression_matches_primary_energy(n_basic, with_storage):
    from linopy import merge

    from pypsa.optimization.effects import build_effect_expression

    n = n_basic
    if with_storage:
        n.add("Carrier", "emitting_storage", co2_emissions=0.15)
        n.add(
            "StorageUnit",
            "gs",
            bus="b",
            carrier="emitting_storage",
            p_nom=40,
            max_hours=6,
            state_of_charge_initial=100,
            cyclic_state_of_charge=False,
            marginal_cost=1.0,
        )
    n.add(
        "GlobalConstraint",
        "co2_cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=1000.0,
    )
    n.optimize.create_model()

    _, opex = build_effect_expression(n, "co2", n.snapshots)
    expr = merge(opex)
    con = n.model.constraints["GlobalConstraint-co2_cap"]
    pd.testing.assert_series_equal(
        _coeffs_by_var(expr), _coeffs_by_var(con.lhs), check_names=False
    )
    # constants of the expression (storage initial SoC) are moved to the rhs
    const = float(expr.const.sum()) if hasattr(expr, "const") else 0.0
    assert_allclose(float(con.rhs), 1000.0 - const)


def test_build_effect_expression_multiinvest_matches_primary_energy():
    from linopy import merge

    from pypsa.optimization.effects import build_effect_expression

    n = pypsa.Network(snapshots=range(8))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [10.0, 10.0]
    n.investment_period_weightings["objective"] = [10.0, 7.0]
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Bus", "b")
    for period in n.investment_periods:
        n.add(
            "Generator",
            f"gas-{period}",
            bus="b",
            carrier="gas",
            build_year=period,
            lifetime=30,
            p_nom_extendable=True,
            capital_cost=1000,
            marginal_cost=50,
            efficiency=0.55,
        )
    n.add("Load", "load", bus="b", p_set=pd.Series(100.0, index=n.snapshots))
    n.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions")
    n.add(
        "GlobalConstraint",
        "co2_cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=1e6,
    )
    n.optimize.create_model(multi_investment_periods=True)

    _, opex = build_effect_expression(n, "co2", n.snapshots)
    expr = merge(opex)
    con = n.model.constraints["GlobalConstraint-co2_cap"]
    pd.testing.assert_series_equal(
        _coeffs_by_var(expr), _coeffs_by_var(con.lhs), check_names=False
    )


# --- Stage 3: effect_limit, price coupling, objective_effect, closure ---


def test_effect_limit_equivalent_to_primary_energy(n_basic):
    a = n_basic.copy()
    a.add(
        "GlobalConstraint",
        "cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=500.0,
    )
    a.optimize()

    b = n_basic.copy()
    b.add(
        "GlobalConstraint",
        "cap",
        type="effect_limit",
        carrier_attribute="co2",
        sense="<=",
        constant=500.0,
    )
    b.optimize()

    assert_allclose(a.objective, b.objective, rtol=1e-9)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-5)
    assert_allclose(
        float(a.global_constraints.loc["cap", "mu"]),
        float(b.global_constraints.loc["cap", "mu"]),
        rtol=1e-6,
    )
    assert float(b.global_constraints.loc["cap", "mu"]) != 0.0  # cap binds
    assert_allclose(float(b.statistics.effect("co2").sum()), 500.0, rtol=1e-6)


def test_effect_price_equivalent_to_marginal_cost_folding():
    def build(objective_weight, energy_weight):
        n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=12, freq="h"))
        n.add("Carrier", "gas", co2_emissions=0.2)
        n.add("Carrier", "coal", co2_emissions=0.34)
        n.add("Bus", "b")
        n.add(
            "Generator",
            "gas",
            bus="b",
            carrier="gas",
            p_nom=100,
            marginal_cost=50,
            efficiency=0.55,
        )
        n.add(
            "Generator",
            "coal",
            bus="b",
            carrier="coal",
            p_nom=80,
            marginal_cost=30,
            efficiency=0.40,
        )
        n.add(
            "Load",
            "load",
            bus="b",
            p_set=90 + 30 * np.cos(np.linspace(0, 2 * np.pi, 12)),
        )
        n.snapshot_weightings["objective"] = objective_weight
        n.snapshot_weightings["generators"] = energy_weight
        return n

    price = 60.0
    # deliberately different objective and energy weightings: the priced
    # contribution must inherit the objective weighting (like folding)
    a = build(3.0, 2.0)
    ep = a.carriers.co2_emissions[a.generators.carrier].set_axis(a.generators.index)
    a.generators["marginal_cost"] += price * ep / a.generators.efficiency
    a.optimize()

    b = build(3.0, 2.0)
    b.add("Effect", "co2", unit="tCO2", carrier_attribute="co2_emissions", price=price)
    b.optimize()

    assert_allclose(a.objective, b.objective, rtol=1e-9)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-5)


def test_objective_effect_swap_with_cost_cap(n_basic):
    base = n_basic.copy()
    base.optimize()
    cost_opt = base.objective
    co2_costmin = float(base.statistics.effect("co2").sum())

    n = n_basic.copy()
    n.add(
        "GlobalConstraint",
        "cost_cap",
        type="effect_limit",
        carrier_attribute="cost",
        sense="<=",
        constant=1.05 * cost_opt,
    )
    n.optimize(objective_effect="co2")

    co2_min = n.objective  # the objective *is* the co2 effect
    assert co2_min <= co2_costmin * (1 + 1e-9)
    assert_allclose(co2_min, float(n.statistics.effect("co2").sum()), rtol=1e-6)
    # cost cap must bind (co2 can always be reduced by shifting to gas)
    mu = float(n.global_constraints.loc["cost_cap", "mu"])
    assert mu != 0.0


def test_unbounded_effect_stays_out_of_model(n_basic):
    from pypsa.optimization.effects import materialized_effects

    a = n_basic.copy()  # co2 effect declared, unbounded, unpriced
    a.add("Effect", "land_use", unit="ha")
    a.optimize()

    b = n_basic.copy()
    b.remove("Effect", ["co2"])
    b.optimize()

    assert materialized_effects(a) == {"cost"}
    assert not any(name.startswith("GlobalConstraint") for name in a.model.constraints)
    assert_allclose(a.objective, b.objective, rtol=1e-12)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-9)


def test_materialized_effects_closure(n_basic):
    from pypsa.optimization.effects import materialized_effects

    n = n_basic
    n.add("Effect", "land_use", unit="ha")
    n.add("Effect", "priced", unit="u", carrier_attribute="co2_emissions", price=1.0)
    n.add(
        "GlobalConstraint",
        "cap",
        type="effect_limit",
        carrier_attribute="co2",
        sense="<=",
        constant=900.0,
    )
    # objective=cost: cost + bounded co2 + priced effect; land_use stays out
    assert materialized_effects(n) == {"cost", "co2", "priced"}
    # objective=co2 without any cost bound: priced effect no longer needed
    assert materialized_effects(n.copy(), "co2") >= {"co2"}


def test_effect_limit_unknown_effect_raises(n_basic):
    n = n_basic
    n.add(
        "GlobalConstraint",
        "cap",
        type="effect_limit",
        carrier_attribute="does_not_exist",
        sense="<=",
        constant=1.0,
    )
    with pytest.raises(ValueError, match="not defined in n.effects"):
        n.optimize()


def test_effect_statistic_unknown_effect(n_basic):
    n = n_basic
    with pytest.raises(ValueError, match="not defined in n.effects"):
        n.statistics.effect("primary_energy")


def test_effect_statistic_no_coefficients(n_basic):
    n = n_basic
    n.add("Effect", "land_use", unit="ha")
    with pytest.raises(ValueError, match="no coefficient sources"):
        n.statistics.effect("land_use")


# --- per-component coefficients: marginal_effect_* / capital_effect_* ---


@pytest.fixture
def n_direct():
    n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=12, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "wind")
    n.add("Bus", "b")
    n.add(
        "Generator",
        "gas",
        bus="b",
        carrier="gas",
        p_nom=150,
        marginal_cost=50,
        efficiency=0.5,
        marginal_effect_water=1.9,
    )
    n.add(
        "Generator",
        "wind",
        bus="b",
        carrier="wind",
        marginal_cost=0.1,
        p_max_pu=0.3 + 0.5 * np.sin(np.linspace(0, 3, 12)) ** 2,
        p_nom_extendable=True,
        capital_cost=60,
        capital_effect_land_use=0.5,
    )
    n.add("Load", "load", bus="b", p_set=100.0)
    n.add("Effect", "water", unit="m3")
    n.add("Effect", "land_use", unit="ha")
    return n


def test_direct_operational_statistic(n_direct):
    n = n_direct
    n.optimize(include_objective_constant=False)
    manual = float((n.generators_t.p["gas"] * 1.9).sum())
    assert manual > 0
    assert_allclose(float(n.statistics.effect("water").sum()), manual, rtol=1e-6)


def test_direct_capacity_statistic_and_limit(n_direct):
    unconstrained = n_direct.copy()
    unconstrained.optimize(include_objective_constant=False)
    build = float(unconstrained.generators.p_nom_opt["wind"])
    assert build > 100  # land cap below is binding
    assert_allclose(
        float(unconstrained.statistics.effect("land_use").sum()),
        0.5 * build,
        rtol=1e-6,
    )

    n = n_direct
    n.add(
        "GlobalConstraint",
        "land_cap",
        type="effect_limit",
        carrier_attribute="land_use",
        sense="<=",
        constant=30.0,
    )
    n.optimize(include_objective_constant=False)
    assert_allclose(float(n.generators.p_nom_opt["wind"]), 60.0, rtol=1e-6)
    assert_allclose(float(n.statistics.effect("land_use").sum()), 30.0, rtol=1e-6)
    assert float(n.global_constraints.loc["land_cap", "mu"]) != 0.0


def test_direct_capacity_nonextendable_is_constant(n_direct):
    n_direct.generators.loc["wind", "p_nom_extendable"] = False
    n_direct.generators.loc["wind", "p_nom"] = 80.0
    m = n_direct.copy()

    n_direct.optimize(include_objective_constant=False)
    assert_allclose(
        float(n_direct.statistics.effect("land_use").sum()), 40.0, rtol=1e-9
    )
    # a bound on a purely constant contribution shifts the rhs
    m.add(
        "GlobalConstraint",
        "land_cap",
        type="effect_limit",
        carrier_attribute="land_use",
        sense="<=",
        constant=50.0,
    )
    status, _ = m.optimize(include_objective_constant=False)
    assert status == "ok"


def test_direct_price_equivalent_to_folding(n_direct):
    a = n_direct.copy()
    a.generators.loc["gas", "marginal_cost"] += 2.0 * 1.9
    a.optimize(include_objective_constant=False)

    b = n_direct.copy()
    b.effects.loc["water", "price"] = 2.0
    b.optimize(include_objective_constant=False)

    assert_allclose(a.objective, b.objective, rtol=1e-9)
    assert_allclose(a.generators_t.p.values, b.generators_t.p.values, atol=1e-5)


def test_direct_effect_unbounded_stays_out(n_direct):
    from pypsa.optimization.effects import materialized_effects

    n = n_direct
    n.optimize(include_objective_constant=False)
    assert materialized_effects(n) == {"cost"}
    assert not any(k.startswith("GlobalConstraint") for k in n.model.constraints)


def test_direct_mixed_with_carrier_route(n_direct):
    n = n_direct
    # co2 via carrier route plus an additional direct co2 coefficient
    n.add("Effect", "co2", unit="t", carrier_attribute="co2_emissions")
    n.generators.loc["gas", "marginal_effect_co2"] = 0.05
    n.optimize(include_objective_constant=False)
    p_gas = n.generators_t.p["gas"]
    manual = float((p_gas / 0.5 * 0.2).sum() + (p_gas * 0.05).sum())
    assert_allclose(float(n.statistics.effect("co2").sum()), manual, rtol=1e-6)


# --- dict-form effect kwargs and consistency ---


def test_dict_kwarg_equivalent_to_flat_columns():
    def base():
        n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=6, freq="h"))
        n.add("Carrier", "gas")
        n.add("Bus", "b")
        n.add("Effect", "water", unit="m3")
        n.add("Effect", "land_use", unit="ha")
        n.add("Load", "l", bus="b", p_set=60.0)
        return n

    a = base()
    a.add(
        "Generator",
        "g",
        bus="b",
        carrier="gas",
        p_nom=100,
        marginal_cost=50,
        marginal_effect_water=1.9,
        capital_effect_land_use=0.5,
    )
    b = base()
    b.add(
        "Generator",
        "g",
        bus="b",
        carrier="gas",
        p_nom=100,
        marginal_cost=50,
        marginal_effect={"water": 1.9},
        capital_effect={"land_use": 0.5},
    )
    cols = ["marginal_effect_water", "capital_effect_land_use"]
    pd.testing.assert_frame_equal(a.generators[cols], b.generators[cols])


def test_dict_kwarg_arbitrary_effect_names(tmp_path):
    n = pypsa.Network(snapshots=pd.date_range("2026-01-01", periods=6, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b")
    n.add("Effect", "Water Consumption ABC", unit="m3")
    n.add(
        "Generator",
        "g",
        bus="b",
        carrier="gas",
        p_nom=100,
        marginal_cost=50,
        marginal_effect={"Water Consumption ABC": 1.9},
    )
    n.add("Load", "l", bus="b", p_set=60.0)
    n.optimize(include_objective_constant=False)
    manual = float((n.generators_t.p["g"] * 1.9).sum())
    assert_allclose(
        float(n.statistics.effect("Water Consumption ABC").sum()), manual, rtol=1e-6
    )
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    col = "marginal_effect_Water Consumption ABC"
    assert float(m.generators.at["g", col]) == 1.9


def test_dict_kwarg_undeclared_effect_warns(caplog):
    n = pypsa.Network()
    n.add("Bus", "b")
    with caplog.at_level("WARNING"):
        n.add("Generator", "g", bus="b", p_nom=10, marginal_effect={"typo": 1.0})
    assert "not declared in n.effects" in caplog.text


def test_dict_kwarg_conflicting_inputs_raises():
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Effect", "water", unit="m3")
    with pytest.raises(ValueError, match="passed both via"):
        n.add(
            "Generator",
            "g",
            bus="b",
            p_nom=10,
            marginal_effect={"water": 1.0},
            marginal_effect_water=2.0,
        )


def test_consistency_check_orphan_effect_column():
    from pypsa.consistency import ConsistencyError

    n = pypsa.Network(snapshots=range(2))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=10, marginal_cost=1.0)
    n.generators["capital_effect_landuse"] = 0.5  # typo: land_use not declared
    with pytest.raises(ConsistencyError, match="not.*declared in n.effects"):
        n.consistency_check(strict=["effect_columns"])
