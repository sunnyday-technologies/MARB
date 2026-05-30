# -*- coding: utf-8 -*-
"""MARB v0.9 scatter: GAP median (functional headline) vs build time.
Lower gap = closer to the intended interface gaps (0 mm bolted, ~1 mm motion).
Rendered through brand_figs -- large legible type, minimal words.
"""
import json, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, SIZES, apply_base   # noqa: E402

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    REPO / "results" / "marb_v0_9_grades.json"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else \
    REPO / "results" / "figures" / "hero_score_vs_time.png"

# build time per run (minutes), from run logs (see results/comparison_claude_tracks.md)
TIMES = {"Claude · CadQuery": 48.8, "Claude · Fusion": 33.7, "Codex · CadQuery": 13.0}
HIGHLIGHT = "Claude · CadQuery"   # the gap-leader gets the green marker

apply_base()
runs = json.loads(DATA.read_text(encoding="utf-8"))["runs"]

W, H = 1400, 820
fig = plt.figure(figsize=(W/100, H/100), dpi=100)
fig.patch.set_facecolor(COLORS["navy"])                     # navy edge band
from matplotlib.patches import Rectangle as _Rect
fig.add_artist(_Rect((0.013, 0.016), 0.974, 0.968, transform=fig.transFigure,
                     facecolor=COLORS["white"], zorder=0))   # white interior
ax = fig.add_axes([0.10, 0.155, 0.855, 0.66]); ax.set_facecolor(COLORS["white"])

# target line at 0 mm (the answer key)
ax.axhline(0, ls=(0, (7, 5)), color=COLORS["green"], lw=2.8, zorder=1)
ax.text(2.5, 0.18, "0 mm = the answer key (target)",
        color="#3d5a00", fontsize=14, ha="left", va="bottom", weight="bold")

# gap value + spread per cell: `_agg` (stats schema) when present, else flat dot.
def _gap(name):
    r = runs[name]
    if "_agg" in r and "gap_median_mm" in r["_agg"]:
        s = r["_agg"]["gap_median_mm"]
        return s.get("median"), s.get("std"), s.get("values") or []
    return r["gap"]["overall"]["median_mm"], None, []

# points (one cell each; if multiple seeds, draw the cluster + a std cross)
for name, t in TIMES.items():
    if name not in runs:
        continue
    g, std, vals = _gap(name)
    is_best = (name == HIGHLIGHT)
    c = COLORS["green"] if is_best else COLORS["navy"]
    edge = "#3d5a00" if is_best else "white"
    if len(vals) > 1:                                  # cluster: faint per-seed dots...
        ax.scatter([t] * len(vals), vals, s=90, color=c, edgecolor="none",
                   alpha=0.35, zorder=2)
        ax.errorbar([t], [g], yerr=[std], color=c, elinewidth=2.2, capsize=6,
                    capthick=2.2, zorder=3)            # ...+ a vertical std bar at the median
    ax.scatter([t], [g], s=320, color=c, edgecolor=edge, linewidth=2.5, zorder=4)
    disp = {"Claude · CadQuery": "Claude Opus 4.7 / CadQuery",
            "Claude · Fusion":   "Claude Opus 4.7 / Fusion",
            "Codex · CadQuery":  "GPT-5 Codex / CadQuery"}.get(name, name.replace(chr(0xb7), '/'))
    n = runs[name].get("_n")
    spread = f"  ±{std:.1f}" if std else ""
    tag = f"  (n={n})" if n and n > 1 else ""
    right = t > 0.62 * 60                          # flip label left near the right edge
    ax.annotate(f"{disp}{tag}\n{g:.1f} mm{spread}  ·  {t:.0f} min",
                (t, g), textcoords="offset points",
                xytext=(-14 if right else 14, 14),
                ha="right" if right else "left",
                fontsize=13, color=COLORS["ink"], weight="bold", va="bottom")

_gaps = [_gap(n)[0] for n in TIMES if n in runs] or [0]
ax.set_xlim(0, 60); ax.set_ylim(-0.6, max(10, max(_gaps) + 1.5))
ax.invert_yaxis()                                 # lower is better, put 0 at top visually
# direction cues: faster = less time = LEFT (the old "faster ->" pointed the wrong way)
ax.set_xlabel("← faster          Build time (minutes)          slower →",
              fontsize=SIZES["axis"], color=COLORS["ink"])
ax.set_ylabel("GAP median (mm), lower is better", fontsize=SIZES["axis"], color=COLORS["ink"])
ax.tick_params(labelsize=SIZES["tick"], colors=COLORS["grey"])
for s in ("top", "right"): ax.spines[s].set_visible(False)
for s in ("left", "bottom"): ax.spines[s].set_color("#c3ccd4")
ax.grid(True, color=COLORS["grid"], lw=1); ax.set_axisbelow(True)

fig.text(0.10, 0.905, "Three AIs build a 100-part machine: gap correctness vs speed",
         fontproperties=NUL, fontsize=18, color=COLORS["navy"])
fig.text(0.10, 0.05,
         "GAP = error vs the answer key's intended interface gap. Lower is closer to correct.",
         fontsize=SIZES["note"], color=COLORS["grey"], style="italic")

# Sunnyday Technologies logo, bottom-right on the white interior (skip cleanly if absent)
LOGO = REPO / "assets" / "sdt_logo.png"
if LOGO.exists():
    from PIL import Image as _Img
    import numpy as _np
    _logo = _np.asarray(_Img.open(LOGO).convert("RGBA"))
    lax = fig.add_axes([0.785, 0.018, 0.175, 0.125]); lax.imshow(_logo); lax.axis("off")

fig.savefig(OUT, dpi=100, facecolor=COLORS["navy"]); print("saved:", OUT)
from PIL import Image; print("size:", Image.open(OUT).size)
