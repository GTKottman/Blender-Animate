"""motionkit: the maths of good motion, independent of Blender.

x(t) = A + (B - A) * f(t)

* ``easing``   - timing curves f(t) and their derivatives (velocity, acceleration, jerk)
* ``bezier``   - cubic bezier timing curves and 3D spatial paths with arc-length mapping
* ``spring``   - closed-form damped springs (under/critically/over damped)
* ``planner``  - converts easing/spring intent into F-curve keyframes with exact handles
* ``analysis`` - measures sampled motion: x, x', x'', x''' and what to fix
"""

from . import analysis, bezier, easing, planner, spring
from .easing import EASINGS, Easing, characterize, interpolate, profile, resolve
from .spring import Spring, resolve_spring

__all__ = [
    "analysis", "bezier", "easing", "planner", "spring",
    "EASINGS", "Easing", "characterize", "interpolate", "profile", "resolve",
    "Spring", "resolve_spring",
]
__version__ = "0.1.0"
