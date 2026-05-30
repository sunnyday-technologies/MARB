# -*- coding: utf-8 -*-
"""MARB v0.9 LinkedIn / OG scoreboard graphic (1200x630).
Reads results/marb_v0_9_grades.json; renders through brand_figs so the figure
is brand-consistent and LEGIBLE: large type, minimal words, the data carries
the message. See ../MARB_SCORING.md.
"""
import json, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, SIZES, apply_base   # noqa: E402

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    REPO / "results" / "marb_v0_9_grades.json"
WORDMARK = REPO.parent / "brand-guide" / "assets" / "sunnyday-technologies-wordmark-blue.png"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else \
    REPO / "results" / "figures" / "marb_scoreboard.png"

apply_base()
runs = json.loads(DATA.read_text(encoding="utf-8"))["runs"]
ref = "Reference (self / ceiling)"

def _drill(d, path):
    for k in path: d = d.get(k) if isinstance(d, dict) else None
    return d

# value + spread per cell: from the `_agg` block (stats schema) when present,
# else from the flat drill path (single-run schema), std=None.
def _val_std(name, agg_key, flat_path):
    r = runs[name]
    if "_agg" in r and agg_key in r["_agg"]:
        s = r["_agg"][agg_key]
        return s.get("median"), s.get("std")
    return _drill(r, flat_path), None

# rank AI cells (everything but the reference) by GAP median, low to high
order_ai = [n for n in runs if n != ref]
def _gm(n):
    v, _ = _val_std(n, "gap_median_mm", ["gap", "overall", "median_mm"])
    return 1e9 if v is None else v
order_ai = sorted(order_ai, key=_gm)

W, H = 1200, 630
fig = plt.figure(figsize=(W/100, H/100), dpi=100); fig.patch.set_facecolor(COLORS["white"])

# ---- header band ----
hh = 0.18
fig.add_artist(Rectangle((0, 1-hh), 1, hh, transform=fig.transFigure, facecolor=COLORS["navy"], zorder=0))
fig.add_artist(Rectangle((0, 1-hh-0.013), 1, 0.013, transform=fig.transFigure, facecolor=COLORS["green"], zorder=1))
fig.text(0.035, 1-hh*0.38, "MARB", fontproperties=NUL, fontsize=36, color=COLORS["white"], va="center")
fig.text(0.19, 1-hh*0.38, "MECHANICAL ASSEMBLY READINESS BENCHMARK",
         fontproperties=NUL, fontsize=14, color=COLORS["green"], va="center")
fig.text(0.037, 1-hh*0.79, "v0.9 first results:  three AIs, one 100-part machine, three metrics",
         fontsize=14, color="#cfe0ee", va="center")

# ---- leaderboard table ----
x_rank, x_name, x_gap, x_ori, x_pos = 0.045, 0.10, 0.485, 0.685, 0.88
ytop = 0.72
for x, h, ha in [(x_rank, "#", "left"), (x_name, "MODEL  ·  TOOL", "left"),
                 (x_gap, "GAP median ↓", "right"), (x_ori, "ORIENT aligned ↑", "right"),
                 (x_pos, "POS rel median ↓", "right")]:
    fig.text(x, ytop, h, fontsize=12.5, weight="bold", color=COLORS["grey"], ha=ha)
fig.add_artist(Rectangle((0.04, ytop-0.018), 0.92, 0.0016, transform=fig.transFigure, facecolor=COLORS["grey"]))

def fmt(v, unit, dec=1):
    return "n/a" if v is None else (f"{v:.{dec}f}{unit}")

def best_of(agg_key, flat_path, lower_is_better=True):
    vals = {n: _val_std(n, agg_key, flat_path)[0] for n in order_ai}
    valid = {n: v for n, v in vals.items() if v is not None}
    if not valid: return None
    return min(valid, key=valid.get) if lower_is_better else max(valid, key=valid.get)

best_gap = best_of("gap_median_mm", ["gap","overall","median_mm"], lower_is_better=True)
best_ori = best_of("orient_aligned_pct", ["orientation","aligned_pct"], lower_is_better=False)
best_pos = best_of("pos_rel_mm", ["position","relative","median_mm"], lower_is_better=True)

# draw a metric number in its column position; stack a small grey "±sd" beneath
# it when the cell has a spread (n>=2). Main number stays exactly where it was.
def num(x, y, v, std, unit, fs, best, dec=1):
    col = COLORS["green_dk"] if best else COLORS["ink"]
    fig.text(x, y, fmt(v, unit, dec), fontsize=fs, weight="bold", ha="right", color=col)
    if std:
        fig.text(x, y - 0.037, f"±{std:.{dec}f}", fontsize=11.5, ha="right", color=COLORS["grey"])

y = ytop - 0.10
for rank, name in enumerate(order_ai, 1):
    gm, gs = _val_std(name, "gap_median_mm", ["gap","overall","median_mm"])
    oa, os_ = _val_std(name, "orient_aligned_pct", ["orientation","aligned_pct"])
    pm, ps = _val_std(name, "pos_rel_mm", ["position","relative","median_mm"])
    n = runs[name].get("_n")
    fig.text(x_rank, y, str(rank), fontsize=30, weight="bold", color=COLORS["navy"])
    disp = {"Claude · CadQuery": "Claude Opus 4.7  ·  CadQuery",
            "Claude · Fusion":   "Claude Opus 4.7  ·  Fusion",
            "Codex · CadQuery":  "GPT-5 Codex  ·  CadQuery"}.get(name, name.replace(" · ", "  ·  "))
    fig.text(x_name, y, disp, fontsize=15, color=COLORS["ink"])
    if n and n > 1:
        fig.text(x_name, y - 0.037, f"median of {n} runs", fontsize=11, color=COLORS["grey"], style="italic")
    num(x_gap, y, gm, gs, " mm", 22, name == best_gap)
    num(x_ori, y, oa, os_, "%", 22, name == best_ori, dec=0)
    num(x_pos, y, pm, ps, " mm", 18, name == best_pos)
    y -= 0.11

# reference row (light, "target / answer key")
fig.add_artist(Rectangle((0.04, y+0.06), 0.92, 0.0014, transform=fig.transFigure, facecolor="#dde3e8"))
y2 = y + 0.005
fig.text(x_rank, y2, "·", fontsize=18, color=COLORS["grey"])
fig.text(x_name, y2, "Reference (answer key)", fontsize=14, color=COLORS["grey"], style="italic")
fig.text(x_gap, y2, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])
fig.text(x_ori, y2, "100%", fontsize=15, ha="right", color=COLORS["grey"])
fig.text(x_pos, y2, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])

# one-line methodology
fig.text(0.045, 0.14,
         "GAP = error vs intended interface gap (~0 mm bolted, ~1 mm motion clearance).  None buildable yet.",
         fontsize=12.5, color=COLORS["ink_dim"], style="italic")

# ---- footer ----
fig.add_artist(Rectangle((0, 0), 1, 0.095, transform=fig.transFigure, facecolor="#f4f6f8"))
fig.text(0.045, 0.045, "SUNNYDAY  TECHNOLOGIES", fontproperties=NUL, fontsize=18, color=COLORS["navy"], va="center")
fig.text(0.955, 0.045, "cadclaw.io/benchmark", fontsize=15, weight="bold", color=COLORS["navy"], ha="right", va="center")

fig.savefig(OUT, dpi=100, facecolor=COLORS["white"]); print("saved:", OUT)
if len(sys.argv) <= 2:   # only refresh the deployed og:image alias on a canonical build
    LEGACY = REPO / "results" / "figures" / "arb_scoreboard.png"   # og:image compat
    fig.savefig(LEGACY, dpi=100, facecolor=COLORS["white"]); print("saved (legacy):", LEGACY)
from PIL import Image; print("size:", Image.open(OUT).size)
