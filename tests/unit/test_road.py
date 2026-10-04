"""Tests for bikephysics.core.road (architecture.md S5.3,
`latex/2d_assembly/wheels_ground.tex` S"Contact point", task.md T2).

Four tests are explicitly mandated by task.md T2 and marked below:
flat-road `x* == x_axle`; constant-grade `x*` against the closed-form
answer; a sine road checked against a brute-force minimum-distance
search; and a low-curvature-radius case asserting the warning fires.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pytest
import tomli_w

from bikephysics.core.road import (
    ConstantGrade,
    CubicSplineRoad,
    Flat,
    PiecewiseGrade,
    SineRoad,
    StepJump,
    contact_reference,
    load_road,
)


def test_flat_road_is_identically_zero() -> None:
    road = Flat()
    for x in (-10.0, 0.0, 10.0, 1000.0):
        assert road.z(x) == 0.0
        assert road.dz_dx(x) == 0.0
        assert road.d2z_dx2(x) == 0.0
        assert road.theta(x) == 0.0
    assert road.curvature_radius(0.0) == math.inf


def test_flat_road_contact_reference_equals_axle_x() -> None:
    # Mandatory test: flat-road x* == x_axle.
    road = Flat()
    for x_axle in (0.0, 5.0, -3.0, 123.456):
        x_star = contact_reference(road, center=(x_axle, 0.335), radius=0.335)
        assert x_star == pytest.approx(x_axle, abs=1e-9)


def test_constant_grade_theta_round_trips() -> None:
    theta = math.atan(0.05)
    road = ConstantGrade(theta=theta)
    assert road.theta(3.0) == pytest.approx(theta)
    assert road.dz_dx(5.0) == pytest.approx(math.tan(theta))
    assert road.d2z_dx2(5.0) == 0.0
    assert road.curvature_radius(5.0) == math.inf


def test_constant_grade_contact_reference_matches_closed_form_projection() -> None:
    # Mandatory test: constant-grade x* against the closed-form answer --
    # the foot of the perpendicular from (x_A, z_A) onto the line
    # z = x*tan(theta) through the origin.
    theta = math.atan(0.05)
    road = ConstantGrade(theta=theta)
    x_a, z_a = 10.0, 1.0
    x_star = contact_reference(road, center=(x_a, z_a), radius=0.335)
    c, s = math.cos(theta), math.sin(theta)
    t = x_a * c + z_a * s
    x_closed_form = t * c
    assert x_star == pytest.approx(x_closed_form, abs=1e-9)


def test_sine_road_contact_reference_matches_brute_force_search() -> None:
    # Mandatory test: a sine road checked against a brute-force
    # minimum-distance search.
    road = SineRoad(amplitude=0.5, wavelength=20.0)
    center = (10.0, 0.3)
    x_star = contact_reference(road, center=center, radius=0.335, x_guess=10.0)

    grid = np.linspace(5.0, 15.0, 200_001)
    distances_sq = (grid - center[0]) ** 2 + (np.array([road.z(x) for x in grid]) - center[1]) ** 2
    x_brute = float(grid[np.argmin(distances_sq)])

    assert x_star == pytest.approx(x_brute, abs=2e-3)


def test_low_curvature_radius_triggers_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    # Mandatory test: a low-curvature-radius case asserting the warning
    # fires. A short-wavelength sine has a small curvature radius; a large
    # wheel radius relative to it violates the `rho > 5*r` well-posedness
    # bound.
    road = SineRoad(amplitude=0.5, wavelength=2.0)
    with caplog.at_level(logging.WARNING, logger="bikephysics.core.road"):
        contact_reference(road, center=(1.0, 0.5), radius=0.335, x_guess=1.0)
    assert any("curvature radius" in record.message for record in caplog.records)


def test_curvature_radius_well_posed_case_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    road = SineRoad(amplitude=0.1, wavelength=200.0)
    with caplog.at_level(logging.WARNING, logger="bikephysics.core.road"):
        contact_reference(road, center=(10.0, 0.0), radius=0.335, x_guess=10.0)
    assert not any("curvature radius" in record.message for record in caplog.records)


def test_step_jump_is_smooth_and_monotonic() -> None:
    road = StepJump(x_jump=10.0, height=0.3, ramp_width=0.1)
    assert road.z(-1000.0) == pytest.approx(0.0, abs=1e-6)
    assert road.z(1000.0) == pytest.approx(0.3, abs=1e-6)
    assert road.z(10.0) == pytest.approx(0.15, abs=1e-9)
    # monotonic non-decreasing across the jump
    xs = np.linspace(9.0, 11.0, 200)
    zs = [road.z(x) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(zs, zs[1:], strict=False))


def test_piecewise_grade_recovers_each_segment_grade_away_from_transitions() -> None:
    road = PiecewiseGrade(
        breakpoints=((0.0, 0.0), (50.0, math.atan(0.05)), (150.0, math.atan(-0.02))),
        transition_width=2.0,
    )
    assert road.dz_dx(10.0) == pytest.approx(0.0, abs=1e-6)
    assert road.dz_dx(100.0) == pytest.approx(0.05, abs=1e-6)
    assert road.dz_dx(250.0) == pytest.approx(-0.02, abs=1e-9)
    assert road.dz_dx(-250.0) == pytest.approx(0.0, abs=1e-9)


def test_piecewise_grade_rejects_non_increasing_breakpoints() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        PiecewiseGrade(breakpoints=((0.0, 0.0), (0.0, 0.1)))


def test_cubic_spline_road_interpolates_through_data_points() -> None:
    x_data = np.array([0.0, 10.0, 20.0, 30.0])
    z_data = np.array([0.0, 1.0, 0.5, 2.0])
    road = CubicSplineRoad(x_data=x_data, z_data=z_data)
    for x, z in zip(x_data, z_data, strict=True):
        assert road.z(float(x)) == pytest.approx(float(z), abs=1e-9)


def test_load_road_dispatches_on_kind(tmp_path: Path) -> None:
    flat_path = tmp_path / "flat.toml"
    flat_path.write_bytes(tomli_w.dumps({"kind": "flat"}).encode("utf-8"))
    road = load_road(flat_path)
    assert isinstance(road, Flat)

    grade_path = tmp_path / "grade.toml"
    grade_path.write_bytes(tomli_w.dumps({"kind": "constant_grade", "grade": 0.05}).encode("utf-8"))
    road_grade = load_road(grade_path)
    assert isinstance(road_grade, ConstantGrade)
    assert road_grade.theta(0.0) == pytest.approx(math.atan(0.05))


def test_load_road_rejects_unknown_kind(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_bytes(tomli_w.dumps({"kind": "not_a_real_road"}).encode("utf-8"))
    with pytest.raises(ValueError, match="unknown road kind"):
        load_road(path)
