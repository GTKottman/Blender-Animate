# Recipes

Worked patterns at 24 fps, each checked against the real tools. Scale frame numbers
to the scene's fps and to the size of the move. Each recipe lists what the final
`analyze_motion` should say, so you can tell a correct result from a broken one.

## Contents

1. Bouncing ball
2. Anticipation → action → settle
3. Flowing camera move through waypoints
4. Fly-through on a path
5. Overlapping action
6. Turntable and spins
7. Pulses on other properties (lights, lenses, custom properties)

---

## 1. Bouncing ball

Gravity makes vertical motion parabolic in time: falling accelerates (`quad_in`),
rising decelerates (`quad_out`). Each bounce is lower and shorter; bounce height
roughly scales with the square of its duration.

```json
animate_keyframes(object="Ball", data_path="location", index=2, waypoints=[
  {"frame": 1,  "value": 3,   "easing": "quad_in"},
  {"frame": 13, "value": 0,   "easing": "quad_out"},
  {"frame": 21, "value": 1.3, "easing": "quad_in"},
  {"frame": 29, "value": 0,   "easing": "quad_out"},
  {"frame": 33, "value": 0.4, "easing": "quad_in"},
  {"frame": 37, "value": 0}])
animate_property(object="Ball", data_path="location", index=0, to=6,
                 start_frame=1, end_frame=40, easing="cubic_out")
```

Horizontal motion is a separate channel with its own easing: it slows gradually
through the bounces.

**Expected report:** velocity jumps at the contact frames (13, 29, 37). **These are
correct.** An impact reverses velocity instantly, so don't smooth them away. The apexes
(21, 33) are reversals, not hitches.

## 2. Anticipation → action → settle

Three beats: a small wind-up in the opposite direction, a fast action that arrives
*with speed*, then a spring that absorbs that speed.

```json
animate_keyframes(object="Box", data_path="location", index=0, waypoints=[
  {"frame": 1,  "value": 0,    "easing": "sine_in_out"},
  {"frame": 8,  "value": -0.4, "easing": "cubic_in"},
  {"frame": 16, "value": 4}])
spring_to(object="Box", data_path="location", index=0, target=4, start_frame=16,
          frequency_hz=2.5, damping_ratio=0.35)
```

`spring_to` inherits the ~38 units/s the object has at frame 16, so the overshoot
comes from real momentum.

**Expected:**
- After the first call alone, the report says "Stops abruptly" and flags a velocity
  jump at frame 16, because the spring isn't there yet.
- The final `analyze_motion(object="Box", data_path="location", index=0,
  space="fcurve", frame_start=1, frame_end=40)` shows anticipation of ~10%, an
  overshoot of ~40%, and no velocity jumps.
- An acceleration jump at 16 (the impact) is expected.

For a subtler settle use `damping_ratio` 0.6–0.7; for less overshoot, arrive slower
(`quad_in`) or give the spring a lower `frequency_hz`.

## 3. Flowing camera move through waypoints

Cameras should never hitch. Put `auto` on **every** waypoint, including the first.
`auto` gives flat tangents at the first and last keys (so the move eases in and out)
and smooth pass-through tangents at the inner ones.

```json
animate_keyframes(object="Camera", data_path="location", waypoints=[
  {"frame": 1,  "value": [0, -10, 2],  "easing": "auto"},
  {"frame": 40, "value": [4, -7, 2.5], "easing": "auto"},
  {"frame": 80, "value": [7, -2, 3],   "easing": "auto"}])
```

Don't give the first segment an in-out easing such as `sine_in_out` or `smootherstep`.
It arrives at frame 40 at rest, and the report shows "Comes to rest mid-move" plus a
velocity jump there. For a two-beat move that must flow, you can also use `cubic_in`
into `cubic_out`.

**Expected:**
- No velocity jumps and no mid-move stops.
- Acceleration jumps at the waypoints are normal for `auto` and invisible at camera
  speeds.

When one smooth move is enough, prefer a single rest-to-rest `smootherstep` segment.
It's the smoothest possible. Aim the camera with a Track To constraint instead of
animating rotation, if the scene allows.

## 4. Fly-through on a path

```json
create_motion_path(name="Route", points=[[0,0,1], [4,3,2], [8,0,3], [12,-3,2], [16,0,1]])
follow_path(object="Drone", path="Route", start_frame=1, end_frame=120, easing="smootherstep")
```

- `easing` applies to distance along the curve. `linear` gives constant speed, which
  suits vehicles cruising through or orbits that loop. `smootherstep` suits taking off
  and landing.
- Use `closed=true` for loops.
- Lower `tension` (0.5) tightens corners.

**Expected:**
- The world-space report shows a single velocity hump for `smootherstep`, with no issues.
- For `linear` it shows a flat speed line that starts and stops abruptly.
- `linear` also reports acceleration jumps where the path's bend changes at its anchor
  points. Speed is constant, but the direction of travel changes. More, evenly spaced
  points, or `tension` below 1, soften this.
- The object's location is reset, because the path drives it now.
- Use `method="bake"` if the result has to export to a game engine or other software
  without the constraint.

## 5. Overlapping action

Animate the main mass first. Copy the same motion onto secondary parts, then delay them:

```json
animate_property(object="Antenna", data_path="rotation_euler", index=1, to=25,
                 start_frame=1, end_frame=12, easing="cubic_out")
shift_keys(object="Antenna", offset=3)
```

- Offset lighter or looser parts more: 2–4 frames each, cascading down a chain.
- For text or logo reveals, offset each letter 1–2 frames more than the one before.
- Springs on secondary parts (lower `frequency_hz` for floppier parts) sell weight.

## 6. Turntable and spins

A product turntable with constant speed that loops seamlessly:

```json
animate_property(object="Product", data_path="rotation_euler", index=2, from_value=0, to=360,
                 start_frame=1, end_frame=241, easing="linear")
```

- `linear` is correct here. The report's "Starts abruptly", "Stops abruptly" and
  velocity jumps at the end frames are expected for a cyclic spin. Make the scene's
  frame range end one frame before the last key, so the loop doesn't repeat a frame.
- If it should spin up and down, use `sine_in_out` over the whole range, or split it
  as `cubic_in` → `linear` middle → `cubic_out` with matching speeds.
- Rotation values are degrees.

## 7. Pulses on other properties

Any animatable numeric property works through `data_path`:

```json
animate_keyframes(object="KeyLight", data_path="data.energy", waypoints=[
  {"frame": 1,  "value": 100,  "easing": "expo_out"},
  {"frame": 6,  "value": 1500, "easing": "cubic_in_out"},
  {"frame": 30, "value": 100}])
animate_property(object="Camera", data_path="data.lens", to=85, start_frame=1,
                 end_frame=48, easing="smootherstep")
```

A flash should be fast in and slow out (`expo_out` up, then a long decay). The report
flags its hard start. That's intended, because a flash is an event.
