"""Integration tests against real Blender (the `bpy` wheel). Skipped when bpy is unavailable."""

import json
import math
import os
import sys
import threading

import pytest

bpy = pytest.importorskip("bpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "addon"))

from blender_animate import bridge, commands as C  # noqa: E402
from motionkit import easing as E  # noqa: E402


@pytest.fixture(autouse=True)
def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = 24
    sc.render.fps_base = 1.0
    sc.frame_start, sc.frame_end = 1, 48
    for name in ("Ball", "Box"):
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        sc.collection.objects.link(obj)
    return sc


def call(_command, **params):
    result = C.dispatch(_command, params)
    json.dumps(result)  # everything returned must be JSON-serializable
    return result


def fcurve(obj_name, path, index):
    return C._find_fcurve(bpy.data.objects[obj_name], path, index)


NATIVE = ["linear", "sine_in", "sine_out", "sine_in_out", "quad_in_out", "cubic_in", "cubic_out",
          "quart_in_out", "quint_out", "expo_in", "expo_out", "expo_in_out", "circ_in", "circ_out",
          "circ_in_out", "back_in", "back_out", "back_in_out", "back_out(2.5)", "bounce_in", "bounce_out",
          "bounce_in_out", "smoothstep", "ease", "ease_in_out", "cubic_bezier(0.2,0,0,1)",
          "cubic_bezier(0.3,-0.4,0.7,1.4)"]


@pytest.mark.parametrize("name", NATIVE)
def test_native_easings_match_blender(name):
    r = call("animate", object="Ball", data_path="location", index=0, mode="native",
             waypoints=[{"frame": 1, "value": 0, "easing": name}, {"frame": 31, "value": 3}])
    assert r["channels"][0]["keys_written"] == 2
    fc = fcurve("Ball", "location", 0)
    e = E.resolve(name)
    for i in range(0, 301):
        f = 1 + i / 10
        assert fc.evaluate(f) == pytest.approx(3 * e((f - 1) / 30), abs=2e-4), f


@pytest.mark.parametrize("name", ["smootherstep", "spring(0.35)", "elastic_out", "sine_in_out"])
def test_baked_easings_match_subframe(name):
    call("animate", object="Ball", data_path="location", index=2, mode="bake",
         waypoints=[{"frame": 1, "value": 1, "easing": name}, {"frame": 25, "value": -1}])
    fc = fcurve("Ball", "location", 2)
    e = E.resolve(name)
    tol = 1e-4 if name in ("smootherstep", "sine_in_out") else 3e-3
    for i in range(0, 241):
        f = 1 + i / 10
        assert fc.evaluate(f) == pytest.approx(1 - 2 * e((f - 1) / 24), abs=tol)
    kinds = {kp.type for kp in fc.keyframe_points}
    assert kinds == {"KEYFRAME", "JITTER"}


def test_vector_waypoints_degrees_and_report():
    r = call("animate", object="Box", data_path="rotation_euler",
             waypoints=[{"frame": 1, "value": [0, 0, 0], "easing": "smootherstep"},
                        {"frame": 25, "value": [0, 0, 90]}])
    assert bpy.data.objects["Box"].rotation_euler is not None
    assert fcurve("Box", "rotation_euler", 2).evaluate(25) == pytest.approx(math.pi / 2)
    m = r["motion"]
    assert m["eases_in"] and m["eases_out"] and m["issues"] == []
    # 90 degrees over 1 s with smootherstep peaks at 1.875 * 90 deg/s
    assert m["peak_velocity"]["value"] == pytest.approx(1.875 * 90, rel=2e-3)


def test_mismatched_segments_flag_a_kink():
    r = call("animate", object="Ball", data_path="location", index=0,
             waypoints=[{"frame": 1, "value": 0, "easing": "smootherstep"},
                        {"frame": 25, "value": 4, "easing": "linear"}, {"frame": 40, "value": 8}])
    assert r["motion"]["velocity_kinks_at_frames"]
    assert any("Velocity jumps" in i for i in r["motion"]["issues"])


def test_null_value_keeps_current_and_replace_range():
    bpy.data.objects["Ball"].location = (2.0, 0, 0)
    call("animate", object="Ball", data_path="location", index=0,
         waypoints=[{"frame": 10, "value": None, "easing": "linear"}, {"frame": 20, "value": 5}])
    fc = fcurve("Ball", "location", 0)
    assert fc.evaluate(10) == pytest.approx(2.0)
    call("animate", object="Ball", data_path="location", index=0,
         waypoints=[{"frame": 10, "value": 0, "easing": "linear"}, {"frame": 20, "value": 1}])
    assert [round(k.co[0]) for k in fc.keyframe_points] == [10, 20]


def test_spring_inherits_velocity():
    call("animate", object="Ball", data_path="location", index=0,
         waypoints=[{"frame": 1, "value": 0, "easing": "quad_in"}, {"frame": 13, "value": 2}])
    r = call("spring", object="Ball", data_path="location", index=0, target=2, start_frame=13,
             spring={"frequency_hz": 2, "damping_ratio": 0.3})
    ch = r["channels"][0]
    # quad_in arrives at 2 * 2 / 0.5 s = 8 units/s
    assert ch["initial_velocity_per_s"] == pytest.approx(8.0, rel=0.02)
    fc = fcurve("Ball", "location", 0)
    assert max(fc.evaluate(13 + i / 4) for i in range(200)) > 2.1  # overshoot from momentum
    assert fc.evaluate(ch["settles_at_frame"]) == pytest.approx(2.0)
    assert r["spring"]["regime"] == "underdamped"


def test_reshape_keeps_poses_and_ignores_baked_keys():
    call("animate", object="Ball", data_path="location", index=1, mode="native",
         waypoints=[{"frame": 1, "value": 0, "easing": "linear"}, {"frame": 13, "value": 3, "easing": "linear"},
                    {"frame": 25, "value": 1}])
    call("reshape", object="Ball", data_path="location", index=1, easing="smootherstep")
    fc = fcurve("Ball", "location", 1)
    assert fc.evaluate(13) == pytest.approx(3.0) and fc.evaluate(25) == pytest.approx(1.0)
    assert fc.evaluate(7) == pytest.approx(1.5)
    r = call("reshape", object="Ball", data_path="location", index=1, easing="sine_in_out")
    assert r["channels"][0]["poses"] == 3
    assert len(fc.keyframe_points) == 3
    assert fc.evaluate(4) == pytest.approx(3 * E.resolve("sine_in_out")(0.25), abs=1e-4)


def test_paths_constraint_and_bake():
    r = call("create_path", name="Arc", points=[[0, 0, 0], [2, 2, 0], [4, 0, 0], [6, 2, 1]])
    assert r["length"] > 6
    r = call("follow_path", object="Ball", path="Arc", start_frame=1, end_frame=49, easing="linear")
    m = r["motion"]
    assert m["moving"] and r["constraint"]
    # linear timing along arc length -> nearly constant speed in the middle of the move
    r2 = call("analyze", object="Ball", frame_start=10, frame_end=40, space="world", table_every=4)
    speeds = [row["velocity"] for row in r2["result"]["table"]]
    assert max(speeds) / min(speeds) < 1.1
    call("clear_animation", object="Ball")
    bpy.data.objects["Ball"].constraints.clear()
    r = call("follow_path", object="Ball", path="Arc", start_frame=1, end_frame=49, easing="smootherstep",
             method="bake")
    assert r["motion"]["eases_in"] and r["motion"]["eases_out"]
    fc = fcurve("Ball", "location", 0)
    assert fc.evaluate(49) == pytest.approx(6.0, abs=1e-3)


def test_analyze_fcurve_components_and_scene_info():
    call("animate", object="Ball", data_path="location",
         waypoints=[{"frame": 1, "value": [0, 0, 0], "easing": "back_out"}, {"frame": 25, "value": [2, 1, 0]}])
    r = call("analyze", object="Ball", data_path="location", space="fcurve", frame_start=1, frame_end=30)
    assert set(r["per_component"]) == {"X", "Y", "Z"}
    assert r["per_component"]["X"]["overshoot"] == pytest.approx(0.1, abs=5e-3)
    s = call("get_scene")
    ball = next(o for o in s["objects"] if o["name"] == "Ball")
    assert "location[0]" in ball["animated"]
    o = call("get_object", name="Ball")
    assert o["fcurves"][0]["keys"][0]["interpolation"] == "BACK"
    moved = call("shift_keys", object="Ball", offset=5)
    assert moved["keys_moved"] == 6
    assert fcurve("Ball", "location", 0).keyframe_points[0].co[0] == pytest.approx(6)


def test_data_paths_on_object_data():
    light = bpy.data.lights.new("Key", "POINT")
    obj = bpy.data.objects.new("Lamp", light)
    bpy.context.scene.collection.objects.link(obj)
    call("animate", object="Lamp", data_path="data.energy",
         waypoints=[{"frame": 1, "value": 0, "easing": "expo_in"}, {"frame": 11, "value": 1000}])
    fc = C._find_fcurve(light, "energy", 0)
    assert fc.evaluate(11) == pytest.approx(1000)


def test_errors_are_helpful():
    with pytest.raises(C.CommandError, match="Did you mean: Ball"):
        call("get_object", name="Bal")
    with pytest.raises(C.CommandError, match="does not exist"):
        call("animate", object="Ball", data_path="locaton",
             waypoints=[{"frame": 1, "value": 0}, {"frame": 2, "value": 1}])
    with pytest.raises(C.CommandError, match="Unknown easing"):
        call("animate", object="Ball", data_path="location",
             waypoints=[{"frame": 1, "value": 0, "easing": "wobbly"}, {"frame": 2, "value": 1}])
    with pytest.raises(C.CommandError, match="Unknown command"):
        C.dispatch("nope", {})


def test_bridge_round_trip_with_mcp_client():
    """Real socket round trip: MCP-side client -> bridge thread -> main-thread pump."""
    from blender_animate_mcp.connection import BlenderConnection, BlenderError

    srv = bridge.BridgeServer("127.0.0.1", 0)
    srv._sock = None
    import socket as _s
    probe = _s.socket()
    probe.bind(("127.0.0.1", 0))
    srv.port = probe.getsockname()[1]
    probe.close()
    srv.start()
    try:
        out = {}

        def client():
            conn = BlenderConnection("127.0.0.1", srv.port, timeout=10)
            out["ping"] = conn.call("ping")
            try:
                conn.call("get_object", {"name": "Nope"})
            except BlenderError as exc:
                out["err"] = str(exc)
            out["anim"] = conn.call("animate", {"object": "Ball", "waypoints": [
                {"frame": 1, "value": 0, "easing": "smootherstep"}, {"frame": 13, "value": 1}], "index": 0})
            conn.close()

        t = threading.Thread(target=client)
        t.start()
        while t.is_alive():
            srv._pump()  # stands in for Blender's timer on the main thread
            t.join(0.01)
        assert out["ping"]["blender"]
        assert "not found" in out["err"]
        assert out["anim"]["motion"]["eases_in"]
    finally:
        srv.stop()
