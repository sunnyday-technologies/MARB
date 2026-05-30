# -*- coding: utf-8 -*-
"""MARB v0.9 orientation grade — % of parts placed in the correct rotation.

We use AXIS-ALIGNED BBOX EXTENTS as a simple, robust orientation proxy: an
asymmetric part (e.g., a C-Beam 4080: 40 x 80 x 1000) placed in the wrong
rotation has different world-axis-aligned bbox dimensions than the reference
(40 x 80 x 1000 becomes 80 x 40 x 1000 under a 90deg rotation about Z). This
catches the dominant defect class the runs all showed -- the Z-posts and
similar parts placed with the C-channel facing the wrong way -- without
needing OCC principal-axes/OBB math.

Symmetric parts (all 3 bbox extents within 5%, or any 2 equal) are skipped
because their orientation is genuinely unobservable in geometry, e.g. round
wheels, square posts, cubic blocks.

BANDS (per asymmetric part)
  ALIGNED   -- bbox dims match the reference per-axis within 1 mm / 1% of axis
  ROTATED   -- dims match a permutation of the reference's (the part was
               rotated by some 90deg increment about an axis -- the channel-
               facing class of defect)
  WRONG     -- dims don't match any permutation; part is mis-sized or placed
               at an off-axis rotation
"""
from __future__ import annotations
import argparse, json, sys
from itertools import permutations
from pathlib import Path
import numpy as np
import cadquery as cq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "grader"))
from grade_native_step import derive_target, _looks_like_belt   # noqa: E402
from cadclaw.inventory import sig                                # noqa: E402
from marb_pose_metric import _kabsch, _match                     # noqa: E402

DEFAULT_SPEC = REPO / "tasks" / "m3_crete" / "m3_reference_assembly.yaml"
SYM_TOL = 0.05            # 5% -> two near-equal extents = symmetric in that axis pair
DIM_TOL_MM = 1.0          # match if |Δdim| <= max(1mm, 1% of dim)


def _label_map(spec_path: Path):
    sig_to_label, *_ = derive_target(spec_path)
    def label(d): return sig_to_label.get(d, "belt" if _looks_like_belt(d) else "other")
    return label


def _solids(step_path: Path, label):
    rows = []
    for s in cq.importers.importStep(str(step_path)).solids().vals():
        b = s.BoundingBox()
        dims = (b.xmax-b.xmin, b.ymax-b.ymin, b.zmax-b.zmin)
        c = np.array([(b.xmin+b.xmax)/2, (b.ymin+b.ymax)/2, (b.zmin+b.zmax)/2])
        rows.append({"label": label(sig(s)), "dims": dims, "c": c})
    return rows


def _is_symmetric(d) -> bool:
    """Two or more axes within SYM_TOL = orientation is geometrically unobservable."""
    a, b, c = sorted(d)
    return (b - a) / max(b, 1e-9) <= SYM_TOL or (c - b) / max(c, 1e-9) <= SYM_TOL


def _dims_match(a, b) -> bool:
    return all(abs(x - y) <= max(DIM_TOL_MM, 0.01 * max(x, y)) for x, y in zip(a, b))


def _classify(run_d, ref_d) -> str:
    if _dims_match(run_d, ref_d):
        return "aligned"
    for p in permutations(run_d):
        if _dims_match(p, ref_d):
            return "rotated"
    return "wrong"


def _align_and_match(ref, run):
    rc = np.array([r["c"] for r in run]); fc = np.array([r["c"] for r in ref])
    R, t = np.eye(3), fc.mean(0) - rc.mean(0)
    for _ in range(8):
        cur = (R @ rc.T).T + t
        pr = _match([(run[i]["label"], cur[i]) for i in range(len(run))],
                    [(r["label"], r["c"]) for r in ref])
        R, t = _kabsch(np.array([rc[i] for i, _ in pr]),
                       np.array([fc[j] for _, j in pr]))
    cur = (R @ rc.T).T + t
    return _match([(run[i]["label"], cur[i]) for i in range(len(run))],
                  [(r["label"], r["c"]) for r in ref])


def grade(ref_step: Path, run_step: Path, spec: Path):
    label = _label_map(spec)
    ref = _solids(ref_step, label); run = _solids(run_step, label)
    pairs = _align_and_match(ref, run)
    asymmetric = aligned = rotated = wrong = 0
    symmetric_skipped = 0
    by_label = {}
    for i, j in pairs:
        if _is_symmetric(ref[j]["dims"]):
            symmetric_skipped += 1; continue
        asymmetric += 1
        cls = _classify(run[i]["dims"], ref[j]["dims"])
        if cls == "aligned":  aligned  += 1
        elif cls == "rotated": rotated += 1
        else:                  wrong   += 1
        b = by_label.setdefault(ref[j]["label"], {"aligned": 0, "rotated": 0, "wrong": 0})
        b[cls] += 1
    n = max(asymmetric, 1)
    return {
        "asymmetric_graded": asymmetric,
        "symmetric_skipped": symmetric_skipped,
        "aligned_pct": round(100*aligned/n, 1),
        "rotated_pct": round(100*rotated/n, 1),
        "wrong_pct":   round(100*wrong/n, 1),
        "counts": {"aligned": aligned, "rotated": rotated, "wrong": wrong},
        "by_label": by_label,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MARB v0.9 orientation grader.")
    ap.add_argument("--ref", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--spec", default=str(DEFAULT_SPEC)); ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    res = grade(Path(a.ref), Path(a.run), Path(a.spec))
    body = json.dumps(res, indent=2)
    Path(a.json).write_text(body + "\n", encoding="utf-8") if a.json else print(body)
    print(f"\nORIENT  aligned={res['aligned_pct']}%  rotated(90deg)={res['rotated_pct']}%  "
          f"wrong={res['wrong_pct']}%   (graded={res['asymmetric_graded']}, "
          f"symmetric_skipped={res['symmetric_skipped']})")
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
