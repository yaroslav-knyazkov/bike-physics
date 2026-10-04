"""Tests for bikephysics.core.limits (`latex/simplified/forward_motion.tex`
S Limits, architecture.md S5.1/S5.5, task.md T2)."""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.core.limits import (
    freewheel_margin,
    limit_margins,
    traction_margin,
    wheel_lift_margin,
)
from bikephysics.core.results import LimitMargins


def test_traction_margin_matches_limit_margins_docstring_example() -> None:
    # core.results.LimitMargins's own docstring: "traction_rear = |R_x| -
    # mu_s * R_z".
    assert traction_margin(50.0, 400.0, 0.8) == pytest.approx(abs(50.0) - 0.8 * 400.0)


def test_traction_margin_negative_means_within_friction_cone() -> None:
    assert traction_margin(50.0, 400.0, 0.8) < 0  # 50 << 0.8*400
    assert traction_margin(400.0, 400.0, 0.8) > 0  # 400 > 0.8*400


def test_traction_margin_is_even_in_r_x() -> None:
    assert traction_margin(100.0, 400.0, 0.8) == traction_margin(-100.0, 400.0, 0.8)


def test_wheel_lift_margin_sign_convention() -> None:
    # shared z-up convention: R_z >= 0 -> loaded -> margin <= 0.
    assert wheel_lift_margin(300.0) <= 0
    # R_z < 0 is unphysical/lifted -> margin > 0.
    assert wheel_lift_margin(-5.0) > 0
    assert wheel_lift_margin(0.0) == 0.0


def test_freewheel_margin_sign_convention() -> None:
    # T >= 0 (in tension) -> margin <= 0 (satisfied).
    assert freewheel_margin(10.0) <= 0
    assert freewheel_margin(-2.0) > 0
    assert freewheel_margin(0.0) == 0.0


def test_margin_functions_work_on_scalars_and_arrays() -> None:
    scalar = traction_margin(50.0, 400.0, 0.8)
    array = traction_margin(np.array([50.0]), np.array([400.0]), 0.8)
    assert array[0] == pytest.approx(scalar)


def test_limit_margins_assembles_all_five_fields_correctly() -> None:
    margins = limit_margins(
        R_2a_x=np.array([50.0, 300.0]),
        R_2a_z=np.array([400.0, 400.0]),
        R_2b_x=np.array([0.0, 0.0]),
        R_2b_z=np.array([300.0, -5.0]),
        T=np.array([10.0, -2.0]),
        mu_s=0.8,
    )
    assert isinstance(margins, LimitMargins)
    np.testing.assert_allclose(
        margins.traction_rear, [abs(50.0) - 0.8 * 400.0, abs(300.0) - 0.8 * 400.0]
    )
    np.testing.assert_allclose(
        margins.traction_front, [abs(0.0) - 0.8 * 300.0, abs(0.0) - 0.8 * (-5.0)]
    )
    # wheelie is the *front* wheel's lift margin (hard acceleration).
    np.testing.assert_allclose(margins.wheelie, [-300.0, 5.0])
    # stoppie is the *rear* wheel's lift margin (hard front braking).
    np.testing.assert_allclose(margins.stoppie, [-400.0, -400.0])
    np.testing.assert_allclose(margins.freewheel, [-10.0, 2.0])
    margins.validate()


def test_limit_margins_front_lift_trips_wheelie_margin_positive() -> None:
    margins = limit_margins(
        R_2a_x=np.array([0.0]),
        R_2a_z=np.array([800.0]),
        R_2b_x=np.array([0.0]),
        R_2b_z=np.array([-10.0]),  # front wheel lifted
        T=np.array([5.0]),
        mu_s=0.8,
    )
    assert margins.wheelie[0] > 0
    assert margins.stoppie[0] < 0


def test_limit_margins_rear_lift_trips_stoppie_margin_positive() -> None:
    margins = limit_margins(
        R_2a_x=np.array([0.0]),
        R_2a_z=np.array([-10.0]),  # rear wheel lifted
        R_2b_x=np.array([0.0]),
        R_2b_z=np.array([800.0]),
        T=np.array([5.0]),
        mu_s=0.8,
    )
    assert margins.stoppie[0] > 0
    assert margins.wheelie[0] < 0
