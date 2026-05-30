# -*- coding: utf-8 -*-
"""MARB v0.9 gap close-up — the *visual* proof that replaces the gap scatter.

A graph is the wrong evidence for "the parts don't touch." This renders the
SAME structural joint in the reference + all three runs, from ONE shared camera
at high magnification, with the grader's measured gap labelled on each panel.
The joint is auto-picked: the flush (should-be-0 mm) interface whose actual gap
diverges most across the runs — the clearest "should touch, but it doesn't."

Real exported geometry, identical camera, brand-styled (large type, navy edge
on white). Fusion produced no close-ups of its own, so every panel is a grader
render for an apples-to-apples row.

USAGE
  python build_marb_gap_closeup.py [--radius MM] [--view auto|front|side|iso]
"""
from __future__ import annotations
import argparse, sys, tempfile
from pathlib import Path

import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, apply_base                                    # noqa: E402
from marb_gap_metric import _label_map, _solids, _find_interfaces, _align_and_match, _bbox_gap  # noqa: E402
from cadclaw.render import render_step_to_png                                       # noqa: E402

REF = REPO / "tasks" / "m3_crete" / "m3_reference_round1.step"
SPEC = REPO / "tasks" / "m3_crete" / "m3_reference_assembly.yaml"
RUNS = [
    ("Reference", "answer key", REF),
    ("Claude Opus 4.7 · CadQuery", "claude-opus-4-7", REPO / "runs" / "claude_cadquery" / "export.step"),
    ("Claude Opus 4.7 · Fusion",   "claude-opus-4-7", REPO / "runs" / "claude_fusion" / "export.step"),
    ("GPT-5 Codex · CadQuery",     "gpt-5-codex",     REPO / "runs" / "codex_cadquery" / "export.step"),
]
OUT = REPO / "results" / "figures" / "marb_gap_closeup.png"

# A should-touch (flush, intended ~0 mm) interface where one run left a real
# positive gap is the clean story. NOTE: coincident/overlapping splices read as
# 0 mm gap (the interference gate catches those, not the gap metric), so the
# visible gap errors are mounting joints (wheel-to-rail, plate, standoff).
MIN_GAP_MM = 3.0     # require a clearly visible gap in at least one run
MAX_GAP_MM = 300.0   # ignore gross >0.3 m mis-placements / incidental-proximity outliers
# drive parts sit by loose proximity, not fastening; their "gaps" are not the structural story
DRIVE = {"m3_nema23_motor", "belt", "gt2_pulley_20t", "smooth_idler", "vs_belt_pinion"}
def _is_drive(lbl):
    return lbl in DRIVE or any(k in lbl for k in ("motor", "belt", "pulley", "idler", "pinion"))


def _contact(bbA, bbB):
    """Per-axis closest-approach center + the axiswise gap (mm). bb = (xmin,xmax,ymin,ymax,zmin,zmax)."""
    center, gaps = [], []
    for k in range(3):
        amin, amax = bbA[2*k], bbA[2*k+1]
        bmin, bmax = bbB[2*k], bbB[2*k+1]
        if amax < bmin:
            center.append((amax + bmin) / 2); gaps.append(bmin - amax)
        elif bmax < amin:
            center.append((bmax + amin) / 2); gaps.append(amin - bmax)
        else:
            center.append((max(amin, bmin) + min(amax, bmax)) / 2); gaps.append(0.0)
    return np.array(center), np.array(gaps)


def pick_joint(ref_solids, runs_solids, maps):
    """Return (i, j, per_run_actual, gap_axis) for the flush ref interface whose
    actual gap diverges most across the runs."""
    best = None
    for i, j, intended in _find_interfaces(ref_solids):
        if intended > 0.5:                        # must be a should-touch (flush) joint
            continue
        if _is_drive(ref_solids[i]["label"]) or _is_drive(ref_solids[j]["label"]):
            continue                              # skip loosely-placed drive parts
        actuals = []
        ok = True
        for rs, m in zip(runs_solids, maps):
            ri, rj = m.get(i), m.get(j)
            if ri is None or rj is None:
                ok = False; break
            actuals.append(_bbox_gap(rs[ri]["bb"], rs[rj]["bb"]))
        if not ok:
            continue
        worst = max(actuals[1:])                  # most-open RUN (panel 0 is the reference)
        if not (MIN_GAP_MM <= worst <= MAX_GAP_MM):
            continue
        if best is None or worst > best[0]:
            best = (worst, i, j, actuals)
    if best is None:
        raise SystemExit("no qualifying flush joint with a visible gap found")
    _, i, j, actuals = best
    return i, j, actuals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=float, default=None, help="focus radius mm (default: from max gap)")
    ap.add_argument("--view", default="auto", help="auto|front|side|top|iso")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--list", action="store_true", help="list candidate joints and exit")
    a = ap.parse_args(argv)

    label = _label_map(SPEC)
    ref_solids = _solids(REF, label)
    runs_solids = [_solids(p, label) for _, _, p in RUNS]
    # ref->run index maps (reference maps to itself by identity for panel 0)
    maps = [{k: k for k in range(len(ref_solids))}] + \
           [_align_and_match(ref_solids, rs) for rs in runs_solids[1:]]

    if a.list:
        cands = []
        for i, j, intended in _find_interfaces(ref_solids):
            if intended > 0.5:
                continue
            acts, ok = [], True
            for rs, m in zip(runs_solids, maps):
                ri, rj = m.get(i), m.get(j)
                if ri is None or rj is None:
                    ok = False; break
                acts.append(round(_bbox_gap(rs[ri]["bb"], rs[rj]["bb"]), 1))
            if ok and max(acts[1:]) >= MIN_GAP_MM:
                cands.append((max(acts[1:]), ref_solids[i]["label"], ref_solids[j]["label"], acts))
        cands.sort(reverse=True)
        print(f"{'maxgap':>7}  {'pair':40}  per-run [ref, cq, fusion, codex]")
        for mx, la, lb, acts in cands[:30]:
            print(f"{mx:7.1f}  {la+' <-> '+lb:40}  {acts}")
        return 0

    i, j, actuals = pick_joint(ref_solids, runs_solids, maps)   # actuals aligned with RUNS (panel 0 = reference)
    lbl_i, lbl_j = ref_solids[i]["label"], ref_solids[j]["label"]
    worst = max(actuals[1:])
    # gap axis (from the most-open run) and a fixed shared focus radius
    open_run = int(np.argmax(actuals))
    rs = runs_solids[open_run]; m = maps[open_run]
    _, gaps = _contact(rs[m[i]]["bb"], rs[m[j]]["bb"])
    gap_axis = int(np.argmax(gaps))
    radius = a.radius or max(20.0, 1.6 * worst)
    view = a.view
    if view == "auto":
        view = {0: "front", 1: "side", 2: "front"}[gap_axis]   # perpendicular to the gap
    print(f"joint: {lbl_i} <-> {lbl_j} | actual gaps {[round(x,1) for x in actuals]} mm "
          f"| axis={'XYZ'[gap_axis]} | view={view} | radius={radius:.0f}mm")

    tmp = Path(tempfile.mkdtemp(prefix="marb_gap_"))
    panels = []
    for k, (name, _, path) in enumerate(RUNS):
        rs, mp = runs_solids[k], maps[k]
        ri, rj = mp[i], mp[j]
        center, _ = _contact(rs[ri]["bb"], rs[rj]["bb"])
        png = tmp / f"panel_{k}.png"
        render_step_to_png(str(path), str(png), width=720, height=720, view=view,
                           focus_center=tuple(center), focus_radius=radius,
                           tessellation_tol=0.3)
        panels.append((name, actuals[k], png))

    # ---- composite: navy edge band on white, large type ----
    apply_base()
    n = len(panels)
    W, H = 1600, 760
    fig = plt.figure(figsize=(W/100, H/100), dpi=100)
    fig.patch.set_facecolor(COLORS["navy"])               # navy edge
    inner = fig.add_axes([0.012, 0.016, 0.976, 0.968]); inner.axis("off")
    inner.add_patch(Rectangle((0, 0), 1, 1, facecolor=COLORS["white"], zorder=0))

    fig.text(0.5, 0.94, "THE SAME FRAME JOINT, FOUR WAYS", fontproperties=NUL,
             fontsize=27, color=COLORS["navy"], ha="center")
    fig.text(0.5, 0.885,
             f"{lbl_i.replace('_',' ')} to {lbl_j.replace('_',' ')}. Target gap 0 mm. "
             f"Identical camera and magnification.",
             fontsize=16, color=COLORS["ink_dim"], ha="center", style="italic")

    pad = 0.012
    pw = (1.0 - pad*(n+1)) / n
    for k, (name, gap, png) in enumerate(panels):
        x0 = pad + k*(pw + pad)
        ax = fig.add_axes([x0, 0.165, pw, 0.66]); ax.axis("off")
        ax.imshow(mpimg.imread(png))
        is_ref = (k == 0)
        is_worst = (not is_ref and gap == max(actuals))
        # gap chip
        chip = COLORS["green"] if is_ref else (COLORS["navy"] if not is_worst else "#c0392b")
        txtcol = COLORS["ink"] if is_ref else "white"
        fig.text(x0 + pw/2, 0.115, f"{gap:.1f} mm", fontproperties=NUL, fontsize=30,
                 ha="center", color=COLORS["navy"] if not is_worst else "#c0392b")
        fig.text(x0 + pw/2, 0.065, name, fontsize=14.5, ha="center",
                 color=COLORS["ink"], weight="bold")
        fig.text(x0 + pw/2, 0.035, "flush (target)" if is_ref else "gap error", fontsize=12.5,
                 ha="center", color=COLORS["grey"], style="italic")

    fig.savefig(a.out, dpi=100, facecolor=COLORS["navy"]); print("saved:", a.out)
    from PIL import Image; print("size:", Image.open(a.out).size)
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
