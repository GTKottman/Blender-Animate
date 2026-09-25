"""Locate motionkit: vendored inside the packaged add-on, or the repo's src/ in development."""

import os
import sys

try:
    from . import motionkit  # noqa: F401  (packaged by scripts/build_addon.py)
except ImportError:
    _src = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "src"))
    if os.path.isdir(os.path.join(_src, "motionkit")) and _src not in sys.path:
        sys.path.insert(0, _src)
    import motionkit  # noqa: F401
