"""Shared test-data factories for bikephysics.core.results/params (not a
test module itself -- no `test_` prefix, so pytest does not collect it)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bikephysics.core.params import BikeSpec
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


def make_minimal_bike_spec_dict(**overrides: Any) -> dict[str, Any]:
    """A minimal, internally-consistent `BikeSpec`-shaped dict (every
    `gt=0`-constrained field satisfied, `rear.r_2a == front.r_2b`,
    `mu_k <= mu_s`) that every `core.params` test can build on without
    repeating the full ~30-field deck. `overrides` are shallow-merged into
    the top-level sections (e.g. `rear={"c_2a_rr": 0.01}` replaces that
    whole section, matching this helper's own simplicity -- callers
    needing a single nested field tweaked should mutate the returned dict
    directly)."""
    data: dict[str, Any] = {
        "frame": {
            "m_1": 61.2,
            "I_1": 1.2,
            "l_1_a": 0.55,
            "h_1_a": 0.65,
            "l_1_B": 1.0,
            "l_1_C": 0.42,
            "h_1_C": -0.07,
            "r_1_pedal": 0.1725,
        },
        "rear": {
            "m_2a": 2.0,
            "I_2a": 0.14,
            "r_2a": 0.335,
            "c_2a_rr": 0.005,
            "r_2a_bk": 0.31,
        },
        "front": {
            "m_2b": 1.8,
            "I_2b": 0.14,
            "r_2b": 0.335,
            "c_2b_rr": 0.005,
            "r_2b_bk": 0.31,
        },
        "drivetrain": {
            "I_3b": 0.05,
            "r_3b_cr": 0.075,
            "r_2a_cg": 0.03,
        },
        "aero": {
            "C_1_x_0": 0.88,
            "A_1": 0.4,
        },
        "contact": {
            "mu_s": 0.8,
            "mu_k": 0.7,
        },
        "env": {
            "g": 9.81,
            "rho": 1.225,
        },
    }
    data.update(overrides)
    return data


def make_minimal_bike_spec(**overrides: Any) -> BikeSpec:
    """`BikeSpec.model_validate(make_minimal_bike_spec_dict(**overrides))`."""
    return BikeSpec.model_validate(make_minimal_bike_spec_dict(**overrides))
