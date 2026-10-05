"""tests/crosstier/test_planar_vs_simplified.py -- the T7 acceptance gate:
architecture.md S7.5.6's five cross-tier checks, executable
(`validation/cross_tier.py`). Per the ticket brief: "every item ... passes
-- that IS the ticket's completion gate," not merely the CLI running.
"""

from __future__ import annotations

import pytest

from bikephysics.core.params import BIKE_PRESET_DIR, BikeSpec, bike_spec_from_dict, read_toml
from bikephysics.validation import cross_tier as ct


@pytest.fixture(scope="module")
def spec() -> BikeSpec:
    return bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))


@pytest.fixture(scope="module")
def report(spec: BikeSpec) -> ct.CrossTierReport:
    return ct.run_all(spec)


def test_reflected_inertia_identity_holds_to_float_precision(spec: BikeSpec) -> None:
    """architecture.md S7.5.6 item 3: pure algebra, exact."""
    result = ct.reflected_inertia_identity_check(spec)
    assert result.ok(rel_tol=1e-12)
    assert result.lhs == pytest.approx(result.rhs, rel=1e-12)


def test_static_load_split_matches_simplified(spec: BikeSpec) -> None:
    """architecture.md S7.5.6 item 4."""
    result = ct.static_load_split_check(spec)
    assert result.ok()


def test_chain_tension_matches_simplified_during_constant_force_accel(spec: BikeSpec) -> None:
    """architecture.md S7.5.6 item 2."""
    result = ct.chain_tension_check(spec)
    assert result.ok(), (
        f"max relative error {result.max_abs_rel_error:.3e} exceeds tol {result.tol}"
    )


def test_steady_state_speed_matches_simplified_across_power_and_grade(spec: BikeSpec) -> None:
    """architecture.md S7.5.6 item 1."""
    result = ct.steady_state_speed_check(spec)
    for case in result.cases:
        assert abs(case.rel_error) <= result.tol, (
            f"power={case.power} grade={case.grade}: planar={case.planar_speed:.4f} "
            f"vs simplified={case.simplified_speed:.4f} (rel_err={case.rel_error:.4%})"
        )
    assert result.ok()


def test_wheelie_stoppie_crossings_match_simplified(spec: BikeSpec) -> None:
    """architecture.md S7.5.6 item 5."""
    result = ct.wheelie_stoppie_check(spec)
    assert result.ok(), (
        f"front fraction at wheelie threshold={result.front_z_fraction_at_wheelie_threshold}, "
        f"rear fraction at stoppie threshold={result.rear_z_fraction_at_stoppie_threshold}"
    )


def test_run_all_aggregate_passes(report: ct.CrossTierReport) -> None:
    """The single entry point `cli planar crosscheck` and the ticket's
    own "Done when" gate both use."""
    assert report.steady_state.ok()
    assert report.chain_tension.ok()
    assert report.reflected_inertia.ok()
    assert report.static_load.ok()
    assert report.wheelie_stoppie.ok()
    assert report.ok()
