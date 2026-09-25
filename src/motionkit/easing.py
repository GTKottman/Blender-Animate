"""Easing functions: the timing curve f(t) in x(t) = A + (B - A) * f(t).

Every easing maps normalized time t in [0, 1] to normalized progress, with
f(0) = 0 and f(1) = 1. Some (back, elastic, spring) overshoot past 1 on the
way. The raw formulas are also defined a little outside [0, 1] where that
is meaningful, which the finite-difference derivatives in ``derivatives``
rely on.

An easing is named by a *spec*:

* a string: ``"smootherstep"``, ``"back_out"``, ``"cubic_bezier(0.25, 0.1, 0.25, 1)"``,
  ``"back_out(2.5)"``, ``"spring(0.35)"``, ``"elastic_out(1, 0.3)"``
* a dict: ``{"type": "cubic_bezier", "x1": 0.4, "y1": 0, "x2": 0.2, "y2": 1}``,
  ``{"type": "spring", "damping_ratio": 0.3}``

``resolve(spec)`` turns a spec into an ``Easing``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .bezier import cubic_bezier_easing

EasingSpec = Union[str, Dict[str, Any], "Easing"]
Fn = Callable[[float], float]


@dataclass
class Easing:
    """A named timing curve plus how (if at all) Blender can play it natively."""

    name: str
    fn: Fn
    description: str = ""
    family: str = ""
    params: Dict[str, float] = field(default_factory=dict)
    # (interpolation, easing, extra keyframe props) if a Blender keyframe can
    # reproduce this curve exactly without baking.
    blender: Optional[Tuple[str, str, Dict[str, float]]] = None
    # Cubic-bezier control points (x1, y1, x2, y2) if the curve is exactly a
    # cubic timing bezier; Blender BEZIER keys can then play it natively.
    bezier: Optional[Tuple[float, float, float, float]] = None

    def __call__(self, t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        return self.fn(t)

    @property
    def native(self) -> bool:
        return self.blender is not None or self.bezier is not None

    def spec(self) -> str:
        if not self.params:
            return self.name
        return "%s(%s)" % (self.name, ", ".join(_fmt(v) for v in self.params.values()))


def _fmt(v: float) -> str:
    return ("%.6g" % v)


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------

def linear(t: float) -> float:
    return t


def smoothstep(t: float) -> float:
    """3t^2 - 2t^3: zero velocity at both ends (acceleration is not zero)."""
    return t * t * (3.0 - 2.0 * t)


def smootherstep(t: float) -> float:
    """6t^5 - 15t^4 + 10t^3: zero velocity AND acceleration at both ends.

    This is exactly the minimum-jerk trajectory between two rests.
    """
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def _power(p: float) -> Tuple[Fn, Fn, Fn]:
    def ease_in(t: float) -> float:
        return math.copysign(abs(t) ** p, t) if t < 0 else t ** p

    def ease_out(t: float) -> float:
        return 1.0 - ease_in(1.0 - t)

    def ease_in_out(t: float) -> float:
        if t < 0.5:
            return 0.5 * ease_in(2.0 * t)
        return 1.0 - 0.5 * ease_in(2.0 - 2.0 * t)

    return ease_in, ease_out, ease_in_out


def sine_in(t: float) -> float:
    return 1.0 - math.cos(t * math.pi / 2.0)


def sine_out(t: float) -> float:
    return math.sin(t * math.pi / 2.0)


def sine_in_out(t: float) -> float:
    return -(math.cos(math.pi * t) - 1.0) / 2.0


_EXPO_MIN = 2.0 ** -10


def _expo(t: float) -> float:
    # Rescaled so the curve starts exactly at 0 (Blender's EXPO does the same).
    return (2.0 ** (10.0 * t - 10.0) - _EXPO_MIN) / (1.0 - _EXPO_MIN)


def expo_in(t: float) -> float:
    return _expo(t)


def expo_out(t: float) -> float:
    return 1.0 - _expo(1.0 - t)


def expo_in_out(t: float) -> float:
    if t < 0.5:
        return _expo(2.0 * t) / 2.0
    return 1.0 - _expo(2.0 - 2.0 * t) / 2.0


def _sqrt0(x: float) -> float:
    return math.sqrt(max(0.0, x))


def circ_in(t: float) -> float:
    return 1.0 - _sqrt0(1.0 - t * t)


def circ_out(t: float) -> float:
    return _sqrt0(1.0 - (t - 1.0) ** 2)


def circ_in_out(t: float) -> float:
    if t < 0.5:
        return (1.0 - _sqrt0(1.0 - (2.0 * t) ** 2)) / 2.0
    return (_sqrt0(1.0 - (-2.0 * t + 2.0) ** 2) + 1.0) / 2.0


def _back(s: float) -> Tuple[Fn, Fn, Fn]:
    c1 = s
    c3 = s + 1.0
    c2 = s * 1.525

    def ease_in(t: float) -> float:
        return c3 * t ** 3 - c1 * t * t

    def ease_out(t: float) -> float:
        u = t - 1.0
        return 1.0 + c3 * u ** 3 + c1 * u * u

    def ease_in_out(t: float) -> float:
        if t < 0.5:
            u = 2.0 * t
            return (u * u * ((c2 + 1.0) * u - c2)) / 2.0
        u = 2.0 * t - 2.0
        return (u * u * ((c2 + 1.0) * u + c2) + 2.0) / 2.0

    return ease_in, ease_out, ease_in_out


def _elastic(amplitude: float, period: float) -> Tuple[Fn, Fn, Fn]:
    a = max(1.0, amplitude)
    p = period
    s = p / (2.0 * math.pi) * math.asin(1.0 / a)

    def ease_out(t: float) -> float:
        if t <= 0.0:
            return 0.0
        return a * 2.0 ** (-10.0 * t) * math.sin((t - s) * 2.0 * math.pi / p) + 1.0

    def ease_in(t: float) -> float:
        return 1.0 - ease_out(1.0 - t)

    def ease_in_out(t: float) -> float:
        if t < 0.5:
            return 0.5 * ease_in(2.0 * t)
        return 0.5 + 0.5 * ease_out(2.0 * t - 1.0)

    return ease_in, ease_out, ease_in_out


def bounce_out(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def bounce_in(t: float) -> float:
    return 1.0 - bounce_out(1.0 - t)


def bounce_in_out(t: float) -> float:
    if t < 0.5:
        return (1.0 - bounce_out(1.0 - 2.0 * t)) / 2.0
    return (1.0 + bounce_out(2.0 * t - 1.0)) / 2.0


def spring_easing(damping_ratio: float = 0.4, oscillations: Optional[float] = None) -> Fn:
    """Normalized damped spring released from 0 toward 1, settled by t = 1.

    The natural frequency is chosen so the envelope decays to 0.1% by t = 1
    (or, if ``oscillations`` is given, so that many half-cycles fit).
    A tiny linear correction guarantees f(1) = 1 exactly.
    """
    from .spring import Spring

    zeta = max(1e-3, float(damping_ratio))
    if oscillations and zeta < 1.0:
        omega = oscillations * math.pi / math.sqrt(1.0 - zeta * zeta)
    else:
        omega = math.log(1000.0) / min(zeta, 1.0) if zeta < 1.0 else 12.0 * zeta
    spring = Spring.from_omega(omega, zeta)
    end = spring.position(1.0, 0.0, 1.0)

    def f(t: float) -> float:
        if t <= 0.0:
            # Continue the rest state before release so derivatives see a hold.
            return 0.0
        return spring.position(t, 0.0, 1.0) + t * (1.0 - end)

    return f


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

EASINGS: Dict[str, Easing] = {}


def _register(e: Easing) -> None:
    EASINGS[e.name] = e


_register(Easing("linear", linear, "Constant velocity. Starts and stops instantly; reads as mechanical.",
                 "linear", blender=("LINEAR", "AUTO", {})))
_register(Easing("constant", lambda t: 0.0 if t < 1.0 else 1.0,
                 "Hold then jump at the end (stepped / pose-to-pose blocking).", "step",
                 blender=("CONSTANT", "AUTO", {})))
_register(Easing("smoothstep", smoothstep,
                 "3t^2-2t^3. Zero velocity at both ends; acceleration jumps at the ends. "
                 "Identical to a cubic bezier with handles (1/3,0),(2/3,1), so Blender plays it natively.",
                 "polynomial", bezier=(1.0 / 3.0, 0.0, 2.0 / 3.0, 1.0)))
_register(Easing("smootherstep", smootherstep,
                 "6t^5-15t^4+10t^3. Zero velocity and acceleration at both ends; the minimum-jerk "
                 "path between two rests. Best default for natural point-to-point moves.",
                 "polynomial"))

for _name, _p in (("quad", 2.0), ("cubic", 3.0), ("quart", 4.0), ("quint", 5.0)):
    _i, _o, _io = _power(_p)
    _up = _name.upper()
    _register(Easing(_name + "_in", _i, "t^%d: starts at rest, accelerates, arrives at full speed." % _p,
                     _name, blender=(_up, "EASE_IN", {})))
    _register(Easing(_name + "_out", _o, "Leaves at full speed, decelerates to rest (power %d)." % _p,
                     _name, blender=(_up, "EASE_OUT", {})))
    _register(Easing(_name + "_in_out", _io, "Accelerate then decelerate, symmetric (power %d)." % _p,
                     _name, blender=(_up, "EASE_IN_OUT", {})))

for _suffix, _f, _bl, _d in (
    ("in", sine_in, "EASE_IN", "Gentle start, full-speed arrival."),
    ("out", sine_out, "EASE_OUT", "Full-speed start, gentle stop."),
    ("in_out", sine_in_out, "EASE_IN_OUT", "Gentle, symmetric; softer than cubic."),
):
    _register(Easing("sine_" + _suffix, _f, _d, "sine", blender=("SINE", _bl, {})))

for _suffix, _f, _bl in (("in", expo_in, "EASE_IN"), ("out", expo_out, "EASE_OUT"),
                         ("in_out", expo_in_out, "EASE_IN_OUT")):
    _register(Easing("expo_" + _suffix, _f, "Exponential; very sharp ramp, snappy UI-style motion.",
                     "expo", blender=("EXPO", _bl, {})))

for _suffix, _f, _bl in (("in", circ_in, "EASE_IN"), ("out", circ_out, "EASE_OUT"),
                         ("in_out", circ_in_out, "EASE_IN_OUT")):
    _register(Easing("circ_" + _suffix, _f, "Circular arc; infinite slope at one end, abrupt feel.",
                     "circ", blender=("CIRC", _bl, {})))

for _suffix, _f, _bl in (("in", bounce_in, "EASE_IN"), ("out", bounce_out, "EASE_OUT"),
                         ("in_out", bounce_in_out, "EASE_IN_OUT")):
    _register(Easing("bounce_" + _suffix, _f, "Ball-drop bounces (piecewise parabolas).",
                     "bounce", blender=("BOUNCE", _bl, {})))

_CSS = {
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease_in": (0.42, 0.0, 1.0, 1.0),
    "ease_out": (0.0, 0.0, 0.58, 1.0),
    "ease_in_out": (0.42, 0.0, 0.58, 1.0),
}
for _name, _cp in _CSS.items():
    _register(Easing(_name, cubic_bezier_easing(*_cp), "CSS '%s' timing: cubic_bezier%s." % (_name, _cp),
                     "cubic_bezier", bezier=_cp))


def back(variant: str = "out", overshoot: float = 1.70158) -> Easing:
    fns = dict(zip(("in", "out", "in_out"), _back(overshoot)))
    bl = {"in": "EASE_IN", "out": "EASE_OUT", "in_out": "EASE_IN_OUT"}[variant]
    return Easing("back_" + variant, fns[variant],
                  "Anticipation/overshoot: pulls back past the start (in) or past the target (out).",
                  "back", params={"overshoot": overshoot}, blender=("BACK", bl, {"back": overshoot}))


def elastic(variant: str = "out", amplitude: float = 1.0, period: float = 0.3) -> Easing:
    fns = dict(zip(("in", "out", "in_out"), _elastic(amplitude, period)))
    return Easing("elastic_" + variant, fns[variant],
                  "Rubber-band oscillation around the target. Baked (Blender's ELASTIC uses other units).",
                  "elastic", params={"amplitude": amplitude, "period": period})


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Easing:
    return Easing("cubic_bezier", cubic_bezier_easing(x1, y1, x2, y2),
                  "Custom cubic timing bezier with handles (x1,y1) and (x2,y2).", "cubic_bezier",
                  params={"x1": x1, "y1": y1, "x2": x2, "y2": y2}, bezier=(x1, y1, x2, y2))


def spring(damping_ratio: float = 0.4, oscillations: float = 0.0) -> Easing:
    return Easing("spring", spring_easing(damping_ratio, oscillations or None),
                  "Damped spring released toward the target, settled by the end. "
                  "damping_ratio < 1 overshoots and rings, 1 is critically damped (fastest with no overshoot).",
                  "spring", params={"damping_ratio": damping_ratio, "oscillations": oscillations})


for _v in ("in", "out", "in_out"):
    _register(back(_v))
    _register(elastic(_v))
_register(spring())

_FACTORIES: Dict[str, Callable[..., Easing]] = {
    "cubic_bezier": cubic_bezier,
    "spring": spring,
    "back_in": lambda *a, **k: back("in", *a, **k),
    "back_out": lambda *a, **k: back("out", *a, **k),
    "back_in_out": lambda *a, **k: back("in_out", *a, **k),
    "elastic_in": lambda *a, **k: elastic("in", *a, **k),
    "elastic_out": lambda *a, **k: elastic("out", *a, **k),
    "elastic_in_out": lambda *a, **k: elastic("in_out", *a, **k),
}

_ALIASES = {
    "ease_in_out_cubic": "cubic_in_out",
    "easeinout": "ease_in_out",
    "easein": "ease_in",
    "easeout": "ease_out",
    "step": "constant",
    "hold": "constant",
    "minimum_jerk": "smootherstep",
    "min_jerk": "smootherstep",
    "bezier": "smoothstep",
}

_CALL_RE = re.compile(r"^\s*([A-Za-z_][\w\- ]*?)\s*(?:\((.*)\))?\s*$")


_PENNER_RE = re.compile(r"^ease_(in_out|in|out)_([a-z]+)$")


def _norm(name: str) -> str:
    """Accept snake_case, kebab-case, camelCase and Penner names (easeOutBack)."""
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.strip())
    n = n.lower().replace("-", "_").replace(" ", "_").replace("inout", "in_out")
    n = re.sub(r"_+", "_", n)
    n = _ALIASES.get(n, n)
    m = _PENNER_RE.match(n)
    if m and n not in EASINGS:
        n = "%s_%s" % (m.group(2), m.group(1))
    return n


def resolve(spec: EasingSpec) -> Easing:
    """Turn a string/dict spec into an Easing. Raises ValueError with suggestions."""
    if isinstance(spec, Easing):
        return spec
    if spec is None:
        return EASINGS["smootherstep"]
    if isinstance(spec, dict):
        d = dict(spec)
        name = _norm(str(d.pop("type", d.pop("name", "smootherstep"))))
        if name in _FACTORIES:
            return _FACTORIES[name](**{k: float(v) for k, v in d.items()})
        if name in EASINGS and not d:
            return EASINGS[name]
        raise ValueError("Unknown easing %r. %s" % (spec, _suggest(name)))
    m = _CALL_RE.match(str(spec))
    if not m:
        raise ValueError("Cannot parse easing spec %r" % (spec,))
    name = _norm(m.group(1))
    args: List[float] = []
    if m.group(2) and m.group(2).strip():
        try:
            args = [float(a) for a in m.group(2).split(",")]
        except ValueError:
            raise ValueError("Easing arguments must be numbers: %r" % (spec,))
    if args:
        if name not in _FACTORIES:
            raise ValueError("Easing %r takes no parameters." % name)
        return _FACTORIES[name](*args)
    if name in EASINGS:
        return EASINGS[name]
    raise ValueError("Unknown easing %r. %s" % (spec, _suggest(name)))


def _suggest(name: str) -> str:
    import difflib

    close = difflib.get_close_matches(name, list(EASINGS) + list(_ALIASES), n=4, cutoff=0.5)
    hint = ("Did you mean: %s? " % ", ".join(close)) if close else ""
    return hint + "Use list_easings to see all names."


def catalog() -> List[Dict[str, Any]]:
    out = []
    for e in EASINGS.values():
        out.append({
            "name": e.name,
            "family": e.family,
            "description": e.description,
            "blender_native": e.native,
            "parameters": e.params or None,
        })
    return out


# ---------------------------------------------------------------------------
# Derivatives: position -> velocity -> acceleration -> jerk
# ---------------------------------------------------------------------------

def _fornberg(offsets: List[float], order: int) -> List[List[float]]:
    """Finite-difference weights at 0 for derivatives 0..order (Fornberg 1988)."""
    n = len(offsets)
    c = [[0.0] * n for _ in range(order + 1)]
    c1 = 1.0
    c4 = offsets[0]
    c[0][0] = 1.0
    for i in range(1, n):
        mn = min(i, order)
        c2 = 1.0
        c5 = c4
        c4 = offsets[i]
        for j in range(i):
            c3 = offsets[i] - offsets[j]
            c2 *= c3
            if j == i - 1:
                for k in range(mn, 0, -1):
                    c[k][i] = c1 * (k * c[k - 1][i - 1] - c5 * c[k][i - 1]) / c2
                c[0][i] = -c1 * c5 * c[0][i - 1] / c2
            for k in range(mn, 0, -1):
                c[k][j] = (c4 * c[k][j] - k * c[k - 1][j]) / c3
            c[0][j] = c4 * c[0][j] / c3
        c1 = c2
    return c


_STENCIL_CACHE: Dict[int, List[List[float]]] = {}


def derivatives(fn: Fn, t: float, h: float = 1e-3, lo: float = 0.0, hi: float = 1.0) -> Tuple[float, float, float, float]:
    """(f, f', f'', f''') at t using a 7-point stencil kept inside [lo, hi]."""
    shift = 0
    if t - 3 * h < lo:
        shift = int(math.ceil((lo - (t - 3 * h)) / h - 1e-9))
    elif t + 3 * h > hi:
        shift = -int(math.ceil(((t + 3 * h) - hi) / h - 1e-9))
    shift = max(-3, min(3, shift))
    if shift not in _STENCIL_CACHE:
        _STENCIL_CACHE[shift] = _fornberg([float(k + shift) for k in range(-3, 4)], 3)
    w = _STENCIL_CACHE[shift]
    ys = [fn(t + (k + shift) * h) for k in range(-3, 4)]
    return (
        fn(t),
        sum(a * b for a, b in zip(w[1], ys)) / h,
        sum(a * b for a, b in zip(w[2], ys)) / (h * h),
        sum(a * b for a, b in zip(w[3], ys)) / (h ** 3),
    )


def _clean(v: float, digits: int = 5) -> float:
    """Round away finite-difference noise so reports read cleanly."""
    r = round(v, digits)
    return 0.0 if r == 0 else r


def profile(spec: EasingSpec, samples: int = 21) -> List[Dict[str, float]]:
    """Sample normalized x, v, a, j of an easing (per unit of normalized time)."""
    e = resolve(spec)
    out = []
    for i in range(samples):
        t = i / (samples - 1)
        x, v, a, j = derivatives(e.fn, t)
        out.append({"t": round(t, 6), "x": _clean(x, 6), "v": _clean(v), "a": _clean(a, 4), "j": _clean(j, 3)})
    return out


def characterize(spec: EasingSpec) -> Dict[str, Any]:
    """Summarize how an easing starts, travels and stops."""
    e = resolve(spec)
    n = 401
    xs, vs, as_, js = [], [], [], []
    for i in range(n):
        x, v, a, j = derivatives(e.fn, i / (n - 1))
        xs.append(x)
        vs.append(v)
        as_.append(a)
        js.append(j)

    def at_peak(vals: List[float]) -> Tuple[float, float]:
        k = max(range(n), key=lambda i: abs(vals[i]))
        return vals[k], k / (n - 1)

    pv, pv_t = at_peak(vs)
    pa, pa_t = at_peak(as_)
    pj, _ = at_peak(js)
    tol_v, tol_a = 0.02, 0.1
    over = max(xs) - 1.0
    under = -min(xs)
    vtol = 1e-3 * max(abs(v) for v in vs)
    signs = [1 if v > 0 else -1 for v in vs if abs(v) > vtol]
    sign_changes = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    notes = []
    if abs(vs[0]) < tol_v and abs(vs[-1]) < tol_v:
        notes.append("Starts and ends at rest (eases in and out).")
    elif abs(vs[0]) < tol_v:
        notes.append("Starts at rest but arrives moving (ease-in only): pair with a follow-through or a hit.")
    elif abs(vs[-1]) < tol_v:
        notes.append("Leaves at speed and settles to rest (ease-out only).")
    else:
        notes.append("Moving at both ends: velocity jumps unless neighbouring motion matches it.")
    if abs(vs[0]) < tol_v and abs(as_[0]) > tol_a or abs(vs[-1]) < tol_v and abs(as_[-1]) > tol_a:
        notes.append("Acceleration is non-zero where velocity is zero, so jerk spikes at that end "
                     "(felt as a slight 'kick'). smootherstep removes it.")
    if over > 1e-3:
        notes.append("Overshoots the target by %.1f%%." % (over * 100))
    if under > 1e-3:
        notes.append("Anticipates: dips %.1f%% behind the start first." % (under * 100))
    return {
        "easing": e.spec(),
        "blender_native": e.native,
        "start": {"velocity": _clean(vs[0]), "acceleration": _clean(as_[0])},
        "end": {"velocity": _clean(vs[-1]), "acceleration": _clean(as_[-1])},
        "peak_velocity": {"value": _clean(pv), "t": pv_t},
        "peak_acceleration": {"value": _clean(pa), "t": pa_t},
        "peak_jerk": _clean(pj, 3),
        "overshoot": _clean(max(0.0, over)),
        "anticipation": _clean(max(0.0, under)),
        "direction_changes": sign_changes,
        "notes": notes,
        "units": "normalized: progress per unit duration (multiply v by (B-A)/T, a by (B-A)/T^2, j by (B-A)/T^3)",
    }


def interpolate(a: float, b: float, t: float, spec: EasingSpec = "smootherstep") -> float:
    """x(t) = A + (B - A) f(t)."""
    return a + (b - a) * resolve(spec)(t)
