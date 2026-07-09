#!/usr/bin/env python3
"""MARB-A (architecture lane) grader for the Pascal MCP track.

Grades a Pascal `export_json` scene against a reference layout YAML
(tasks/pascal_house/ph1_reference_layout.yaml). This is the architecture-lane
analog of the mechanical board's STEP metrics:

  WALL     median wall-centerline midpoint error (mm)          ~ POS for structure
  OPEN     median door/window center error (mm)                ~ GAP at interfaces
  POS-A    median furnishing placement error (mm)              ~ POS
  ORIENT-A share of asymmetric furnishings with correct yaw    ~ ORIENT

Gates: BOM match, item-item AABB collisions, envelope. Room connectivity is
recorded as not_gated in v0.1.

Alignment: the run is aligned to the reference by translation (wall-bbox
center) plus the best of the four 90-degree plan rotations, mirroring the
best-fit rigid alignment on the mechanical board. No mirroring (plans are
chiral).

Usage:
  python grader/pascal_scene_grade.py --scene run_scene.json \
      --ref tasks/pascal_house/ph1_reference_layout.yaml [--json out.json]
  python grader/pascal_scene_grade.py --ref ... --self-test
  python grader/pascal_scene_grade.py --ref ... --emit-ref-scene out.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import yaml

MM = 1000.0


# ---------------------------------------------------------------- reference

def load_ref(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        ref = yaml.safe_load(fh)
    if ref.get("schema_version") != "pascal_house_reference.v0.1":
        raise SystemExit(f"unsupported reference schema: {ref.get('schema_version')}")
    return ref


# ------------------------------------------------------------------- scene

def _iter_nodes(scene: dict):
    nodes = scene.get("nodes", {})
    if isinstance(nodes, dict):
        return list(nodes.values())
    return list(nodes)  # tolerate array form


def _wall_frame(wall: dict):
    (sx, sz), (ex, ez) = wall["start"], wall["end"]
    dx, dz = ex - sx, ez - sz
    length = math.hypot(dx, dz)
    if length == 0:
        return (sx, sz), (0.0, 0.0), 0.0
    return (sx, sz), (dx / length, dz / length), length


def extract_scene(scene: dict) -> dict:
    """Pull plan-space walls, items, doors, windows out of an export_json dump."""
    walls, items, doors, windows = {}, [], [], []
    for node in _iter_nodes(scene):
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "wall" and "start" in node and "end" in node:
            walls[node.get("id", f"wall_{len(walls)}")] = node
    for node in _iter_nodes(scene):
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype in ("door", "window"):
            host = walls.get(node.get("wallId", ""))
            if host is None:  # fall back to the wall whose children list it
                nid = node.get("id")
                host = next(
                    (w for w in walls.values() if nid in (w.get("children") or [])),
                    None,
                )
            if host is None:
                continue
            origin, u, length = _wall_frame(host)
            pos = node.get("position") or [0, 0, 0]
            local_x = float(pos[0])
            if local_x == 0 and node.get("wallT") is not None:
                local_x = float(node["wallT"]) * length
            center = (origin[0] + u[0] * local_x, origin[1] + u[1] * local_x)
            rec = {"center": center, "width": float(node.get("width", 0.9))}
            (doors if ntype == "door" else windows).append(rec)
        elif ntype == "item":
            if node.get("wallId"):
                continue  # no wall-attached items in this program
            pos = node.get("position") or [0, 0, 0]
            rot = node.get("rotation") or [0, 0, 0]
            asset = (node.get("asset") or {}).get("id", "unknown")
            dims = (node.get("asset") or {}).get("dimensions", [0.5, 0.5, 0.5])
            items.append(
                {
                    "asset": asset,
                    "center": (float(pos[0]), float(pos[2])),
                    "yaw_deg": math.degrees(float(rot[1])) % 360.0,
                    "dims": [float(d) for d in dims],
                }
            )
    wall_segs = [
        {"start": tuple(map(float, w["start"])), "end": tuple(map(float, w["end"]))}
        for w in walls.values()
    ]
    return {"walls": wall_segs, "items": items, "doors": doors, "windows": windows}


# --------------------------------------------------------------- alignment

def _bbox(points):
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    return min(xs), min(zs), max(xs), max(zs)


def _rot(p, k, about):
    """Rotate plan point p by k*90 deg CCW about `about`."""
    x, z = p[0] - about[0], p[1] - about[1]
    for _ in range(k % 4):
        x, z = -z, x
    return (x + about[0], z + about[1])


def align_run(run: dict, ref: dict) -> tuple[dict, int]:
    """Translate run wall-bbox center onto the reference center, then pick the
    90-degree rotation that best matches wall midpoints. Returns (run', k)."""
    ref_bb = ref["envelope"]["centerline_bbox"]
    ref_center = ((ref_bb[0] + ref_bb[2]) / 2.0, (ref_bb[1] + ref_bb[3]) / 2.0)
    pts = [w["start"] for w in run["walls"]] + [w["end"] for w in run["walls"]]
    if not pts:
        return run, 0
    bb = _bbox(pts)
    run_center = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
    dx, dz = ref_center[0] - run_center[0], ref_center[1] - run_center[1]

    def shift(p):
        return (p[0] + dx, p[1] + dz)

    ref_mids = [
        (
            (w["start"][0] + w["end"][0]) / 2.0,
            (w["start"][1] + w["end"][1]) / 2.0,
        )
        for w in ref["walls"]
    ]

    def cost(k):
        total = 0.0
        for w in run["walls"]:
            m = shift(((w["start"][0] + w["end"][0]) / 2.0, (w["start"][1] + w["end"][1]) / 2.0))
            m = _rot(m, k, ref_center)
            total += min(math.dist(m, rm) for rm in ref_mids)
        return total

    best_k = min(range(4), key=cost)

    def xform(p):
        return _rot(shift(p), best_k, ref_center)

    out = {
        "walls": [{"start": xform(w["start"]), "end": xform(w["end"])} for w in run["walls"]],
        "items": [
            # A CCW plan rotation by 90*k subtracts 90*k from yaw under the
            # three.js front convention front(yaw) = (sin yaw, cos yaw).
            {**it, "center": xform(it["center"]), "yaw_deg": (it["yaw_deg"] - 90 * best_k) % 360}
            for it in run["items"]
        ],
        "doors": [{**d, "center": xform(d["center"])} for d in run["doors"]],
        "windows": [{**w, "center": xform(w["center"])} for w in run["windows"]],
    }
    return out, best_k


# ----------------------------------------------------------------- metrics

def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _greedy_match(ref_pts, run_pts, radius):
    """Greedy nearest pairing; returns list of (ref_idx, run_idx, dist)."""
    pairs = []
    used = set()
    order = sorted(
        (
            (math.dist(rp, sp), i, j)
            for i, rp in enumerate(ref_pts)
            for j, sp in enumerate(run_pts)
        )
    )
    matched_ref = set()
    for d, i, j in order:
        if i in matched_ref or j in used or d > radius:
            continue
        matched_ref.add(i)
        used.add(j)
        pairs.append((i, j, d))
    return pairs


def _pt_seg_dist(p, a, b):
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    l2 = dx * dx + dz * dz
    if l2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / l2))
    return math.dist(p, (ax + t * dx, az + t * dz))


def _sample_seg(start, end, n=9):
    return [
        (start[0] + (end[0] - start[0]) * i / (n - 1), start[1] + (end[1] - start[1]) * i / (n - 1))
        for i in range(n)
    ]


def grade_walls(ref, run, tol_mm):
    """Coverage-based, segmentation-invariant: a run may realize one reference
    wall as several collinear segments (create_room does this) and still score
    0. Each ref centerline is sampled and measured to the nearest run segment;
    run length lying near no ref centerline is reported as extra."""
    tol = tol_mm / MM
    run_segs = [(w["start"], w["end"]) for w in run["walls"]]
    ref_segs = [(w["start"], w["end"]) for w in ref["walls"]]
    errs, covered = [], 0
    for start, end in ref_segs:
        d = [
            min((_pt_seg_dist(p, a, b) for a, b in run_segs), default=float("inf"))
            for p in _sample_seg(start, end)
        ]
        finite = [x for x in d if x != float("inf")]
        errs.extend(finite)
        if finite and max(d) <= tol:
            covered += 1
    extra_len = 0.0
    for start, end in run_segs:
        pts = _sample_seg(start, end)
        far = sum(
            1
            for p in pts
            if min((_pt_seg_dist(p, a, b) for a, b in ref_segs), default=float("inf")) > tol
        )
        extra_len += math.dist(start, end) * far / len(pts)
    med = _median(errs)
    return {
        "median_mm": None if med is None else med * MM,
        "covered": covered,
        "ref_total": len(ref_segs),
        "run_total": len(run_segs),
        "extra_length_m": round(extra_len, 2),
    }


def grade_openings(ref_list, run_list, radius_m):
    ref_pts = [tuple(o["center"]) for o in ref_list]
    run_pts = [tuple(o["center"]) for o in run_list]
    pairs = _greedy_match(ref_pts, run_pts, radius_m)
    errs = [d * MM for _, _, d in pairs]
    return {
        "median_mm": _median(errs),
        "matched": len(pairs),
        "ref_total": len(ref_pts),
        "run_total": len(run_pts),
    }


def grade_items(ref_cfg, run_items, radius_m):
    ref_items = ref_cfg["items"]
    orient = ref_cfg.get("orientation", {})
    tol = float(orient.get("yaw_tolerance_deg", 15.0))
    sym = orient.get("symmetry_mod_deg", {}) or {}
    skip = set(orient.get("skip", []) or [])
    offsets = orient.get("yaw_offset_deg", {}) or {}

    pos_errs, orient_total, orient_ok = [], 0, 0
    per_asset = {}
    assets = sorted({r["asset"] for r in ref_items})
    matched_total = 0
    for asset in assets:
        r_pts = [tuple(r["center"]) for r in ref_items if r["asset"] == asset]
        r_yaws = [float(r["yaw_deg"]) for r in ref_items if r["asset"] == asset]
        s_sub = [it for it in run_items if it["asset"] == asset]
        s_pts = [tuple(it["center"]) for it in s_sub]
        pairs = _greedy_match(r_pts, s_pts, radius_m)
        matched_total += len(pairs)
        errs = [d * MM for _, _, d in pairs]
        pos_errs.extend(errs)
        a_orient = None
        if asset not in skip:
            mod = float(sym.get(asset, 360.0))
            off = float(offsets.get(asset, 0.0))
            ok = 0
            for i, j, _ in pairs:
                dyaw = (s_sub[j]["yaw_deg"] - (r_yaws[i] + off)) % mod
                if min(dyaw, mod - dyaw) <= tol:
                    ok += 1
            orient_total += len(pairs)
            orient_ok += ok
            a_orient = f"{ok}/{len(pairs)}"
        per_asset[asset] = {
            "matched": len(pairs),
            "ref": len(r_pts),
            "run": len(s_pts),
            "median_mm": _median(errs),
            "orient": a_orient,
        }
    return {
        "pos_median_mm": _median(pos_errs),
        "matched": matched_total,
        "ref_total": len(ref_items),
        "orient_pct": (100.0 * orient_ok / orient_total) if orient_total else None,
        "orient_gradeable": orient_total,
        "per_asset": per_asset,
    }


# ------------------------------------------------------------------- gates

def _item_aabb(item):
    w, _, d = item["dims"]
    k = round(item["yaw_deg"] / 90.0) % 2
    ex, ez = (w / 2.0, d / 2.0) if k == 0 else (d / 2.0, w / 2.0)
    cx, cz = item["center"]
    return cx - ex, cz - ez, cx + ex, cz + ez


def gate_collisions(items, min_pen=0.01):
    clips = []
    for i in range(len(items)):
        a = _item_aabb(items[i])
        for j in range(i + 1, len(items)):
            b = _item_aabb(items[j])
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oz = min(a[3], b[3]) - max(a[1], b[1])
            if ox > min_pen and oz > min_pen:
                clips.append((items[i]["asset"], items[j]["asset"], round(min(ox, oz), 3)))
    return clips


def gate_bom(ref_cfg, run):
    want = dict(ref_cfg["bom"]["items"])
    have = {}
    for it in run["items"]:
        have[it["asset"]] = have.get(it["asset"], 0) + 1
    diffs = {
        a: {"want": want.get(a, 0), "have": have.get(a, 0)}
        for a in sorted(set(want) | set(have))
        if want.get(a, 0) != have.get(a, 0)
    }
    doors_ok = len(run["doors"]) == ref_cfg["bom"]["doors"]
    windows_ok = len(run["windows"]) == ref_cfg["bom"]["windows"]
    return {
        "items_match": not diffs,
        "item_diffs": diffs,
        "doors": {"want": ref_cfg["bom"]["doors"], "have": len(run["doors"]), "ok": doors_ok},
        "windows": {"want": ref_cfg["bom"]["windows"], "have": len(run["windows"]), "ok": windows_ok},
    }


def gate_envelope(ref_cfg, run, tol_mm):
    pts = [w["start"] for w in run["walls"]] + [w["end"] for w in run["walls"]]
    if not pts:
        return {"ok": False, "reason": "no walls"}
    bb = _bbox(pts)
    rb = ref_cfg["envelope"]["centerline_bbox"]
    dw = abs((bb[2] - bb[0]) - (rb[2] - rb[0])) * MM
    dd = abs((bb[3] - bb[1]) - (rb[3] - rb[1])) * MM
    return {"ok": dw <= tol_mm and dd <= tol_mm, "dx_mm": round(dw, 1), "dz_mm": round(dd, 1)}


# ---------------------------------------------------- synthetic ref scene

def make_scene_from_ref(ref_cfg: dict) -> dict:
    """Emit a Pascal-shaped export_json scene realizing the reference exactly.
    Used for the grader self-test and as the stored answer-key scene."""
    nodes = {}
    t = ref_cfg["envelope"]["wall_thickness"]
    h = ref_cfg["envelope"]["wall_height"]
    wall_ids = {}
    for w in ref_cfg["walls"]:
        wid = f"wall_{w['id']}"
        wall_ids[w["id"]] = wid
        nodes[wid] = {
            "id": wid,
            "type": "wall",
            "start": list(w["start"]),
            "end": list(w["end"]),
            "thickness": t,
            "height": h,
            "children": [],
        }
    for kind in ("doors", "windows"):
        for o in ref_cfg[kind]:
            host = next(w for w in ref_cfg["walls"] if w["id"] == o["wall"])
            origin, u, _ = _wall_frame({"start": host["start"], "end": host["end"]})
            local_x = (o["center"][0] - origin[0]) * u[0] + (o["center"][1] - origin[1]) * u[1]
            oid = f"{kind[:-1]}_{o['id']}"
            node = {
                "id": oid,
                "type": kind[:-1],
                "wallId": wall_ids[o["wall"]],
                "position": [local_x, 0, 0],
                "width": 0.9 if kind == "doors" else 1.2,
                "height": 2.1 if kind == "doors" else 1.2,
            }
            nodes[oid] = node
            nodes[wall_ids[o["wall"]]]["children"].append(oid)
    for i, it in enumerate(ref_cfg["items"]):
        iid = f"item_{i}_{it['asset']}"
        nodes[iid] = {
            "id": iid,
            "type": "item",
            "position": [it["center"][0], 0, it["center"][1]],
            "rotation": [0, math.radians(it["yaw_deg"]), 0],
            "asset": {"id": it["asset"], "dimensions": it["dims"], "src": f"/items/{it['asset']}/model.glb"},
        }
    return {"nodes": nodes, "rootNodeIds": [], "collections": {}}


# -------------------------------------------------------------------- main

def grade(ref_cfg: dict, scene: dict) -> dict:
    tol = ref_cfg["tolerances"]
    run = extract_scene(scene)
    run, k = align_run(run, ref_cfg)
    result = {
        "schema_version": "pascal_house_grades.v0.1",
        "alignment_rotation_deg": 90 * k,
        "WALL": grade_walls(ref_cfg, run, tol["wall_match_mm"]),
        "OPEN": {
            "doors": grade_openings(ref_cfg["doors"], run["doors"], tol["open_match_m"]),
            "windows": grade_openings(ref_cfg["windows"], run["windows"], tol["open_match_m"]),
        },
        "ITEMS": grade_items(ref_cfg, run["items"], tol["item_match_m"]),
        "gates": {
            "bom": gate_bom(ref_cfg, run),
            "collisions": {"count": 0, "pairs": []},
            "envelope": gate_envelope(ref_cfg, run, tol["envelope_mm"]),
            "connectivity": {"status": "not_gated_v0.1"},
        },
    }
    clips = gate_collisions(run["items"])
    result["gates"]["collisions"] = {"count": len(clips), "pairs": clips}
    return result


def summarize(res: dict) -> str:
    it = res["ITEMS"]
    dw = res["OPEN"]
    lines = [
        f"alignment rotation : {res['alignment_rotation_deg']} deg",
        f"WALL median        : {res['WALL']['median_mm']} mm "
        f"({res['WALL']['covered']}/{res['WALL']['ref_total']} covered, "
        f"{res['WALL']['extra_length_m']} m extra)",
        f"OPEN doors median  : {dw['doors']['median_mm']} mm "
        f"({dw['doors']['matched']}/{dw['doors']['ref_total']})",
        f"OPEN windows median: {dw['windows']['median_mm']} mm "
        f"({dw['windows']['matched']}/{dw['windows']['ref_total']})",
        f"POS-A median       : {it['pos_median_mm']} mm "
        f"({it['matched']}/{it['ref_total']} items matched)",
        f"ORIENT-A aligned   : {it['orient_pct']}% of {it['orient_gradeable']} gradeable",
        f"gate BOM           : {'PASS' if res['gates']['bom']['items_match'] and res['gates']['bom']['doors']['ok'] and res['gates']['bom']['windows']['ok'] else 'FAIL'}",
        f"gate collisions    : {'PASS' if res['gates']['collisions']['count'] == 0 else 'FAIL'} ({res['gates']['collisions']['count']} clips)",
        f"gate envelope      : {'PASS' if res['gates']['envelope'].get('ok') else 'FAIL'}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--scene", type=Path)
    ap.add_argument("--json", type=Path, help="write full grades JSON here")
    ap.add_argument("--self-test", action="store_true", help="grade the reference against itself")
    ap.add_argument("--emit-ref-scene", type=Path, help="write the synthetic answer-key scene JSON")
    args = ap.parse_args()

    ref_cfg = load_ref(args.ref)

    if args.emit_ref_scene:
        scene = make_scene_from_ref(ref_cfg)
        args.emit_ref_scene.parent.mkdir(parents=True, exist_ok=True)
        args.emit_ref_scene.write_text(json.dumps(scene, indent=2), encoding="utf-8")
        print(f"wrote {args.emit_ref_scene}")
        if not (args.self_test or args.scene):
            return 0

    if args.self_test:
        scene = make_scene_from_ref(ref_cfg)
    elif args.scene:
        scene = json.loads(args.scene.read_text(encoding="utf-8"))
        # export_json returns {"json": "..."} at the tool level; accept both.
        if set(scene.keys()) == {"json"}:
            scene = json.loads(scene["json"])
    else:
        ap.error("need --scene, --self-test, or --emit-ref-scene")
        return 2

    res = grade(ref_cfg, scene)
    print(summarize(res))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    if args.self_test:
        ok = (
            (res["WALL"]["median_mm"] or 0) < 1.0
            and res["WALL"]["covered"] == res["WALL"]["ref_total"]
            and res["WALL"]["extra_length_m"] < 0.5
            and (res["ITEMS"]["pos_median_mm"] or 0) < 1.0
            and res["ITEMS"]["orient_pct"] == 100.0
            and res["gates"]["bom"]["items_match"]
            and res["gates"]["collisions"]["count"] == 0
            and res["gates"]["envelope"]["ok"]
        )
        print(f"\nself-test: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
