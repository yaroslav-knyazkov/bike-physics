"""
bikephysics -- Longitudinal single-DOF bike dynamics simulation package.

See main.tex for the physics derivation. Re-exports the main public names
so callers can do `from bikephysics import BikeParameters, ...` instead of
reaching into submodules.
"""

from bikephysics.analysis import steady_state_velocity
from bikephysics.parameters import BikeParameters
from bikephysics.plotting import BikePlotter
from bikephysics.profiles import (
    ConstantForce,
    RampForce,
    SinusoidalForce,
    StepForce,
)
from bikephysics.simulation import BikeSimulator, SimulationResult

__all__ = [
    "BikeParameters",
    "ConstantForce",
    "RampForce",
    "StepForce",
    "SinusoidalForce",
    "BikeSimulator",
    "SimulationResult",
    "BikePlotter",
    "steady_state_velocity",
]
