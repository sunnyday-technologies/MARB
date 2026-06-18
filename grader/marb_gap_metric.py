# -*- coding: utf-8 -*-
"""MARB v0.9 functional-gap grade — the heart of the v0.9 scoring (see
../MARB_SCORING.md §3).

For each interface in the reference (pair of nearby parts), the reference's
own gap *is* the intended gap by construction (per spec: flush_adjacency = 0
mm, v-slot handoff = 1 mm running clearance, etc.). For each run we measure
the same pair's gap and grade the error.

What this distinguishes (your earlier point): a functional SYSTEM — parts
that bolt together touch (~0 mm); parts that move have clearance (~1 mm) — vs
a monolithic blob or a pile of parts. Frame-invariant by construction: a gap
between two parts in the same assembly is intrinsic, no global alignment
needed to measure it (alignment is still used for label-matching).

GAP CONVENTION
  bbox-min-distance, clamped at 0 (interference handled by the separate
  buildability gate). True solid-distance via BRepExtrema is more accurate
  and is the obvious next refinement, but for extrusions+plates the bbox
  proxy is good to ~ mm at the scales we care about.

USAGE
  python marb_gap_metric.py --ref <ref.step> --run <run.step> [--json out.json]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
import cadquery as cq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "grader"))
from grade_native_step import derive_target, _looks_like_belt   # noqa: E402
from cadclaw.inventory import sig                                # noqa: E402
from marb_pose_metric import _kabsch, _match                     # noqa: E402

from _answer_key import require_answer_key  # noqa: E402

DEFAULT_SPEC = REPO / "tasks" / "m3_crete" / "m3_reference_assembly.yaml"
INTERFACE_THRESH_MM = 5.0      # pairs of parts within this bbox-gap are interfaces
GAP_BANDS = (0.1, 1.0, 5.0)    # error bands in mm: exact, close, ok


def _label_map(spec_path: Path):
    sig_to_label, *_ = derive_target(spec_path)
    def label(d): return sig_to_label.get(d, "belt" if _looks_like_belt(d) else "other")
    return label


def _solids(step_path: Path, label):
    rows = []
    for s in cq.importers.importStep(str(step_path)).solids().vals():
        b = s.BoundingBox()
        rows.append({
            "label": label(sig(s)),
            "bb": (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax),
            "c": np.array([(b.xmin+b.xmax)/2, (b.ymin+b.ymax)/2, (b.zmin+b.zmax)/2]),
        })
    return rows


def _bbox_gap(a, b) -> float:
    """Axis-aligned bbox min-distance; 0 when bboxes touch or overlap."""
    dx = max(0.0, a[0]-b[1], b[0]-a[1])
    dy = max(0.0, a[2]-b[3], b[2]-a[3])
    dz = max(0.0, a[4]-b[5], b[4]-a[5])
    return float(np.sqrt(dx*dx + dy*dy + dz*dz))


def _find_interfaces(rows, thresh=INTERFACE_THRESH_MM):
    """Pairs (i,j,intended_gap_mm) where the ref bboxes are within `thresh`."""
    out, n = [], len(rows)
    for i in range(n):
        for j in range(i+1, n):
            g = _bbox_gap(rows[i]["bb"], rows[j]["bb"])
            if g <= thresh:
                out.append((i, j, g))
    return out


def _classify(intended_mm: float) -> str:
    if intended_mm <= 0.5: return "attachment"
    if intended_mm <= 2.5: return "motion"
    return "standoff"


def _align_and_match(ref_rows, run_rows):
    rc = np.array([r["c"] for r in run_rows]); fc = np.array([r["c"] for r in ref_rows])
    R, t = np.eye(3), fc.mean(0) - rc.mean(0)
    for _ in range(8):
        cur = (R @ rc.T).T + t
        pr = _match([(run_rows[i]["label"], cur[i]) for i in range(len(run_rows))],
                    [(r["label"], r["c"]) for r in ref_rows])
        R, t = _kabsch(np.array([rc[i] for i, _ in pr]),
                       np.array([fc[j] for _, j in pr]))
    cur = (R @ rc.T).T + t
    pr = _match([(run_rows[i]["label"], cur[i]) for i in range(len(run_rows))],
                [(r["label"], r["c"]) for r in ref_rows])
    return {j: i for i, j in pr}    # ref_idx -> run_idx


def grade(ref_step: Path, run_step: Path, spec: Path):
    label = _label_map(spec)
    ref = _solids(ref_step, label); run = _solids(run_step, label)
    ref2run = _align_and_match(ref, run)
    interfaces = _find_interfaces(ref)
    rows = []
    for i, j, intended in interfaces:
        ri, rj = ref2run.get(i), ref2run.get(j)
        if ri is None or rj is None:
            rows.append({"labels": [ref[i]["label"], ref[j]["label"]],
                         "intended": intended, "actual": None, "err": None,
                         "type": _classify(intended)})
            continue
        actual = _bbox_gap(run[ri]["bb"], run[rj]["bb"])
        rows.append({"labels": [ref[i]["label"], ref[j]["label"]],
                     "intended": intended, "actual": actual,
                     "err": abs(actual - intended), "type": _classify(intended)})

    def bands(items):
        errs = np.array([r["err"] for r in items if r["err"] is not None])
        n = len(errs)
        if n == 0:
            return {"n": 0, "median_mm": None, "bands_pct": {f"<= {t} mm": 0.0 for t in GAP_BANDS}}
        return {"n": int(n), "median_mm": round(float(np.median(errs)), 2),
                "bands_pct": {f"<= {t} mm": round(float((errs <= t).sum())*100/n, 1) for t in GAP_BANDS}}

    return {
        "interface_threshold_mm": INTERFACE_THRESH_MM,
        "total_interfaces": len(interfaces),
        "by_type": {
            "attachment": bands([r for r in rows if r["type"] == "attachment"]),
            "motion":     bands([r for r in rows if r["type"] == "motion"]),
            "standoff":   bands([r for r in rows if r["type"] == "standoff"]),
        },
        "overall": bands(rows),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MARB v0.9 functional-gap grader.")
    ap.add_argument("--ref", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--spec", default=str(DEFAULT_SPEC)); ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    require_answer_key(Path(a.ref), Path(a.spec))
    res = grade(Path(a.ref), Path(a.run), Path(a.spec))
    body = json.dumps(res, indent=2)
    Path(a.json).write_text(body + "\n", encoding="utf-8") if a.json else print(body)
    print(f"\nGAP grade  overall median={res['overall']['median_mm']}mm  "
          f"<=1mm={res['overall']['bands_pct']['<= 1.0 mm']}%  "
          f"interfaces={res['total_interfaces']} "
          f"(attach={res['by_type']['attachment']['n']} "
          f"motion={res['by_type']['motion']['n']} "
          f"standoff={res['by_type']['standoff']['n']})")
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
