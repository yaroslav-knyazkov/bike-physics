"""core/road.py -- the road (architecture.md S5.3; `latex/2d_assembly/
wheels_ground.tex` S"Contact point").

`Road` is the elevation-profile abstraction every tier's contact model
reads from: `z(x)` the road graph, `dz_dx`/`d2z_dx2` its derivatives,
`theta(x) = atan(z'(x))` the local slope angle, `frame0(x)` the
ground-local unit-vector pair `(x_hat_0, z_hat_0)`, and `curvature_radius(x)`
for the foot-of-normal well-posedness check.

`contact_reference` solves `wheels_ground.tex`'s tangency condition
`b_2a A . x_hat_0 = 0` (rewritten as the scalar residual `f(x) = (x_A - x) +
(z_A - z(x))*z'(x) = 0`) by warm-started Newton with a bracketed-bisection
fallback, and logs a warning (not an exception -- this is an observability
improvement over a silently-wrong answer, per `refinements.tex`'s
well-posedness caveat) when the resulting curvature radius is too small for
the point-contact model to be trustworthy.
"""

from __future__ import annotations

import logging
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline

FloatArray = npt.NDArray[np.float64]

logger = logging.getLogger(__name__)

#: Shipped road presets (architecture.md S9: "bike spec presets").
ROAD_PRESET_DIR = Path(__file__).resolve().parents[1] / "data" / "roads"


class Road(Protocol):
    """`latex/2d_assembly/wheels_ground.tex`'s road-graph abstraction:
    `z(x)` is given as a graph (matching how real elevation data -- GPS
    routes, surveys, DEMs -- is almost always already represented)."""

    def z(self, x: float) -> float: ...
    def dz_dx(self, x: float) -> float: ...
    def d2z_dx2(self, x: float) -> float: ...

    def theta(self, x: float) -> float:
        """`theta(x) = atan(z'(x))`, the local slope angle."""
        return math.atan(self.dz_dx(x))

    def frame0(self, x: float) -> tuple[tuple[float, float], tuple[float, float]]:
        """`(x_hat_0, z_hat_0)`, the ground-local frame tilted by `theta(x)`
        from the fixed frame `i` (wheels_ground.tex)."""
        theta = self.theta(x)
        c, s = math.cos(theta), math.sin(theta)
        return (c, s), (-s, c)

    def curvature_radius(self, x: float) -> float:
        """`rho(x) = (1 + z'(x)^2)^(3/2) / |z''(x)|` -- the well-posedness
        check for the foot-of-normal construction (shared default; every
        implementation below gets it for free from `z`'s derivatives)."""
        return _curvature_radius(self.dz_dx(x), self.d2z_dx2(x))


def _curvature_radius(dz_dx: float, d2z_dx2: float) -> float:
    if d2z_dx2 == 0.0:
        return math.inf
    return (1.0 + dz_dx**2) ** 1.5 / abs(d2z_dx2)


@dataclass(frozen=True)
class Flat(Road):
    """`z(x) = 0` everywhere."""

    def z(self, x: float) -> float:
        return 0.0

    def dz_dx(self, x: float) -> float:
        return 0.0

    def d2z_dx2(self, x: float) -> float:
        return 0.0

    def theta(self, x: float) -> float:
        return 0.0

    def frame0(self, x: float) -> tuple[tuple[float, float], tuple[float, float]]:
        return (1.0, 0.0), (0.0, 1.0)

    def curvature_radius(self, x: float) -> float:
        return math.inf


class ConstantGrade(Road):
    """`z(x) = x*tan(theta)`, a constant-slope line through the origin.
    `theta(x)` recovers the constructor's `theta` exactly via the
    inherited `atan(dz_dx(x))` default -- no override needed.

    Written by hand (not `@dataclass`) because the constructor parameter
    the spec names, `theta`, collides with `Road.theta(x)`'s method name:
    a dataclass field and a same-named inherited method cannot coexist (the
    instance attribute shadows the method). The grade value is stored
    under `_theta` instead, with `theta` kept free for the protocol
    method."""

    __slots__ = ("_theta",)
    _theta: float

    def __init__(self, theta: float) -> None:
        object.__setattr__(self, "_theta", theta)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is frozen")

    def __repr__(self) -> str:
        return f"ConstantGrade(theta={self._theta!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ConstantGrade) and self._theta == other._theta

    def z(self, x: float) -> float:
        return x * math.tan(self._theta)

    def dz_dx(self, x: float) -> float:
        return math.tan(self._theta)

    def d2z_dx2(self, x: float) -> float:
        return 0.0

    def curvature_radius(self, x: float) -> float:
        return math.inf


@dataclass(frozen=True)
class SineRoad(Road):
    """`z(x) = amplitude*sin(2*pi*x/wavelength)`."""

    amplitude: float
    wavelength: float

    def _omega(self) -> float:
        return 2.0 * math.pi / self.wavelength

    def z(self, x: float) -> float:
        return self.amplitude * math.sin(self._omega() * x)

    def dz_dx(self, x: float) -> float:
        w = self._omega()
        return self.amplitude * w * math.cos(w * x)

    def d2z_dx2(self, x: float) -> float:
        w = self._omega()
        return -self.amplitude * w**2 * math.sin(w * x)


@dataclass(frozen=True)
class StepJump(Road):
    """A smoothed step of `height` at `x_jump`, `tanh`-blended over
    `ramp_width` so `z` stays C-infinity (for airborne-phase testing)."""

    x_jump: float
    height: float
    ramp_width: float = 0.05

    def z(self, x: float) -> float:
        return self.height * 0.5 * (1.0 + math.tanh((x - self.x_jump) / self.ramp_width))

    def dz_dx(self, x: float) -> float:
        u = (x - self.x_jump) / self.ramp_width
        sech2 = 1.0 - math.tanh(u) ** 2
        return self.height * 0.5 * sech2 / self.ramp_width

    def d2z_dx2(self, x: float) -> float:
        u = (x - self.x_jump) / self.ramp_width
        t = math.tanh(u)
        sech2 = 1.0 - t**2
        return -self.height * sech2 * t / self.ramp_width**2


@dataclass(frozen=True)
class PiecewiseGrade(Road):
    """Piecewise-constant grade between `breakpoints = [(x_0, theta_0),
    (x_1, theta_1), ...]`, C1-smoothed: the grade transitions from one
    segment's `theta` to the next via a `tanh` blend of half-width
    `transition_width`, and `z` is the numerically-integrated elevation of
    that smoothed grade profile, cached on a fine grid and interpolated
    with a `CubicSpline` (C2, strictly smoother than the C1 the grade
    profile itself guarantees)."""

    breakpoints: tuple[tuple[float, float], ...]
    transition_width: float = 2.0
    grid_step: float = 0.1

    _spline: CubicSpline = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        xs = [bp[0] for bp in self.breakpoints]
        if len(xs) < 2:
            raise ValueError("PiecewiseGrade needs at least 2 breakpoints")
        if any(b <= a for a, b in zip(xs, xs[1:], strict=False)):
            raise ValueError("PiecewiseGrade breakpoints must be strictly increasing in x")
        x_lo, x_hi = xs[0] - 5 * self.transition_width, xs[-1] + 5 * self.transition_width
        n = max(int((x_hi - x_lo) / self.grid_step) + 1, 2)
        grid = np.linspace(x_lo, x_hi, n)
        grade = np.array([self._blended_theta(float(x)) for x in grid])
        slope = np.tan(grade)
        z_grid = np.concatenate(([0.0], np.cumsum((slope[1:] + slope[:-1]) * 0.5 * np.diff(grid))))
        object.__setattr__(self, "_spline", CubicSpline(grid, z_grid))

    def _blended_theta(self, x: float) -> float:
        theta = self.breakpoints[0][1]
        for x_b, theta_next in self.breakpoints[1:]:
            weight = 0.5 * (1.0 + math.tanh((x - x_b) / self.transition_width))
            theta = theta + weight * (theta_next - theta)
        return theta

    def z(self, x: float) -> float:
        lo, hi = float(self._spline.x[0]), float(self._spline.x[-1])
        if x < lo:
            return float(self._spline(lo)) + (x - lo) * math.tan(self.breakpoints[0][1])
        if x > hi:
            return float(self._spline(hi)) + (x - hi) * math.tan(self.breakpoints[-1][1])
        return float(self._spline(x))

    def dz_dx(self, x: float) -> float:
        lo, hi = float(self._spline.x[0]), float(self._spline.x[-1])
        if x < lo:
            return math.tan(self.breakpoints[0][1])
        if x > hi:
            return math.tan(self.breakpoints[-1][1])
        return float(self._spline(x, 1))

    def d2z_dx2(self, x: float) -> float:
        lo, hi = float(self._spline.x[0]), float(self._spline.x[-1])
        if x < lo or x > hi:
            return 0.0
        return float(self._spline(x, 2))


@dataclass(frozen=True)
class CubicSplineRoad(Road):
    """Real-elevation-data road: a `scipy.interpolate.CubicSpline` (C2)
    through measured `(x, z)` points (GPS routes, road surveys, DEMs)."""

    x_data: FloatArray
    z_data: FloatArray

    _spline: CubicSpline = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_spline", CubicSpline(self.x_data, self.z_data))

    def z(self, x: float) -> float:
        return float(self._spline(x))

    def dz_dx(self, x: float) -> float:
        return float(self._spline(x, 1))

    def d2z_dx2(self, x: float) -> float:
        return float(self._spline(x, 2))

    @classmethod
    def from_csv(cls, path: Path, *, x_column: str = "x", z_column: str = "z") -> CubicSplineRoad:
        """Load `(x, z)` columns from a CSV file with a header row."""
        data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding="utf-8")
        return cls(
            x_data=np.asarray(data[x_column], dtype=np.float64),
            z_data=np.asarray(data[z_column], dtype=np.float64),
        )

    @classmethod
    def from_gpx(cls, path: Path) -> CubicSplineRoad:
        """Load a GPX track: cumulative great-circle distance (equirectangular
        approximation -- adequate for road-grade purposes at the latitude
        spans a single ride covers) as `x`, `<ele>` as `z`."""
        import xml.etree.ElementTree as ET

        ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
        tree = ET.parse(path)
        points = []
        for trkpt in tree.getroot().iter("{http://www.topografix.com/GPX/1/1}trkpt"):
            lat = math.radians(float(trkpt.attrib["lat"]))
            lon = math.radians(float(trkpt.attrib["lon"]))
            ele_el = trkpt.find("gpx:ele", ns)
            ele = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0
            points.append((lat, lon, ele))
        if len(points) < 2:
            raise ValueError(f"GPX file {path} has fewer than 2 track points")
        earth_radius = 6371000.0
        lat0 = points[0][0]
        xs = [0.0]
        for (lat_a, lon_a, _), (lat_b, lon_b, _) in zip(points, points[1:], strict=False):
            dx = (lon_b - lon_a) * math.cos(lat0) * earth_radius
            dy = (lat_b - lat_a) * earth_radius
            xs.append(xs[-1] + math.hypot(dx, dy))
        zs = [p[2] for p in points]
        return cls(x_data=np.asarray(xs, dtype=np.float64), z_data=np.asarray(zs, dtype=np.float64))


def contact_reference(
    road: Road,
    center: tuple[float, float],
    radius: float,
    x_guess: float | None = None,
    *,
    tol: float = 1e-10,
    max_newton_iter: int = 20,
) -> float:
    """Solve `wheels_ground.tex`'s foot-of-normal residual
    `f(x) = (x_A - x) + (z_A - z(x))*z'(x) = 0` for the geometric contact
    reference `x*`, warm-started from `x_guess` (the previous step's `x*`),
    with a bracketed-bisection fallback on `[x_A - r, x_A + r]` if Newton
    does not converge. Logs a warning if `curvature_radius(x*) < 5*radius`
    (the well-posedness caveat made observable, per `refinements.tex`)."""
    x_a, z_a = center

    def f(x: float) -> float:
        return (x_a - x) + (z_a - road.z(x)) * road.dz_dx(x)

    def fprime(x: float) -> float:
        return -1.0 - road.dz_dx(x) ** 2 + (z_a - road.z(x)) * road.d2z_dx2(x)

    x = x_guess if x_guess is not None else x_a
    converged = False
    for _ in range(max_newton_iter):
        fx = f(x)
        if abs(fx) < tol:
            converged = True
            break
        fpx = fprime(x)
        if fpx == 0.0:
            break
        x_next = x - fx / fpx
        if not math.isfinite(x_next) or abs(x_next - x_a) > 10.0 * max(radius, 1.0):
            break
        x = x_next

    if not converged:
        lo, hi = x_a - radius, x_a + radius
        f_lo, f_hi = f(lo), f(hi)
        if f_lo == 0.0:
            x, converged = lo, True
        elif f_hi == 0.0:
            x, converged = hi, True
        elif (f_lo < 0.0) != (f_hi < 0.0):
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                f_mid = f(mid)
                if abs(f_mid) < tol or (hi - lo) < 1e-14:
                    x, converged = mid, True
                    break
                if (f_lo < 0.0) == (f_mid < 0.0):
                    lo, f_lo = mid, f_mid
                else:
                    hi, f_hi = mid, f_mid
            else:
                x = 0.5 * (lo + hi)
        else:
            logger.warning(
                "contact_reference: no sign change on [%.6g, %.6g] around "
                "center=(%.6g, %.6g); returning the best Newton estimate "
                "%.6g unconverged.",
                lo,
                hi,
                x_a,
                z_a,
                x,
            )

    rho_x = road.curvature_radius(x)
    if rho_x < 5.0 * radius:
        logger.warning(
            "contact_reference: curvature radius %.6g m at x*=%.6g is < "
            "5*wheel_radius (%.6g m); the point-contact foot-of-normal "
            "model may be invalid here (refinements.tex well-posedness "
            "caveat).",
            rho_x,
            x,
            5.0 * radius,
        )
    return x


def load_road(path: Path) -> Road:
    """Build a `Road` from a TOML file with a `kind` field selecting the
    implementation and the rest of the table its constructor arguments."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    kind = data.pop("kind")
    if kind == "flat":
        return Flat()
    if kind == "constant_grade":
        theta = data["theta"] if "theta" in data else math.atan(data["grade"])
        return ConstantGrade(theta=theta)
    if kind == "sine":
        return SineRoad(amplitude=data["amplitude"], wavelength=data["wavelength"])
    if kind == "step_jump":
        return StepJump(
            x_jump=data["x_jump"],
            height=data["height"],
            ramp_width=data.get("ramp_width", 0.05),
        )
    if kind == "piecewise_grade":
        breakpoints = tuple((bp[0], bp[1]) for bp in data["breakpoints"])
        return PiecewiseGrade(
            breakpoints=breakpoints,
            transition_width=data.get("transition_width", 2.0),
        )
    raise ValueError(f"unknown road kind {kind!r} in {path}")


def list_road_presets() -> list[str]:
    """Names (without `.toml`) of every shipped road preset."""
    if not ROAD_PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in ROAD_PRESET_DIR.glob("*.toml"))
