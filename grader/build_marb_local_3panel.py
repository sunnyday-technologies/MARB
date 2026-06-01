# -*- coding: utf-8 -*-
"""MARB local-anchor track — the 3-panel "goal vs best local builds" figure.

The local/anchor track plants a real point on the capability curve: an 80B
open-weight model (qwen3-coder-next, text-only, on a local AI supercomputer) building the
M3-CRETE assembly blind in CadQuery. This figure shows, side by side from ONE
shared iso camera, the GOAL (answer-key reference) next to the two best local
builds — the mechanics-v2 cohort winner and the lean-v5 cohort winner.

The honest headline is the caveat: the local builds are loosely-placed parts at
roughly the right part COUNT, not a rigid box-beam-jointed frame. The figure is
meant to make that legible at a glance, not to flatter the local model.

Real exported geometry, identical camera, brand-styled (navy edge band on white,
large type) — the same idiom as build_marb_gap_closeup.py.

USAGE
  python build_marb_local_3panel.py [--view iso] [--out <png>]
"""
from __future__ import annotations
import argparse, sys, tempfile
from pathlib import Path

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, apply_base                # noqa: E402
from cadclaw.render import render_step_to_png                 # noqa: E402

REF = REPO / "tasks" / "m3_crete" / "m3_reference_round1.step"
LOCAL = REPO / "runs" / "local_batch_A"

# (heading, sub-line, STEP path, solid-count chip). Panel 0 is always the goal.
PANELS = [
    ("GOAL", "M3-CRETE reference (answer key)", REF, "~100 parts"),
    ("BEST LOCAL BUILD", "qwen3-coder-next 80B · mechanics v2", LOCAL / "run_08" / "export.step", "110 solids"),
    ("LEAN v5 BEST", "qwen3-coder-next 80B · lean v5", LOCAL / "run_26" / "export.step", "102 solids"),
]
OUT = REPO / "results" / "figures" / "marb_local_3panel.png"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", default="iso", help="iso|front|side|top")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    for _, _, p, _ in PANELS:
        if not Path(p).exists():
            raise SystemExit(f"missing STEP: {p}")

    tmp = Path(tempfile.mkdtemp(prefix="marb_local3_"))
    rendered = []
    for k, (head, sub, path, chip) in enumerate(PANELS):
        png = tmp / f"panel_{k}.png"
        print(f"rendering {head}: {path}")
        # Goal panel uses the brand green default; local builds use a cool grey so
        # the eye reads "reference vs attempt" without a legend.
        kw = dict(width=900, height=900, view=a.view, tessellation_tol=0.4)
        if k > 0:
            kw["use_step_colors"] = False
            kw["default_color"] = (0.62, 0.68, 0.74)   # cool slate for the local attempts
        render_step_to_png(str(path), str(png), **kw)
        rendered.append((head, sub, chip, png))

    # ---- composite: navy edge band on white, large type ----
    apply_base()
    n = len(rendered)
    W, H = 1680, 760
    fig = plt.figure(figsize=(W/100, H/100), dpi=100)
    fig.patch.set_facecolor(COLORS["navy"])
    inner = fig.add_axes([0.011, 0.014, 0.978, 0.972]); inner.axis("off")
    inner.add_patch(Rectangle((0, 0), 1, 1, facecolor=COLORS["white"], zorder=0))

    fig.text(0.5, 0.945, "AN 80B LOCAL MODEL vs THE GOAL", fontproperties=NUL,
             fontsize=27, color=COLORS["navy"], ha="center")
    fig.text(0.5, 0.892,
             "Blind CadQuery builds on a local AI supercomputer. Right part COUNT, loosely-placed "
             "parts — not a rigid jointed frame. Identical iso camera.",
             fontsize=15.5, color=COLORS["ink_dim"], ha="center", style="italic")

    pad = 0.012
    pw = (1.0 - pad*(n+1)) / n
    for k, (head, sub, chip, png) in enumerate(rendered):
        x0 = pad + k*(pw + pad)
        ax = fig.add_axes([x0, 0.205, pw, 0.64]); ax.axis("off")
        ax.imshow(mpimg.imread(png))
        is_goal = (k == 0)
        fig.text(x0 + pw/2, 0.150, head, fontproperties=NUL, fontsize=22,
                 ha="center", color=COLORS["green_dk"] if is_goal else COLORS["navy"])
        fig.text(x0 + pw/2, 0.097, chip, fontproperties=NUL, fontsize=17,
                 ha="center", color=COLORS["ink"])
        fig.text(x0 + pw/2, 0.052, sub, fontsize=13.5, ha="center",
                 color=COLORS["grey"], style="italic")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=100, facecolor=COLORS["navy"]); print("saved:", a.out)
    from PIL import Image; print("size:", Image.open(a.out).size)
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
