import pytest

from motionkit import easing as E
from motionkit.planner import Waypoint, plan, plan_spring, sample_keys
from motionkit.spring import Spring


def wp(*args):
    return [Waypoint(*a) for a in args]


@pytest.mark.parametrize("name", ["sine_in_out", "cubic_out", "back_out", "bounce_out", "expo_in", "linear"])
def test_native_single_key(name):
    keys = plan(wp((1, 0.0, name), (25, 10.0)))
    assert len(keys) == 2
    e = E.resolve(name)
    for f in (1, 5, 13, 20, 25):
        assert sample_keys(keys, f) == pytest.approx(10 * e((f - 1) / 24), abs=1e-6)


@pytest.mark.parametrize("name", ["smoothstep", "ease", "ease_in_out", "cubic_bezier(0.2,0,0,1)"])
def test_bezier_native_is_exact(name):
    keys = plan(wp((10, 2.0, name), (40, -4.0)))
    assert len(keys) == 2 and keys[0].interpolation == "BEZIER"
    e = E.resolve(name)
    for i in range(31):
        f = 10 + i
        assert sample_keys(keys, f) == pytest.approx(2 - 6 * e(i / 30), abs=1e-6)


@pytest.mark.parametrize("name", ["smootherstep", "spring(0.3)", "elastic_out"])
def test_bake_hermite_accuracy_between_frames(name):
    keys = plan(wp((0, 0.0, name), (24, 1.0)))
    assert len(keys) == 25
    assert sum(k.baked for k in keys) == 23
    e = E.resolve(name)
    # Sub-frame accuracy (what motion blur sees) thanks to exact-slope handles.
    tol = 1e-4 if name == "smootherstep" else 2e-3
    for i in range(0, 240):
        f = i / 10
        assert sample_keys(keys, f) == pytest.approx(e(f / 24), abs=tol)


def test_native_mode_rejects_non_native():
    with pytest.raises(ValueError, match="no exact Blender equivalent"):
        plan(wp((0, 0, "smootherstep"), (10, 1)), mode="native")


def test_multi_segment_and_auto():
    keys = plan(wp((0, 0.0, "auto"), (10, 5.0, "auto"), (20, 8.0, "auto"), (30, 2.0)))
    assert [k.frame for k in keys] == [0, 10, 20, 30]
    # key at local maximum (20) is flat
    k = keys[2]
    assert k.handle_left[1] == pytest.approx(8.0) and k.handle_right[1] == pytest.approx(8.0)
    # key 10 has continuous slope
    k = keys[1]
    sl = (k.value - k.handle_left[1]) / (k.frame - k.handle_left[0])
    sr = (k.handle_right[1] - k.value) / (k.handle_right[0] - k.frame)
    assert sl == pytest.approx(sr)


def test_bad_waypoints():
    with pytest.raises(ValueError):
        plan(wp((5, 0, "linear"), (5, 1)))
    with pytest.raises(ValueError):
        plan(wp((5, 0, "linear")))


def test_plan_spring_settles_on_target_with_inherited_velocity():
    s = Spring.from_frequency(2.0, 0.4)
    keys = plan_spring(0.0, 3.0, 10, 24, s, initial_velocity=5.0)
    assert keys[0].frame == 10 and keys[-1].value == pytest.approx(3.0)
    # the first handle carries the initial velocity (units per frame)
    k = keys[0]
    slope = (k.handle_right[1] - k.value) / (k.handle_right[0] - k.frame)
    assert slope == pytest.approx(5.0 / 24)
    assert max(k.value for k in keys) > 3.0  # overshoot


def test_bake_bounce_exact_on_frames():
    # Bounce kinks fall inside frame intervals, so only whole frames are exact when baked;
    # mode 'auto' uses Blender's native BOUNCE instead.
    keys = plan(wp((0, 0.0, "bounce_out"), (24, 1.0)), mode="bake")
    e = E.resolve("bounce_out")
    for f in range(25):
        assert sample_keys(keys, f) == pytest.approx(e(f / 24), abs=1e-9)
    assert plan(wp((0, 0.0, "bounce_out"), (24, 1.0)))[0].interpolation == "BOUNCE"
