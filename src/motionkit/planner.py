"""Turn "go from A to B with this easing" into concrete F-curve keyframes.

This module is Blender-independent: it outputs a list of ``Key`` records
(frame, value, interpolation, easing and bezier handles) that the Blender
add-on writes into an F-curve verbatim. Keeping the maths here means it can
be unit-tested and reused outside Blender.

Strategy per segment:

* The easing maps to a Blender interpolation (SINE, QUAD, BACK, BOUNCE, ...):
  one key with that interpolation, nothing baked.
* The easing is a cubic timing bezier (smoothstep, CSS eases, cubic_bezier):
  one BEZIER key with handles placed so the F-curve *is* that bezier.
* Anything else (smootherstep, spring, elastic): baked into keys every
  ``bake_step`` frames. Each baked key gets bezier handles from the exact
  derivative (cubic Hermite), so velocity is continuous between samples and
  sub-frame evaluation (motion blur) stays faithful.
* ``"auto"``: smooth pass-through of the waypoint, Blender auto-clamped style
  (flat at local extremes, otherwise the average of neighbouring slopes).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .easing import EasingSpec, derivatives, resolve
from .spring import Spring

Handle = Tuple[float, float]


@dataclass
class Key:
    frame: float
    value: float
    interpolation: str = "BEZIER"
    easing: str = "AUTO"
    handle_left: Optional[Handle] = None
    handle_right: Optional[Handle] = None
    back: Optional[float] = None
    baked: bool = False
    extra: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, {}, False)}


@dataclass
class Waypoint:
    frame: float
    value: float
    easing: EasingSpec = "smootherstep"  # timing of the segment that *starts* here


def _hermite_handles(frame: float, value: float, slope_left: float, slope_right: float,
                     dl: float, dr: float) -> Tuple[Handle, Handle]:
    """Bezier handles reproducing a cubic Hermite spline with given slopes (value/frame)."""
    return ((frame - dl / 3.0, value - slope_left * dl / 3.0),
            (frame + dr / 3.0, value + slope_right * dr / 3.0))


def _auto_slope(prev: Optional[Tuple[float, float]], cur: Tuple[float, float],
                nxt: Optional[Tuple[float, float]]) -> float:
    if prev is None or nxt is None:
        return 0.0
    (f0, v0), (_, v1), (f2, v2) = prev, cur, nxt
    # Local extreme -> flat (Blender's "auto clamped").
    if (v1 - v0) * (v2 - v1) <= 0:
        return 0.0
    return (v2 - v0) / (f2 - f0)


def plan(waypoints: Sequence[Waypoint], mode: str = "auto", bake_step: float = 1.0) -> List[Key]:
    """Keyframes for a single scalar channel passing through the waypoints.

    mode: "auto" (native where exact, else bake), "native" (error if not
    possible), "bake" (always sample).
    """
    if mode not in ("auto", "native", "bake"):
        raise ValueError("mode must be 'auto', 'native' or 'bake'")
    if len(waypoints) < 2:
        raise ValueError("Need at least two waypoints")
    wps = sorted(waypoints, key=lambda w: w.frame)
    for a, b in zip(wps, wps[1:]):
        if b.frame - a.frame <= 0:
            raise ValueError("Waypoint frames must be strictly increasing (got %g then %g)" % (a.frame, b.frame))
    if bake_step <= 0:
        raise ValueError("bake_step must be > 0")

    keys: List[Key] = [Key(w.frame, w.value) for w in wps]
    baked_between: Dict[int, List[Key]] = {}

    for i in range(len(wps) - 1):
        w0, w1 = wps[i], wps[i + 1]
        k0, k1 = keys[i], keys[i + 1]
        df, dv = w1.frame - w0.frame, w1.value - w0.value
        if isinstance(w0.easing, str) and w0.easing.strip().lower() == "auto":
            prev = (wps[i - 1].frame, wps[i - 1].value) if i > 0 else None
            nxt = (wps[i + 2].frame, wps[i + 2].value) if i + 2 < len(wps) else None
            s0 = _auto_slope(prev, (w0.frame, w0.value), (w1.frame, w1.value))
            s1 = _auto_slope((w0.frame, w0.value), (w1.frame, w1.value), nxt)
            k0.interpolation = "BEZIER"
            k0.handle_right = (w0.frame + df / 3.0, w0.value + s0 * df / 3.0)
            k1.handle_left = (w1.frame - df / 3.0, w1.value - s1 * df / 3.0)
            continue

        e = resolve(w0.easing)
        use_native = mode != "bake" and e.native
        if mode == "native" and not e.native:
            raise ValueError("Easing %r has no exact Blender equivalent; use mode 'auto' or 'bake'" % e.spec())
        if use_native and e.blender is not None:
            interp, ease, extra = e.blender
            k0.interpolation = interp
            k0.easing = ease
            if "back" in extra:
                k0.back = extra["back"]
            continue
        if use_native and e.bezier is not None:
            x1, y1, x2, y2 = e.bezier
            k0.interpolation = "BEZIER"
            k0.handle_right = (w0.frame + x1 * df, w0.value + y1 * dv)
            k1.handle_left = (w0.frame + x2 * df, w0.value + y2 * dv)
            continue

        # Bake: samples every bake_step frames with exact-slope handles.
        steps = max(1, int(round(df / bake_step)))
        ts = [j / steps for j in range(steps + 1)]
        slopes_l, slopes_r, values = [], [], []
        for t in ts:
            # One-sided derivatives so kinks inside the easing (bounce) stay sharp.
            _, dl, _, _ = derivatives(e.fn, t, lo=max(0.0, t - 0.5 / steps), hi=t)
            _, dr, _, _ = derivatives(e.fn, t, lo=t, hi=min(1.0, t + 0.5 / steps))
            values.append(w0.value + dv * e(t))
            slopes_l.append(dv * dl / df)
            slopes_r.append(dv * dr / df)
        seg_df = df / steps
        k0.interpolation = "BEZIER"
        k0.handle_right = (w0.frame + seg_df / 3.0, w0.value + slopes_r[0] * seg_df / 3.0)
        k1.handle_left = (w1.frame - seg_df / 3.0, w1.value - slopes_l[-1] * seg_df / 3.0)
        inner = []
        for j in range(1, steps):
            f = w0.frame + j * seg_df
            hl, hr = _hermite_handles(f, values[j], slopes_l[j], slopes_r[j], seg_df, seg_df)
            inner.append(Key(f, values[j], "BEZIER", "AUTO", hl, hr, baked=True))
        baked_between[i] = inner

    # Fill missing outer handles by mirroring so Blender draws something sane.
    for k in keys:
        if k.handle_left is None and k.handle_right is not None:
            k.handle_left = (2 * k.frame - k.handle_right[0], 2 * k.value - k.handle_right[1])
        elif k.handle_right is None and k.handle_left is not None:
            k.handle_right = (2 * k.frame - k.handle_left[0], 2 * k.value - k.handle_left[1])

    out: List[Key] = []
    for i, k in enumerate(keys):
        out.append(k)
        out.extend(baked_between.get(i, []))
    return out


def plan_spring(start: float, target: float, start_frame: float, fps: float, spring: Spring,
                initial_velocity: float = 0.0, bake_step: float = 1.0, tolerance: float = 0.001,
                max_seconds: float = 20.0) -> List[Key]:
    """Bake a physical spring from start to target (velocity in units/second).

    Keys run until the spring settles within tolerance; the last key sits
    exactly on the target.
    """
    settle = min(max_seconds, spring.settle_time(start, target, initial_velocity, tolerance))
    total_frames = max(bake_step, math.ceil(settle * fps / bake_step) * bake_step)
    steps = max(1, int(round(total_frames / bake_step)))
    d0 = start - target
    keys: List[Key] = []
    h = 1e-4
    for j in range(steps + 1):
        f = start_frame + j * bake_step
        t = (f - start_frame) / fps
        if j == steps:
            value, slope = target, 0.0
        else:
            value = target + spring.displacement(t, d0, initial_velocity)
            if j == 0:
                vel = initial_velocity
            else:
                vel = (spring.displacement(t + h, d0, initial_velocity)
                       - spring.displacement(t - h, d0, initial_velocity)) / (2 * h)
            slope = vel / fps
        hl, hr = _hermite_handles(f, value, slope, slope, bake_step, bake_step)
        keys.append(Key(f, value, "BEZIER", "AUTO", hl, hr, baked=0 < j < steps))
    return keys


def sample_keys(keys: Sequence[Key], frame: float) -> float:
    """Evaluate a plan like Blender would for BEZIER/LINEAR/CONSTANT segments.

    Used by tests and previews; native Penner interpolations are evaluated
    through the matching easing.
    """
    from .easing import EASINGS, back as back_easing

    if frame <= keys[0].frame:
        return keys[0].value
    if frame >= keys[-1].frame:
        return keys[-1].value
    for k0, k1 in zip(keys, keys[1:]):
        if k0.frame <= frame <= k1.frame:
            break
    t_lin = (frame - k0.frame) / (k1.frame - k0.frame)
    if k0.interpolation == "CONSTANT":
        return k0.value
    if k0.interpolation == "LINEAR":
        return k0.value + (k1.value - k0.value) * t_lin
    if k0.interpolation != "BEZIER":
        suffix = {"EASE_IN": "in", "EASE_OUT": "out", "EASE_IN_OUT": "in_out", "AUTO": None}[k0.easing]
        if suffix is None:
            # Blender's AUTO easing: in for most, out for BACK/BOUNCE/ELASTIC.
            suffix = "out" if k0.interpolation in ("BACK", "BOUNCE", "ELASTIC") else "in"
        name = "%s_%s" % (k0.interpolation.lower(), suffix)
        e = back_easing(suffix, k0.back) if k0.interpolation == "BACK" else EASINGS[name]
        return k0.value + (k1.value - k0.value) * e(t_lin)
    p0 = (k0.frame, k0.value)
    p1 = k0.handle_right or p0
    p2 = k1.handle_left or (k1.frame, k1.value)
    p3 = (k1.frame, k1.value)
    from .bezier import _bez, _bez_d

    s = t_lin
    for _ in range(30):
        err = _bez(p0[0], p1[0], p2[0], p3[0], s) - frame
        d = _bez_d(p0[0], p1[0], p2[0], p3[0], s)
        if abs(err) < 1e-10 or abs(d) < 1e-12:
            break
        s = min(1.0, max(0.0, s - err / d))
    return _bez(p0[1], p1[1], p2[1], p3[1], s)
