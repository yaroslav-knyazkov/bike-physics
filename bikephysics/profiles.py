"""
profiles.py -- Rider drive-force time profiles.

Each profile is a small callable object: `profile(t) -> F` returns the
rider's propulsive force [N] at time `t` [s]. Any instance can be passed
directly to `BikeSimulator(force_profile=...)` in place of the old
`F_of_t` function from the standalone script.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class ConstantForce:
    """Constant propulsive force for all time (e.g. steady cruising effort)."""

    value: float

    def __call__(self, t: float) -> float:
        return self.value


@dataclass
class RampForce:
    """Linear ramp from f_start to f_end over ramp_time seconds, then holds
    at f_end (matches the old commented example: 0->55N over 30s then hold).

    Caveat when starting from rest (v0=0) with f_start=0.0: the model's
    rolling-resistance term is defined as exactly 0 at v=0 but jumps to
    +-c_rr*m*g*cos(theta1) for any v != 0. If F(0) is also exactly 0, the
    initial acceleration is exactly 0 too, so the very first integration
    step sits precisely on that discontinuity. With BikeSimulator's tight
    default tolerances (rtol=1e-8, atol=1e-10), the adaptive RK45 step
    size can collapse trying to resolve it. Use a small nonzero f_start
    (e.g. 5.0 instead of 0.0) when ramping up from a standing start to
    avoid this; it does not affect the physics, only the numerics of
    hitting the discontinuity exactly at t=0."""

    f_start: float
    f_end: float
    ramp_time: float

    def __call__(self, t: float) -> float:
        frac = min(t / self.ramp_time, 1.0)
        return self.f_start + (self.f_end - self.f_start) * frac


@dataclass
class StepForce:
    """Step change from f_before to f_after at time t_step (matches the old
    commented example: 25N -> 55N at t=20s)."""

    f_before: float
    f_after: float
    t_step: float

    def __call__(self, t: float) -> float:
        return self.f_before if t < self.t_step else self.f_after


@dataclass
class SinusoidalForce:
    """Sinusoidal effort oscillating around f_mean with amplitude f_amp and
    the given period [s] (matches the old commented example:
    45 + 15*sin(2*pi*t/60))."""

    f_mean: float
    f_amp: float
    period: float

    def __call__(self, t: float) -> float:
        return self.f_mean + self.f_amp * np.sin(2.0 * np.pi * t / self.period)
