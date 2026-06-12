# -*- coding: utf-8 -*-
"""MARB sighted-cells figure — "what seeing got you", 3 panels, one camera.

GOAL (answer key) | the blind 80B text model's best build | the sighted 32B
vision model's best build (goal image in-loop on turn 1). The sighted build's
near-empty frame IS the finding: at local scale, giving the model eyes made
the machine smaller, not better. Same idiom as build_marb_local_3panel.py.

USAGE
  python build_marb_sighted_3panel.py [--view iso] [--out <png>]
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

# (heading, sub-line, STEP path, chip). Panel 0 is always the goal.
PANELS = [
    ("GOAL", "M3-CRETE reference (answer key)", REF, "~100 parts"),
    ("BLIND · TEXT 80B", "qwen3-coder-next, no image, n=9 cohort best",
     Path("D:/tmp/runs/local_batch_A/run_08/export.step"), "110 solids"),
    ("SIGHTED · VISION 32B", "qwen3-vl, goal image on turn 1, n=5 best",
     Path("D:/tmp/runs/sighted_qwen3vl/run_04/export.step"), "17 solids"),
]
OUT = REPO / "results" / "figures" / "marb_sighted_3panel.png"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", default="iso", help="iso|front|side|top")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    for _, _, p, _ in PANELS:
        if not Path(p).exists():
            raise SystemExit(f"missing STEP: {p}")

    tmp = Path(tempfile.mkdtemp(prefix="marb_sighted3_"))
    rendered = []
    for k, (head, sub, path, chip) in enumerate(PANELS):
        png = tmp / f"panel_{k}.png"
        print(f"rendering {head}: {path}")
        kw = dict(width=900, height=900, view=a.view, tessellation_tol=0.4)
        if k > 0:
            kw["use_step_colors"] = False
            kw["default_color"] = (0.62, 0.68, 0.74)   # cool slate for the attempts
        render_step_to_png(str(path), str(png), **kw)
        rendered.append((head, sub, chip, png))

    apply_base()
    n = len(rendered)
    W, H = 1680, 760
    fig = plt.figure(figsize=(W/100, H/100), dpi=100)
    fig.patch.set_facecolor(COLORS["navy"])
    inner = fig.add_axes([0.011, 0.014, 0.978, 0.972]); inner.axis("off")
    inner.add_patch(Rectangle((0, 0), 1, 1, facecolor=COLORS["white"], zorder=0))

    fig.text(0.5, 0.945, "WE GAVE THE MODEL EYES. IT BUILT LESS.", fontproperties=NUL,
             fontsize=27, color=COLORS["navy"], ha="center")
    fig.text(0.5, 0.892,
             "Same kit, same grader, identical iso camera. The blind text model places ~100 parts; "
             "the sighted vision model places 17 — and farther from home.",
             fontsize=15.5, color=COLORS["ink_dim"], ha="center", style="italic")

    pad = 0.012
    pw = (1.0 - pad*(n+1)) / n
    for k, (head, sub, chip, png) in enumerate(rendered):
        x0 = pad + k*(pw + pad)
        ax = fig.add_axes([x0, 0.205, pw, 0.64]); ax.axis("off")
        ax.imshow(mpimg.imread(png))
        is_goal = (k == 0)
        fig.text(x0 + pw/2, 0.150, head, fontproperties=NUL, fontsize=21,
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
