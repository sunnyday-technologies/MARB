# -*- coding: utf-8 -*-
"""MARB token-efficiency hero: billed tokens vs GAP, six frontier builds.
The money chart for the recap article -- what a millimeter of precision costs.
Token bills recovered from session transcripts (see
recover_tokens_from_transcripts.py); rendered through brand_figs.
"""
import sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle as _Rect

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, SIZES, apply_base   # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    REPO / "results" / "figures" / "marb_token_efficiency.png"

# (billed tokens M, GAP median mm, label, dx, dy, ha)  -- offsets in points
RUNS = [
    (22.1, 0.0, "Claude Opus 4.7 / CadQuery\n22.1M  ·  0.0 mm",   -14, 12,  "right"),
    (34.2, 2.0, "Claude Opus 4.7 / Fusion\n34.2M  ·  2.0 mm",     -14, 12,  "right"),
    (23.0, 3.0, "Claude Fable 5 / ultra\n23.0M  ·  3.0 mm",        14, 10,  "left"),
    (18.5, 7.0, "Claude Fable 5 / high\n18.5M  ·  7.0 mm",         14, 10,  "left"),
    ( 4.7, 6.5, "Claude Fable 5 / medium\n4.7M  ·  6.5 mm",        16, -4,  "left"),
    ( 4.2, 7.0, "Claude Fable 5 / low\n4.2M  ·  7.0 mm",          -10, -40, "left"),
]
HIGHLIGHT = "medium"   # the value corner gets the green marker

apply_base()
W, H = 1400, 820
fig = plt.figure(figsize=(W/100, H/100), dpi=100)
fig.patch.set_facecolor(COLORS["navy"])                       # navy edge band
fig.add_artist(_Rect((0.013, 0.016), 0.974, 0.968, transform=fig.transFigure,
                     facecolor=COLORS["white"], zorder=0))    # white interior
ax = fig.add_axes([0.085, 0.175, 0.875, 0.63]); ax.set_facecolor(COLORS["white"])

# target line at 0 mm (the answer key)
ax.axhline(0, ls=(0, (7, 5)), color=COLORS["green"], lw=2.8, zorder=1)
ax.text(0.8, 0.38, "0 mm = the answer key (target)",
        color="#3d5a00", fontsize=14, ha="left", va="top", weight="bold")

for tokens, gap, label, dx, dy, ha in RUNS:
    is_best = "medium" in label
    c = COLORS["green"] if is_best else COLORS["navy"]
    edge = "#3d5a00" if is_best else "white"
    ax.scatter([tokens], [gap], s=340, color=c, edgecolor=edge, linewidth=2.5, zorder=4)
    ax.annotate(label, (tokens, gap), textcoords="offset points",
                xytext=(dx, dy), ha=ha, fontsize=13, color=COLORS["ink"],
                weight="bold", va="bottom", zorder=5)

# the value corner cue
ax.annotate("the value corner:\n~1/5 of the Opus bill", (4.7, 6.5),
            textcoords="offset points", xytext=(38, -56), fontsize=13,
            color="#3d5a00", weight="bold", ha="left", va="top",
            arrowprops=dict(arrowstyle="-", color="#3d5a00", lw=1.6))

ax.set_xlim(0, 37); ax.set_ylim(-0.6, 8.6)
ax.invert_yaxis()                                  # lower GAP is better -> top
ax.set_xlabel("← cheaper          Billed tokens (millions)          costlier →",
              fontsize=SIZES["axis"], color=COLORS["ink"])
ax.set_ylabel("GAP median (mm), lower is better", fontsize=SIZES["axis"], color=COLORS["ink"])
ax.tick_params(labelsize=SIZES["tick"], colors=COLORS["grey"])
for s in ("top", "right"): ax.spines[s].set_visible(False)
for s in ("left", "bottom"): ax.spines[s].set_color("#c3ccd4")
ax.grid(True, color=COLORS["grid"], lw=1); ax.set_axisbelow(True)

fig.text(0.085, 0.905, "What a millimeter costs: token bill vs GAP, six frontier builds",
         fontproperties=NUL, fontsize=18, color=COLORS["navy"])
fig.text(0.085, 0.085,
         "Token bills recovered from session transcripts.  High effort billed 3.9× medium for a worse score.",
         fontsize=SIZES["note"], color=COLORS["ink_dim"], style="italic")
fig.text(0.085, 0.045,
         "Unplotted: GPT-5 Codex (host exposes no token count; GAP 7.8 mm) and the local 80B (~0.075M/run at 308–410 mm GAP).",
         fontsize=13, color=COLORS["grey"], style="italic")

# Sunnyday Technologies logo, bottom-right on the white interior (skip cleanly if absent)
LOGO = REPO / "assets" / "sdt_logo.png"
if LOGO.exists():
    from PIL import Image as _Img
    import numpy as _np
    _logo = _np.asarray(_Img.open(LOGO).convert("RGBA"))
    lax = fig.add_axes([0.785, 0.018, 0.175, 0.125]); lax.imshow(_logo); lax.axis("off")

fig.savefig(OUT, dpi=100, facecolor=COLORS["navy"]); print("saved:", OUT)
from PIL import Image; print("size:", Image.open(OUT).size)
