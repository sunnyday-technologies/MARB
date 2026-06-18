# -*- coding: utf-8 -*-
"""MARB v0.9 positional-accuracy grader (LOCATED).

Grades how well a run placed parts vs the answer key, under the tight digital
standard (see ../MARB_SCORING.md). For each part: position error, binned into
fixed tight bands; reported as median error + band %, at two scales:

  * ABSOLUTE (system) — after a best-fit rigid alignment (Kabsch/ICP), since runs
    are authored in different global frames.
  * RELATIVE (neighbor) — each part's offset to its nearest neighbour vs the
    reference; frame-invariant, isolates local structural correctness.

Tolerance is EXACT by design (<=5 mm = located, <0.01 mm = exact); never loosened.

USAGE
  .venv\\Scripts\\python.exe benchmarks\\m3_ai_assembly\\scripts\\marb_pose_metric.py \\
    --ref examples\\m3_crete\\build\\m3_reference_round1.step \\
    --run <export.step> [--json out.json]

OPEN WORK (see ../MARB_SCORING.md §7), kept as stubs so they're improved in place:
  * orientation_error()  — per-part principal-axis rotation delta (median deg, %<=5deg)
  * functional_gap_grade() — classify each interface attachment(0mm)/motion(1-2mm)
                             and grade actual-vs-intended gap (the primary v1 score)
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment
import cadquery as cq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "grader"))
from grade_native_step import derive_target, _looks_like_belt   # noqa: E402
from cadclaw.inventory import sig                                # noqa: E402

from _answer_key import require_answer_key  # noqa: E402

DEFAULT_SPEC = REPO / "tasks" / "m3_crete" / "m3_reference_assembly.yaml"
POS_BANDS = (0.01, 1.0, 5.0, 25.0, 50.0, 100.0)   # mm; 5 mm == "located"


def _label_map(spec_path: Path):
    sig_to_label, *_ = derive_target(spec_path)
    def label(d):
        return sig_to_label.get(d, "belt" if _looks_like_belt(d) else "other")
    return label


def parts(step_path: Path, label):
    """[(label, bbox-center xyz)] for every solid in the STEP."""
    rows = []
    for s in cq.importers.importStep(str(step_path)).solids().vals():
        b = s.BoundingBox()
        rows.append((label(sig(s)),
                     np.array([(b.xmin+b.xmax)/2, (b.ymin+b.ymax)/2, (b.zmin+b.zmax)/2])))
    return rows


def _kabsch(P, Q):
    Pc, Qc = P - P.mean(0), Q - Q.mean(0)
    U, _, Vt = np.linalg.svd(Pc.T @ Qc)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, Q.mean(0) - R @ P.mean(0)


def _match(run, ref):
    """Per-label optimal assignment of run solids to reference solids."""
    br, bf = defaultdict(list), defaultdict(list)
    for i, (l, _) in enumerate(run): br[l].append(i)
    for j, (l, _) in enumerate(ref): bf[l].append(j)
    pairs = []
    for l, ri in br.items():
        rj = bf.get(l, [])
        if not rj:
            continue
        C = np.array([[np.linalg.norm(run[i][1]-ref[j][1]) for j in rj] for i in ri])
        a, b = linear_sum_assignment(C)
        pairs += [(ri[x], rj[y]) for x, y in zip(a, b)]
    return pairs


def _bands(errs):
    a = np.array(errs)
    n = max(len(a), 1)
    return {f"<= {t} mm": round(float((a <= t).sum())*100/n, 1) for t in POS_BANDS}, \
           round(float(np.median(a)), 1) if len(a) else None


def grade(ref_rows, run_rows):
    rc = np.array([c for _, c in run_rows]); fc = np.array([c for _, c in ref_rows])
    R, t = np.eye(3), fc.mean(0) - rc.mean(0)
    for _ in range(8):                       # ICP-lite: align -> match -> refit
        cur = (R @ rc.T).T + t
        pr = _match([(run_rows[i][0], cur[i]) for i in range(len(run_rows))], ref_rows)
        R, t = _kabsch(np.array([rc[i] for i, _ in pr]), np.array([fc[j] for _, j in pr]))
    cur = (R @ rc.T).T + t
    pr = _match([(run_rows[i][0], cur[i]) for i in range(len(run_rows))], ref_rows)
    absr = [float(np.linalg.norm(cur[i]-fc[j])) for i, j in pr]
    relr = []
    for i, j in pr:                          # neighbour-relative (frame-invariant)
        d = np.linalg.norm(fc - fc[j], axis=1); d[j] = 1e9; k = int(d.argmin())
        ki = [ii for ii, jj in pr if jj == k]
        if ki:
            relr.append(float(np.linalg.norm((cur[i]-cur[ki[0]]) - (fc[j]-fc[k]))))
    ab, am = _bands(absr); rb, rm = _bands(relr)
    return {"matched": len(pr), "total_run": len(run_rows),
            "absolute": {"median_mm": am, "bands_pct": ab},
            "relative": {"median_mm": rm, "bands_pct": rb}}


# --- stubs for the next iteration (see MARB_SCORING.md) ---------------------
def orientation_error(ref_rows, run_rows):   # TODO: principal-axis rotation delta
    raise NotImplementedError("orientation metric — MARB_SCORING.md §7.2")


def functional_gap_grade(ref_step, run_step):  # TODO: attachment(0)/motion(1-2mm)
    raise NotImplementedError("functional-gap grade — MARB_SCORING.md §3/§7.1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MARB v0.9 positional-accuracy grader.")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    require_answer_key(Path(a.spec), Path(a.ref))
    label = _label_map(Path(a.spec))
    res = grade(parts(Path(a.ref), label), parts(Path(a.run), label))
    body = json.dumps(res, indent=2)
    Path(a.json).write_text(body + "\n", encoding="utf-8") if a.json else print(body)
    print(f"\nLOCATED  absolute median={res['absolute']['median_mm']}mm  "
          f"relative median={res['relative']['median_mm']}mm")
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
