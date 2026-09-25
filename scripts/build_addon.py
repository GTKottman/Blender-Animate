#!/usr/bin/env python3
"""Package the Blender add-on as an installable zip with motionkit vendored inside.

    python scripts/build_addon.py            # -> dist/blender_animate.zip

Install in Blender: Edit > Preferences > Add-ons > Install from Disk (or drag the zip in).
"""

import argparse
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "blender_animate")
MOTIONKIT = os.path.join(ROOT, "src", "motionkit")


def _files(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            yield os.path.join(dirpath, name)


def build(out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _files(ADDON):
            zf.write(path, os.path.join("blender_animate", os.path.relpath(path, ADDON)))
        for path in _files(MOTIONKIT):
            zf.write(path, os.path.join("blender_animate", "motionkit", os.path.relpath(path, MOTIONKIT)))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", default=os.path.join(ROOT, "dist", "blender_animate.zip"))
    print(build(parser.parse_args().output))
