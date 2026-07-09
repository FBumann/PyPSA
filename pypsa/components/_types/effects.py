# SPDX-FileCopyrightText: PyPSA Contributors
#
# SPDX-License-Identifier: MIT

"""Effects components module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pypsa.components._types._patch import patch_add_docstring
from pypsa.components.components import Components

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pandas as pd


@patch_add_docstring
class Effects(Components):
    """Effects components class.

    Effects are named tracked quantities (e.g. cost, CO2 emissions, primary
    energy, land use). Components contribute to effects via coefficients;
    effects can be reported per component, bounded via global constraints,
    priced into the objective, or serve as the objective. All functionality
    specific to effects is implemented here. Functionality for all components
    is implemented in the abstract base class.

    See Also
    --------
    [pypsa.Components][]

    """

    def add(
        self,
        name: str | int | Sequence[int | str],
        suffix: str | Sequence[str] = "",
        overwrite: bool = False,
        return_names: bool | None = None,
        **kwargs: Any,
    ) -> pd.Index | None:
        """Wrap Components.add() and docstring is patched via decorator."""
        return super().add(
            name=name,
            suffix=suffix,
            overwrite=overwrite,
            return_names=return_names,
            **kwargs,
        )
