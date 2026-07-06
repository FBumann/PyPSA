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
    n.add("Generator", "gas", bus="b", carrier="gas", p_nom=100, marginal_cost=50,
          efficiency=0.55)
    n.add("Generator", "coal", bus="b", carrier="coal", p_nom=80, marginal_cost=30,
          efficiency=0.40)
    n.add("Generator", "wind", bus="b", carrier="wind", p_nom=120, marginal_cost=0.1,
          p_max_pu=0.3 + 0.5 * np.sin(np.linspace(0, 3 * np.pi, 12)) ** 2)
    n.add("Load", "load", bus="b", p_set=90 + 30 * np.cos(np.linspace(0, 2 * np.pi, 12)))
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
    sus = n.storage_units.query("carrier in @coeffs.index and not cyclic_state_of_charge")
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
    pd.testing.assert_frame_equal(m.effects_t.price, n.effects_t.price, check_freq=False)

    csv_path = tmp_path / "csvdir"
    n.export_to_csv_folder(csv_path)
    m = pypsa.Network()
    m.import_from_csv_folder(csv_path)
    pd.testing.assert_frame_equal(m.effects, n.effects)
    pd.testing.assert_frame_equal(m.effects_t.price, n.effects_t.price, check_freq=False)


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
    n.add("StorageUnit", "gs", bus="b", carrier="emitting_storage", p_nom=40,
          max_hours=6, state_of_charge_initial=100,
          cyclic_state_of_charge=False, marginal_cost=1.0)
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
        n.add("Generator", f"gas-{period}", bus="b", carrier="gas",
              build_year=period, lifetime=30, p_nom_extendable=True,
              capital_cost=1000, marginal_cost=50, efficiency=0.55)
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


def test_effect_statistic_unknown_effect(n_basic):
    n = n_basic
    with pytest.raises(ValueError, match="not defined in n.effects"):
        n.statistics.effect("primary_energy")


def test_effect_statistic_no_coefficients(n_basic):
    n = n_basic
    n.add("Effect", "land_use", unit="ha")
    with pytest.raises(ValueError, match="no `carrier_attribute`"):
        n.statistics.effect("land_use")
