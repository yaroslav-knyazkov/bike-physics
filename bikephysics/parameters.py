"""
parameters.py -- Physical parameters of the bike+rider system.

All defaults and main.tex symbol mappings are carried over unchanged from
the original standalone `simulate_bike.py` script.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BikeParameters:
    """Physical parameters of the bike+rider system (main.tex symbols in
    parentheses). Defaults ~ road bike + rider.

    This dataclass is frozen (immutable) so instances are safe to share and
    reuse across simulations. To experiment with a modified parameter set,
    derive a copy with `dataclasses.replace`, e.g.::

        from dataclasses import replace
        heavier_rolling = replace(BikeParameters(), c_rr=0.008)

    `heavier_rolling` is then a brand-new, independent `BikeParameters`
    instance with only `c_rr` changed -- the original is untouched.
    """

    m: float = 65        # kg      -- total mass of rider + bike (symbol: m)
    r_w: float = 0.335     # m       -- wheel radius (symbol: r_w)
    I2: float = 0.14       # kg*m^2  -- front wheel rotational inertia (symbol: I2)
    I3: float = 0.14       # kg*m^2  -- rear wheel rotational inertia (symbol: I3)
    I4: float = 0.05       # kg*m^2  -- crankset + chainring rotational inertia (symbol: I4)
    r_cg: float = 0.03     # m       -- rear cog (sprocket) radius (symbol: r_cg)
    r_cr: float = 0.075    # m       -- chainring radius (symbol: r_cr)
    c_rr: float = 0.005    # --      -- rolling resistance coefficient (symbol: c_rr)
    rho: float = 1.225     # kg/m^3  -- air density
    cx: float = 0.88       # --      -- aerodynamic drag coefficient (symbol: c_x)
    A: float = 0.4         # m^2     -- frontal area
    theta1: float = 0.0    # rad     -- road incline angle, positive = uphill (symbol: theta1)
                            #            change this to test hills / descents, e.g. 0.03 for a ~3% grade
    g: float = 9.81        # m/s^2   -- gravitational acceleration

    @property
    def m_eq(self) -> float:
        """Equivalent mass: real mass + rotational inertia of wheels/drivetrain
        reflected to linear motion (main.tex: m_eq = m + (I2+I3)/r_w^2
        + I4*r_cg^2/(r_cr^2*r_w^2))."""
        return (
            self.m
            + (self.I2 + self.I3) / self.r_w**2
            + self.I4 * self.r_cg**2 / (self.r_cr**2 * self.r_w**2)
        )
