"""Tests for bikephysics.core.pedals (`latex/2d_models/models.tex`
S pedal-force-model, architecture.md S5.4, task.md T2).

Mandatory assertion (task.md T2): "Each template must be non-negative
everywhere and `P_F` must be `P_D` offset by `pi` -- assert both in tests
across the full crank revolution." Checked below for every periodic
template (`CosSquared`, `HarmonicSeries`, `ClippedSine`, `VonMises`).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.core.pedals import (
    ClippedSine,
    ConstantCrankTorque,
    CosSquared,
    HarmonicSeries,
    PiecewiseConstantTime,
    TabulatedCrankAngle,
    TabulatedTime,
    VonMises,
    ZeroPedal,
)

THETA_SWEEP = np.linspace(0.0, 2.0 * math.pi, 361)

PERIODIC_TEMPLATES = [
    CosSquared(P_0=100.0, phi=0.3),
    CosSquared(P_0=50.0, phi=0.0),
    HarmonicSeries(a_0=5.0, a_n=(2.0, 1.0), phi_n=(0.2, 0.5)),
    HarmonicSeries(a_0=10.0, a_n=(), phi_n=()),
    ClippedSine(P_0=100.0, theta_0=0.1, n=2.0),
    ClippedSine(P_0=80.0, theta_0=-0.4, n=3.0),
    VonMises(P_0=100.0, theta_0=0.1, kappa=2.0),
    VonMises(P_0=60.0, theta_0=0.0, kappa=0.5),
]


@pytest.mark.parametrize("template", PERIODIC_TEMPLATES, ids=lambda t: type(t).__name__)
def test_periodic_template_is_non_negative_everywhere(template: object) -> None:
    for theta in THETA_SWEEP:
        p_d, p_f = template.forces(0.0, float(theta), 0.0)  # type: ignore[attr-defined]
        assert p_d >= -1e-12, (template, theta, p_d)
        assert p_f >= -1e-12, (template, theta, p_f)


@pytest.mark.parametrize("template", PERIODIC_TEMPLATES, ids=lambda t: type(t).__name__)
def test_periodic_template_p_f_is_p_d_offset_by_pi(template: object) -> None:
    for theta in THETA_SWEEP:
        p_d, p_f = template.forces(0.0, float(theta), 0.0)  # type: ignore[attr-defined]
        p_d_shifted, _ = template.forces(0.0, float(theta) + math.pi, 0.0)  # type: ignore[attr-defined]
        assert p_f == pytest.approx(p_d_shifted, abs=1e-9), (template, theta, p_f, p_d_shifted)


def test_cos_squared_matches_boxed_formula() -> None:
    model = CosSquared(P_0=100.0, phi=0.3)
    theta = 1.2
    p_d, p_f = model.forces(0.0, theta, 0.0)
    assert p_d == pytest.approx(100.0 * math.cos(theta - 0.3) ** 2)
    assert p_f == pytest.approx(100.0 * math.cos(theta - 0.3 + math.pi) ** 2)


def test_clipped_sine_matches_boxed_formula() -> None:
    model = ClippedSine(P_0=50.0, theta_0=0.2, n=2.0)
    theta = 1.0
    p_d, _ = model.forces(0.0, theta, 0.0)
    assert p_d == pytest.approx(50.0 * max(0.0, math.sin(theta - 0.2)) ** 2)


def test_von_mises_matches_boxed_formula() -> None:
    model = VonMises(P_0=75.0, theta_0=0.0, kappa=1.5)
    theta = 0.7
    p_d, _ = model.forces(0.0, theta, 0.0)
    assert p_d == pytest.approx(75.0 * math.exp(1.5 * (math.cos(theta) - 1.0)))


def test_harmonic_series_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        HarmonicSeries(a_0=1.0, a_n=(1.0, 2.0), phi_n=(0.1,))


def test_zero_pedal_always_zero() -> None:
    model = ZeroPedal()
    for theta in THETA_SWEEP:
        assert model.forces(0.0, float(theta), 0.0) == (0.0, 0.0)
    assert model.full(0.0, 0.0, 0.0) is None


def test_constant_crank_torque_splits_evenly() -> None:
    model = ConstantCrankTorque(tau=20.0, r_1_pedal=0.1725)
    p_d, p_f = model.forces(0.0, 0.0, 0.0)
    assert p_d == pytest.approx(p_f)
    assert p_d == pytest.approx(20.0 / (2.0 * 0.1725))
    # independent of crank angle
    p_d2, p_f2 = model.forces(0.0, 2.345, 10.0)
    assert (p_d2, p_f2) == (p_d, p_f)


def test_constant_crank_torque_rejects_negative_tau() -> None:
    with pytest.raises(ValueError, match="tau"):
        ConstantCrankTorque(tau=-1.0, r_1_pedal=0.1725)


def test_tabulated_time_interpolates() -> None:
    model = TabulatedTime(
        t=np.array([0.0, 1.0, 2.0]),
        P_D=np.array([0.0, 10.0, 0.0]),
        P_F=np.array([0.0, 5.0, 0.0]),
    )
    p_d, p_f = model.forces(0.5, 0.0, 0.0)
    assert p_d == pytest.approx(5.0)
    assert p_f == pytest.approx(2.5)


def test_piecewise_constant_time_holds_value_per_interval() -> None:
    model = PiecewiseConstantTime(
        knots=np.array([0.0, 1.0, 2.0]),
        P_D_values=np.array([10.0, 20.0]),
        P_F_values=np.array([5.0, 6.0]),
    )
    assert model.forces(0.5, 0.0, 0.0) == (10.0, 5.0)
    assert model.forces(1.5, 0.0, 0.0) == (20.0, 6.0)
    assert model.forces(-1.0, 0.0, 0.0) == (10.0, 5.0)
    assert model.forces(5.0, 0.0, 0.0) == (20.0, 6.0)


def test_piecewise_constant_time_rejects_mismatched_value_count() -> None:
    with pytest.raises(ValueError, match="knots"):
        PiecewiseConstantTime(
            knots=np.array([0.0, 1.0, 2.0]),
            P_D_values=np.array([10.0]),
            P_F_values=np.array([5.0, 6.0]),
        )


def test_tabulated_crank_angle_wraps_around() -> None:
    model = TabulatedCrankAngle(
        theta=np.array([0.0, math.pi, 2.0 * math.pi]),
        P_D=np.array([0.0, 100.0, 0.0]),
        P_F=np.array([100.0, 0.0, 100.0]),
    )
    p_d, p_f = model.forces(0.0, math.pi / 2, 0.0)
    assert p_d == pytest.approx(50.0)
    assert p_f == pytest.approx(50.0)
    # wraps past 2*pi correctly
    p_d2, _ = model.forces(0.0, 2.0 * math.pi + math.pi / 2, 0.0)
    assert p_d2 == pytest.approx(p_d)
