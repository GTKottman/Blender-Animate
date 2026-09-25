"""Command handlers executed on Blender's main thread.

Each handler takes a params dict and returns something JSON-serializable.
Handlers that change the scene push an undo step, so every AI edit can be
reverted with Ctrl+Z.
"""

import math

import bpy

from ._mk import motionkit

mk_easing = motionkit.easing
mk_planner = motionkit.planner
mk_analysis = motionkit.analysis
mk_bezier = motionkit.bezier
mk_spring = motionkit.spring


class CommandError(Exception):
    """An error the caller can fix; its message is returned to the AI verbatim."""


_COMMANDS = {}


def command(name, mutates=False):
    def wrap(fn):
        _COMMANDS[name] = (fn, mutates)
        return fn
    return wrap


def dispatch(name, params):
    if name not in _COMMANDS:
        raise CommandError("Unknown command %r. Available: %s" % (name, ", ".join(sorted(_COMMANDS))))
    fn, mutates = _COMMANDS[name]
    result = fn(**params) if params else fn()
    if mutates:
        try:
            bpy.ops.ed.undo_push(message="MCP: %s" % name)
        except Exception:  # noqa: BLE001 - undo is best effort outside UI contexts
            pass
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ANGLE_PATHS = ("rotation_euler", "delta_rotation_euler")


def _scene():
    return bpy.context.scene


def _fps(scene=None):
    scene = scene or _scene()
    return scene.render.fps / scene.render.fps_base


def _jsonable(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    try:
        return [_jsonable(x) for x in v]
    except TypeError:
        return str(v)


def _round(v, nd=5):
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, (list, tuple)):
        return [_round(x, nd) for x in v]
    if isinstance(v, dict):
        return {k: _round(x, nd) for k, x in v.items()}
    return v


def _get_object(name):
    if not name:
        obj = bpy.context.view_layer.objects.active
        if obj is None:
            raise CommandError("No object name given and no active object.")
        return obj
    obj = bpy.data.objects.get(name)
    if obj is None:
        names = [o.name for o in bpy.data.objects]
        import difflib
        close = difflib.get_close_matches(name, names, n=5, cutoff=0.4)
        raise CommandError("Object %r not found.%s" % (
            name, (" Did you mean: %s?" % ", ".join(close)) if close else " Objects: %s" % ", ".join(names[:30])))
    return obj


def _resolve_target(obj, data_path):
    """Map a user data path to (ID that owns the F-curve, path relative to it)."""
    if data_path.startswith("data."):
        if obj.data is None:
            raise CommandError("Object %r has no data block for %r" % (obj.name, data_path))
        return obj.data, data_path[len("data."):]
    return obj, data_path


def _prop_value(id_block, data_path):
    try:
        return id_block.path_resolve(data_path)
    except ValueError:
        raise CommandError("Property path %r does not exist on %r. Examples: location, rotation_euler, scale, "
                           "data.energy, data.lens, constraints[\"Follow Path\"].offset_factor, "
                           "pose.bones[\"Bone\"].location, [\"my_custom_prop\"]" % (data_path, id_block.name))


def _prop_length(value):
    if isinstance(value, (int, float, bool)):
        return 0
    try:
        return len(value)
    except TypeError:
        raise CommandError("Property is not animatable as numbers: %r" % (value,))


def _is_angle(data_path):
    return data_path.split(".")[-1] in ANGLE_PATHS


def _to_internal(value, data_path, angle_unit):
    if value is None:
        return None
    if _is_angle(data_path) and angle_unit == "degrees":
        return math.radians(value)
    return float(value)


def _to_display(value, data_path, angle_unit):
    if _is_angle(data_path) and angle_unit == "degrees":
        return math.degrees(value)
    return value


def _action_fcurves(id_block):
    ad = id_block.animation_data
    if ad is None or ad.action is None:
        return []
    action = ad.action
    slot = getattr(ad, "action_slot", None)
    if slot is not None:
        try:
            from bpy_extras import anim_utils
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            return list(bag.fcurves) if bag is not None else []
        except (ImportError, AttributeError):
            pass
    return list(getattr(action, "fcurves", []))


def _find_fcurve(id_block, data_path, index):
    for fc in _action_fcurves(id_block):
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    return None


def _ensure_fcurve(id_block, data_path, index, is_array, frame):
    fc = _find_fcurve(id_block, data_path, index)
    if fc is not None:
        return fc
    ok = id_block.keyframe_insert(data_path=data_path, index=index if is_array else -1, frame=frame)
    fc = _find_fcurve(id_block, data_path, index)
    if not ok or fc is None:
        raise CommandError("Could not create an F-curve for %r[%d] (is the property animatable?)" % (data_path, index))
    return fc


def _value_at(id_block, data_path, index, is_array, frame):
    fc = _find_fcurve(id_block, data_path, index)
    if fc is not None and len(fc.keyframe_points):
        return fc.evaluate(frame)
    v = _prop_value(id_block, data_path)
    return float(v[index] if is_array else v)


def _write_keys(fc, keys, replace):
    """Write planned keys; replace: 'range' clears keys inside the planned span."""
    if keys and replace in ("range", "all"):
        lo, hi = keys[0].frame - 1e-4, keys[-1].frame + 1e-4
        pts = fc.keyframe_points
        for i in range(len(pts) - 1, -1, -1):
            f = pts[i].co[0]
            if replace == "all" or lo <= f <= hi:
                pts.remove(pts[i], fast=True)
    for k in keys:
        kp = fc.keyframe_points.insert(k.frame, k.value, options={"FAST"})
        kp.interpolation = k.interpolation
        kp.easing = k.easing
        # Baked in-betweens are tagged so reshape() can tell them from poses.
        kp.type = "JITTER" if k.baked else "KEYFRAME"
        if k.back is not None:
            kp.back = k.back
        if k.handle_left is not None and k.handle_right is not None:
            kp.handle_left_type = "FREE"
            kp.handle_right_type = "FREE"
            kp.handle_left = k.handle_left
            kp.handle_right = k.handle_right
    fc.update()


def _channel_indices(value, index):
    n = _prop_length(value)
    if n == 0:
        return [0], False
    if index is None:
        return list(range(n)), True
    if not 0 <= index < n:
        raise CommandError("index %d out of range for a %d-component property" % (index, n))
    return [index], True


def _component(v, i, n_channels, what):
    """Pick component i from a scalar-or-list value."""
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, tuple)):
        if len(v) == n_channels:
            return v[i]
        raise CommandError("%s has %d components but %d channel(s) are being animated" % (what, len(v), n_channels))
    raise CommandError("%s must be a number or list of numbers" % what)


def _keys_summary(keys):
    baked = sum(1 for k in keys if k.baked)
    return {"keys_written": len(keys), "baked_keys": baked,
            "interpolations": sorted({k.interpolation for k in keys if not k.baked})}


def _sample_fcurves(id_block, data_path, indices, is_array, f0, f1, step):
    n = max(3, int(round((f1 - f0) / step)))
    frames = [f0 + (f1 - f0) * i / n for i in range(n + 1)]
    cols = []
    for idx in indices:
        fc = _find_fcurve(id_block, data_path, idx)
        if fc is None:
            v = _value_at(id_block, data_path, idx, is_array, f0)
            cols.append([v] * len(frames))
        else:
            cols.append([fc.evaluate(f) for f in frames])
    return frames, cols


def _quick_analysis(id_block, data_path, indices, is_array, f0, f1, angle_unit):
    """Short motion report attached to every write so the AI sees the result."""
    fps = _fps()
    step = 0.25 if (f1 - f0) <= 120 else (f1 - f0) / 480.0
    # A few frames of margin show how the move leaves and meets the surrounding
    # animation (or the hold before/after), where velocity kinks and kicks live.
    lo, hi = f0 - 4.0, f1 + 4.0
    frames, cols = _sample_fcurves(id_block, data_path, indices, is_array, lo, hi, step)
    if _is_angle(data_path) and angle_unit == "degrees":
        cols = [[math.degrees(v) for v in col] for col in cols]
    values = [list(r) for r in zip(*cols)] if len(cols) > 1 else cols[0]
    dt_step = (hi - lo) / (len(frames) - 1)
    r = mk_analysis.analyze(values, lo, dt_step, fps, label=data_path)
    keep = ("summary", "issues", "peak_velocity", "overshoot", "eases_in", "eases_out",
            "velocity_kinks_at_frames", "acceleration_jumps_at_frames", "stops_at_frames",
            "smoothness_vs_min_jerk", "sparklines")
    return {k: _round(_jsonable(r[k])) for k in keep if k in r}


# ---------------------------------------------------------------------------
# Scene inspection
# ---------------------------------------------------------------------------

@command("ping")
def ping():
    return {"blender": bpy.app.version_string, "file": bpy.data.filepath or "<unsaved>",
            "motionkit": getattr(motionkit, "__version__", "?")}


def _object_brief(obj):
    info = {
        "name": obj.name,
        "type": obj.type,
        "location": _round(list(obj.location), 4),
        "rotation_deg": _round([math.degrees(a) for a in obj.rotation_euler], 3),
        "rotation_mode": obj.rotation_mode,
        "scale": _round(list(obj.scale), 4),
        "parent": obj.parent.name if obj.parent else None,
    }
    channels = sorted({"%s[%d]" % (fc.data_path, fc.array_index) for fc in _action_fcurves(obj)})
    if obj.data is not None and hasattr(obj.data, "animation_data"):
        channels += sorted({"data.%s[%d]" % (fc.data_path, fc.array_index) for fc in _action_fcurves(obj.data)})
    if channels:
        info["animated"] = channels
    if obj.constraints:
        info["constraints"] = [c.name for c in obj.constraints]
    return info


@command("get_scene")
def get_scene(include_objects=True, limit=200):
    scene = _scene()
    out = {
        "scene": scene.name,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "fps": _fps(scene),
        "unit_system": scene.unit_settings.system,
        "active_object": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        "selected": [o.name for o in bpy.context.selected_objects] if hasattr(bpy.context, "selected_objects") else [],
        "object_count": len(scene.objects),
    }
    if include_objects:
        out["objects"] = [_object_brief(o) for o in list(scene.objects)[:limit]]
    return out


def _fcurve_detail(fc, owner_prefix="", angle_unit="degrees", max_keys=200):
    path = owner_prefix + fc.data_path
    conv = (lambda v: math.degrees(v)) if (_is_angle(fc.data_path) and angle_unit == "degrees") else (lambda v: v)
    keys = []
    for kp in list(fc.keyframe_points)[:max_keys]:
        k = {"frame": _round(kp.co[0], 3), "value": _round(conv(kp.co[1])), "interpolation": kp.interpolation}
        if kp.interpolation not in ("BEZIER", "LINEAR", "CONSTANT"):
            k["easing"] = kp.easing
        if kp.interpolation == "BEZIER":
            k["handles"] = {"left": [_round(kp.handle_left[0], 3), _round(conv(kp.handle_left[1]))],
                            "right": [_round(kp.handle_right[0], 3), _round(conv(kp.handle_right[1]))],
                            "types": [kp.handle_left_type, kp.handle_right_type]}
        keys.append(k)
    d = {"data_path": path, "index": fc.array_index, "key_count": len(fc.keyframe_points), "keys": keys,
         "extrapolation": fc.extrapolation}
    if fc.modifiers:
        d["modifiers"] = [m.type for m in fc.modifiers]
    return d


@command("get_object")
def get_object(name=None, include_keys=True, angle_unit="degrees"):
    obj = _get_object(name)
    info = _object_brief(obj)
    info["dimensions"] = _round(list(obj.dimensions), 4)
    info["world_location"] = _round(list(obj.matrix_world.translation), 4)
    if obj.constraints:
        info["constraints"] = [{"name": c.name, "type": c.type, "target": getattr(getattr(c, "target", None), "name", None)}
                               for c in obj.constraints]
    if include_keys:
        curves = [_fcurve_detail(fc, "", angle_unit) for fc in _action_fcurves(obj)]
        if obj.data is not None and hasattr(obj.data, "animation_data"):
            curves += [_fcurve_detail(fc, "data.", angle_unit) for fc in _action_fcurves(obj.data)]
        info["fcurves"] = curves
    if obj.type == "CURVE":
        info["path_length"] = _round(_curve_to_path(obj).length, 4)
    return info


@command("set_scene", mutates=True)
def set_scene(frame_start=None, frame_end=None, fps=None, frame_current=None):
    scene = _scene()
    if frame_start is not None:
        scene.frame_start = int(frame_start)
    if frame_end is not None:
        scene.frame_end = int(frame_end)
    if fps is not None:
        fps = float(fps)
        whole = int(math.ceil(fps - 1e-6))
        scene.render.fps = whole
        scene.render.fps_base = whole / fps  # e.g. 29.97 -> 30 / 1.001
    if frame_current is not None:
        scene.frame_set(int(frame_current))
    return {"frame_start": scene.frame_start, "frame_end": scene.frame_end, "fps": _fps(scene),
            "frame_current": scene.frame_current}


# ---------------------------------------------------------------------------
# Animation writers
# ---------------------------------------------------------------------------

@command("animate", mutates=True)
def animate(object=None, data_path="location", waypoints=None, index=None, mode="auto", bake_step=1.0,
            angle_unit="degrees", replace="range", analyze=True):
    """Animate a property through waypoints [{frame, value, easing}].

    value may be a number (applied to every animated component), a list with
    one entry per component, or null to keep the current value at that frame.
    easing is the timing of the segment that starts at that waypoint.
    """
    obj = _get_object(object)
    if not waypoints or len(waypoints) < 2:
        raise CommandError("Provide at least two waypoints: [{frame, value, easing}, ...]")
    id_block, path = _resolve_target(obj, data_path)
    current = _prop_value(id_block, path)
    indices, is_array = _channel_indices(current, index)
    report = {"object": obj.name, "data_path": data_path, "channels": []}
    frames = [float(w["frame"]) for w in waypoints]
    for n, idx in enumerate(indices):
        wps = []
        for w in waypoints:
            v = _component(w.get("value"), n, len(indices), "waypoint value at frame %s" % w.get("frame"))
            f = float(w["frame"])
            v = _value_at(id_block, path, idx, is_array, f) if v is None else _to_internal(v, data_path, angle_unit)
            wps.append(mk_planner.Waypoint(f, v, w.get("easing", "smootherstep")))
        try:
            keys = mk_planner.plan(wps, mode=mode, bake_step=float(bake_step))
        except ValueError as exc:
            raise CommandError(str(exc))
        fc = _ensure_fcurve(id_block, path, idx, is_array, frames[0])
        _write_keys(fc, keys, replace)
        ch = {"index": idx if is_array else None}
        ch.update(_keys_summary(keys))
        report["channels"].append(ch)
    if analyze:
        report["motion"] = _quick_analysis(id_block, path, indices, is_array, min(frames), max(frames), angle_unit)
    return report


@command("spring", mutates=True)
def spring(object=None, data_path="location", target=None, start_frame=None, start_value=None, index=None,
           spring=None, inherit_velocity=True, initial_velocity=None, bake_step=1.0, tolerance=0.001,
           angle_unit="degrees", replace="range", analyze=True):
    """Bake a damped spring from the value at start_frame to target.

    With inherit_velocity the spring starts with the velocity the existing
    animation has at start_frame, so it reads as follow-through.
    """
    obj = _get_object(object)
    if target is None:
        raise CommandError("target is required")
    scene = _scene()
    fps = _fps(scene)
    start_frame = float(scene.frame_current if start_frame is None else start_frame)
    id_block, path = _resolve_target(obj, data_path)
    current = _prop_value(id_block, path)
    indices, is_array = _channel_indices(current, index)
    sp = mk_spring.resolve_spring(spring or {})
    report = {"object": obj.name, "data_path": data_path, "spring": _round(sp.describe()), "channels": []}
    end_frame = start_frame
    for n, idx in enumerate(indices):
        tgt = _to_internal(_component(target, n, len(indices), "target"), data_path, angle_unit)
        sv = _component(start_value, n, len(indices), "start_value")
        start = _value_at(id_block, path, idx, is_array, start_frame) if sv is None else _to_internal(sv, data_path, angle_unit)
        v0 = 0.0
        iv = _component(initial_velocity, n, len(indices), "initial_velocity")
        if iv is not None:
            v0 = _to_internal(iv, data_path, angle_unit)
        elif inherit_velocity:
            fc_prev = _find_fcurve(id_block, path, idx)
            if fc_prev is not None and len(fc_prev.keyframe_points):
                h = 0.05
                v0 = (fc_prev.evaluate(start_frame) - fc_prev.evaluate(start_frame - h)) / h * fps
        keys = mk_planner.plan_spring(start, tgt, start_frame, fps, sp, v0, float(bake_step), float(tolerance))
        fc = _ensure_fcurve(id_block, path, idx, is_array, start_frame)
        _write_keys(fc, keys, replace)
        end_frame = max(end_frame, keys[-1].frame)
        ch = {"index": idx if is_array else None, "start": _round(_to_display(start, data_path, angle_unit)),
              "initial_velocity_per_s": _round(_to_display(v0, data_path, angle_unit)),
              "settles_at_frame": _round(keys[-1].frame, 2)}
        ch.update(_keys_summary(keys))
        report["channels"].append(ch)
    if analyze:
        report["motion"] = _quick_analysis(id_block, path, indices, is_array, start_frame, end_frame, angle_unit)
    return report


@command("reshape", mutates=True)
def reshape(object=None, data_path="location", index=None, easing="smootherstep", frame_start=None,
            frame_end=None, mode="auto", bake_step=1.0, angle_unit="degrees", analyze=True):
    """Re-time the segments between existing keys with a new easing, keeping key poses."""
    obj = _get_object(object)
    id_block, path = _resolve_target(obj, data_path)
    current = _prop_value(id_block, path)
    indices, is_array = _channel_indices(current, index)
    lo = -1e9 if frame_start is None else float(frame_start)
    hi = 1e9 if frame_end is None else float(frame_end)
    report = {"object": obj.name, "data_path": data_path, "channels": []}
    span = [None, None]
    for idx in indices:
        fc = _find_fcurve(id_block, path, idx)
        if fc is None:
            continue
        poses = [(kp.co[0], kp.co[1]) for kp in fc.keyframe_points
                 if lo - 1e-4 <= kp.co[0] <= hi + 1e-4 and not _is_baked_key(fc, kp)]
        if len(poses) < 2:
            continue
        wps = [mk_planner.Waypoint(f, v, easing) for f, v in poses]
        try:
            keys = mk_planner.plan(wps, mode=mode, bake_step=float(bake_step))
        except ValueError as exc:
            raise CommandError(str(exc))
        _write_keys(fc, keys, "range")
        span[0] = poses[0][0] if span[0] is None else min(span[0], poses[0][0])
        span[1] = poses[-1][0] if span[1] is None else max(span[1], poses[-1][0])
        ch = {"index": idx if is_array else None, "poses": len(poses)}
        ch.update(_keys_summary(keys))
        report["channels"].append(ch)
    if not report["channels"]:
        raise CommandError("No channel of %r has two or more keys in that range to reshape." % data_path)
    if analyze:
        report["motion"] = _quick_analysis(id_block, path, indices, is_array, span[0], span[1], angle_unit)
    return report


def _is_baked_key(fc, kp):
    """Heuristic: keys this add-on baked are tagged as JITTER keyframe type."""
    return kp.type == "JITTER"


@command("clear_animation", mutates=True)
def clear_animation(object=None, data_path=None, index=None, frame_start=None, frame_end=None):
    obj = _get_object(object)
    removed = 0
    targets = [(obj, "")]
    if obj.data is not None and hasattr(obj.data, "animation_data"):
        targets.append((obj.data, "data."))
    for id_block, prefix in targets:
        for fc in _action_fcurves(id_block):
            full = prefix + fc.data_path
            if data_path and full != data_path:
                continue
            if index is not None and fc.array_index != index:
                continue
            pts = fc.keyframe_points
            for i in range(len(pts) - 1, -1, -1):
                f = pts[i].co[0]
                if (frame_start is None or f >= frame_start) and (frame_end is None or f <= frame_end):
                    pts.remove(pts[i], fast=True)
                    removed += 1
            fc.update()
    return {"object": obj.name, "keys_removed": removed}


@command("shift_keys", mutates=True)
def shift_keys(object=None, offset=0.0, data_path=None, frame_start=None):
    """Slide keys in time (offsetting children/secondary parts creates overlap)."""
    obj = _get_object(object)
    moved = 0
    targets = [(obj, "")]
    if obj.data is not None and hasattr(obj.data, "animation_data"):
        targets.append((obj.data, "data."))
    for id_block, prefix in targets:
        for fc in _action_fcurves(id_block):
            if data_path and prefix + fc.data_path != data_path:
                continue
            for kp in fc.keyframe_points:
                if frame_start is None or kp.co[0] >= frame_start:
                    kp.co[0] += offset
                    kp.handle_left[0] += offset
                    kp.handle_right[0] += offset
                    moved += 1
            fc.update()
    return {"object": obj.name, "keys_moved": moved, "offset": offset}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _curve_to_path(curve_obj):
    spline = next((s for s in curve_obj.data.splines if s.type == "BEZIER"), None)
    mw = curve_obj.matrix_world
    if spline is not None:
        pts = spline.bezier_points
        anchors = [tuple(mw @ p.co) for p in pts]
        left = [tuple(mw @ p.handle_left) for p in pts]
        right = [tuple(mw @ p.handle_right) for p in pts]
        return mk_bezier.BezierPath(anchors, left, right, closed=spline.use_cyclic_u)
    spline = curve_obj.data.splines[0]
    pts = [tuple(mw @ p.co.xyz) for p in spline.points]
    return mk_bezier.BezierPath.through_points(pts, closed=spline.use_cyclic_u)


@command("create_path", mutates=True)
def create_path(name="MotionPath", points=None, closed=False, tension=1.0, handles=None):
    """Create a Bezier curve object for motion paths.

    points: [[x,y,z], ...] anchors. By default handles are generated for a
    smooth Catmull-Rom path; pass handles=[{"left": [...], "right": [...]}]
    to control each anchor explicitly.
    """
    if not points or len(points) < 2:
        raise CommandError("points needs at least two [x, y, z] entries")
    if handles:
        if len(handles) != len(points):
            raise CommandError("handles must have one {left, right} entry per point")
        path = mk_bezier.BezierPath(points, [h["left"] for h in handles], [h["right"] for h in handles], closed)
    else:
        path = mk_bezier.BezierPath.through_points(points, closed=closed, tension=float(tension))
    data = bpy.data.curves.new(name, type="CURVE")
    data.dimensions = "3D"
    data.use_path = True
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(path.anchors) - 1)
    for bp, a, hl, hr in zip(spline.bezier_points, path.anchors, path.handles_left, path.handles_right):
        bp.co = a
        bp.handle_left_type = "FREE"
        bp.handle_right_type = "FREE"
        bp.handle_left = hl
        bp.handle_right = hr
    spline.use_cyclic_u = bool(closed)
    obj = bpy.data.objects.new(name, data)
    _scene().collection.objects.link(obj)
    return {"name": obj.name, "length": _round(path.length, 4), "points": len(path.anchors), "closed": bool(closed)}


@command("follow_path", mutates=True)
def follow_path(object=None, path=None, start_frame=None, end_frame=None, easing="smootherstep",
                orient=True, forward_axis="FORWARD_Y", up_axis="UP_Z", method="constraint", bake_step=1.0,
                zero_location=True, analyze=True):
    """Move an object along a curve; easing sets how distance along the path changes over time.

    method "constraint": Follow Path constraint with an eased offset_factor
    (editable, stays linked to the curve). method "bake": location (and
    rotation when orient) keys sampled at constant arc-length spacing.
    """
    obj = _get_object(object)
    curve = _get_object(path)
    if curve.type != "CURVE":
        raise CommandError("%r is a %s, not a curve. Create one with create_path." % (curve.name, curve.type))
    scene = _scene()
    f0 = float(scene.frame_start if start_frame is None else start_frame)
    f1 = float(scene.frame_end if end_frame is None else end_frame)
    if f1 <= f0:
        raise CommandError("end_frame must be after start_frame")
    report = {"object": obj.name, "path": curve.name, "method": method}
    if method == "constraint":
        con = next((c for c in obj.constraints if c.type == "FOLLOW_PATH" and c.target == curve), None)
        if con is None:
            con = obj.constraints.new("FOLLOW_PATH")
            con.target = curve
        con.use_fixed_location = True
        con.use_curve_follow = bool(orient)
        con.forward_axis = forward_axis
        con.up_axis = up_axis
        if zero_location:
            obj.location = (0.0, 0.0, 0.0)
            if orient:
                obj.rotation_euler = (0.0, 0.0, 0.0)
        dp = 'constraints["%s"].offset_factor' % con.name
        report["constraint"] = con.name
        report["animated_path"] = dp
        report.update(animate(object=obj.name, data_path=dp, mode="auto", bake_step=bake_step,
                              waypoints=[{"frame": f0, "value": 0.0, "easing": easing},
                                         {"frame": f1, "value": 1.0}], analyze=False))
        if analyze:
            report["motion"] = _world_analysis(obj, f0, f1)
        return report
    if method != "bake":
        raise CommandError("method must be 'constraint' or 'bake'")
    bpath = _curve_to_path(curve)
    e = mk_easing.resolve(easing)
    steps = max(1, int(round((f1 - f0) / float(bake_step))))
    frames = [f0 + (f1 - f0) * i / steps for i in range(steps + 1)]
    positions, tangents = [], []
    for f in frames:
        u = bpath.parameter_at_fraction(e((f - f0) / (f1 - f0)))
        positions.append(bpath.point(u))
        tangents.append(bpath.tangent(u))
    for axis in range(3):
        wps = [mk_planner.Waypoint(f, p[axis], "auto") for f, p in zip(frames, positions)]
        keys = mk_planner.plan(wps, mode="auto")
        fc = _ensure_fcurve(obj, "location", axis, True, f0)
        _write_keys(fc, keys, "range")
    if orient:
        from mathutils import Vector
        axis_map = {"FORWARD_X": "X", "FORWARD_Y": "Y", "FORWARD_Z": "Z",
                    "TRACK_NEGATIVE_X": "-X", "TRACK_NEGATIVE_Y": "-Y", "TRACK_NEGATIVE_Z": "-Z"}
        track = axis_map.get(forward_axis, "Y")
        up = up_axis.replace("UP_", "")
        eulers = []
        prev = None
        for tng in tangents:
            v = Vector(tng)
            if v.length < 1e-9:
                eul = eulers[-1] if eulers else obj.rotation_euler.copy()
            else:
                eul = v.to_track_quat(track, up).to_euler("XYZ", prev) if prev else v.to_track_quat(track, up).to_euler("XYZ")
            prev = eul
            eulers.append(eul)
        obj.rotation_mode = "XYZ"
        for axis in range(3):
            wps = [mk_planner.Waypoint(f, eul[axis], "auto") for f, eul in zip(frames, eulers)]
            fc = _ensure_fcurve(obj, "rotation_euler", axis, True, f0)
            _write_keys(fc, mk_planner.plan(wps, mode="auto"), "range")
    report["path_length"] = _round(bpath.length, 4)
    report["keys_per_channel"] = len(frames)
    if analyze:
        report["motion"] = _world_analysis(obj, f0, f1)
    return report


def _world_analysis(obj, f0, f1, step=None, table_every=0):
    """Sample the evaluated world position (parents, constraints, drivers included)."""
    scene = _scene()
    fps = _fps(scene)
    step = float(step or (0.5 if f1 - f0 <= 240 else (f1 - f0) / 480.0))
    n = max(4, int(round((f1 - f0) / step)))
    frames = [f0 + (f1 - f0) * i / n for i in range(n + 1)]
    saved = (scene.frame_current, scene.frame_subframe)
    pts = []
    try:
        for f in frames:
            fi = math.floor(f)
            scene.frame_set(int(fi), subframe=f - fi)
            pts.append(list(obj.matrix_world.translation))
    finally:
        scene.frame_set(saved[0], subframe=saved[1])
    r = mk_analysis.analyze(pts, f0, (f1 - f0) / n, fps, label="%s world position" % obj.name,
                            table_every=table_every)
    return _round(_jsonable(r))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@command("analyze")
def analyze(object=None, data_path=None, index=None, frame_start=None, frame_end=None, step=None,
            space="auto", table_every=0, angle_unit="degrees"):
    """Measure position, velocity, acceleration and jerk of an animated property.

    space "world" samples the evaluated world position (includes parents and
    constraints). "fcurve" samples the property's own F-curves.
    """
    obj = _get_object(object)
    scene = _scene()
    f0 = float(scene.frame_start if frame_start is None else frame_start)
    f1 = float(scene.frame_end if frame_end is None else frame_end)
    if f1 <= f0:
        raise CommandError("frame_end must be after frame_start")
    if space == "auto":
        space = "world" if data_path in (None, "", "location", "world") else "fcurve"
    if space == "world":
        return {"object": obj.name, "space": "world", "result": _world_analysis(obj, f0, f1, step, table_every)}
    id_block, path = _resolve_target(obj, data_path)
    current = _prop_value(id_block, path)
    indices, is_array = _channel_indices(current, index)
    step = float(step or (0.25 if f1 - f0 <= 240 else (f1 - f0) / 960.0))
    frames, cols = _sample_fcurves(id_block, path, indices, is_array, f0, f1, step)
    if _is_angle(data_path) and angle_unit == "degrees":
        cols = [[math.degrees(v) for v in col] for col in cols]
    fps = _fps(scene)
    dstep = (f1 - f0) / (len(frames) - 1)
    out = {"object": obj.name, "data_path": data_path, "space": "fcurve"}
    if is_array and len(indices) > 1:
        out["combined"] = _round(_jsonable(mk_analysis.analyze([list(r) for r in zip(*cols)], f0, dstep, fps,
                                                               label=data_path, table_every=table_every)))
        comps = "XYZW"
        out["per_component"] = {
            comps[i] if i < 4 else str(i): _round(_jsonable(
                {k: v for k, v in mk_analysis.analyze(col, f0, dstep, fps).items()
                 if k in ("summary", "issues", "peak_velocity", "overshoot", "moving")}))
            for i, col in zip(indices, cols)}
    else:
        out["result"] = _round(_jsonable(mk_analysis.analyze(cols[0], f0, dstep, fps, label=data_path,
                                                             table_every=table_every)))
    return out


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

@command("execute_python", mutates=True)
def execute_python(code=""):
    from . import preferences
    if not preferences.get().allow_python:
        raise CommandError("Arbitrary Python is disabled. Enable 'Allow execute_python' in the add-on "
                           "preferences (Edit > Preferences > Add-ons > Blender Animate MCP).")
    import contextlib
    import io
    buf = io.StringIO()
    ns = {"bpy": bpy, "motionkit": motionkit, "result": None}
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "<mcp>", "exec"), ns)  # noqa: S102 - explicit opt-in above
    return {"stdout": buf.getvalue()[-20000:], "result": _jsonable(ns.get("result"))}
