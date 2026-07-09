# SPDX-FileCopyrightText: PyPSA Contributors
#
# SPDX-License-Identifier: MIT

"""Build effect expressions for optimisation problems.

Effects are named tracked quantities declared in ``n.effects`` (see the
Effect component). This module assembles their linopy expressions from the
same building blocks the objective and the ``primary_energy`` global
constraint use: [build_cost_terms][pypsa.optimization.optimize.build_cost_terms]
for the built-in ``cost`` effect and
`_carrier_contribution_terms` for carrier-derived effects.

An effect enters the model only if it is the objective
(``objective_effect``), bounded by an ``effect_limit`` global constraint,
or priced into the cost effect (``price`` attribute). All other declared
effects never touch the model and are reported post-solve via
``n.statistics.effect``. Effects add no decision variables; their
expressions are linear in existing variables.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from linopy.expressions import merge
from numpy import isnan

from pypsa.descriptors import nominal_attrs

if TYPE_CHECKING:
    from pypsa import Network

logger = logging.getLogger(__name__)


def operational_effect_attr(effect: str) -> str:
    """Column name of per-unit-of-operation coefficients for `effect`.

    The per-component analog of `marginal_cost`: a static column
    ``marginal_effect_{effect}`` on a component table contributes
    `coefficient x operation` per snapshot to the effect (e.g. water use in
    m3/MWh of one specific plant).
    """
    return f"marginal_effect_{effect}"


def capacity_effect_attr(effect: str) -> str:
    """Column name of per-unit-of-capacity coefficients for `effect`.

    The per-component analog of `capital_cost`: a static column
    ``capital_effect_{effect}`` on a component table contributes
    `coefficient x nominal capacity` per active period to the effect (e.g.
    land use in ha/MW).
    """
    return f"capital_effect_{effect}"


def _has_direct_coefficients(n: Network, effect: str) -> bool:
    cols = {operational_effect_attr(effect), capacity_effect_attr(effect)}
    return any(
        not cols.isdisjoint(n.c[c].static.columns)
        or operational_effect_attr(effect) in n.c[c].dynamic
        for c in n.all_components
        if not n.c[c].static.empty
    )


def _dense_operational_coefficients(
    c: Any, col: str, sns: pd.Index
) -> pd.DataFrame | None:
    """Combine static and time-varying operational effect coefficients.

    Mirrors `marginal_cost` semantics: the static column provides the
    per-asset default, a DataFrame under the same key in the dynamic dict
    overrides it per snapshot (and may cover assets with no static value).
    Returns a dense (snapshot x asset) frame of nonzero coefficients, or
    None.
    """
    static = c.static.get(col)
    parts = None
    if static is not None:
        coeff = pd.to_numeric(static, errors="coerce").fillna(0.0)
        coeff = coeff[coeff != 0]
        if not coeff.empty:
            parts = pd.DataFrame(
                np.repeat(coeff.values[None, :], len(sns), axis=0),
                index=sns,
                columns=coeff.index,
            )
    dynamic = c.dynamic.get(col)
    if dynamic is not None and not dynamic.empty:
        dynamic = dynamic.reindex(sns).fillna(0.0)
        if parts is None:
            parts = dynamic
        else:
            parts = parts.drop(
                columns=dynamic.columns.intersection(parts.columns)
            ).join(dynamic)
    if parts is None:
        return None
    parts = parts.loc[:, (parts != 0).any()]
    return None if parts.empty else parts.rename_axis(columns="name")


def _direct_effect_terms(
    n: Network,
    effect: str,
    sns: pd.Index,
    flow_weight_col: str,
    period_weight_col: str,
) -> tuple[list[Any], float]:
    """Build terms from per-component coefficient columns.

    Reads ``marginal_effect_{effect}`` (per unit of the component's
    operational variable, snapshot-weighted like the corresponding
    `marginal_cost` term; static or series — a DataFrame under the same
    key in the dynamic dict overrides the static column per snapshot, e.g.
    a COP(t) series for heat output per unit electricity of one heat pump)
    and ``capital_effect_{effect}`` (per unit of nominal capacity and
    active period, like the `capital_cost` term but without
    annuitization; static only). Contributions of non-extendable capacity
    are constants and returned separately.
    """
    from pypsa.optimization.optimize import lookup  # noqa: PLC0415

    m = n.model
    terms: list[Any] = []
    constant = 0.0

    if n._multi_invest:
        periods = sns.unique("period")
        period_weighting = n.investment_period_weightings[period_weight_col][periods]

    # operational channel: coefficient x operation x snapshot weighting
    weighting = n.snapshot_weightings[flow_weight_col]
    if n._multi_invest:
        weighting = weighting.mul(period_weighting, level=0)
    weighting = weighting.loc[sns]

    op_col = operational_effect_attr(effect)
    for c_name, attr in lookup.query("marginal_cost").index:
        c = n.c[c_name]
        if c.static.empty:
            continue
        dense = _dense_operational_coefficients(c, op_col, sns)
        if dense is None:
            continue
        assets = dense.columns.difference(c.inactive_assets)
        if assets.empty:
            continue
        em = dense[assets].mul(weighting, axis=0)
        var = m[f"{c_name}-{attr}"].sel(name=assets, snapshot=sns)
        terms.append((var * em).sum())

    # capacity channel: coefficient x nominal capacity x active periods
    cap_col = capacity_effect_attr(effect)
    for c_name, attr in nominal_attrs.items():
        c = n.c[c_name]
        if c.static.empty or cap_col not in c.static.columns:
            continue
        coeff = pd.to_numeric(c.static[cap_col], errors="coerce").fillna(0.0)
        assets = coeff.index[coeff != 0].difference(c.inactive_assets)
        if assets.empty:
            continue
        coeff_da = coeff[assets].rename_axis("name").to_xarray()
        if n._multi_invest:
            weighted_coeff = 0
            for period in periods:
                active = c.da.active.sel(period=period, name=assets).any(dim="timestep")
                weighted_coeff = (
                    weighted_coeff + active * coeff_da * period_weighting.loc[period]
                )
        else:
            active = c.da.active.sel(name=assets).any(dim="snapshot")
            weighted_coeff = active * coeff_da
        ext = assets.intersection(c.extendables)
        fix = assets.difference(c.extendables)
        if not ext.empty:
            terms.append(
                (
                    m[f"{c_name}-{attr}"].sel(name=ext) * weighted_coeff.sel(name=ext)
                ).sum(dim=["name"])
            )
        if not fix.empty:
            constant += float(
                (weighted_coeff.sel(name=fix) * c.da[attr].sel(name=fix)).sum()
            )
    return terms, constant


def _effects_static(n: Network) -> pd.DataFrame:
    effects = n.c.effects.static
    if isinstance(effects.index, pd.MultiIndex):
        msg = "Effect expressions are not yet supported for stochastic networks."
        raise NotImplementedError(msg)
    return effects


def _carrier_effect_terms(
    n: Network,
    coeffs: pd.Series,
    sns: pd.Index,
    flow_weight_col: str,
    period_weight_col: str,
) -> list[Any]:
    """Build carrier-derived contribution terms with the given weighting."""
    from pypsa.optimization.global_constraints import (  # noqa: PLC0415
        _carrier_contribution_terms,
    )

    weightings = n.snapshot_weightings.loc[sns]
    period_weighting = None
    storage_weightings = None
    period_last_sns = None
    if n._multi_invest:
        period_weighting = n.investment_period_weightings[period_weight_col][
            sns.unique("period")
        ]
        weightings = weightings.mul(period_weighting, level=0, axis=0)
        period_last_sns = pd.MultiIndex.from_frame(
            sns.to_frame(index=False).groupby("period").timestep.last().reset_index()
        )
        storage_weightings = (
            pd.Series(1, n.snapshots).mul(period_weighting).loc[period_last_sns]
        )

    return _carrier_contribution_terms(
        n,
        n.model,
        coeffs,
        sns,
        slice(None),
        weightings,
        None,
        slice(None),
        period_weighting=period_weighting,
        storage_weightings=storage_weightings,
        period_last_sns=period_last_sns,
        flow_weight_col=flow_weight_col,
    )


def _priced_effect_terms(n: Network, sns: pd.Index) -> list[Any]:
    """Build the cost contributions of priced effects (e.g. a CO2 price).

    Priced contributions inherit the ``objective`` weightings from the cost
    effect they feed, mirroring the folding of emission prices into
    marginal costs (per-snapshot coupling, cost-side aggregation).
    """
    effects = n.c.effects.static
    if effects.empty or "price" not in effects:
        return []
    if isinstance(effects.index, pd.MultiIndex):
        if (effects.price != 0).any():
            msg = "Priced effects are not yet supported for stochastic networks."
            raise NotImplementedError(msg)
        return []

    priced = effects.query("price != 0")
    dynamic_price = n.c.effects.dynamic.get("price")
    if dynamic_price is not None and not dynamic_price.empty:
        msg = "Time-varying effect prices are not yet supported."
        raise NotImplementedError(msg)
    if priced.empty:
        return []
    if "cost" in priced.index:
        msg = "The cost effect cannot carry a price (it would price into itself)."
        raise ValueError(msg)

    terms = []
    for name, row in priced.iterrows():
        if not row.carrier_attribute and not _has_direct_coefficients(n, name):
            msg = (
                f"Priced effect '{name}' has no coefficient sources: neither "
                f"`carrier_attribute` is set nor does any component carry "
                f"`{operational_effect_attr(name)}` or "
                f"`{capacity_effect_attr(name)}` columns."
            )
            raise ValueError(msg)
        effect_terms = []
        if row.carrier_attribute:
            coeffs = n.c.carriers.static[row.carrier_attribute]
            coeffs = coeffs[coeffs != 0]
            if not coeffs.empty:
                effect_terms += _carrier_effect_terms(
                    n,
                    coeffs,
                    sns,
                    flow_weight_col="objective",
                    period_weight_col="objective",
                )
        direct_terms, direct_constant = _direct_effect_terms(
            n, name, sns, "objective", "objective"
        )
        effect_terms += direct_terms
        if direct_constant and effect_terms:
            effect_terms[-1] = effect_terms[-1] + direct_constant
        terms.extend(float(row.price) * term for term in effect_terms)
    return terms


def build_effect_expression(
    n: Network, name: str, sns: pd.Index
) -> tuple[list[Any], list[Any]]:
    """Build the capex and opex expression terms of an effect over `sns`.

    For the built-in ``cost`` effect this returns exactly the terms the
    objective is assembled from (excluding the objective constant),
    including the contributions of priced effects. For effects with
    `carrier_attribute` set, contributions follow the `primary_energy`
    semantics (generator output divided by efficiency times the carrier
    coefficient, non-cyclic storage depletion), weighted according to the
    effect's `accounting` scheme: ``physical`` uses the
    `generators`/`stores` snapshot weightings and `years` investment period
    weighting, ``cost`` uses the `objective` weightings.

    Effects add no decision variables; the returned terms are linear
    expressions of existing variables.

    Parameters
    ----------
    n : pypsa.Network
        The network with a built model (`n.model`).
    name : str
        Name of the effect. Either ``"cost"`` (always available) or a name
        present in `n.effects`.
    sns : pd.Index
        Snapshots (and, for multi-investment, periods) over which to build
        the expression.

    Returns
    -------
    tuple[list, list]
        Lists of capex and opex linopy expression terms.

    """
    from pypsa.optimization.optimize import build_cost_terms  # noqa: PLC0415

    if name == "cost":
        capex_terms, opex_terms, _ = build_cost_terms(n, sns)
        opex_terms = opex_terms + _priced_effect_terms(n, sns)
        return capex_terms, opex_terms

    effects = _effects_static(n)
    if name not in effects.index:
        msg = (
            f"Effect '{name}' is not defined in n.effects. "
            f"Available effects: {list(effects.index)}."
        )
        raise ValueError(msg)

    carrier_attribute = effects.at[name, "carrier_attribute"]
    accounting = effects.at[name, "accounting"]
    if not carrier_attribute and not _has_direct_coefficients(n, name):
        msg = (
            f"Effect '{name}' has no coefficient sources: neither "
            f"`carrier_attribute` is set nor does any component carry "
            f"`{operational_effect_attr(name)}` or "
            f"`{capacity_effect_attr(name)}` columns."
        )
        raise ValueError(msg)

    period_weight_col = "years" if accounting == "physical" else "objective"
    flow_weight_col = "generators" if accounting == "physical" else "objective"

    opex_terms = []
    if carrier_attribute:
        coeffs = n.c.carriers.static[carrier_attribute]
        coeffs = coeffs[coeffs != 0]
        if not coeffs.empty:
            opex_terms += _carrier_effect_terms(
                n,
                coeffs,
                sns,
                flow_weight_col=flow_weight_col,
                period_weight_col=period_weight_col,
            )

    direct_terms, direct_constant = _direct_effect_terms(
        n, name, sns, flow_weight_col, period_weight_col
    )
    opex_terms += direct_terms
    if direct_constant:
        if opex_terms:
            opex_terms[-1] = opex_terms[-1] + direct_constant
        else:
            logger.warning(
                "Effect '%s' only has constant contributions from "
                "non-extendable capacity; they cannot enter the model.",
                name,
            )
    return [], opex_terms


def materialized_effects(n: Network, objective_effect: str = "cost") -> set[str]:
    """Return the set of effects that enter the model.

    An effect is materialized iff it is the objective, bounded by an
    ``effect_limit`` global constraint, or (transitively) feeds a
    materialized effect — with the price coupling, that means priced
    effects are materialized whenever ``cost`` is. All other declared
    effects never enter the model and are recomputed post-solve from their
    stored coefficients.
    """
    needed = {objective_effect}
    glcs = n.c.global_constraints.static
    if not glcs.empty:
        limits = glcs.query('type == "effect_limit"')
        needed |= set(limits.carrier_attribute)
    effects = n.c.effects.static
    if "cost" in needed and not effects.empty and "price" in effects:
        if isinstance(effects.index, pd.MultiIndex):
            priced = effects[effects.price != 0].index.get_level_values("name")
        else:
            priced = effects.index[effects.price != 0]
        needed |= set(priced)
    return needed


def define_effect_limit(n: Network, sns: pd.Index) -> None:
    """Define bounds on effects from ``effect_limit`` global constraints.

    The ``carrier_attribute`` field of the global constraint names the
    bounded effect. As for other global constraints, ``investment_period``
    restricts the bound to one period (NaN bounds the whole horizon), and
    the shadow price is written back to ``mu``.

    Parameters
    ----------
    n : pypsa.Network
        The network to apply constraints to.
    sns : list-like
        Set of snapshots to which the constraint should be applied.

    """
    glcs = n.c.global_constraints.static.query('type == "effect_limit"')
    if glcs.empty:
        return
    if n.has_scenarios:
        msg = "effect_limit constraints are not yet supported for stochastic networks."
        raise NotImplementedError(msg)

    m = n.model
    for name, glc in glcs.iterrows():
        effect = glc.carrier_attribute
        if isnan(glc.investment_period):
            sns_use = sns
        elif glc.investment_period in sns.unique("period"):
            sns_use = sns[sns.get_loc(glc.investment_period)]
        else:
            continue

        capex_terms, opex_terms = build_effect_expression(n, effect, sns_use)
        terms = capex_terms + opex_terms
        if not terms:
            logger.warning(
                "Effect limit '%s' on effect '%s' has no contributions and is skipped.",
                name,
                effect,
            )
            continue
        lhs = merge(terms) if len(terms) > 1 else terms[0]
        m.add_constraints(lhs, glc.sense, glc.constant, name=f"GlobalConstraint-{name}")
