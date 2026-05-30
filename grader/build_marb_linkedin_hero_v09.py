# -*- coding: utf-8 -*-
"""MARB v0.9 LinkedIn hero (1200x630) — leads with the GAP close-up.

Regenerative: embeds the approved gap close-up (`marb_gap_closeup.png`) and reads
the live grades JSON for the v0.9 mini-leaderboard, so it auto-updates when new
runs land (Opus 4.8, Codex, local models). Re-run after rebuilding the gap figure
and re-grading. Brand-styled via brand_figs (no em-dashes, large type).
"""
import json, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL   # noqa: E402

DATA = REPO / "results" / "marb_v0_9_grades.json"
GAP_IMG = REPO / "results" / "figures" / "marb_gap_closeup.png"
OUT = REPO / "results" / "figures" / "linkedin_hero_v09.png"

DISP = {"Claude · CadQuery": "Claude Opus 4.7 · CadQuery",
        "Claude · Fusion":   "Claude Opus 4.7 · Fusion",
        "Codex · CadQuery":  "GPT-5 Codex · CadQuery"}

runs = json.loads(DATA.read_text(encoding="utf-8"))["runs"]
ref = "Reference (self / ceiling)"
order = sorted([n for n in runs if n != ref],
               key=lambda n: runs[n]["gap"]["overall"]["median_mm"])

W, H = 1200, 630
fig = plt.figure(figsize=(W/100, H/100), dpi=100); fig.patch.set_facecolor(COLORS["white"])

# ---- header band ----
hh = 0.155
fig.add_artist(Rectangle((0, 1-hh), 1, hh, transform=fig.transFigure, facecolor=COLORS["navy"], zorder=0))
fig.add_artist(Rectangle((0, 1-hh-0.012), 1, 0.012, transform=fig.transFigure, facecolor=COLORS["green"], zorder=1))
fig.text(0.032, 1-hh*0.40, "MARB", fontproperties=NUL, fontsize=30, color=COLORS["white"], va="center")
fig.text(0.165, 1-hh*0.40, "MECHANICAL ASSEMBLY READINESS BENCHMARK",
         fontproperties=NUL, fontsize=11, color=COLORS["green"], va="center")
fig.text(0.034, 1-hh*0.80, "v0.9 first results: placing the parts is easy, locating them is the hard part",
         fontsize=11, color="#cfe0ee", va="center")

# ---- gap close-up (the hero visual), left ~64% ----
axg = fig.add_axes([0.028, 0.12, 0.63, 0.70]); axg.axis("off")
axg.imshow(mpimg.imread(str(GAP_IMG)))

# ---- v0.9 mini-leaderboard, right ----
x0 = 0.69
fig.text(x0, 0.78, "v0.9 metrics", fontproperties=NUL, fontsize=13, color=COLORS["navy"])
cx_g, cx_o, cx_p = 0.815, 0.905, 0.985
fig.text(cx_g, 0.735, "GAP", fontsize=9.5, weight="bold", color=COLORS["grey"], ha="right")
fig.text(cx_o, 0.735, "ORIENT", fontsize=9.5, weight="bold", color=COLORS["grey"], ha="right")
fig.text(cx_p, 0.735, "POS rel", fontsize=9.5, weight="bold", color=COLORS["grey"], ha="right")
best_gap = order[0]
y = 0.685
for n in order:
    r = runs[n]
    gm = r["gap"]["overall"]["median_mm"]; oa = r["orientation"]["aligned_pct"]; pr = r["position"]["relative"]["median_mm"]
    fig.text(x0, y, DISP.get(n, n), fontsize=10, color=COLORS["ink"])
    fig.text(cx_g, y-0.035, f"{gm:.1f} mm", fontsize=12, weight="bold", ha="right",
             color=COLORS["green_dk"] if n == best_gap else COLORS["ink"])
    fig.text(cx_o, y-0.035, f"{oa:.0f}%", fontsize=12, weight="bold", ha="right", color=COLORS["ink"])
    fig.text(cx_p, y-0.035, f"{pr:.0f} mm", fontsize=12, ha="right", color=COLORS["ink"])
    y -= 0.105
fig.add_artist(Rectangle((x0, y+0.055), 0.295, 0.0016, transform=fig.transFigure, facecolor="#dde3e8"))
fig.text(x0, y+0.012, "GAP = error vs the intended interface gap.", fontsize=8.7, color=COLORS["grey"], style="italic")
fig.text(x0, y-0.016, "Lower GAP and POS, higher ORIENT, are better.", fontsize=8.7, color=COLORS["grey"], style="italic")
fig.text(x0, y-0.052, "None buildable yet. The starting line.",
         fontsize=9.3, color=COLORS["navy"], weight="bold")

# ---- footer ----
fig.add_artist(Rectangle((0, 0), 1, 0.082, transform=fig.transFigure, facecolor="#f4f6f8", zorder=0))
fig.text(0.032, 0.04, "SUNNYDAY  TECHNOLOGIES", fontproperties=NUL, fontsize=15, color=COLORS["navy"], va="center")
fig.text(0.968, 0.04, "cadclaw.io/benchmark", fontsize=11.5, weight="bold", color=COLORS["navy"], ha="right", va="center")

fig.savefig(OUT, dpi=100, facecolor=COLORS["white"]); print("saved:", OUT)
from PIL import Image; print("size:", Image.open(OUT).size)
