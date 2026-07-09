#!/usr/bin/env python3
"""Build a piece-by-piece assembly timelapse GIF for a MARB-A Pascal run.

Creation ORDER comes from the Claude Code session transcript (every MCP tool
call and its returned node ids, in chronological order); GEOMETRY comes from
the run's exported scene JSON. Nodes created and later deleted never appear.
Items are drawn with their AS-STORED rotation interpreted per the Pascal
schema (radians, three.js R_y) — so a driver that wrote degrees into the
radians field shows up as visibly skewed furniture. That skew is the point:
the GIF is the proof a reader can evaluate without reading the article.

Also writes a static goal-vs-built side-by-side PNG.

Usage:
  py -3.11 grader/build_pascal_timelapse.py \
      --scene D:/tmp/ph1_run_01/run_scene.json \
      --transcript-dir C:/Users/ngson/.claude/projects/D--tmp-ph1-run-01 \
      --ref tasks/pascal_house/ph1_reference_layout.yaml \
      --gif results/figures/ph1_run_timelapse.gif \
      --png results/figures/ph1_run_final.png
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image

CREATED_KEYS = ("wallIds", "wallId", "doorId", "windowId", "itemId", "createdIds", "zoneId", "slabId", "ceilingId")


def creation_order_from_transcript(transcript_dir: Path) -> list[tuple[str, str]]:
    """Return [(node_id, tool_name), ...] in chronological creation order."""
    order: list[tuple[str, str]] = []
    pending: dict[str, str] = {}  # tool_use_id -> tool name
    files = sorted(glob.glob(str(transcript_dir / "*.jsonl")))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and str(block.get("name", "")).startswith("mcp__"):
                        pending[block["id"]] = block["name"]
                    elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                        tool = pending.pop(block["tool_use_id"])
                        texts = []
                        bc = block.get("content")
                        if isinstance(bc, list):
                            texts = [c.get("text", "") for c in bc if isinstance(c, dict) and c.get("type") == "text"]
                        elif isinstance(bc, str):
                            texts = [bc]
                        for text in texts:
                            try:
                                payload = json.loads(text)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(payload, dict):
                                continue
                            for key in CREATED_KEYS:
                                val = payload.get(key)
                                if isinstance(val, str):
                                    order.append((val, tool))
                                elif isinstance(val, list):
                                    order.extend((v, tool) for v in val if isinstance(v, str))
    return order


def creation_order_from_codex(transcript_dir: Path) -> list[tuple[str, str]]:
    """Codex CLI rollouts drive pascal-mcp via exec_command pipes, so node ids
    only appear inside tool-output text blobs. Scan outputs chronologically and
    take each node id's FIRST occurrence as its creation point (the final
    export_json blob repeats all ids, but by then they are already ordered)."""
    import re

    pat = re.compile(r"\b(?:wall|door|window|item|zone|slab|ceiling)_[a-z0-9]{6,}\b")
    order: list[tuple[str, str]] = []
    seen: set[str] = set()
    files = sorted(glob.glob(str(transcript_dir / "*.jsonl")))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = rec.get("payload") or {}
                if p.get("type") != "function_call_output":
                    continue
                out = p.get("output")
                if not isinstance(out, str):
                    out = json.dumps(out) if out else ""
                for m in pat.finditer(out):
                    nid = m.group(0)
                    if nid not in seen:
                        seen.add(nid)
                        order.append((nid, "codex"))
    return order


def rotated_footprint(center, dims, yaw_rad):
    """Plan-view corners under three.js R_y: local(+X)->(cos,-sin), local(+Z)->(sin,cos)."""
    w, _, d = dims
    cx, cz = center
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    corners = []
    for u, v in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)):
        corners.append((cx + u * c + v * s, cz + u * -s + v * c))
    return corners


def wall_child_world(walls, node):
    host = walls.get(node.get("wallId"))
    if host is None:
        return None
    (sx, sz), (ex, ez) = host["start"], host["end"]
    length = math.hypot(ex - sx, ez - sz) or 1.0
    ux, uz = (ex - sx) / length, (ez - sz) / length
    lx = float((node.get("position") or [0, 0, 0])[0])
    return (sx + ux * lx, sz + uz * lx), (ux, uz)


def draw_scene(ax, elements, upto, ref_env, highlight_last=True):
    ax.set_aspect("equal")
    ax.set_xlim(-0.6, ref_env["outer_x"] + 0.6)
    ax.set_ylim(-0.6, ref_env["outer_z"] + 0.6)
    ax.set_xticks([])
    ax.set_yticks([])
    for i, el in enumerate(elements[:upto]):
        new = highlight_last and (i == upto - 1)
        kind, node, walls = el["kind"], el["node"], el["walls"]
        if kind == "wall":
            (sx, sz), (ex, ez) = node["start"], node["end"]
            ax.plot([sx, ex], [sz, ez], color="#c2410c" if new else "#222222",
                    linewidth=6, solid_capstyle="projecting", zorder=2)
        elif kind in ("door", "window"):
            res = wall_child_world(walls, node)
            if res is None:
                continue
            (cx, cz), (ux, uz) = res
            half = float(node.get("width", 1.0)) / 2.0
            color = "#c2410c" if kind == "door" else "#1d4ed8"
            ax.plot([cx - ux * half, cx + ux * half], [cz - uz * half, cz + uz * half],
                    color="white", linewidth=7, solid_capstyle="butt", zorder=3)
            ax.plot([cx - ux * half, cx + ux * half], [cz - uz * half, cz + uz * half],
                    color="#dc2626" if new else color, linewidth=3.2, solid_capstyle="butt", zorder=4)
        elif kind == "item":
            pos = node.get("position") or [0, 0, 0]
            yaw = float((node.get("rotation") or [0, 0, 0])[1])  # radians per schema
            dims = (node.get("asset") or {}).get("dimensions", [0.5, 0.5, 0.5])
            corners = rotated_footprint((pos[0], pos[2]), dims, yaw)
            ax.add_patch(MplPolygon(corners, closed=True,
                                    facecolor="#fca5a5" if new else "#a7f3d0",
                                    edgecolor="#dc2626" if new else "#047857",
                                    linewidth=1.2 if new else 0.8, alpha=0.9, zorder=5))
            fx, fz = math.sin(yaw), math.cos(yaw)
            ax.annotate("", xy=(pos[0] + fx * 0.3, pos[2] + fz * 0.3), xytext=(pos[0], pos[2]),
                        arrowprops=dict(arrowstyle="->", color="#065f46", lw=1.0), zorder=6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, type=Path)
    ap.add_argument("--transcript-dir", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--gif", required=True, type=Path)
    ap.add_argument("--png", required=True, type=Path)
    ap.add_argument("--frame-ms", type=int, default=220)
    ap.add_argument("--transcript-format", choices=["claude", "codex"], default="claude")
    args = ap.parse_args()

    ref = yaml.safe_load(args.ref.read_text(encoding="utf-8"))
    scene = json.loads(args.scene.read_text(encoding="utf-8"))
    if set(scene.keys()) == {"json"}:
        scene = json.loads(scene["json"])
    nodes = scene["nodes"] if isinstance(scene["nodes"], dict) else {n["id"]: n for n in scene["nodes"]}
    walls = {nid: n for nid, n in nodes.items() if n.get("type") == "wall"}

    if args.transcript_format == "codex":
        order = creation_order_from_codex(args.transcript_dir)
    else:
        order = creation_order_from_transcript(args.transcript_dir)
    seen = set()
    elements = []
    for nid, tool in order:
        node = nodes.get(nid)
        if node is None or nid in seen:  # deleted later, or duplicate mention
            continue
        kind = node.get("type")
        if kind not in ("wall", "door", "window", "item"):
            continue
        seen.add(nid)
        elements.append({"kind": kind, "node": node, "walls": walls, "tool": tool})
    # anything in the final scene the transcript parse missed goes at the end
    for nid, node in nodes.items():
        if nid not in seen and node.get("type") in ("wall", "door", "window", "item"):
            elements.append({"kind": node["type"], "node": node, "walls": walls, "tool": "unparsed"})

    env = ref["envelope"]
    frames = []
    for upto in range(1, len(elements) + 1):
        fig, ax = plt.subplots(figsize=(7.5, 5.5), dpi=90)
        el = elements[upto - 1]
        label = (el["node"].get("asset") or {}).get("id", "") if el["kind"] == "item" else ""
        ax.set_title(f"PH-1 build — step {upto}/{len(elements)}: {el['kind']} {label}", fontsize=10)
        draw_scene(ax, elements, upto, env)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        frames.append(Image.open(buf).convert("P", palette=Image.ADAPTIVE))
    durations = [args.frame_ms] * len(frames)
    durations[-1] = 4000  # hold the finished build
    args.gif.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(args.gif, save_all=True, append_images=frames[1:], duration=durations, loop=0)
    print(f"wrote {args.gif} ({len(frames)} frames)")

    # Side-by-side: goal (reference intent) vs built (as stored).
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.8), dpi=110)
    goal_walls = {w["id"]: {"start": w["start"], "end": w["end"]} for w in ref["walls"]}
    def goal_opening(o, kind, width):
        host = goal_walls[o["wall"]]
        (sx, sz) = host["start"]
        (ex, ez) = host["end"]
        length = math.hypot(ex - sx, ez - sz) or 1.0
        ux, uz = (ex - sx) / length, (ez - sz) / length
        local_x = (o["center"][0] - sx) * ux + (o["center"][1] - sz) * uz
        return {"kind": kind, "walls": goal_walls,
                "node": {"wallId": o["wall"], "position": [local_x, 0, 0], "width": width}}

    goal_elements = (
        [{"kind": "wall", "node": {"start": w["start"], "end": w["end"]}, "walls": goal_walls} for w in ref["walls"]]
        + [goal_opening(d, "door", 0.9) for d in ref["doors"]]
        + [goal_opening(w, "window", 1.2) for w in ref["windows"]]
        + [{"kind": "item", "walls": goal_walls,
            "node": {"position": [it["center"][0], 0, it["center"][1]],
                     "rotation": [0, math.radians(it["yaw_deg"]), 0],
                     "asset": {"id": it["asset"], "dimensions": it["dims"]}}} for it in ref["items"]]
    )
    axes[0].set_title("Goal (reference intent)", fontsize=11)
    draw_scene(axes[0], goal_elements, len(goal_elements), env, highlight_last=False)
    axes[1].set_title("Built (as stored by the run — rotations rendered per schema)", fontsize=11)
    draw_scene(axes[1], elements, len(elements), env, highlight_last=False)
    fig.tight_layout()
    fig.savefig(args.png)
    print(f"wrote {args.png}")


if __name__ == "__main__":
    main()
