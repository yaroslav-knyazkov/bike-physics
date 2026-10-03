"""Tests for bikephysics.core.units (architecture.md S5, task.md T1)."""

from __future__ import annotations

import math

from bikephysics.core import units


def test_g_standard_value() -> None:
    assert units.G_STANDARD == 9.80665


def test_kmh_ms_round_trip() -> None:
    speed_kmh = 36.0
    speed_ms = units.kmh_to_ms(speed_kmh)
    assert speed_ms == 10.0
    assert units.ms_to_kmh(speed_ms) == speed_kmh


def test_deg_rad_round_trip() -> None:
    angle_deg = 45.0
    angle_rad = units.deg_to_rad(angle_deg)
    assert math.isclose(angle_rad, math.pi / 4, rel_tol=1e-12)
    assert math.isclose(units.rad_to_deg(angle_rad), angle_deg, rel_tol=1e-12)


def test_power_force_round_trip() -> None:
    power_w = 250.0
    speed_ms = 8.0
    force_n = units.power_to_force(power_w, speed_ms)
    assert math.isclose(force_n, 31.25, rel_tol=1e-12)
    assert math.isclose(units.force_to_power(force_n, speed_ms), power_w, rel_tol=1e-12)
