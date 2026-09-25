"""Cubic Bézier curves, used two ways.

1. As a *timing* curve (CSS-style ``cubic-bezier(x1, y1, x2, y2)``): x is time,
   y is progress. The x handles must stay in [0, 1] so time never runs
   backwards; the y handles may leave [0, 1] to overshoot or anticipate.
2. As a *spatial* path in 3D: where an object travels. How fast it travels
   along the path is a separate timing curve applied to arc length.
"""

from __future__ import annotations

import bisect
import math
from typing import Callable, List, Optional, Sequence, Tuple

Vec = Tuple[float, float, float]


def _bez(p0: float, p1: float, p2: float, p3: float, s: float) -> float:
    u = 1.0 - s
    return u * u * u * p0 + 3.0 * u * u * s * p1 + 3.0 * u * s * s * p2 + s * s * s * p3


def _bez_d(p0: float, p1: float, p2: float, p3: float, s: float) -> float:
    u = 1.0 - s
    return 3.0 * u * u * (p1 - p0) + 6.0 * u * s * (p2 - p1) + 3.0 * s * s * (p3 - p2)


def cubic_bezier_easing(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """Timing function through (0,0), (x1,y1), (x2,y2), (1,1)."""
    if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0):
        raise ValueError("cubic_bezier x1 and x2 must be within [0, 1] (time cannot run backwards)")

    def solve_s(t: float) -> float:
        # Newton first, then bisection as a safety net.
        s = t
        for _ in range(8):
            err = _bez(0.0, x1, x2, 1.0, s) - t
            if abs(err) < 1e-12:
                return s
            d = _bez_d(0.0, x1, x2, 1.0, s)
            if abs(d) < 1e-9:
                break
            s -= err / d
        lo, hi = (0.0, 1.0) if 0.0 <= t <= 1.0 else (min(t, 0.0) - 1.0, max(t, 1.0) + 1.0)
        s = min(max(s, lo), hi)
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _bez(0.0, x1, x2, 1.0, mid) < t:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def f(t: float) -> float:
        return _bez(0.0, y1, y2, 1.0, solve_s(t))

    return f


# ---------------------------------------------------------------------------
# Spatial paths
# ---------------------------------------------------------------------------

def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _len(a: Vec) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _vec(p: Sequence[float]) -> Vec:
    p = list(p) + [0.0] * (3 - len(p))
    return (float(p[0]), float(p[1]), float(p[2]))


class BezierPath:
    """Piecewise cubic Bézier through anchors, with per-anchor handles.

    ``anchors[i]`` has ``handles_left[i]`` and ``handles_right[i]``; segment i
    runs anchors[i] -> handles_right[i] -> handles_left[i+1] -> anchors[i+1].
    """

    def __init__(self, anchors: Sequence[Sequence[float]], handles_left: Sequence[Sequence[float]],
                 handles_right: Sequence[Sequence[float]], closed: bool = False):
        if len(anchors) < 2:
            raise ValueError("A path needs at least two points")
        self.anchors = [_vec(p) for p in anchors]
        self.handles_left = [_vec(p) for p in handles_left]
        self.handles_right = [_vec(p) for p in handles_right]
        self.closed = closed
        self._lut: Optional[Tuple[List[float], List[float]]] = None

    @classmethod
    def through_points(cls, points: Sequence[Sequence[float]], closed: bool = False,
                       tension: float = 1.0) -> "BezierPath":
        """Smooth path through points (Catmull-Rom converted to Bézier handles).

        tension 1.0 is standard Catmull-Rom; lower values tighten corners,
        0 gives straight segments.
        """
        pts = [_vec(p) for p in points]
        n = len(pts)
        if n < 2:
            raise ValueError("A path needs at least two points")
        left, right = [], []
        for i in range(n):
            if closed:
                prev, nxt = pts[(i - 1) % n], pts[(i + 1) % n]
            else:
                prev = pts[i - 1] if i > 0 else _sub(_mul(pts[0], 2.0), pts[1])
                nxt = pts[i + 1] if i < n - 1 else _sub(_mul(pts[-1], 2.0), pts[-2])
            tangent = _mul(_sub(nxt, prev), tension / 6.0)
            left.append(_sub(pts[i], tangent))
            right.append(_add(pts[i], tangent))
        return cls(pts, left, right, closed)

    @property
    def segment_count(self) -> int:
        return len(self.anchors) if self.closed else len(self.anchors) - 1

    def _segment(self, i: int) -> Tuple[Vec, Vec, Vec, Vec]:
        j = (i + 1) % len(self.anchors)
        return self.anchors[i], self.handles_right[i], self.handles_left[j], self.anchors[j]

    def _locate(self, u: float) -> Tuple[int, float]:
        n = self.segment_count
        u = min(max(u, 0.0), float(n))
        i = min(int(u), n - 1)
        return i, u - i

    def point(self, u: float) -> Vec:
        """Point at curve parameter u in [0, segment_count]."""
        i, s = self._locate(u)
        p0, p1, p2, p3 = self._segment(i)
        return tuple(_bez(p0[k], p1[k], p2[k], p3[k], s) for k in range(3))  # type: ignore[return-value]

    def tangent(self, u: float) -> Vec:
        i, s = self._locate(u)
        p0, p1, p2, p3 = self._segment(i)
        return tuple(_bez_d(p0[k], p1[k], p2[k], p3[k], s) for k in range(3))  # type: ignore[return-value]

    def _build_lut(self, per_segment: int = 64) -> Tuple[List[float], List[float]]:
        if self._lut is None:
            us, ds = [0.0], [0.0]
            prev = self.point(0.0)
            total = 0.0
            steps = self.segment_count * per_segment
            for k in range(1, steps + 1):
                u = k / per_segment
                p = self.point(u)
                total += _len(_sub(p, prev))
                prev = p
                us.append(u)
                ds.append(total)
            self._lut = (us, ds)
        return self._lut

    @property
    def length(self) -> float:
        return self._build_lut()[1][-1]

    def parameter_at_fraction(self, fraction: float) -> float:
        """Curve parameter u at a fraction of total arc length (constant-speed mapping)."""
        us, ds = self._build_lut()
        target = min(max(fraction, 0.0), 1.0) * ds[-1]
        k = bisect.bisect_left(ds, target)
        if k <= 0:
            return 0.0
        if k >= len(ds):
            return us[-1]
        span = ds[k] - ds[k - 1]
        w = 0.0 if span <= 0 else (target - ds[k - 1]) / span
        return us[k - 1] + w * (us[k] - us[k - 1])

    def point_at_fraction(self, fraction: float) -> Vec:
        return self.point(self.parameter_at_fraction(fraction))

    def to_dict(self) -> dict:
        return {
            "anchors": [list(p) for p in self.anchors],
            "handles_left": [list(p) for p in self.handles_left],
            "handles_right": [list(p) for p in self.handles_right],
            "closed": self.closed,
            "length": self.length,
        }


def bezier_handles_for_segment(f0: float, v0: float, f1: float, v1: float,
                               x1: float, y1: float, x2: float, y2: float) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Map a normalized timing bezier onto an F-curve segment (frame, value).

    Returns (right handle of the first key, left handle of the second key).
    """
    df, dv = f1 - f0, v1 - v0
    return (f0 + x1 * df, v0 + y1 * dv), (f0 + x2 * df, v0 + y2 * dv)
