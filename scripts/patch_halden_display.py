"""Apply two reviewed display corrections to the original supplied JS bundle.

Usage: python scripts/patch_halden_display.py ORIGINAL_JS OUTPUT_JS
No hand geometry, transforms, kinematics or pose values are changed.
"""
from pathlib import Path
import hashlib
import sys

ORIGINAL_SHA256 = '923eca1464579132789cfa19ea5f05fe14e1ce2f36340078ce56052effa54248'
PATCHES = (
    (b'shadows:!0,dpr:[1,1.75],camera:{position:[-200,128,286],fov:30,',
     b'shadows:{type:1},dpr:[1,1.75],camera:{position:[-200,128,286],fov:65,'),
)


def patch(data):
    if hashlib.sha256(data).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('Expected the exact original Halden bundle')
    for before, after in PATCHES:
        if data.count(before) != 1:
            raise ValueError('Display patch target is not unique')
        data = data.replace(before, after)
    return data


if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:])
    target.write_bytes(patch(source.read_bytes()))
