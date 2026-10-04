"""Tests for bikephysics.optim.constraints (architecture.md S8.3)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from bikephysics.optim.constraints import (
    CadenceBound,
    ChainTensionNonNegative,
    PedalForceBound,
    PowerCeiling,
    SpeedBounds,
    StoppieMargin,
    TerminalState,
    TractionMargin,
    WheelieMargin,
)
from tests.unit.factories import make_synthetic_trajectory


def test_power_ceiling_scalar_limit() -> None:
    traj = make_synthetic_trajectory()  # power_pedal is all ones
    c = PowerCeiling(p_max=0.5)
    g = c.g(traj)
    assert np.all(g == pytest.approx(0.5))
    assert c.penalty(traj, weight=2.0) > 0.0


def test_power_ceiling_zero_penalty_when_satisfied() -> None:
    traj = make_synthetic_trajectory()
    c = PowerCeiling(p_max=10.0)
    assert c.penalty(traj, weight=2.0) == pytest.approx(0.0)


def test_power_ceiling_callable_limit_uses_omega_3b() -> None:
    traj = make_synthetic_trajectory()  # omega_3b is all ones, power_pedal is all ones
    c = PowerCeiling(p_max=lambda omega: 2.0 * omega)
    g = c.g(traj)
    assert np.all(g == pytest.approx(1.0 - 2.0))


def test_pedal_force_bound_requires_meta_p_d_p_f() -> None:
    traj = make_synthetic_trajectory()
    c = PedalForceBound(p_max=10.0)
    with pytest.raises(KeyError, match="P_D"):
        c.g(traj)


def test_pedal_force_bound_reads_meta_and_flags_violation() -> None:
    traj = make_synthetic_trajectory()
    n = len(traj.t)
    traj = replace(traj, meta={**traj.meta, "P_D": np.full(n, 5.0), "P_F": np.full(n, 3.0)})
    c = PedalForceBound(p_max=4.0, p_min=4.0)
    g = c.g(traj)
    assert np.any(g > 0.0)  # P_D = 5 > p_max = 4


def test_pedal_force_bound_no_lower_bound_by_default() -> None:
    traj = make_synthetic_trajectory()
    n = len(traj.t)
    traj = replace(traj, meta={**traj.meta, "P_D": np.zeros(n), "P_F": np.zeros(n)})
    c = PedalForceBound(p_max=10.0)
    assert np.all(c.g(traj) <= 0.0)


def test_cadence_bound_reads_omega_3b() -> None:
    traj = make_synthetic_trajectory()  # omega_3b is all ones
    c = CadenceBound(omega_max=0.5)
    assert np.all(c.g(traj) == pytest.approx(0.5))


def test_traction_margin_reads_both_wheels_directly() -> None:
    traj = make_synthetic_trajectory()  # margins are all -1 (satisfied)
    assert np.all(TractionMargin().g(traj) == pytest.approx(-1.0))


def test_wheelie_stoppie_chain_tension_read_margins_directly() -> None:
    traj = make_synthetic_trajectory()
    assert np.all(WheelieMargin().g(traj) == pytest.approx(-1.0))
    assert np.all(StoppieMargin().g(traj) == pytest.approx(-1.0))
    assert np.all(ChainTensionNonNegative().g(traj) == pytest.approx(-1.0))


def test_speed_bounds_requires_at_least_one_bound() -> None:
    with pytest.raises(ValueError):
        SpeedBounds()


def test_speed_bounds_flags_violation() -> None:
    traj = make_synthetic_trajectory()  # v is all ones
    c = SpeedBounds(v_min=0.0, v_max=0.5)
    assert np.any(c.g(traj) > 0.0)


def test_terminal_state_requires_at_least_one_target() -> None:
    with pytest.raises(ValueError):
        TerminalState()


def test_terminal_state_residual_satisfied_at_exact_target() -> None:
    traj = make_synthetic_trajectory()
    c = TerminalState(x_target=float(traj.x[-1]), tol=1e-9)
    assert c.g(traj)[0] <= 0.0


def test_terminal_state_residual_violated_when_off_target() -> None:
    traj = make_synthetic_trajectory()
    c = TerminalState(x_target=float(traj.x[-1]) + 10.0, tol=1e-9)
    assert c.g(traj)[0] > 0.0
