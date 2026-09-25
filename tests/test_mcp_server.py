"""MCP tool layer: schemas, local maths tools, and parameter forwarding to a fake Blender bridge."""

import asyncio
import json
import socket
import threading

import pytest

pytest.importorskip("mcp")

from blender_animate_mcp import server  # noqa: E402
from blender_animate_mcp.connection import BlenderConnection  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def result_json(res):
    """Normalize call_tool output across mcp 1.x / 2.x to the tool's dict."""
    if isinstance(res, tuple):  # mcp 1.x: (content, structured)
        res = res[0]
    content = getattr(res, "content", res)
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("no text content in %r" % (res,))


class FakeBlender:
    """Records commands and echoes them back like the add-on bridge would."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.calls = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        buf = b""
        while True:
            data = conn.recv(65536)
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                self.calls.append(req)
                if req["command"] == "boom":
                    resp = {"id": req["id"], "ok": False, "error": "Object 'X' not found."}
                else:
                    resp = {"id": req["id"], "ok": True, "result": {"echo": req["params"]}}
                conn.sendall((json.dumps(resp) + "\n").encode())


@pytest.fixture
def fake():
    fb = FakeBlender()
    server._conn = BlenderConnection("127.0.0.1", fb.port, timeout=5)
    yield fb
    server._conn.close()
    server._conn = None


def test_tool_catalog_and_schemas():
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    expected = {"list_easings", "inspect_easing", "compare_easings", "design_spring", "get_scene", "get_object",
                "set_scene", "animate_property", "animate_keyframes", "spring_to", "reshape_timing",
                "create_motion_path", "follow_path", "shift_keys", "clear_animation", "analyze_motion",
                "execute_blender_python"}
    assert expected == set(tools)
    for t in tools.values():
        assert t.description and len(t.description) > 40, t.name
    schema = getattr(tools["animate_property"], "inputSchema", None) or tools["animate_property"].input_schema
    assert "easing" in schema["properties"]
    assert set(schema["required"]) == {"object", "to", "start_frame", "end_frame"}


def test_local_tools():
    info = result_json(run(server.mcp.call_tool("inspect_easing", {"easing": "smootherstep", "samples": 5})))
    assert info["start"]["acceleration"] == 0 and info["end"]["velocity"] == 0
    assert len(info["table"]) == 5
    cmp_ = result_json(run(server.mcp.call_tool("compare_easings", {"easings": ["linear", "smoothstep", "bogus"]})))
    assert cmp_["comparison"][0]["start_v"] == 1.0
    assert "error" in cmp_["comparison"][2]
    sp = result_json(run(server.mcp.call_tool("design_spring", {"frequency_hz": 2, "damping_ratio": 0.2})))
    assert sp["regime"] == "underdamped" and sp["overshoot"] > 0.5
    cat = result_json(run(server.mcp.call_tool("list_easings", {})))
    assert any(e["name"] == "smootherstep" for e in cat["easings"])


def test_forwarding(fake):
    r = result_json(run(server.mcp.call_tool("animate_property", {
        "object": "Ball", "to": [1, 2, 3], "start_frame": 1, "end_frame": 24, "easing": "back_out(2)"})))
    params = r["echo"]
    assert fake.calls[-1]["command"] == "animate"
    assert params["waypoints"] == [{"frame": 1, "easing": "back_out(2)", "value": None},
                                   {"frame": 24, "value": [1, 2, 3]}]
    assert "index" not in params  # None values are dropped
    r = result_json(run(server.mcp.call_tool("spring_to", {"object": "Ball", "target": 2, "damping_ratio": 0.3})))
    assert r["echo"]["spring"] == {"frequency_hz": 2.0, "damping_ratio": 0.3, "mass": 1.0}
    assert fake.calls[-1]["command"] == "spring"


def test_blender_errors_surface(fake):
    with pytest.raises(Exception, match="not found"):
        server._call("boom")


def test_unreachable_blender_message():
    conn = BlenderConnection("127.0.0.1", 1, timeout=1)
    with pytest.raises(Exception, match="Start MCP Bridge"):
        conn.call("ping")


def test_guide_resource():
    assert "smootherstep" in server.GUIDE and "jerk" in server.GUIDE
