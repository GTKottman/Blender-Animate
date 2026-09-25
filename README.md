# Blender Animate MCP

An MCP-first Blender add-on that helps an AI animate **how things move**, not just where they end up.

Linear motion, `x(t) = A + (B − A)·t`, starts and stops instantly. Good motion replaces
`t` with a timing curve: `x(t) = A + (B − A)·f(t)`. This project gives an AI (Claude
or any MCP client) tools built around that idea:

- **Easing**: `smoothstep` (3t² − 2t³), `smootherstep` (6t⁵ − 15t⁴ + 10t³, the
  minimum-jerk curve), the Penner families, CSS-style `cubic_bezier(x1, y1, x2, y2)`,
  back, elastic, bounce and spring.
- **Derivatives**: every write returns a report on position, velocity, acceleration
  and jerk. It covers ease-in/out, velocity kinks, acceleration "kicks", overshoot,
  and smoothness relative to the minimum-jerk ideal.
- **Cubic Bézier paths**: a spatial path decides *where* an object goes. A separate
  timing curve, applied to **arc length**, decides *how fast* it covers the path.
- **Damped springs**: closed-form under-, critically and over-damped solutions for
  overshoot, bounce and settling. They can inherit the velocity the object already
  has, for follow-through.

```
 MCP client (Claude, ...)  ──stdio──►  blender-animate-mcp  ──TCP 127.0.0.1:9877──►  Blender add-on
                                       (tools, guide,                               (bridge + F-curve
                                        easing/spring maths)                          writer/sampler)
```

## Install

### 1. Blender add-on (Blender 3.6 – 5.x)

```bash
python scripts/build_addon.py        # -> dist/blender_animate.zip (motionkit vendored inside)
```

In Blender, open **Edit › Preferences › Add-ons › Install from Disk** and choose
`dist/blender_animate.zip`, then enable **Blender Animate MCP**. It installs as an
extension on 4.2+ and as a legacy add-on on older versions. By default the bridge
starts automatically. You can also start or stop it from **3D View › Sidebar (N) ›
Animate MCP**.

### 2. MCP server

```bash
pip install .            # or: uvx --from git+https://github.com/gtkottman/blender-animate blender-animate-mcp
```

Claude Code:

```bash
claude mcp add blender-animate -- uvx --from git+https://github.com/gtkottman/blender-animate blender-animate-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "blender-animate": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/gtkottman/blender-animate", "blender-animate-mcp"]
    }
  }
}
```

`BLENDER_HOST`, `BLENDER_PORT` (default `9877`) and `BLENDER_TIMEOUT` override the
bridge address. Works with `mcp` 1.x (FastMCP) and 2.x (MCPServer).

## Tools

| tool | what it does |
|---|---|
| `list_easings`, `inspect_easing`, `compare_easings` | Timing curves with x/v/a/j tables, end behaviour and sparklines. No Blender needed. |
| `design_spring` | Spring regime, overshoot, settle time and period before baking. No Blender needed. |
| `get_scene`, `get_object`, `set_scene` | fps, frame range, objects, and every F-curve key with handles |
| `animate_property` | A → B between two frames with an easing |
| `animate_keyframes` | Pose-to-pose waypoints, each segment with its own easing (`auto` = smooth pass-through) |
| `spring_to` | Bake a damped spring onto a target, optionally inheriting current velocity |
| `reshape_timing` | Keep the poses and change the easing between them (e.g. linear blocking → smootherstep) |
| `create_motion_path`, `follow_path` | Bézier path through points, and eased travel along arc length (constraint or baked) |
| `shift_keys`, `clear_animation` | Offset for overlapping action, and cleanup |
| `analyze_motion` | Position/velocity/acceleration/jerk diagnosis in world or F-curve space |
| `execute_blender_python` | Escape hatch. Off unless enabled in the add-on preferences. |

Resources: `animate://guide` (the motion playbook the model should read) and
`animate://easings`. Prompt: `animate_move(object, intent)`.

Every scene edit is one undo step in Blender. Rotations are in degrees unless
`angle_unit="radians"`. Rates are per second, using the scene fps.

## How easings become keyframes

`motionkit.planner` chooses the most faithful representation for each segment:

- **Native interpolation**: Blender's SINE/QUAD/CUBIC/QUART/QUINT/EXPO/CIRC/BACK/BOUNCE
  reproduce the Penner curves exactly (verified against Blender in the tests).
- **Exact bezier handles**: `smoothstep` is the cubic bezier (1/3, 0), (2/3, 1), and
  any `cubic_bezier(...)` maps directly onto F-curve handles. One key, and still editable.
- **Baked with exact tangents**: `smootherstep`, springs and elastic are sampled every
  `bake_step` frames. Each sample gets bezier handles from the true derivative (cubic
  Hermite), so velocity stays continuous and sub-frame evaluation (motion blur) matches
  the maths. Baked in-betweens are tagged as *jitter* keys, so `reshape_timing` can tell
  them from real poses.

## What the analysis tells the AI

```text
summary: eases in and out; peak speed 9.37/s at frame 24; smoothness 1.00 of minimum-jerk ideal.
velocity:     ▁▁▁▁▂▂▂▃▃▄▄▄▅▅▆▆▇▇▇▇█████████▇▇▇▇▆▆▅▅▄▄▄▃▃▂▂▂▁▁▁
issues (linear):     Starts abruptly ... Velocity jumps near frame(s) 0, 48 ...
issues (smoothstep): Acceleration jumps (a 'kick', infinite jerk) near frame(s) 0, 48 ...
```

Smoothness is `720 / (T⁵/L² · ∫ jerk² dt)`, where 720 is the jerk cost of
`smootherstep`, the minimum-jerk move between two rests.

## Development

```
src/motionkit/          pure-Python motion maths (no Blender): easing, bezier, spring, planner, analysis
src/blender_animate_mcp MCP server (tools, resources, prompt, socket client)
addon/blender_animate/  Blender add-on (bridge thread + main-thread command handlers)
scripts/build_addon.py  builds the installable zip
tests/                  maths, MCP layer, and real-Blender integration tests
```

```bash
pip install -e ".[dev]"
pytest                   # Blender tests run when the `bpy` wheel is installed (pip install bpy)
```

For live add-on development, symlink `addon/blender_animate` into Blender's add-ons
folder. It finds `src/motionkit` automatically when motionkit is not vendored.
