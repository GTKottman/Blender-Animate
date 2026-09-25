"""MCP server exposing motion-design tools for Blender.

Two kinds of tools:

* **Motion maths** (work without Blender): list and inspect easings, design
  springs. Use them to choose timing before touching the scene.
* **Scene tools** (forwarded to the Blender add-on over a local socket):
  animate properties with easings, springs and paths, re-time existing keys,
  and analyze the resulting position, velocity, acceleration and jerk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import Field

import motionkit
from motionkit import analysis as mk_analysis
from motionkit import easing as mk_easing
from motionkit.spring import resolve_spring

from .connection import BlenderConnection, BlenderError

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

from mcp.types import ToolAnnotations

GUIDE = (Path(__file__).parent / "guide.md").read_text(encoding="utf-8")

INSTRUCTIONS = """\
Tools for animating Blender scenes with deliberate motion. Core idea: x(t) = A + (B-A)*f(t);
choose the timing curve f, not just the endpoints. Default to 'smootherstep' (zero velocity and
acceleration at both ends, minimum jerk). Use springs for physical settle/overshoot, and
motion paths for spatial arcs, with a separate easing for speed along the path.
Every write returns a 'motion' report (eases_in/out, peak velocity, kinks, overshoot,
smoothness vs. minimum-jerk). Read it and fix listed issues. Read resource animate://guide
for the full playbook. Rotations are in degrees by default. Frames are converted with scene fps.
"""

mcp = _Server(name="blender-animate", instructions=INSTRUCTIONS)

_conn: Optional[BlenderConnection] = None


def blender() -> BlenderConnection:
    global _conn
    if _conn is None:
        _conn = BlenderConnection()
    return _conn


def _call(command: str, **params: Any) -> Any:
    """Forward to Blender; errors become ToolErrors so their message reaches the model."""
    try:
        return blender().call(command, params)
    except BlenderError as exc:
        raise ToolError(str(exc)) from exc


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

EasingArg = Annotated[Union[str, Dict[str, Any]], Field(
    description="Timing curve f(t). Name like 'smootherstep', 'sine_in_out', 'cubic_out', 'back_out', "
                "'bounce_out', 'expo_out', 'linear', 'constant', 'auto'; parametric like "
                "'cubic_bezier(0.2,0,0,1)', 'back_out(2.5)', 'spring(0.3)', 'elastic_out(1,0.3)'; "
                "or a dict {\"type\": \"cubic_bezier\", \"x1\":..,\"y1\":..,\"x2\":..,\"y2\":..}. "
                "See list_easings.")]
Value = Union[float, List[float]]
ModeArg = Annotated[Literal["auto", "native", "bake"], Field(
    description="'auto': use Blender's native interpolation when it reproduces the easing exactly "
                "(Penner curves, cubic beziers incl. smoothstep), otherwise bake. "
                "'bake': always sample keys (engine-independent). 'native': fail if not exact.")]
AngleArg = Annotated[Literal["degrees", "radians"], Field(
    description="Units for rotation_euler values (other properties are unaffected).")]


# ---------------------------------------------------------------------------
# Resources and prompts
# ---------------------------------------------------------------------------

@mcp.resource("animate://guide", name="motion-guide", mime_type="text/markdown",
              description="How to design motion: easing, derivatives, springs, paths, workflow.")
def guide_resource() -> str:
    return GUIDE


@mcp.resource("animate://easings", name="easing-catalog", mime_type="application/json",
              description="Every easing name with description and whether Blender plays it natively.")
def easings_resource() -> str:
    import json
    return json.dumps(mk_easing.catalog(), indent=1)


@mcp.prompt(name="animate_move", description="Plan and execute a well-timed move for an object.")
def animate_move(object: str, intent: str) -> str:
    return (
        "Animate '%s' so that: %s\n\n"
        "1. Call get_scene (fps, frame range) and get_object('%s').\n"
        "2. Decide poses and timing in seconds, then convert to frames. Pick an easing per segment "
        "for the intent (see animate://guide; inspect_easing if unsure).\n"
        "3. Write it with animate_keyframes / animate_property, spring_to or follow_path.\n"
        "4. Read the returned motion report; fix every issue (abrupt start/stop, velocity kinks, "
        "unwanted overshoot). Aim for smoothness_vs_min_jerk near 1 on simple moves.\n"
        "5. Add secondary action: springs for settle, shift_keys 2-4 frames for overlap.\n"
        "6. Finish with analyze_motion(space='world') and summarise the motion in words."
        % (object, intent, object)
    )


# ---------------------------------------------------------------------------
# Motion maths (no Blender needed)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=LOCAL)
def list_easings() -> Dict[str, Any]:
    """List every timing curve: name, family, description, and whether Blender can play it without baking.

    Parametric forms: cubic_bezier(x1,y1,x2,y2), back_in/out/in_out(overshoot),
    elastic_in/out/in_out(amplitude, period), spring(damping_ratio, oscillations).
    Aliases accepted: camelCase / Penner names (easeInOutCubic), 'ease-in-out', 'min_jerk'.
    """
    return {"easings": mk_easing.catalog(),
            "special": {"auto": "smooth pass-through of waypoints (auto-clamped tangents)",
                        "constant": "stepped hold"}}


@mcp.tool(annotations=LOCAL)
def inspect_easing(
    easing: EasingArg,
    samples: Annotated[int, Field(ge=3, le=101, description="Rows in the returned x/v/a/j table")] = 11,
) -> Dict[str, Any]:
    """Show how an easing behaves: position, velocity, acceleration and jerk over normalized time.

    Reports end velocities and accelerations (does it ease in/out? does it kick?), the velocity
    peak, overshoot and anticipation, plus a sampled table and text sparklines. Values are
    normalized; for a move of distance D over T seconds, multiply v by D/T, a by D/T^2, j by D/T^3.
    """
    try:
        info = mk_easing.characterize(easing)
        prof = mk_easing.profile(easing, samples)
        fine = mk_easing.profile(easing, 49)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    info["table"] = [{k: round(v, 5) for k, v in row.items()} for row in prof]
    info["sparklines"] = {key: mk_analysis.sparkline([r[key] for r in fine]) for key in ("x", "v", "a", "j")}
    return info


@mcp.tool(annotations=LOCAL)
def compare_easings(
    easings: Annotated[List[Union[str, Dict[str, Any]]], Field(min_length=2, max_length=8,
                                                               description="Easing specs to compare")],
) -> Dict[str, Any]:
    """Compare several easings side by side: end behaviour, peak speed, overshoot and a velocity sparkline."""
    rows = []
    for spec in easings:
        try:
            c = mk_easing.characterize(spec)
        except ValueError as exc:
            rows.append({"easing": spec, "error": str(exc)})
            continue
        prof = mk_easing.profile(spec, 49)
        rows.append({
            "easing": c["easing"],
            "start_v": round(c["start"]["velocity"], 4), "end_v": round(c["end"]["velocity"], 4),
            "start_a": round(c["start"]["acceleration"], 4), "end_a": round(c["end"]["acceleration"], 4),
            "peak_v": round(c["peak_velocity"]["value"], 4),
            "overshoot": round(c["overshoot"], 4),
            "blender_native": c["blender_native"],
            "velocity": mk_analysis.sparkline([r["v"] for r in prof]),
        })
    return {"comparison": rows}


@mcp.tool(annotations=LOCAL)
def design_spring(
    frequency_hz: Annotated[Optional[float], Field(gt=0, description="Undamped oscillation frequency")] = None,
    damping_ratio: Annotated[Optional[float], Field(gt=0, description="<1 bouncy, 1 critical, >1 sluggish")] = None,
    stiffness: Annotated[Optional[float], Field(gt=0, description="k (alternative to frequency_hz)")] = None,
    damping: Annotated[Optional[float], Field(ge=0, description="c (alternative to damping_ratio)")] = None,
    mass: float = 1.0,
    distance: Annotated[float, Field(description="Size of the move, for absolute overshoot")] = 1.0,
    initial_velocity: Annotated[float, Field(description="Units/second at release")] = 0.0,
    fps: float = 24.0,
) -> Dict[str, Any]:
    """Preview a damped spring before baking it: regime, overshoot, settle time (seconds and frames), period.

    Solves m x'' + c x' + k (x - target) = 0 in closed form. Defaults: 2 Hz, damping_ratio 0.5.
    """
    params = {"frequency_hz": frequency_hz, "damping_ratio": damping_ratio, "stiffness": stiffness,
              "damping": damping, "mass": mass}
    sp = resolve_spring({k: v for k, v in params.items() if v is not None})
    info = sp.describe(0.0, distance, initial_velocity)
    info["settle_frames"] = info["settle_time_s"] * fps
    info["overshoot_distance"] = info["overshoot"] * distance
    curve = sp.sample(0.0, distance, info["settle_time_s"], fps, initial_velocity)
    info["position_sparkline"] = mk_analysis.sparkline(curve)
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in info.items()}


# ---------------------------------------------------------------------------
# Scene inspection
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def get_scene(include_objects: bool = True) -> Dict[str, Any]:
    """Scene overview: fps, frame range, current frame, selection, and objects with transforms and animated channels."""
    return _call("get_scene", include_objects=include_objects)


@mcp.tool(annotations=READ)
def get_object(
    name: Annotated[Optional[str], Field(description="Object name; omit for the active object")] = None,
    include_keys: bool = True,
    angle_unit: AngleArg = "degrees",
) -> Dict[str, Any]:
    """Object details: transforms, constraints, and every F-curve key (frame, value, interpolation, handles)."""
    return _call("get_object", name=name, include_keys=include_keys, angle_unit=angle_unit)


@mcp.tool(annotations=WRITE)
def set_scene(
    frame_start: Optional[int] = None,
    frame_end: Optional[int] = None,
    fps: Annotated[Optional[float], Field(gt=0, description="e.g. 24, 25, 30, 29.97, 60")] = None,
    frame_current: Optional[int] = None,
) -> Dict[str, Any]:
    """Set the frame range, frame rate and/or current frame."""
    return _call("set_scene", frame_start=frame_start, frame_end=frame_end, fps=fps, frame_current=frame_current)


# ---------------------------------------------------------------------------
# Animation writers
# ---------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
def animate_property(
    object: Annotated[str, Field(description="Object name")],
    to: Annotated[Value, Field(description="Target value: number (all components) or list per component")],
    start_frame: float,
    end_frame: float,
    data_path: Annotated[str, Field(description="location, rotation_euler, scale, data.energy, data.lens, "
                                                 "pose.bones[\"Bone\"].location, [\"custom\"], ...")] = "location",
    from_value: Annotated[Optional[Value], Field(
        description="Start value; omit to start from the current/animated value at start_frame")] = None,
    easing: EasingArg = "smootherstep",
    index: Annotated[Optional[int], Field(description="Animate one component only (0=X,1=Y,2=Z)")] = None,
    mode: ModeArg = "auto",
    bake_step: Annotated[float, Field(gt=0, description="Frames between baked keys")] = 1.0,
    angle_unit: AngleArg = "degrees",
) -> Dict[str, Any]:
    """Move a property from A to B between two frames with an easing: x(t) = A + (B-A) f(t).

    Returns the keys written and a motion report (eases in/out, peak velocity, kinks,
    overshoot, smoothness vs. minimum jerk, sparklines of x/v/a/j).
    """
    return _call("animate", object=object, data_path=data_path, index=index, mode=mode, bake_step=bake_step,
                 angle_unit=angle_unit,
                 waypoints=[{"frame": start_frame, "value": from_value, "easing": easing},
                            {"frame": end_frame, "value": to}])


WaypointsArg = Annotated[List[Dict[str, Any]], Field(
    min_length=2,
    description="Poses in time: [{\"frame\": 1, \"value\": [0,0,0], \"easing\": \"cubic_out\"}, "
                "{\"frame\": 24, \"value\": [4,0,0], \"easing\": \"back_out\"}, {\"frame\": 40, \"value\": [4,0,2]}]. "
                "'easing' is the timing of the segment that STARTS at that waypoint (default smootherstep; "
                "'auto' for smooth pass-through). value null keeps the current value at that frame.")]


@mcp.tool(annotations=WRITE)
def animate_keyframes(
    object: str,
    waypoints: WaypointsArg,
    data_path: str = "location",
    index: Optional[int] = None,
    mode: ModeArg = "auto",
    bake_step: Annotated[float, Field(gt=0)] = 1.0,
    angle_unit: AngleArg = "degrees",
    replace: Annotated[Literal["range", "all", "none"], Field(
        description="'range' removes existing keys inside the waypoint span; 'all' clears the channel")] = "range",
) -> Dict[str, Any]:
    """Pose-to-pose animation: a property through several waypoints, each segment with its own easing.

    Use one easing per segment to shape the performance, e.g. anticipation ('back_in'),
    a fast action ('expo_out'), then a settle ('spring(0.4)'). Returns keys written and a
    motion report for the whole span.
    """
    return _call("animate", object=object, data_path=data_path, waypoints=waypoints, index=index, mode=mode,
                 bake_step=bake_step, angle_unit=angle_unit, replace=replace)


@mcp.tool(annotations=WRITE)
def spring_to(
    object: str,
    target: Annotated[Value, Field(description="Rest value the spring settles to")],
    data_path: str = "location",
    start_frame: Annotated[Optional[float], Field(description="Release frame; default current frame")] = None,
    start_value: Annotated[Optional[Value], Field(description="Omit to start from the animated value")] = None,
    frequency_hz: Annotated[float, Field(gt=0)] = 2.0,
    damping_ratio: Annotated[float, Field(gt=0, description="0.2 bouncy, 0.5 lively, 1 critical")] = 0.5,
    stiffness: Annotated[Optional[float], Field(gt=0, description="Overrides frequency_hz if set")] = None,
    damping: Annotated[Optional[float], Field(ge=0, description="Overrides damping_ratio if set")] = None,
    mass: float = 1.0,
    inherit_velocity: Annotated[bool, Field(
        description="Start with the velocity the existing animation has at start_frame (follow-through)")] = True,
    initial_velocity: Annotated[Optional[Value], Field(description="Explicit units/second; overrides inherit")] = None,
    index: Optional[int] = None,
    bake_step: Annotated[float, Field(gt=0)] = 1.0,
    angle_unit: AngleArg = "degrees",
) -> Dict[str, Any]:
    """Physically settle a property onto a target with a damped spring (overshoot, bounce, or critical settle).

    Bakes the closed-form solution until it settles within 0.1%, with exact-slope handles.
    Returns the spring's regime, overshoot, settle frame and a motion report.
    """
    spring = {"frequency_hz": frequency_hz, "damping_ratio": damping_ratio, "mass": mass}
    if stiffness is not None:
        spring["stiffness"] = stiffness
    if damping is not None:
        spring["damping"] = damping
    return _call("spring", object=object, data_path=data_path, target=target, start_frame=start_frame,
                 start_value=start_value, spring=spring, inherit_velocity=inherit_velocity,
                 initial_velocity=initial_velocity, index=index, bake_step=bake_step, angle_unit=angle_unit)


@mcp.tool(annotations=WRITE)
def reshape_timing(
    object: str,
    easing: EasingArg,
    data_path: str = "location",
    frame_start: Optional[float] = None,
    frame_end: Optional[float] = None,
    index: Optional[int] = None,
    mode: ModeArg = "auto",
    bake_step: Annotated[float, Field(gt=0)] = 1.0,
) -> Dict[str, Any]:
    """Keep the existing poses (keys) but change the timing between them to a new easing.

    Ideal for polishing: turn linear blocking into smootherstep, or make every move 'cubic_out'.
    Keys previously baked by these tools are recognised and replaced, not treated as poses.
    """
    return _call("reshape", object=object, data_path=data_path, easing=easing, frame_start=frame_start,
                 frame_end=frame_end, index=index, mode=mode, bake_step=bake_step)


@mcp.tool(annotations=WRITE)
def create_motion_path(
    points: Annotated[List[List[float]], Field(min_length=2, description="Anchor points [[x,y,z], ...]")],
    name: str = "MotionPath",
    closed: bool = False,
    tension: Annotated[float, Field(ge=0, le=2, description="1 = Catmull-Rom smooth; 0 = straight segments")] = 1.0,
    handles: Annotated[Optional[List[Dict[str, List[float]]]], Field(
        description="Optional explicit cubic bezier handles per point: [{\"left\": [x,y,z], \"right\": [x,y,z]}]")] = None,
) -> Dict[str, Any]:
    """Create a 3D cubic Bézier curve for an object to travel along (the spatial path).

    By default handles are generated so the curve passes smoothly through every point.
    Returns the curve name and its arc length.
    """
    return _call("create_path", name=name, points=points, closed=closed, tension=tension, handles=handles)


@mcp.tool(annotations=WRITE)
def follow_path(
    object: str,
    path: Annotated[str, Field(description="Curve object name (see create_motion_path)")],
    start_frame: Optional[float] = None,
    end_frame: Optional[float] = None,
    easing: EasingArg = "smootherstep",
    orient: Annotated[bool, Field(description="Rotate the object to face along the path")] = True,
    method: Annotated[Literal["constraint", "bake"], Field(
        description="'constraint': Follow Path constraint with eased offset (editable). "
                    "'bake': location/rotation keys at constant arc-length spacing")] = "constraint",
    forward_axis: Literal["FORWARD_X", "FORWARD_Y", "FORWARD_Z",
                          "TRACK_NEGATIVE_X", "TRACK_NEGATIVE_Y", "TRACK_NEGATIVE_Z"] = "FORWARD_Y",
    up_axis: Literal["UP_X", "UP_Y", "UP_Z"] = "UP_Z",
    bake_step: Annotated[float, Field(gt=0)] = 1.0,
) -> Dict[str, Any]:
    """Send an object along a curve. The easing controls distance travelled over time (arc length).

    'linear' = constant speed; 'smootherstep' = rest to rest; 'cubic_in' = accelerating launch.
    The constraint method resets the object's location (and rotation if orient) so the path
    fully drives it. Returns a world-space motion report (speed profile along the path).
    """
    return _call("follow_path", object=object, path=path, start_frame=start_frame, end_frame=end_frame,
                 easing=easing, orient=orient, method=method, forward_axis=forward_axis, up_axis=up_axis,
                 bake_step=bake_step)


@mcp.tool(annotations=WRITE)
def shift_keys(
    object: str,
    offset: Annotated[float, Field(description="Frames to slide (negative = earlier)")],
    data_path: Annotated[Optional[str], Field(description="Only this channel; omit for all")] = None,
    frame_start: Annotated[Optional[float], Field(description="Only keys at or after this frame")] = None,
) -> Dict[str, Any]:
    """Slide keys in time. Offsetting secondary parts by a few frames creates overlapping action."""
    return _call("shift_keys", object=object, offset=offset, data_path=data_path, frame_start=frame_start)


@mcp.tool(annotations=DESTRUCTIVE)
def clear_animation(
    object: str,
    data_path: Annotated[Optional[str], Field(description="Omit to clear every channel")] = None,
    index: Optional[int] = None,
    frame_start: Optional[float] = None,
    frame_end: Optional[float] = None,
) -> Dict[str, Any]:
    """Delete keyframes from an object (optionally one channel / component / frame range). Undoable in Blender."""
    return _call("clear_animation", object=object, data_path=data_path, index=index,
                 frame_start=frame_start, frame_end=frame_end)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def analyze_motion(
    object: str,
    data_path: Annotated[Optional[str], Field(
        description="Omit (or 'location') for evaluated world position; otherwise the property to sample")] = None,
    frame_start: Optional[float] = None,
    frame_end: Optional[float] = None,
    space: Annotated[Literal["auto", "world", "fcurve"], Field(
        description="'world' includes parents/constraints/paths; 'fcurve' samples the property's curves")] = "auto",
    step: Annotated[Optional[float], Field(gt=0, description="Sampling step in frames")] = None,
    table_every: Annotated[int, Field(ge=0, description="Include every Nth sample as a table row (0 = none)")] = 0,
    index: Optional[int] = None,
    angle_unit: AngleArg = "degrees",
) -> Dict[str, Any]:
    """Measure position, velocity, acceleration and jerk over a frame range and diagnose the motion.

    Reports: eases_in / eases_out, peak velocity/acceleration/jerk with frames, velocity kinks
    (sudden speed/direction changes), overshoot/anticipation, direction reversals,
    dimensionless jerk and smoothness vs. the minimum-jerk ideal (1.0 = ideal), sparklines,
    and a list of human-readable issues with suggested fixes.
    """
    return _call("analyze", object=object, data_path=data_path, frame_start=frame_start, frame_end=frame_end,
                 space=space, step=step, table_every=table_every, index=index, angle_unit=angle_unit)


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

@mcp.tool(annotations=DESTRUCTIVE)
def execute_blender_python(
    code: Annotated[str, Field(description="Python run inside Blender; set `result` to return a value")],
) -> Dict[str, Any]:
    """Run arbitrary Python in Blender (bpy and motionkit are pre-imported). Disabled unless the user
    enables 'Allow execute_python' in the add-on preferences. Prefer the dedicated tools."""
    return _call("execute_python", code=code)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Blender Animate MCP server")
    parser.add_argument("--host", default=os.environ.get("BLENDER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BLENDER_PORT", "9877")))
    parser.add_argument("--version", action="version", version=motionkit.__version__)
    args = parser.parse_args()
    global _conn
    _conn = BlenderConnection(args.host, args.port)
    mcp.run()


if __name__ == "__main__":
    main()
