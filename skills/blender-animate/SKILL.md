---
name: blender-animate
description: Animate Blender scenes with deliberate, physically believable motion using the blender-animate MCP tools (animate_property, animate_keyframes, spring_to, follow_path, create_motion_path, reshape_timing, analyze_motion, inspect_easing, design_spring). Use this whenever the user wants to animate, keyframe, move, bounce, spin, fly, settle, ease, or time anything in Blender, including camera moves, logo reveals, product turntables, bouncing balls, motion graphics, or fixing animation that looks stiff, robotic, floaty or jerky, even if they never mention easing, curves or MCP.
---

# Animating in Blender with blender-animate

This skill is about **how position changes over time**, not just where things end up.
Every move is `x(t) = A + (B − A)·f(t)`, and your job is to choose `f`, the timing
curve, as deliberately as the poses. The tools measure the result for you: position,
velocity, acceleration and jerk. Treat those measurements as your eyes, because you
can't watch the playback.

## The loop

1. **Look.** Call `get_scene` for the fps, frame range and objects. Call `get_object`
   for anything already animated. Work out timing in seconds, then convert:
   `frames = seconds × fps`. Durations that read well:
   - UI or motion-graphics pops: 0.2–0.4 s
   - moving a prop: 0.5–1.2 s
   - heavy objects and camera moves: 1.5–4 s
   - holds: at least 6–12 frames, so the eye can register a pose
2. **Choose the timing** for the intent (table below). If you're unsure, call
   `inspect_easing` or `compare_easings`; it's free and needs no Blender.
3. **Write** with the right tool (next section).
4. **Read the `motion` report** that comes back from every write. Fix issues that
   contradict the intent, and leave the ones the intent calls for (see "Reading the
   report").
5. **Check the whole shot** at the end with `analyze_motion`, using `space="world"`
   for anything parented, constrained or on a path. The report after each individual
   write only covers that edit.

## Picking a timing curve

| intent | easing | why |
|---|---|---|
| natural move from rest to rest | `smootherstep` | Zero velocity *and* acceleration at both ends: the minimum-jerk move. Baked into keys. |
| same, but keep one editable key | `sine_in_out`, `smoothstep` | Native in Blender. Small acceleration "kick" at rest; usually fine. |
| leaves fast, lands softly | `cubic_out`, `quint_out`, `expo_out` | Most of the distance covered early. |
| gathers speed into an impact | `quad_in`, `cubic_in` | Arrives at full speed. Follow with a spring or a bounce. |
| snappy UI or motion graphics | `expo_out`, `cubic_bezier(0.2, 0, 0, 1)` | Very fast start, long gentle tail. |
| overshoot then settle | `back_out(1.7)`, or `spring_to` for real physics | Appeal and weight. |
| wind-up before the action | `back_in`, or a small opposite waypoint | Anticipation. |
| free fall and rebound | `quad_in` falling, `quad_out` rising | Gravity makes motion parabolic in time. |
| flow *through* waypoints | `auto` on every waypoint, or `cubic_in` into `cubic_out` | Eases in at the first key, out at the last, and never stops in between (see below). |
| blocking / stepped | `constant` | Holds, then pops. |

Custom timing works like CSS: `cubic_bezier(x1, y1, x2, y2)`. Keep x1 and x2 in [0, 1].
y values outside [0, 1] overshoot or anticipate.

**The chaining trap.** A waypoint's `easing` sets the timing of the segment that
*starts* at that waypoint. If every segment eases in and out (`smootherstep`,
`sine_in_out`), the object **comes to a full stop at every waypoint**. That's right for
pose-to-pose holds, but it reads as a hitch in a move that should flow. For flow, use
`auto` on every waypoint (it still eases in at the first key and out at the last), or
pair an ease-in segment with an ease-out one. The
report calls this out as "Comes to rest mid-move".

## Which tool

- **`animate_property`**: one A→B move of one property. Omit `from_value` to start
  from wherever the object currently is at `start_frame`.
- **`animate_keyframes`**: pose-to-pose with several waypoints, each segment with its
  own easing. This is where most performance happens. `value` can be a number, a list
  per component, or `null` to keep the existing value at that frame.
- **`spring_to`**: physical settle onto a target. Run it after a move that *arrives
  with speed* (for example one ending on `cubic_in`), and leave `inherit_velocity` on.
  The spring then picks up that momentum and overshoots naturally; this is
  follow-through. Preview with `design_spring` first:
  - `damping_ratio`: 0.2 is bouncy, 0.35–0.5 lively, 0.7 subtle, 1 settles with no overshoot
  - `frequency_hz`: 1–3 is natural for props, 4–8 snappy
- **`create_motion_path` + `follow_path`**: when the *shape* of the route matters
  (arcs, fly-throughs, orbits). The path decides where the object goes; `easing`
  decides how quickly it covers the distance along it (arc length). So `linear` gives
  constant speed however the curve bends. Natural motion travels on arcs, so prefer a
  curved path over straight location keys for big moves.
- **`reshape_timing`**: keep the existing poses and swap the easing between them. It's
  the fastest way to fix stiff or linear blocking (for example, turn everything into
  `smootherstep` or `cubic_out`).
- **`shift_keys`**: offset secondary parts (antenna, tail, text lines, child objects)
  by 2–4 frames so everything doesn't move in lockstep. This is overlapping action.
- **`clear_animation`**: start a channel over. It's destructive but undoable.

Notes that save you from wrong results:

- **Rotations** are in degrees unless you pass `angle_unit="radians"`.
- **Other data paths**: `data.energy` (light), `data.lens` (camera focal length),
  `pose.bones["Bone"].location`, and `["prop"]` for custom properties.
- **Replacing keys**: `replace="range"` (the default) deletes existing keys inside the
  span you write. Split writes so they don't overlap unless you intend to replace.
- **Follow Path constraint**: `follow_path` with the default `method="constraint"`
  zeroes the object's location (and rotation, when `orient` is on), because the path
  now drives it. Say so to the user if the object had a meaningful offset.
- **`mode`**:
  - `"auto"` (the default) uses Blender's native interpolation when it is exact, and
    bakes per-frame keys with exact slopes otherwise. Baked keys are small "jitter" keys
    that `reshape_timing` knows to replace.
  - `"native"` guarantees few, editable keys; it errors on easings Blender can't play.
  - `"bake"` always samples.
- **Undo**: every edit is one undo step in Blender, so experimenting is cheap.

## Reading the report

The report after each edit is a heuristic that describes the motion. It isn't a
verdict. Compare every item with the intent before you "fix" it.

| report says | usually means | but it's intended when |
|---|---|---|
| Starts or Stops abruptly | no ease at that end | the segment continues or feeds other motion (a spring comes next, an impact) |
| Velocity jumps at frame N | linear keys, or segments whose end and start speeds don't match | contact frames: a ball hitting the ground, a hit, a cut |
| Acceleration jumps (kick) | smoothstep, sine or power easings meeting rest; the midpoint of `*_in_out` | usually invisible. Switch to `smootherstep` only when the motion must feel silky (camera moves, slow reveals) |
| Comes to rest mid-move | eased segments chained in the same direction | pose-to-pose holds |
| Overshoots X% | back, elastic or spring | that was the point. Check the size is tasteful (5–15% subtle, 30%+ cartoony) |
| smoothness_vs_min_jerk | 1.0 is ideal | only meaningful for a single rest-to-rest move; multi-beat shots score low by nature |

The sparklines (`▁▂▃▅▇█`) are the quickest read. A single smooth hump in `velocity`
is a clean move. A velocity line that jumps straight to its top is a hard start, and
two humps are two beats.

Reports on an intermediate step flag the open end you are about to fill. For example,
an action that ends at speed says "Stops abruptly" until you add the spring. Judge the
final `analyze_motion` of the whole shot, not each step.

## Finishing

Tell the user what you made in animation terms: the beats, their timing in seconds
and frames, the easing for each, and anything the analysis still flags with why it's
intended. If you changed something they didn't ask for (a zeroed location for a path,
replaced keys), say so.

For worked, tested recipes (bouncing ball, anticipation → action → settle, flowing
camera move, path fly-through, overlapping action, light pulse), read
`references/recipes.md`.
