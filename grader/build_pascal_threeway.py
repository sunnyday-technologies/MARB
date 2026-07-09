#!/usr/bin/env python3
"""Three-way MARB-A comparison figure (16:9) for the PH-1 cohort.

Small multiples — one panel per measure, one shared model order and one fixed
color per model (color follows the entity). No dual axes. Direct labels on
every bar (palette relief rule). Data from results/pascal_runs.json.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK = "#1f1f1e"
INK2 = "#5f5e57"
GRID = "#e4e3dc"

MODELS = [  # fixed order + fixed hue assignment (never re-cycled)
    {"label": "GPT-5.5\nCodex · xhigh", "color": "#2a78d6",
     "orient": 100.0, "gates": "all gates PASS", "gates_ok": True,
     "minutes": 9.9, "min_note": "", "tokens_m": 2.11, "tok_note": "22k out"},
    {"label": "Claude Opus 4.8\nmax", "color": "#1baf7a",
     "orient": 31.6, "gates": "collision gate FAIL", "gates_ok": False,
     "minutes": 19.9, "min_note": "", "tokens_m": 13.50, "tok_note": "313k out"},
    {"label": "Claude Fable 5\nmedium", "color": "#eda100",
     "orient": 31.6, "gates": "collision gate FAIL", "gates_ok": False,
     "minutes": 117.8, "min_note": "*", "tokens_m": 4.83, "tok_note": "129k out"},
]

PANELS = [
    {"title": "Orientation accuracy — ORIENT-A", "sub": "higher is better",
     "key": "orient", "fmt": lambda m: f"{m['orient']:.0f}%" if m["orient"] == 100 else f"{m['orient']:.1f}%",
     "xmax": 118, "extra": "gates"},
    {"title": "Wall-clock", "sub": "minutes — lower is better",
     "key": "minutes", "fmt": lambda m: f"{m['minutes']:.1f}{m['min_note']}",
     "xmax": 140, "extra": None},
    {"title": "Token bill", "sub": "millions billed incl. cached context — lower is better",
     "key": "tokens_m", "fmt": lambda m: f"{m['tokens_m']:.2f}M  ({m['tok_note']})",
     "xmax": 16.5, "extra": None},
]


def main() -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 7.2), dpi=150)
    fig.patch.set_facecolor("white")

    y = list(range(len(MODELS)))[::-1]
    for ax, panel in zip(axes, PANELS):
        vals = [m[panel["key"]] for m in MODELS]
        colors = [m["color"] for m in MODELS]
        ax.barh(y, vals, height=0.52, color=colors, edgecolor="white", linewidth=1.5, zorder=3)
        for yi, m, v in zip(y, MODELS, vals):
            ax.text(v + panel["xmax"] * 0.015, yi, panel["fmt"](m),
                    va="center", ha="left", fontsize=11.5, color=INK, zorder=4)
            if panel["extra"] == "gates":
                mark = "✓" if m["gates_ok"] else "✗"
                ax.text(panel["xmax"] * 0.015, yi - 0.42, f"{mark} {m['gates']}",
                        va="center", ha="left", fontsize=9, color=INK2, zorder=4)
        ax.set_yticks(y)
        ax.set_yticklabels([m["label"] for m in MODELS], fontsize=11.5, color=INK)
        ax.set_xlim(0, panel["xmax"])
        ax.set_title(panel["title"], fontsize=13.5, color=INK, loc="left", pad=30)
        ax.text(0, 1.045, panel["sub"], transform=ax.transAxes, fontsize=10, color=INK2)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelsize=9.5, colors=INK2)
        ax.tick_params(axis="y", length=0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        if ax is not axes[0]:
            ax.set_yticklabels([])

    fig.suptitle("Same house, same kit, same grader — three models on MARB-A PH-1",
                 fontsize=17, color=INK, x=0.065, ha="left", y=0.99)
    fig.text(0.065, 0.905,
             "All three placed every wall, door, window, and furniture item at 0.0 mm median error. "
             "Orientation — one silent units convention — and cost separate them.",
             fontsize=11.5, color=INK2)
    fig.text(0.065, 0.02,
             "* Fable 5 wall-clock includes operator approval waits (this run predates auto-approval); "
             "Codex and Opus ran fully auto-approved.   Tokens = billed total; output tokens in parentheses.   "
             "n=1 per cell · kit pascal-v0.1 · grader v0.1.1 (frozen)",
             fontsize=9, color=INK2)

    fig.tight_layout(rect=(0.02, 0.06, 0.99, 0.84))
    out = "results/figures/pascal_ph1_threeway.png"
    fig.savefig(out, facecolor="white")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
