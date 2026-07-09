#!/usr/bin/env python3
"""Self-render tool — kit v0.2 candidate (NOT in the v0.1 blind kit).

Gives a driver eyes: renders its current `export_json` scene as a top view
plus front and side elevations, with items drawn at their AS-STORED rotation
(radians, three.js R_y). This closes the verification-loop gap found in the
v0.1 cohort: a model that renders after placing its first item sees a
rotation-units mistake immediately, the same way mechanical-lane drivers can
inspect their in-progress assembly. Shipping this in a kit starts a new
cohort — do not hand it to v0.1 runs.

Usage:
  py -3.11 render_views.py --scene scene.json --out views.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Rectangle


def load_scene(path: Path) -> dict:
    scene = json.loads(path.read_text(encoding="utf-8"))
    if set(scene.keys()) == {"json"}:
        scene = json.loads(scene["json"])
    nodes = scene["nodes"]
    return nodes if isinstance(nodes, dict) else {n["id"]: n for n in nodes}


def footprint(center, dims, yaw):
    w, _, d = dims
    c, s = math.cos(yaw), math.sin(yaw)
    cx, cz = center
    return [
        (cx + u * c + v * s, cz + u * -s + v * c)
        for u, v in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    nodes = load_scene(args.scene)
    walls = {i: n for i, n in nodes.items() if n.get("type") == "wall"}
    items = [n for n in nodes.values() if n.get("type") == "item"]
    opens = [n for n in nodes.values() if n.get("type") in ("door", "window")]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5), dpi=110)
    top, front, side = axes

    # --- top (plan) ---
    top.set_title("TOP — plan (item boxes at stored rotation; arrow = facing)")
    for w in walls.values():
        (sx, sz), (ex, ez) = w["start"], w["end"]
        top.plot([sx, ex], [sz, ez], color="#222", lw=5, solid_capstyle="projecting")
    for o in opens:
        host = walls.get(o.get("wallId"))
        if not host:
            continue
        (sx, sz), (ex, ez) = host["start"], host["end"]
        L = math.hypot(ex - sx, ez - sz) or 1
        ux, uz = (ex - sx) / L, (ez - sz) / L
        lx = float((o.get("position") or [0, 0, 0])[0])
        cx, cz = sx + ux * lx, sz + uz * lx
        h = float(o.get("width", 1.0)) / 2
        col = "#c2410c" if o["type"] == "door" else "#1d4ed8"
        top.plot([cx - ux * h, cx + ux * h], [cz - uz * h, cz + uz * h], color="white", lw=6)
        top.plot([cx - ux * h, cx + ux * h], [cz - uz * h, cz + uz * h], color=col, lw=2.5)
    for it in items:
        p = it.get("position") or [0, 0, 0]
        yaw = float((it.get("rotation") or [0, 0, 0])[1])
        dims = (it.get("asset") or {}).get("dimensions", [0.5, 0.5, 0.5])
        top.add_patch(MplPolygon(footprint((p[0], p[2]), dims, yaw), closed=True,
                                 facecolor="#a7f3d0", edgecolor="#047857", lw=0.8, alpha=0.9))
        top.annotate("", xy=(p[0] + math.sin(yaw) * 0.3, p[2] + math.cos(yaw) * 0.3),
                     xytext=(p[0], p[2]),
                     arrowprops=dict(arrowstyle="->", color="#065f46", lw=1))

    # --- elevations: front looks along +Z (sees X-Y), side looks along -X (sees Z-Y) ---
    def elevation(ax, axis, title):
        ax.set_title(title)
        for w in walls.values():
            (sx, sz), (ex, ez) = w["start"], w["end"]
            h = float(w.get("height", 2.6))
            a, b = (sx, ex) if axis == "x" else (sz, ez)
            ax.add_patch(Rectangle((min(a, b), 0), abs(b - a) or 0.05, h,
                                   facecolor="#e5e7eb", edgecolor="#555", lw=0.5, alpha=0.6))
        for it in items:
            p = it.get("position") or [0, 0, 0]
            yaw = float((it.get("rotation") or [0, 0, 0])[1])
            dims = (it.get("asset") or {}).get("dimensions", [0.5, 0.5, 0.5])
            pts = footprint((p[0], p[2]), dims, yaw)
            vals = [q[0] for q in pts] if axis == "x" else [q[1] for q in pts]
            ax.add_patch(Rectangle((min(vals), p[1]), max(vals) - min(vals), dims[1],
                                   facecolor="#a7f3d0", edgecolor="#047857", lw=0.8, alpha=0.9))
        ax.set_ylim(-0.2, 3.2)
        ax.set_xlabel("X (m)" if axis == "x" else "Z (m)")

    elevation(front, "x", "FRONT — looking north (X vs height)")
    elevation(side, "z", "SIDE — looking east (Z vs height)")
    for ax in axes:
        ax.set_aspect("equal")
        ax.grid(True, lw=0.3, alpha=0.4)

    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
