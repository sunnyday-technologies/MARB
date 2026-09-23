"""Extract reviewed Mk-IV assets from the exact supplied static HTML.

Usage: python scripts/patch_halden_mkiv.py ORIGINAL_HTML OUTPUT_DIRECTORY
Geometry, transforms, pose values and motion logic are unchanged.
"""
from pathlib import Path
import hashlib
import re
import sys

ORIGINAL_SHA256 = 'c564cb03c7c7c071246300504b6f3213df3a9c1bbb9685ab244b1d52062a1c66'
PATCHES = (
    (b'shadows:!0,dpr:[1,1.75],camera:{position:[-210,168,312],fov:32,',
     b'shadows:{type:1},dpr:[1,1.75],camera:{position:[-210,168,312],fov:50,'),
    (b'e.fov=n<1.05?52:32,e.updateProjectionMatrix()',
     b'e.fov=n<1.05?52:50,e.updateProjectionMatrix()'),
    (b'children:`FUNCTIONAL HAND`', b'children:`HAND CONCEPT`'),
    (b'Six Feetech STS3215 servos close four fingers and a thumb. Each digit is a printed bone chain, an aluminum capstan, a UHMWPE tendon, a dorsal elastic, steel pins, and a TPU pad.',
     b'The concept models six STS3215 servos, printed bones, aluminum capstans, UHMWPE tendons, return elastics, steel pins and TPU pads.'),
)


def extract(data):
    if hashlib.sha256(data).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('Expected the exact supplied Mk-IV HTML')
    modules = re.findall(rb'<script type="module">([\s\S]*?)</script>', data)
    styles = re.findall(rb'<style[^>]*>([\s\S]*?)</style>', data)
    if len(modules) != 1 or len(styles) != 1:
        raise ValueError('Expected one inline module and one stylesheet')
    module = modules[0]
    for before, after in PATCHES:
        if module.count(before) != 1:
            raise ValueError('Reviewed patch target is not unique')
        module = module.replace(before, after)
    return module, styles[0]


if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:])
    target.mkdir(parents=True, exist_ok=True)
    for extension, data in zip(('js', 'css'), extract(source.read_bytes())):
        name = f'halden-mkiv-{hashlib.sha256(data).hexdigest()[:12]}.{extension}'
        (target / name).write_bytes(data)
        print(name)
