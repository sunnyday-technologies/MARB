# -*- coding: utf-8 -*-
"""MARB scoreboard graphic (1200 px wide, height grows with the board).
Reads results/marb_v0_9_grades.json (+ the graded local-anchor cells) and
renders through brand_figs so the figure is brand-consistent and LEGIBLE:
large type, fixed per-row pitch (rows never compress or overlap), minimal
words. See ../MARB_SCORING.md and brand_figs.py LEGIBILITY RULES.
"""
import json, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, SIZES, apply_base   # noqa: E402

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    REPO / "results" / "marb_v0_9_grades.json"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else \
    REPO / "results" / "figures" / "marb_scoreboard.png"

apply_base()
runs = json.loads(DATA.read_text(encoding="utf-8"))["runs"]
ref = "Reference (self / ceiling)"

# merge the local-anchor cells (graded with the same rubric) into the board,
# so the ranking shows frontier and local on one ladder
LOCAL = REPO / "results" / "marb_local_grades.json"
if len(sys.argv) <= 1 and LOCAL.exists():
    for name, cell in json.loads(LOCAL.read_text(encoding="utf-8"))["runs"].items():
        if name != ref:
            runs[name] = cell

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
N = len(order_ai)

# ---- fixed-pitch vertical layout (pixels) -----------------------------------
# Every row gets the same two-line space; the CANVAS grows with the board so
# rows, ± spreads, the reference line, and the footer can never collide.
W      = 1200
HEAD   = 113      # navy header band
STRIP  = 8        # green strip under the band
COLHDR = 64       # gap + column-header line + rule
ROW    = 66       # per AI row: main line + sub line + clearance
REFROW = 58       # divider + reference row
NOTE   = 42       # methodology line
FOOT   = 62       # footer band
H = HEAD + STRIP + COLHDR + N*ROW + REFROW + NOTE + 16 + FOOT

fig = plt.figure(figsize=(W/100, H/100), dpi=100); fig.patch.set_facecolor(COLORS["white"])
def yf(px):       # pixels from the top -> figure fraction
    return 1 - px/H

# ---- header band ----
fig.add_artist(Rectangle((0, yf(HEAD)), 1, HEAD/H, transform=fig.transFigure, facecolor=COLORS["navy"], zorder=0))
fig.add_artist(Rectangle((0, yf(HEAD+STRIP)), 1, STRIP/H, transform=fig.transFigure, facecolor=COLORS["green"], zorder=1))
fig.text(0.035, yf(HEAD*0.42), "MARB", fontproperties=NUL, fontsize=36, color=COLORS["white"], va="center")
fig.text(0.19, yf(HEAD*0.42), "MECHANICAL ASSEMBLY READINESS BENCHMARK",
         fontproperties=NUL, fontsize=14, color=COLORS["green"], va="center")
fig.text(0.037, yf(HEAD*0.82), f"v0.9 results:  {N} AI builds, frontier to local, one 100-part machine, three metrics",
         fontsize=14, color="#cfe0ee", va="center")

# ---- leaderboard table ----
x_rank, x_name, x_gap, x_ori, x_pos = 0.045, 0.10, 0.485, 0.685, 0.88
y_hdr = yf(HEAD + STRIP + COLHDR - 18)
for x, h, ha in [(x_rank, "#", "left"), (x_name, "MODEL  ·  TOOL", "left"),
                 (x_gap, "GAP median ↓", "right"), (x_ori, "ORIENT aligned ↑", "right"),
                 (x_pos, "POS rel median ↓", "right")]:
    fig.text(x, y_hdr, h, fontsize=12.5, weight="bold", color=COLORS["grey"], ha=ha)
fig.add_artist(Rectangle((0.04, y_hdr - 10/H), 0.92, 1.8/H, transform=fig.transFigure, facecolor=COLORS["grey"]))

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

SUB = 26 / H      # sub-line offset under a row's main line (px -> fraction)

# draw a metric number in its column; stack a small grey "±sd" on the sub-line
# when the cell has a spread (n>=2). Fixed pitch guarantees it cannot collide.
def num(x, y, v, std, unit, fs, best, dec=1):
    col = COLORS["green_dk"] if best else COLORS["ink"]
    fig.text(x, y, fmt(v, unit, dec), fontsize=fs, weight="bold", ha="right", color=col)
    if std:
        fig.text(x, y - SUB, f"±{std:.{dec}f}", fontsize=11, ha="right", color=COLORS["grey"])

row_top = HEAD + STRIP + COLHDR
for rank, name in enumerate(order_ai, 1):
    y = yf(row_top + (rank-1)*ROW + 20)
    gm, gs = _val_std(name, "gap_median_mm", ["gap","overall","median_mm"])
    oa, os_ = _val_std(name, "orient_aligned_pct", ["orientation","aligned_pct"])
    pm, ps = _val_std(name, "pos_rel_mm", ["position","relative","median_mm"])
    n = runs[name].get("_n")
    fig.text(x_rank, y, str(rank), fontsize=26, weight="bold", color=COLORS["navy"])
    disp = {"Claude · CadQuery": "Claude Opus 4.7  ·  CadQuery",
            "Claude · Fusion":   "Claude Opus 4.7  ·  Fusion",
            "Codex · CadQuery":  "GPT-5 Codex  ·  CadQuery",
            "Fable 5 (low) · CadQuery":    "Claude Fable 5  ·  CadQuery",
            "Fable 5 (medium) · CadQuery": "Claude Fable 5  ·  CadQuery",
            "Fable 5 (high) · CadQuery":   "Claude Fable 5  ·  CadQuery",
            "Fable 5 (ultra) · CadQuery":  "Claude Fable 5  ·  CadQuery",
            "Local · qwen3-coder-next (mechanics v2)": "qwen3-coder-next 80B",
            "Local · qwen3-coder-next (lean v5)":      "qwen3-coder-next 80B"}.get(name, name.replace(" · ", "  ·  "))
    fig.text(x_name, y, disp, fontsize=15, color=COLORS["ink"])
    if name.startswith("Local · "):
        cohort = name.split("(")[1].split(")")[0]
        sub = f"local open-weight · CadQuery · {cohort} · median, n={n}"
    elif n and n > 1:
        sub = f"median of {n} runs"
    elif name.startswith("Fable 5 ("):
        sub = "effort: " + name.split("(")[1].split(")")[0]
    elif name in ("Claude · CadQuery", "Claude · Fusion", "Codex · CadQuery"):
        sub = "effort: max"
    else:
        sub = None
    if sub:
        fig.text(x_name, y - SUB, sub, fontsize=11, color=COLORS["grey"], style="italic")
    num(x_gap, y, gm, gs, " mm", 21, name == best_gap)
    num(x_ori, y, oa, os_, "%", 21, name == best_ori, dec=0)
    num(x_pos, y, pm, ps, " mm", 18, name == best_pos)

# reference row (light, "target / answer key")
y_div = yf(row_top + N*ROW + 6)
fig.add_artist(Rectangle((0.04, y_div), 0.92, 1.4/H, transform=fig.transFigure, facecolor="#dde3e8"))
y2 = yf(row_top + N*ROW + 38)
fig.text(x_rank, y2, "·", fontsize=18, color=COLORS["grey"])
fig.text(x_name, y2, "Reference (answer key)", fontsize=14, color=COLORS["grey"], style="italic")
fig.text(x_gap, y2, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])
fig.text(x_ori, y2, "100%", fontsize=15, ha="right", color=COLORS["grey"])
fig.text(x_pos, y2, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])

# one-line methodology
fig.text(0.045, yf(row_top + N*ROW + REFROW + 26),
         "GAP = error vs intended interface gap (~0 mm bolted, ~1 mm motion clearance).  None buildable yet.",
         fontsize=12.5, color=COLORS["ink_dim"], style="italic")

# ---- footer ----
fig.add_artist(Rectangle((0, 0), 1, FOOT/H, transform=fig.transFigure, facecolor="#f4f6f8"))
fig.text(0.045, (FOOT/2)/H, "SUNNYDAY  TECHNOLOGIES", fontproperties=NUL, fontsize=18, color=COLORS["navy"], va="center")
fig.text(0.955, (FOOT/2)/H, "cadclaw.io/benchmark", fontsize=15, weight="bold", color=COLORS["navy"], ha="right", va="center")

fig.savefig(OUT, dpi=100, facecolor=COLORS["white"]); print("saved:", OUT)
if len(sys.argv) <= 2:   # only refresh the deployed og:image alias on a canonical build
    LEGACY = REPO / "results" / "figures" / "arb_scoreboard.png"   # og:image compat
    fig.savefig(LEGACY, dpi=100, facecolor=COLORS["white"]); print("saved (legacy):", LEGACY)
from PIL import Image; print("size:", Image.open(OUT).size)
