"""Motion analysis: from sampled position to velocity, acceleration and jerk.

Given uniformly sampled values (scalar or vector), estimate x', x'', x''' by
finite differences and summarize the motion in terms an animator (human or
AI) can act on: does it ease in and out, where are the speed peaks, where
does velocity jump (a kink), how smooth is it compared with the
minimum-jerk ideal?
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Union

Number = float
Sample = Union[float, Sequence[float]]

SPARK = "▁▂▃▄▅▆▇█"

# Integral of jerk^2 for the minimum-jerk (smootherstep) move over unit
# distance and unit time. Dimensionless-jerk values are reported relative to it.
MIN_JERK_COST = 720.0


def _as_vectors(values: Sequence[Sample]) -> List[List[float]]:
    out = []
    for v in values:
        if isinstance(v, (int, float)):
            out.append([float(v)])
        else:
            out.append([float(c) for c in v])
    return out


def differentiate(series: Sequence[List[float]], dt: float) -> List[List[float]]:
    """Central differences inside, second-order one-sided at the ends."""
    n = len(series)
    if n < 2:
        return [[0.0] * len(series[0])] * n if n else []
    dim = len(series[0])
    out = []
    for i in range(n):
        if n >= 3 and i == 0:
            d = [(-3 * series[0][k] + 4 * series[1][k] - series[2][k]) / (2 * dt) for k in range(dim)]
        elif n >= 3 and i == n - 1:
            d = [(3 * series[-1][k] - 4 * series[-2][k] + series[-3][k]) / (2 * dt) for k in range(dim)]
        elif i == 0:
            d = [(series[1][k] - series[0][k]) / dt for k in range(dim)]
        elif i == n - 1:
            d = [(series[-1][k] - series[-2][k]) / dt for k in range(dim)]
        else:
            d = [(series[i + 1][k] - series[i - 1][k]) / (2 * dt) for k in range(dim)]
        out.append(d)
    return out


def third_derivative(series: Sequence[List[float]], dt: float) -> List[List[float]]:
    """Jerk from a 5-point stencil (central inside, shifted at the ends).

    Nesting three first differences smears and biases the ends; a direct
    stencil keeps the jerk estimate second-order accurate everywhere.
    """
    from .easing import _fornberg

    n = len(series)
    dim = len(series[0])
    if n < 5:
        return differentiate(differentiate(differentiate(series, dt), dt), dt)
    cache: Dict[int, List[float]] = {}
    out = []
    for i in range(n):
        lo = min(max(i - 2, 0), n - 5)
        shift = lo - i
        if shift not in cache:
            cache[shift] = _fornberg([float(shift + k) for k in range(5)], 3)[3]
        w = cache[shift]
        out.append([sum(w[k] * series[lo + k][c] for k in range(5)) / dt ** 3 for c in range(dim)])
    return out


def _mag(v: List[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


def sparkline(values: Sequence[float], width: int = 48) -> str:
    """Compact text chart of a series (min..max mapped to ▁..█)."""
    if not values:
        return ""
    n = len(values)
    if n > width:
        buckets = []
        for b in range(width):
            lo, hi = b * n // width, max(b * n // width + 1, (b + 1) * n // width)
            chunk = values[lo:hi]
            buckets.append(max(chunk, key=abs))
        values = buckets
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, int((v - lo) / (hi - lo) * (len(SPARK) - 1) + 0.5))] for v in values)


def _peak(frames: Sequence[float], vals: Sequence[float]) -> Dict[str, float]:
    k = max(range(len(vals)), key=lambda i: abs(vals[i]))
    return {"value": vals[k], "frame": frames[k]}


def analyze(values: Sequence[Sample], frame_start: float, frame_step: float, fps: float,
            label: str = "", table_every: int = 0) -> Dict[str, Any]:
    """Analyze a sampled motion.

    values: samples at frame_start + i * frame_step (scalars or vectors).
    Rates are reported per second (units/s, units/s^2, units/s^3).
    For vectors, speed/acceleration/jerk are magnitudes.
    """
    xs = _as_vectors(values)
    n = len(xs)
    if n < 4:
        raise ValueError("Need at least 4 samples to analyze motion")
    dt = frame_step / fps
    frames = [frame_start + i * frame_step for i in range(n)]
    vs = differentiate(xs, dt)
    as_ = differentiate(vs, dt)
    js = third_derivative(xs, dt)
    scalar = len(xs[0]) == 1
    if scalar:
        speed = [v[0] for v in vs]
        acc = [a[0] for a in as_]
        jerk = [j[0] for j in js]
    else:
        speed = [_mag(v) for v in vs]
        acc = [_mag(a) for a in as_]
        jerk = [_mag(j) for j in js]
    abs_speed = [abs(s) for s in speed]
    peak_speed = max(abs_speed) or 0.0
    displacement = [xs[-1][k] - xs[0][k] for k in range(len(xs[0]))]
    distance = sum(_mag([xs[i + 1][k] - xs[i][k] for k in range(len(xs[0]))]) for i in range(n - 1))
    duration = (n - 1) * dt

    # Active window: where the thing is actually moving.
    moving_tol = max(1e-9, peak_speed * 1e-4)
    active = [i for i in range(n) if abs_speed[i] > moving_tol]
    issues: List[str] = []
    result: Dict[str, Any] = {
        "label": label,
        "frames": [frames[0], frames[-1]],
        "fps": fps,
        "duration_s": duration,
        "start_value": xs[0] if not scalar else xs[0][0],
        "end_value": xs[-1] if not scalar else xs[-1][0],
        "displacement": displacement if not scalar else displacement[0],
        "path_length": distance,
    }
    if not active:
        result.update({"moving": False, "summary": "No motion in this range."})
        return result
    # Motion window: from the last sample still at the start pose to the first
    # sample already at the end pose (holds before/after are excluded).
    dim = len(xs[0])
    extent = max(_mag([x[k] - xs[0][k] for k in range(dim)]) for x in xs) or 1.0
    tol = extent * 1e-7
    a0 = next(i for i in range(n) if _mag([xs[i][k] - xs[0][k] for k in range(dim)]) > tol)
    a0 = max(0, a0 - 1)
    a1 = next(i for i in range(n - 1, -1, -1) if _mag([xs[i][k] - xs[-1][k] for k in range(dim)]) > tol)
    a1 = min(n - 1, a1 + 1)
    result["moving"] = True
    result["motion_window_frames"] = [frames[a0], frames[a1]]
    result["peak_velocity"] = _peak(frames, speed)
    result["peak_acceleration"] = _peak(frames, acc)
    result["peak_jerk"] = _peak(frames, jerk)
    result["velocity_at_start"] = speed[a0]
    result["velocity_at_end"] = speed[a1]
    start_ratio = abs_speed[a0] / peak_speed
    end_ratio = abs_speed[a1] / peak_speed
    result["eases_in"] = start_ratio < 0.05
    result["eases_out"] = end_ratio < 0.05

    if not result["eases_in"]:
        issues.append("Starts abruptly: speed at frame %g is %.0f%% of peak. Use an ease-in "
                      "(smootherstep, sine_in_out) unless it is meant to continue earlier motion."
                      % (frames[a0], start_ratio * 100))
    if not result["eases_out"]:
        issues.append("Stops abruptly: speed at frame %g is %.0f%% of peak. Use an ease-out, or a spring "
                      "for a physical settle." % (frames[a1], end_ratio * 100))

    # Velocity discontinuities (kinks): a sample-to-sample velocity jump that is
    # large relative to peak speed and far above the local trend.
    kinks = []
    for i in range(1, n - 1):
        jump = _mag([vs[i + 1][k] - vs[i - 1][k] for k in range(dim)]) / 2.0
        if peak_speed <= 0 or jump < 0.1 * peak_speed:
            continue
        neigh = [abs(acc[j]) for j in (i - 3, i + 3) if 0 <= j < n]
        if neigh and abs(acc[i]) > 2.5 * max(neigh):
            kinks.append(i)
    merged: List[int] = []
    for i in kinks:
        if not merged or i - merged[-1] > 2:
            merged.append(i)
        elif abs(acc[i]) > abs(acc[merged[-1]]):
            merged[-1] = i
    result["velocity_kinks_at_frames"] = [frames[i] for i in merged]
    if merged:
        issues.append("Velocity jumps (sudden direction/speed change) near frame(s) %s. Typical causes: "
                      "linear keys, broken bezier handles, or neighbouring segments with mismatched "
                      "end/start speeds." % ", ".join("%g" % frames[i] for i in merged[:8]))

    # Acceleration jumps (felt as a kick): jerk spikes not explained by a velocity kink.
    peak_acc = max(abs(a) for a in acc) or 0.0
    kicks: List[int] = []
    for i in range(1, n - 1):
        if any(abs(i - k) <= 3 for k in merged):
            continue
        jump = abs(acc[i + 1] - acc[i - 1]) / 2.0
        if peak_acc <= 0 or jump < 0.15 * peak_acc:
            continue
        neigh = [abs(jerk[j]) for j in (i - 4, i + 4) if 0 <= j < n]
        if neigh and abs(jerk[i]) > 2.5 * max(neigh):
            if kicks and i - kicks[-1] <= 3:
                if abs(jerk[i]) > abs(jerk[kicks[-1]]):
                    kicks[-1] = i
            else:
                kicks.append(i)
    result["acceleration_jumps_at_frames"] = [frames[i] for i in kicks]
    if kicks:
        issues.append("Acceleration jumps (a 'kick', infinite jerk) near frame(s) %s. Typical of smoothstep, "
                      "sine and power in-out easings (at rest, or at the midpoint for cubic/quart/quint "
                      "in-out); smootherstep keeps acceleration continuous and zero at rest." % ", ".join("%g" % frames[i] for i in kicks[:8]))

    # Mid-move stops: speed falls to ~0 between two moving stretches.
    stop_tol = 0.03 * peak_speed
    stops: List[int] = []
    i = a0 + 1
    while i < a1:
        if abs_speed[i] < stop_tol and any(s > 0.2 * peak_speed for s in abs_speed[a0:i]) \
                and any(s > 0.2 * peak_speed for s in abs_speed[i:a1 + 1]):
            j = i
            while j + 1 < a1 and abs_speed[j + 1] < stop_tol:
                j += 1
            # A scalar channel that turns around (apex, spring overshoot) is reversing, not hitching.
            before = next((speed[k] for k in range(i - 1, a0 - 1, -1) if abs_speed[k] >= stop_tol), 0.0)
            after = next((speed[k] for k in range(j + 1, a1 + 1) if abs_speed[k] >= stop_tol), 0.0)
            if not scalar or before * after > 0:
                stops.append(min(range(i, j + 1), key=lambda k: abs_speed[k]))
            i = j + 1
        else:
            i += 1
    result["stops_at_frames"] = [frames[k] for k in stops]
    if stops:
        issues.append("Comes to rest mid-move at frame(s) %s. Right for a held pose; a hitch if the move "
                      "should flow through. For flow, use easing 'auto' on those waypoints or pair an "
                      "ease-in segment with an ease-out segment (e.g. cubic_in then cubic_out)."
                      % ", ".join("%g" % frames[k] for k in stops[:8]))

    # Overshoot / oscillation (scalar channels).
    if scalar and abs(displacement[0]) > 1e-9:
        start, end = xs[0][0], xs[-1][0]
        span = end - start
        vals = [x[0] for x in xs]
        over = max((v - end) / span for v in vals)
        under = max((start - v) / span for v in vals)
        result["overshoot"] = max(0.0, over)
        result["anticipation"] = max(0.0, under)
        if over > 0.005:
            issues.append("Overshoots the final value by %.1f%% of the move." % (over * 100))
        if under > 0.005:
            issues.append("Anticipates: moves %.1f%% backwards before going." % (under * 100))
    if scalar:
        signs = [1 if s > 0 else -1 for s in speed if abs(s) > moving_tol]
        result["direction_reversals"] = sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    # Smoothness vs. minimum jerk: DJ = (T^5 / L^2) * integral(jerk^2 dt).
    T = (a1 - a0) * dt
    L = distance if not scalar else abs(displacement[0]) or distance
    if T > 0 and L > 1e-9:
        sq = [j * j for j in jerk[a0:a1 + 1]]
        integral = (sum(sq) - 0.5 * (sq[0] + sq[-1])) * dt  # trapezoid rule
        dj = T ** 5 / (L * L) * integral
        result["dimensionless_jerk"] = dj
        # Minimum jerk is the theoretical optimum; values a hair above 1 are sampling error.
        result["smoothness_vs_min_jerk"] = min(1.0, MIN_JERK_COST / dj) if dj > 0 else None

    result["sparklines"] = {
        "position": sparkline([x[0] for x in xs] if scalar else [_mag([c - s for c, s in zip(x, xs[0])]) for x in xs]),
        "velocity": sparkline(speed),
        "acceleration": sparkline(acc),
        "jerk": sparkline(jerk),
    }
    if table_every and table_every > 0:
        rows = []
        for i in range(0, n, table_every):
            rows.append({
                "frame": round(frames[i], 4),
                "value": xs[i][0] if scalar else [round(c, 6) for c in xs[i]],
                "velocity": speed[i],
                "acceleration": acc[i],
                "jerk": jerk[i],
            })
        result["table"] = rows
    result["issues"] = issues
    result["summary"] = _summary(result)
    return result


def _summary(r: Dict[str, Any]) -> str:
    parts = []
    ein, eout = r.get("eases_in"), r.get("eases_out")
    if ein and eout:
        parts.append("eases in and out")
    elif ein:
        parts.append("eases in, hard stop")
    elif eout:
        parts.append("hard start, eases out")
    else:
        parts.append("hard start and stop")
    pv = r.get("peak_velocity")
    if pv:
        parts.append("peak speed %.3g/s at frame %g" % (abs(pv["value"]), pv["frame"]))
    if r.get("overshoot", 0) > 0.005:
        parts.append("%.0f%% overshoot" % (r["overshoot"] * 100))
    if r.get("velocity_kinks_at_frames"):
        parts.append("%d velocity kink(s)" % len(r["velocity_kinks_at_frames"]))
    if r.get("stops_at_frames"):
        parts.append("%d mid-move stop(s)" % len(r["stops_at_frames"]))
    sm = r.get("smoothness_vs_min_jerk")
    if sm:
        parts.append("smoothness %.2f of minimum-jerk ideal" % sm)
    return "; ".join(parts) + "."


def analyze_channels(channels: Dict[str, Sequence[Sample]], frame_start: float, frame_step: float,
                     fps: float, table_every: int = 0) -> Dict[str, Any]:
    return {name: analyze(vals, frame_start, frame_step, fps, label=name, table_every=table_every)
            for name, vals in channels.items()}


def compare(before: Dict[str, Any], after: Dict[str, Any]) -> Optional[str]:
    """One-line diff of two analyze() results (useful after an edit)."""
    keys = ("eases_in", "eases_out", "overshoot", "smoothness_vs_min_jerk")
    diffs = []
    for k in keys:
        if before.get(k) != after.get(k):
            diffs.append("%s: %s -> %s" % (k, before.get(k), after.get(k)))
    return "; ".join(diffs) or None
