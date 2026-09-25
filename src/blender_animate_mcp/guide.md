# Motion guide for AI animators

Good animation controls **how position changes over time**, not just where
things end up. Read this before animating.

## 1. Position is a function of time

Linear motion, `x(t) = A + (B − A)·t`, starts and stops instantly. It reads as
mechanical. Replace `t` with an easing function `f(t)`:

    x(t) = A + (B − A) · f(t),   f(0) = 0, f(1) = 1

| easing | f(t) | at the ends |
|---|---|---|
| `linear` | t | velocity jumps from 0 to full speed |
| `smoothstep` | 3t² − 2t³ | velocity 0, **acceleration jumps** (a slight "kick") |
| `smootherstep` | 6t⁵ − 15t⁴ + 10t³ | velocity 0 **and** acceleration 0. This is the minimum-jerk move |

`smootherstep` is the best default for a natural point-to-point move.
`smoothstep` is exactly a cubic bezier with handles (1/3, 0), (2/3, 1), so
Blender plays it natively as an ordinary BEZIER key.

## 2. Think in derivatives

    position x(t) → velocity x′(t) → acceleration x″(t) → jerk x‴(t)

* **Velocity** discontinuities look like hits or glitches (linear keys, broken handles).
* **Acceleration** discontinuities feel like a kick or twitch.
* **Jerk** is what the eye reads as smoothness. Minimum jerk looks like
  deliberate, human-made motion.

`analyze_motion` measures all four and flags abrupt starts and stops,
velocity kinks, overshoot, and how smooth the motion is compared with the
minimum-jerk ideal (`smoothness_vs_min_jerk`, where 1.0 is ideal). Every
animation tool also returns this report for what it just wrote, so check it.

## 3. Pick the timing for the intent

| intent | easing |
|---|---|
| natural move between two rests | `smootherstep`, `sine_in_out`, `cubic_in_out` |
| object leaves quickly, lands softly | `cubic_out`, `quint_out`, `expo_out` |
| object gathers speed and hits something | `quad_in`, `cubic_in` (then a spring or bounce) |
| snappy UI or motion graphics | `expo_out`, `cubic_bezier(0.2, 0, 0, 1)` |
| overshoot then settle (appeal) | `back_out(1.7)`, or a real spring |
| anticipation (wind-up before a move) | `back_in`, `back_in_out` |
| dropped object | `bounce_out` |
| rubbery wobble | `elastic_out`, `spring(0.25)` |
| hold or step (blocking) | `constant` |
| smooth pass-through of many waypoints | `auto` |

Custom timing: `cubic_bezier(x1, y1, x2, y2)` works like CSS. Keep x1 and x2
in [0, 1]. y values outside [0, 1] overshoot or anticipate.

## 4. Space and time are separate

A cubic Bézier **path** decides *where* something goes (`create_motion_path`).
A **timing curve** decides *how fast* it covers the path (`follow_path`
`easing`). The timing is applied to arc length, so `linear` gives constant
speed along the whole path, and `smootherstep` accelerates away from the
start and decelerates into the end regardless of how the path bends.

## 5. Physical motion: damped springs

    m·x″ + c·x′ + k·(x − target) = 0

* `damping_ratio` ζ < 1: overshoots and rings. 0.2 is bouncy, 0.5 lively, 0.7 subtle.
* ζ = 1: critically damped, the fastest settle with no overshoot.
* ζ > 1: sluggish, heavy.
* `frequency_hz`: how fast it oscillates. 1–3 Hz feels natural for props, 4–8 Hz reads as snappy.

`spring_to` bakes the exact closed-form solution. With `inherit_velocity`
it picks up the speed the object already has at the start frame, which is
how follow-through and overlapping action stay continuous. Use `design_spring`
to preview overshoot and settle time before committing.

## 6. Workflow

1. `get_scene` to see fps, the frame range and objects.
2. Block the poses with `animate_keyframes` (waypoints, with an easing per segment).
3. Read the returned `motion` report. Fix `issues` (abrupt starts or stops, kinks).
4. Polish: `reshape_timing` to swap easing without moving poses. Use `spring_to`
   for settles, and `shift_keys` to offset secondary parts by 2–4 frames (overlap).
5. `analyze_motion` in `world` space to check the final result, including
   parents and constraints.

Frames are converted to seconds with the scene fps. Velocities are units per
second. Rotations are in degrees unless `angle_unit="radians"`.
