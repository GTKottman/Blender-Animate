import pytest

from motionkit import easing as E


@pytest.mark.parametrize("name", sorted(E.EASINGS))
def test_endpoints(name):
    e = E.EASINGS[name]
    assert e(0.0) == pytest.approx(0.0, abs=1e-9)
    assert e(1.0) == pytest.approx(1.0, abs=1e-9)
    if name != "constant":
        assert abs(e.fn(1.0) - 1.0) < 2e-3, "raw formula should also land on 1"


def test_smoothstep_formula_and_derivatives():
    for t in (0.1, 0.35, 0.5, 0.8):
        x, v, a, j = E.derivatives(E.smoothstep, t)
        assert x == pytest.approx(3 * t * t - 2 * t ** 3)
        assert v == pytest.approx(6 * t - 6 * t * t, abs=1e-6)
        assert a == pytest.approx(6 - 12 * t, abs=1e-4)
        assert j == pytest.approx(-12, abs=1e-2)


def test_smootherstep_zero_velocity_and_acceleration_at_ends():
    for t in (0.0, 1.0):
        _, v, a, _ = E.derivatives(E.smootherstep, t)
        assert abs(v) < 1e-6
        assert abs(a) < 1e-3
    # jerk = 60 - 360t + 360t^2
    _, _, _, j = E.derivatives(E.smootherstep, 0.3)
    assert j == pytest.approx(60 - 360 * 0.3 + 360 * 0.09, abs=1e-2)


def test_smoothstep_is_exactly_the_one_third_bezier():
    e = E.resolve("cubic_bezier(0.3333333333333333, 0, 0.6666666666666666, 1)")
    for i in range(21):
        t = i / 20
        assert e(t) == pytest.approx(E.smoothstep(t), abs=1e-9)
    assert E.EASINGS["smoothstep"].bezier is not None


def test_characterize_notes():
    c = E.characterize("smoothstep")
    assert any("jerk spikes" in n for n in c["notes"])
    c = E.characterize("smootherstep")
    assert c["notes"] == ["Starts and ends at rest (eases in and out)."]
    assert c["peak_velocity"]["value"] == pytest.approx(1.875, abs=1e-4)
    c = E.characterize("back_out")
    assert c["overshoot"] == pytest.approx(0.1, abs=5e-3)
    c = E.characterize("linear")
    assert c["start"]["velocity"] == pytest.approx(1.0)


@pytest.mark.parametrize("spec,expected", [
    ("easeInOutCubic", "cubic_in_out"),
    ("ease-in-out", "ease_in_out"),
    ("bounceOut", "bounce_out"),
    ("Sine In Out", "sine_in_out"),
    ("min_jerk", "smootherstep"),
    ("back_out(2.5)", "back_out(2.5)"),
    ("cubic-bezier(0.4, 0, 0.2, 1)", "cubic_bezier(0.4, 0, 0.2, 1)"),
    ({"type": "spring", "damping_ratio": 0.3}, "spring(0.3, 0)"),
])
def test_resolve_forms(spec, expected):
    assert E.resolve(spec).spec() == expected


def test_resolve_errors_suggest():
    with pytest.raises(ValueError, match="smoothstep"):
        E.resolve("smoothstp")
    with pytest.raises(ValueError):
        E.resolve("cubic_bezier(1.5, 0, 0.5, 1)")


def test_spring_easing_overshoots_and_settles():
    e = E.resolve("spring(0.3)")
    xs = [e(i / 200) for i in range(201)]
    assert max(xs) > 1.2
    assert xs[-1] == pytest.approx(1.0)
    assert abs(xs[-5] - 1) < 0.01
    crit = E.resolve("spring(1)")
    assert max(crit(i / 200) for i in range(201)) <= 1.0 + 1e-6


def test_back_matches_penner_constant():
    # back_out overshoot peak for s=1.70158 is ~10%
    e = E.resolve("back_out")
    assert max(e(i / 1000) for i in range(1001)) == pytest.approx(1.1, abs=2e-3)


def test_interpolate():
    assert E.interpolate(2.0, 6.0, 0.5, "smootherstep") == pytest.approx(4.0)
    assert E.interpolate(2.0, 6.0, 0.25, "linear") == pytest.approx(3.0)
