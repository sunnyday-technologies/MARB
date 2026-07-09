#!/usr/bin/env python3
"""Build the PH-1 goal floorplan render bundled in the Pascal blind kit.

Reads the reference layout YAML and draws a dimensioned 2D plan: walls,
door/window openings, and furnishing footprints with facing arrows. This is
the architecture-lane analog of the mechanical kit's reference_overview.png —
it shows the target, and realization fidelity is what gets graded.

Usage:
  python grader/build_pascal_goal_render.py \
      --ref tasks/pascal_house/ph1_reference_layout.yaml \
      --out kits/pascal_house_blind_kit_v0.1/reference_floorplan.png
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml
from matplotlib.patches import FancyArrow, Rectangle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    ref = yaml.safe_load(args.ref.read_text(encoding="utf-8"))
    t = ref["envelope"]["wall_thickness"]

    fig, ax = plt.subplots(figsize=(13, 9), dpi=150)

    # Walls as thick centerline strokes.
    for w in ref["walls"]:
        (sx, sz), (ex, ez) = w["start"], w["end"]
        ax.plot([sx, ex], [sz, ez], color="#222222", linewidth=t * 72 / 2.0,
                solid_capstyle="projecting", zorder=2)

    # Openings drawn as white gaps + markers on the wall line.
    def opening(o, width, color, label):
        host = next(w for w in ref["walls"] if w["id"] == o["wall"])
        (sx, sz), (ex, ez) = host["start"], host["end"]
        L = math.hypot(ex - sx, ez - sz)
        ux, uz = (ex - sx) / L, (ez - sz) / L
        cx, cz = o["center"]
        hx, hz = ux * width / 2.0, uz * width / 2.0
        ax.plot([cx - hx, cx + hx], [cz - hz, cz + hz], color="white",
                linewidth=t * 72 / 2.0 + 1, solid_capstyle="butt", zorder=3)
        ax.plot([cx - hx, cx + hx], [cz - hz, cz + hz], color=color,
                linewidth=3, solid_capstyle="butt", zorder=4)
        ax.annotate(label, (cx, cz), textcoords="offset points",
                    xytext=(0, 7), ha="center", fontsize=6.5, color=color, zorder=6)

    for d in ref["doors"]:
        opening(d, 0.9, "#c2410c", "D")
    for w in ref["windows"]:
        opening(w, 1.2, "#1d4ed8", "W")

    # Room labels.
    for room in ref["rooms"]:
        x0, z0, x1, z1 = room["rect"]
        ax.annotate(
            f"{room['id']}\n{x1 - x0:.1f} x {z1 - z0:.1f} m",
            ((x0 + x1) / 2.0, (z0 + z1) / 2.0),
            ha="center", va="center", fontsize=9, color="#555555", zorder=5,
        )

    # Furnishing footprints with facing arrows.
    for it in ref["items"]:
        w, _, d = it["dims"]
        yaw = math.radians(it["yaw_deg"])
        k = round(it["yaw_deg"] / 90.0) % 2
        ex, ez = (w, d) if k == 0 else (d, w)
        cx, cz = it["center"]
        ax.add_patch(Rectangle((cx - ex / 2.0, cz - ez / 2.0), ex, ez,
                               facecolor="#a7f3d0", edgecolor="#047857",
                               linewidth=0.8, alpha=0.85, zorder=4))
        # facing arrow: yaw 0 = +Z; three.js R_y gives front = (sin yaw, cos yaw)
        fx, fz = math.sin(yaw), math.cos(yaw)
        ax.add_patch(FancyArrow(cx, cz, fx * 0.28, fz * 0.28, width=0.02,
                                head_width=0.12, color="#047857", zorder=5))
        ax.annotate(it["asset"], (cx, cz), textcoords="offset points",
                    xytext=(0, -9), ha="center", fontsize=5.5,
                    color="#065f46", zorder=6)

    ax.set_aspect("equal")
    ax.set_xlim(-0.8, ref["envelope"]["outer_x"] + 0.8)
    ax.set_ylim(-0.8, ref["envelope"]["outer_z"] + 0.8)
    ax.set_xlabel("X — east (m)")
    ax.set_ylabel("Z — north (m)")
    ax.set_title("PH-1 Bungalow — goal floorplan (walls, openings, furnishing footprints + facing)")
    ax.grid(True, linewidth=0.3, alpha=0.4)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
