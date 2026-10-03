"""Shared test-data factories for bikephysics.core.results (not a test
module itself -- no `test_` prefix, so pytest does not collect it)."""

from __future__ import annotations

import numpy as np

from bikephysics.core.results import LimitMargins, Trajectory


def make_synthetic_trajectory(n: int = 5) -> Trajectory:
    """A minimal, internally-consistent `Trajectory` of length `n`, for
    `validate()`/round-trip tests that don't need physically meaningful
    values."""
    t = np.linspace(0.0, 1.0, n)
    ones = np.ones(n)
    margins = LimitMargins(
        traction_rear=-ones, traction_front=-ones, wheelie=-ones, stoppie=-ones, freewheel=-ones
    )
    return Trajectory(
        t=t,
        x=t,
        z=np.zeros(n),
        v=ones,
        a=np.zeros(n),
        pitch=np.zeros(n),
        pitch_rate=np.zeros(n),
        omega_2a=ones,
        omega_2b=ones,
        omega_3b=ones,
        T=ones,
        R_2a_x=ones,
        R_2a_z=ones,
        R_2b_x=ones,
        R_2b_z=ones,
        power_pedal=ones,
        power_ground=ones,
        power_aero=ones,
        power_rr=ones,
        power_brake=ones,
        power_gravity=ones,
        margins=margins,
        modes=None,
        spec=None,
        meta={"solver": "test"},
    )
