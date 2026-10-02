"""
simulation.py -- Longitudinal bike dynamics simulation (single-DOF).

Model (derived in main.tex, "Equations of Motion" / "Kinematics" sections):

    m_eq * a = F(t) - sign(v)*c_rr*m*g*cos(theta1)
               - m*g*sin(theta1) - 0.5*rho*cx*A*v*abs(v)

The bike+rider system is reduced to one second-order ODE for the forward
position x(t) along the direction of travel (v = x_dot, a = x_ddot).
Rotational inertia of the wheels and drivetrain is reflected into an
"equivalent mass" m_eq. The rider is modeled as a constant propulsive
FORCE source F(t) [N] applied at the contact point (no power/speed
coupling -- F(t) is prescribed directly, independent of v).

Wheel rotation angle theta_{2,3} and drivetrain/crank rotation angle
theta_4 follow directly and algebraically from x(t) under the pure-rolling
kinematic constraint (main.tex "Kinematics"):

    theta_ddot_2 = theta_ddot_3 = -x_ddot / r_w
    theta_ddot_4 = -(r_cg / (r_cr * r_w)) * x_ddot

Since x(0) = 0, v(0) = 0, and the angular equations above are pure
double-integrals of x_ddot with zero initial angle/rate, these angles are
algebraically theta_23(t) = -x(t)/r_w and theta_4(t) = -(r_cg/(r_cr*r_w))*x(t)
-- no extra ODE states needed. Sign is negative for forward rolling motion
per the main.tex convention (kept as-is here, not flipped for cosmetics).

Mechanical power delivered at the pedals, P(t) [W], follows with no new
physics: the drivetrain is ideal/lossless in this model, so main.tex's
gear-ratio relation between crank power and F(t) collapses algebraically
to the simple identity P(t) = F(t) * v(t).
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from bikephysics.parameters import BikeParameters

ForceProfile = Callable[[float], float]


@dataclass
class SimulationResult:
    """Output of a `BikeSimulator.run()` call: time series plus the params
    and force profile that produced them (kept for plot labeling and
    reference in the playground)."""

    t: NDArray[np.float64]
    x: NDArray[np.float64]
    v: NDArray[np.float64]
    a: NDArray[np.float64]
    theta_23: NDArray[np.float64]
    theta_4: NDArray[np.float64]
    power: NDArray[np.float64]
    params: BikeParameters
    force_profile: ForceProfile


class BikeSimulator:
    """Integrates the single-DOF longitudinal bike dynamics ODE for a given
    set of parameters and rider force profile."""

    def __init__(
        self,
        params: BikeParameters,
        force_profile: ForceProfile,
        t_end: float = 120.0,
        n_points: int = 2000,
        x0: float = 0.0,
        v0: float = 0.0,
    ) -> None:
        self.params = params
        self.force_profile = force_profile
        self.t_end = t_end
        self.n_points = n_points
        self.x0 = x0
        self.v0 = v0

    def _acceleration(self, t: float, v: float) -> float:
        """Right-hand side of m_eq * a = F_drive - rolling_resistance -
        gravity - drag."""
        p = self.params
        F_drive = self.force_profile(t)

        # Rolling resistance opposes motion; explicitly zero at exact rest
        # (per spec: sign(0) = 0 here, so no rolling resistance term at v=0).
        if v == 0.0:
            F_roll = 0.0
        else:
            F_roll = np.sign(v) * p.c_rr * p.m * p.g * np.cos(p.theta1)

        F_gravity = p.m * p.g * np.sin(p.theta1)
        F_drag = 0.5 * p.rho * p.cx * p.A * v * abs(v)

        return (F_drive - F_roll - F_gravity - F_drag) / p.m_eq

    def _rhs(self, t: float, state: NDArray[np.float64]) -> list[float]:
        """ODE right-hand side for solve_ivp. state = [x, v]."""
        x, v = state
        a = self._acceleration(t, v)
        return [v, a]

    def run(self) -> SimulationResult:
        """Integrate the ODE from (x0, v0) at t=0 to t_end and reconstruct
        a(t), theta_23(t), theta_4(t) from the resulting x(t), v(t)."""
        p = self.params
        t_eval = np.linspace(0.0, self.t_end, self.n_points)

        sol = solve_ivp(
            self._rhs,
            t_span=(0.0, self.t_end),
            y0=[self.x0, self.v0],
            method="RK45",
            t_eval=t_eval,
            rtol=1e-8,
            atol=1e-10,
        )

        t = sol.t
        x = sol.y[0]
        v = sol.y[1]

        # Reconstruct a(t) after integration: solve_ivp only returns x(t),
        # v(t), so recompute acceleration at each output sample using the
        # same RHS.
        a = np.array([self._acceleration(ti, vi) for ti, vi in zip(t, v)])

        # Kinematic angles: exact algebraic functions of x(t) (no extra ODE
        # states needed -- see module docstring).
        theta_23 = -x / p.r_w
        theta_4 = -(p.r_cg / (p.r_cr * p.r_w)) * x

        # Mechanical power at the pedals: P(t) = F(t) * v(t) -- see module
        # docstring for the derivation (lossless-drivetrain identity).
        F_t = np.array([self.force_profile(ti) for ti in t])
        power = F_t * v

        return SimulationResult(
            t=t,
            x=x,
            v=v,
            a=a,
            theta_23=theta_23,
            theta_4=theta_4,
            power=power,
            params=p,
            force_profile=self.force_profile,
        )
