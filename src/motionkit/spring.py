"""Damped harmonic oscillator: m x'' + c x' + k (x - target) = 0.

Closed-form solutions for all three regimes:

* underdamped  (zeta < 1): overshoots and rings, decaying like e^(-zeta w0 t)
* critically damped (zeta = 1): fastest return with no overshoot
* overdamped   (zeta > 1): sluggish, no overshoot
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class Spring:
    stiffness: float = 100.0  # k
    damping: float = 10.0     # c
    mass: float = 1.0         # m

    @classmethod
    def from_omega(cls, omega: float, damping_ratio: float, mass: float = 1.0) -> "Spring":
        k = mass * omega * omega
        return cls(stiffness=k, damping=2.0 * damping_ratio * math.sqrt(k * mass), mass=mass)

    @classmethod
    def from_frequency(cls, frequency_hz: float, damping_ratio: float, mass: float = 1.0) -> "Spring":
        """frequency_hz is the undamped natural frequency (oscillations per second)."""
        return cls.from_omega(2.0 * math.pi * frequency_hz, damping_ratio, mass)

    @property
    def omega0(self) -> float:
        return math.sqrt(self.stiffness / self.mass)

    @property
    def zeta(self) -> float:
        return self.damping / (2.0 * math.sqrt(self.stiffness * self.mass))

    @property
    def regime(self) -> str:
        z = self.zeta
        if abs(z - 1.0) < 1e-6:
            return "critically_damped"
        return "underdamped" if z < 1.0 else "overdamped"

    def displacement(self, t: float, d0: float, v0: float = 0.0) -> float:
        """Offset from the target at time t, starting offset d0 and velocity v0."""
        if t <= 0.0:
            return d0 + v0 * t
        w, z = self.omega0, self.zeta
        if abs(z - 1.0) < 1e-6:
            return math.exp(-w * t) * (d0 + (v0 + w * d0) * t)
        if z < 1.0:
            wd = w * math.sqrt(1.0 - z * z)
            return math.exp(-z * w * t) * (d0 * math.cos(wd * t) + (v0 + z * w * d0) / wd * math.sin(wd * t))
        r = w * math.sqrt(z * z - 1.0)
        r1, r2 = -z * w + r, -z * w - r
        c2 = (v0 - r1 * d0) / (r2 - r1)
        c1 = d0 - c2
        return c1 * math.exp(r1 * t) + c2 * math.exp(r2 * t)

    def position(self, t: float, start: float, target: float, v0: float = 0.0) -> float:
        return target + self.displacement(t, start - target, v0)

    def settle_time(self, start: float = 0.0, target: float = 1.0, v0: float = 0.0,
                    tolerance: float = 0.001) -> float:
        """Time after which |x - target| stays within tolerance * |target - start|."""
        scale = abs(target - start) or abs(v0) / max(self.omega0, 1e-9) or 1.0
        eps = tolerance * scale
        w, z = self.omega0, self.zeta
        decay = z * w if z <= 1.0 else w * (z - math.sqrt(z * z - 1.0))
        horizon = max(1e-3, 12.0 / max(decay, 1e-9))
        n = 4000
        last = 0.0
        d0 = start - target
        for i in range(n + 1):
            t = horizon * i / n
            if abs(self.displacement(t, d0, v0)) > eps:
                last = t
        return min(horizon, last + horizon / n)

    def describe(self, start: float = 0.0, target: float = 1.0, v0: float = 0.0) -> Dict[str, Any]:
        settle = self.settle_time(start, target, v0)
        span = target - start
        peak = 0.0
        n = 2000
        for i in range(n + 1):
            x = self.position(settle * i / n, start, target, v0)
            if span != 0:
                peak = max(peak, (x - target) / span)
        w, z = self.omega0, self.zeta
        info = {
            "regime": self.regime,
            "stiffness": self.stiffness,
            "damping": self.damping,
            "mass": self.mass,
            "damping_ratio": z,
            "natural_frequency_hz": w / (2.0 * math.pi),
            "settle_time_s": settle,
            "overshoot": peak,
        }
        if z < 1.0:
            wd = w * math.sqrt(1.0 - z * z)
            info["oscillation_period_s"] = 2.0 * math.pi / wd
            info["visible_oscillations"] = settle / (2.0 * math.pi / wd)
        return info

    def sample(self, start: float, target: float, duration: float, fps: float, v0: float = 0.0) -> List[float]:
        frames = max(1, int(math.ceil(duration * fps)))
        return [self.position(i / fps, start, target, v0) for i in range(frames + 1)]


def resolve_spring(params: Dict[str, Any]) -> Spring:
    """Build a Spring from whichever parameter style the caller used.

    Accepts {frequency_hz, damping_ratio}, {stiffness, damping, mass},
    or {period_s, damping_ratio}.
    """
    mass = float(params.get("mass", 1.0) or 1.0)
    if params.get("stiffness") is not None:
        k = float(params["stiffness"])
        if params.get("damping") is not None:
            return Spring(k, float(params["damping"]), mass)
        z = float(params.get("damping_ratio", 0.5))
        return Spring(k, 2.0 * z * math.sqrt(k * mass), mass)
    z = float(params.get("damping_ratio", 0.5))
    if params.get("period_s"):
        return Spring.from_frequency(1.0 / float(params["period_s"]), z, mass)
    return Spring.from_frequency(float(params.get("frequency_hz", 2.0)), z, mass)
