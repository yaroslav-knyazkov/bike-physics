"""core/units.py -- SI constants and lightweight unit-conversion helpers.

architecture.md S2/S14: no runtime unit library. Every physical quantity
inside this codebase is a plain float/array in SI base units; the functions
in this module are the only sanctioned conversion points, meant to sit at
CLI/config/display boundaries (e.g. printing a speed in km/h, accepting a
grade in degrees), never inside the physics RHS itself.
"""

from __future__ import annotations

import numpy as np

#: Standard gravity, m/s^2. This is `env.g`'s default (architecture.md S5.1);
#: a specific `BikeSpec` may override it (e.g. for a non-Earth sanity check).
G_STANDARD: float = 9.80665


def kmh_to_ms(speed_kmh: float) -> float:
    """Convert a speed from km/h to m/s."""
    return speed_kmh / 3.6


def ms_to_kmh(speed_ms: float) -> float:
    """Convert a speed from m/s to km/h."""
    return speed_ms * 3.6


def deg_to_rad(angle_deg: float) -> float:
    """Convert an angle from degrees to radians (array-safe via numpy)."""
    return float(np.deg2rad(angle_deg))


def rad_to_deg(angle_rad: float) -> float:
    """Convert an angle from radians to degrees (array-safe via numpy)."""
    return float(np.rad2deg(angle_rad))


def power_to_force(power_w: float, speed_ms: float) -> float:
    """`F = P / v`: equivalent tangential force [N] for a mechanical power
    [W] delivered at a given speed [m/s]. 1 W = 1 N*m/s, so this is not a
    unit conversion but a physical relation -- it is the helper the pedal
    and optimization code use to go from a power-based spec to a force.
    Does not regularize `v` near zero; callers needing that use
    `core.mathx.sign_smooth`-based regularization explicitly."""
    return power_w / speed_ms


def force_to_power(force_n: float, speed_ms: float) -> float:
    """`P = F * v`: mechanical power [W] delivered by a tangential force
    [N] at a given speed [m/s]. Inverse of `power_to_force`."""
    return force_n * speed_ms
