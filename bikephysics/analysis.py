"""
analysis.py -- Closed-form analytic checks for the bike dynamics model.
"""

import math

from bikephysics.parameters import BikeParameters


def steady_state_velocity(params: BikeParameters, F: float) -> float:
    """Analytic steady-state forward speed [m/s] for a constant drive force
    F [N] (a=0 case, i.e. driving forward and holding a constant speed).

    Solves for v > 0 in:

        F = sign(v)*c_rr*m*g*cos(theta1) + m*g*sin(theta1)
            + 0.5*rho*cx*A*v*abs(v)

    which for the forward-driving case (v > 0, sign(v) = 1) rearranges to
    the quadratic:

        0.5*rho*cx*A*v**2 = F - c_rr*m*g*cos(theta1) - m*g*sin(theta1)
        v = sqrt(max(rhs, 0) / (0.5*rho*cx*A))

    Edge case: if the right-hand side is <= 0, the drive force cannot
    overcome rolling resistance + gravity even at v=0, so the bike cannot
    sustain forward motion under this force -- this function returns 0.0
    in that case rather than raising or returning a complex/negative value.
    """
    p = params
    resistive = p.c_rr * p.m * p.g * math.cos(p.theta1) + p.m * p.g * math.sin(p.theta1)
    rhs = F - resistive
    if rhs <= 0.0:
        return 0.0
    return math.sqrt(rhs / (0.5 * p.rho * p.cx * p.A))
