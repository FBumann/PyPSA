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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from pypsa import Network

logger = logging.getLogger(__name__)


def build_effect_expression(
    n: Network, name: str, sns: pd.Index
) -> tuple[list[Any], list[Any]]:
    """Build the capex and opex expression terms of an effect over `sns`.

    For the built-in ``cost`` effect this returns exactly the terms the
    objective is assembled from (excluding the objective constant). For
    effects with `carrier_attribute` set, contributions follow the
    `primary_energy` semantics (generator output divided by efficiency
    times the carrier coefficient, non-cyclic storage depletion), weighted
    according to the effect's `accounting` scheme: ``physical`` uses the
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
    # Lazy imports to avoid circular imports (optimize imports
    # global_constraints; both are needed here).
    from pypsa.optimization.global_constraints import (  # noqa: PLC0415
        _carrier_contribution_terms,
    )
    from pypsa.optimization.optimize import build_cost_terms  # noqa: PLC0415

    if name == "cost" and name not in n.c.effects.static.index:
        capex_terms, opex_terms, _ = build_cost_terms(n, sns)
        return capex_terms, opex_terms

    effects = n.c.effects.static
    if isinstance(effects.index, pd.MultiIndex):
        msg = "Effect expressions are not yet supported for stochastic networks."
        raise NotImplementedError(msg)
    if name not in effects.index:
        msg = (
            f"Effect '{name}' is not defined in n.effects. "
            f"Available effects: {list(effects.index)}."
        )
        raise ValueError(msg)

    carrier_attribute = effects.at[name, "carrier_attribute"]
    accounting = effects.at[name, "accounting"]
    if not carrier_attribute:
        msg = (
            f"Effect '{name}' has no `carrier_attribute` set; there are no "
            "coefficient sources to build an expression from."
        )
        raise ValueError(msg)

    coeffs = n.c.carriers.static[carrier_attribute]
    coeffs = coeffs[coeffs != 0]
    if coeffs.empty:
        return [], []

    period_weight_col = "years" if accounting == "physical" else "objective"
    flow_weight_col = "generators" if accounting == "physical" else "objective"

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

    opex_terms = _carrier_contribution_terms(
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
    return [], opex_terms
