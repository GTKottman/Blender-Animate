import pytest

from motionkit import easing as E
from motionkit.analysis import analyze, sparkline


def sample(fn, frames=48, a=0.0, b=10.0, step=0.25):
    n = int(frames / step)
    return [a + (b - a) * fn(i * step / frames) for i in range(n + 1)]


def test_smootherstep_is_min_jerk():
    r = analyze(sample(E.EASINGS["smootherstep"]), 0, 0.25, 24)
    assert r["eases_in"] and r["eases_out"]
    assert r["smoothness_vs_min_jerk"] == pytest.approx(1.0, abs=0.03)
    assert r["issues"] == []
    assert r["peak_velocity"]["frame"] == pytest.approx(24)
    # peak speed = 1.875 * D / T
    assert r["peak_velocity"]["value"] == pytest.approx(1.875 * 10 / 2.0, rel=1e-3)


def test_linear_flags_abrupt_start_and_stop():
    r = analyze(sample(E.linear), 0, 0.25, 24)
    assert not r["eases_in"] and not r["eases_out"]
    assert any("Starts abruptly" in i for i in r["issues"])
    assert any("Stops abruptly" in i for i in r["issues"])


def test_kink_detection_on_piecewise_linear():
    # hold, linear move, hold: velocity jumps at frames 10 and 30
    vals = []
    for i in range(0, 161):
        f = i * 0.25
        vals.append(0 if f < 10 else (f - 10) / 20 * 5 if f < 30 else 5)
    r = analyze(vals, 0, 0.25, 24)
    kinks = r["velocity_kinks_at_frames"]
    assert any(abs(k - 10) <= 0.5 for k in kinks) and any(abs(k - 30) <= 0.5 for k in kinks)
    smooth = analyze(sample(E.EASINGS["sine_in_out"]), 0, 0.25, 24)
    assert smooth["velocity_kinks_at_frames"] == []


def test_overshoot_and_vectors():
    r = analyze(sample(E.resolve("back_out")), 0, 0.25, 24)
    assert r["overshoot"] == pytest.approx(0.1, abs=5e-3)
    assert any("Overshoots" in i for i in r["issues"])
    vec = [[x, 2 * x, 0] for x in sample(E.EASINGS["smootherstep"])]
    rv = analyze(vec, 0, 0.25, 24)
    assert rv["eases_in"] and rv["path_length"] == pytest.approx(10 * 5 ** 0.5, rel=1e-6)


def test_no_motion():
    r = analyze([1.0] * 10, 0, 1, 24)
    assert r["moving"] is False


def test_sparkline():
    s = sparkline([0, 1, 2, 3, 4, 5, 6, 7])
    assert s == "▁▂▃▄▅▆▇█"
    assert len(sparkline(list(range(1000)), width=40)) == 40
