# -*- coding: utf-8 -*-
"""MARB — 16:9 (1920x1080) variants of the three LinkedIn figures.

Makes hero-shaped (16:9) versions that fit LinkedIn's link/feed image frame
without the platform cropping the labels:

  marb_sighted_3panel_16x9.png   the lead "we gave it eyes" 3-panel
  marb_local_3panel_16x9.png     the 80B-local-vs-goal 3-panel
  marb_scoreboard_16x9.png       the board, as a brand-rail + table hero

The originals (build_marb_sighted_3panel.py / _local_3panel.py / _scoreboard.py)
are unchanged; this is a separate, additive builder. Same brand kit + render
engine, layouts retuned for the wider frame.

USAGE
  python grader/build_marb_linkedin_16x9.py [--view iso]
"""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "grader"))
from brand_figs import COLORS, NUL, apply_base            # noqa: E402
from cadclaw.render import render_step_to_png             # noqa: E402

REF = REPO / "tasks" / "m3_crete" / "m3_reference_round1.step"
RUNS = Path("D:/tmp/runs")
FIGS = REPO / "results" / "figures"

W, H = 1920, 1080
DPI = 200            # 2x -> 3840x2160 output, retina-crisp text + edges
RENDER = 2000        # supersample each CAD panel, then downscale = sharp lines
SLATE = (0.62, 0.68, 0.74)   # cool grey for non-goal builds

# --- the two 3-panel figures: (title, subtitle, [(head, sub, step, chip)...]) --
SIGHTED = (
    "WE GAVE THE MODEL EYES. IT BUILT LESS.",
    "Same kit, same grader, identical iso camera. The blind text model places "
    "~100 parts; the sighted vision model places 17 — and farther from home.",
    [("GOAL", "M3-CRETE reference (answer key)", REF, "~100 parts"),
     ("BLIND · TEXT 80B", "qwen3-coder-next, no image, n=9 cohort best",
      RUNS / "local_batch_A" / "run_08" / "export.step", "110 solids"),
     ("SIGHTED · VISION 32B", "qwen3-vl, goal image on turn 1, n=5 best",
      RUNS / "sighted_qwen3vl" / "run_04" / "export.step", "17 solids")],
)
LOCAL = (
    "AN 80B LOCAL MODEL vs THE GOAL",
    "Blind CadQuery builds on a local AI supercomputer. Right part COUNT, "
    "loosely-placed parts — not a rigid jointed frame. Identical iso camera.",
    [("GOAL", "M3-CRETE reference (answer key)", REF, "~100 parts"),
     ("BEST LOCAL BUILD", "qwen3-coder-next 80B · mechanics v2",
      RUNS / "local_batch_A" / "run_08" / "export.step", "110 solids"),
     ("LEAN v5 BEST", "qwen3-coder-next 80B · lean v5",
      RUNS / "local_batch_A" / "run_26" / "export.step", "102 solids")],
)


def build_3panel(title, subtitle, panels, out, view):
    for _, _, p, _ in panels:
        if not Path(p).exists():
            raise SystemExit(f"missing STEP: {p}")
    tmp = Path(tempfile.mkdtemp(prefix="marb16x9_"))
    rendered = []
    for k, (head, sub, path, chip) in enumerate(panels):
        png = tmp / f"p{k}.png"
        print(f"  render {head}: {path}")
        kw = dict(width=RENDER, height=RENDER, view=view, tessellation_tol=0.25)
        if k > 0:
            kw["use_step_colors"] = False; kw["default_color"] = SLATE
        render_step_to_png(str(path), str(png), **kw)
        rendered.append((head, sub, chip, png))

    apply_base()
    n = len(rendered)
    fig = plt.figure(figsize=(W/100, H/100), dpi=DPI)
    fig.patch.set_facecolor(COLORS["navy"])
    inner = fig.add_axes([0.010, 0.012, 0.980, 0.976]); inner.axis("off")
    inner.add_patch(Rectangle((0, 0), 1, 1, facecolor=COLORS["white"], zorder=0))

    fig.text(0.5, 0.930, title, fontproperties=NUL, fontsize=33,
             color=COLORS["navy"], ha="center")
    fig.text(0.5, 0.867, subtitle, fontsize=17.5, color=COLORS["ink_dim"],
             ha="center", style="italic")

    pad = 0.012
    pw = (1.0 - pad*(n+1)) / n          # ~0.317 -> 609 px wide
    ph = pw * W / H                     # square panels in the 16:9 frame
    y0 = 0.215
    for k, (head, sub, chip, png) in enumerate(rendered):
        x0 = pad + k*(pw + pad)
        ax = fig.add_axes([x0, y0, pw, ph]); ax.axis("off")
        ax.imshow(mpimg.imread(png))
        is_goal = (k == 0)
        fig.text(x0 + pw/2, 0.158, head, fontproperties=NUL, fontsize=24,
                 ha="center", color=COLORS["green_dk"] if is_goal else COLORS["navy"])
        fig.text(x0 + pw/2, 0.103, chip, fontproperties=NUL, fontsize=19,
                 ha="center", color=COLORS["ink"])
        fig.text(x0 + pw/2, 0.050, sub, fontsize=14, ha="center",
                 color=COLORS["grey"], style="italic")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=COLORS["navy"]); plt.close(fig)
    from PIL import Image; print("  saved:", out, Image.open(out).size)


# ----------------------------- scoreboard (16:9) -----------------------------
def _load_runs():
    runs = json.loads((REPO / "results" / "marb_v0_9_grades.json").read_text("utf-8"))["runs"]
    ref = "Reference (self / ceiling)"
    loc = REPO / "results" / "marb_local_grades.json"
    if loc.exists():
        for name, cell in json.loads(loc.read_text("utf-8"))["runs"].items():
            if name != ref:
                runs[name] = cell
    return runs, ref


def _drill(d, path):
    for k in path: d = d.get(k) if isinstance(d, dict) else None
    return d


def build_scoreboard(out):
    runs, ref = _load_runs()

    def val_std(name, agg_key, flat):
        r = runs[name]
        if "_agg" in r and agg_key in r["_agg"]:
            s = r["_agg"][agg_key]; return s.get("median"), s.get("std")
        return _drill(r, flat), None

    GAP = ("gap_median_mm", ["gap", "overall", "median_mm"])
    ORI = ("orient_aligned_pct", ["orientation", "aligned_pct"])
    POS = ("pos_rel_mm", ["position", "relative", "median_mm"])
    order = sorted([n for n in runs if n != ref],
                   key=lambda n: (lambda v: 1e9 if v is None else v)(val_std(n, *GAP)[0]))
    N = len(order)

    def best(spec, lower=True):
        vals = {n: val_std(n, *spec)[0] for n in order}
        vals = {n: v for n, v in vals.items() if v is not None}
        if not vals: return None
        return (min if lower else max)(vals, key=vals.get)
    b_gap, b_ori, b_pos = best(GAP), best(ORI, lower=False), best(POS)

    disp = {"Claude · CadQuery": "Claude Opus 4.7  ·  CadQuery",
            "Claude · Fusion": "Claude Opus 4.7  ·  Fusion",
            "Codex · CadQuery": "GPT-5 Codex  ·  CadQuery",
            "Opus 4.8 · Fusion": "Claude Opus 4.8  ·  Fusion",
            "Fable 5 (low) · CadQuery": "Claude Fable 5  ·  CadQuery",
            "Fable 5 (medium) · CadQuery": "Claude Fable 5  ·  CadQuery",
            "Fable 5 (high) · CadQuery": "Claude Fable 5  ·  CadQuery",
            "Fable 5 (ultra) · CadQuery": "Claude Fable 5  ·  CadQuery",
            "Local · qwen3-coder-next (mechanics v2)": "qwen3-coder-next 80B",
            "Local · qwen3-coder-next (lean v5)": "qwen3-coder-next 80B",
            "Sighted · qwen3-vl (lean v5, 8 turns)": "qwen3-vl 32B (sighted)"}

    def subline(name, n):
        if name.startswith("Sighted · "): return f"local vision · goal in-loop · n={n}"
        if name.startswith("Local · "):
            return f"local open-weight · {name.split('(')[1].split(')')[0]} · n={n}"
        if name.startswith("Fable 5 ("): return "effort: " + name.split("(")[1].split(")")[0]
        if name in ("Claude · CadQuery", "Claude · Fusion", "Codex · CadQuery"): return "effort: max"
        if name == "Opus 4.8 · Fusion": return "kit v1.3 (hint) · n=1"
        if n and n > 1: return f"median of {n} runs"
        return None

    apply_base()
    fig = plt.figure(figsize=(W/100, H/100), dpi=100)
    fig.patch.set_facecolor(COLORS["white"])

    # ---- left brand rail (navy) ----
    RAIL = 0.330
    fig.add_artist(Rectangle((0, 0), RAIL, 1, transform=fig.transFigure, facecolor=COLORS["navy"], zorder=0))
    fig.add_artist(Rectangle((RAIL, 0), 0.006, 1, transform=fig.transFigure, facecolor=COLORS["green"], zorder=1))
    fig.text(0.028, 0.86, "MARB", fontproperties=NUL, fontsize=66, color=COLORS["white"])
    fig.text(0.030, 0.795, "MECHANICAL ASSEMBLY", fontproperties=NUL, fontsize=17, color=COLORS["green"])
    fig.text(0.030, 0.760, "READINESS BENCHMARK", fontproperties=NUL, fontsize=17, color=COLORS["green"])
    fig.text(0.030, 0.660, "Can AI assemble a real machine?", fontsize=21, color=COLORS["white"], style="italic")
    for i, line in enumerate([
            f"{N} AI builds — frontier hosted",
            "models down to a local 80B anchor.",
            "One 100-part machine. One grader.",
            "Three metrics. None buildable yet."]):
        fig.text(0.030, 0.560 - i*0.052, line, fontsize=16, color="#cfe0ee")
    fig.text(0.028, 0.085, "SUNNYDAY  TECHNOLOGIES", fontproperties=NUL, fontsize=17, color=COLORS["green"])
    fig.text(0.030, 0.045, "marb.cadclaw.io", fontsize=15, weight="bold", color="#cfe0ee")

    # ---- right table ----
    x_rank, x_name, x_gap, x_ori, x_pos = 0.360, 0.392, 0.735, 0.860, 0.972
    # column header
    y_hdr = 0.905
    for x, h, ha in [(x_rank, "#", "left"), (x_name, "MODEL  ·  TOOL", "left"),
                     (x_gap, "GAP ↓", "right"), (x_ori, "ORIENT ↑", "right"),
                     (x_pos, "POS rel ↓", "right")]:
        fig.text(x, y_hdr, h, fontsize=13, weight="bold", color=COLORS["grey"], ha=ha)
    fig.add_artist(Rectangle((0.355, y_hdr - 0.018), 0.625, 0.0018, transform=fig.transFigure, facecolor=COLORS["grey"]))

    def fmt(v, unit, dec=1): return "n/a" if v is None else f"{v:.{dec}f}{unit}"

    def num(x, y, v, std, unit, fs, is_best, dec=1):
        fig.text(x, y, fmt(v, unit, dec), fontsize=fs, weight="bold", ha="right",
                 color=COLORS["green_dk"] if is_best else COLORS["ink"])
        if std:
            fig.text(x, y - 0.022, f"±{std:.{dec}f}", fontsize=11, ha="right", color=COLORS["grey"])

    y0, step = 0.840, 0.0655
    for rank, name in enumerate(order, 1):
        y = y0 - (rank-1)*step
        gm, gs = val_std(name, *GAP); oa, oss = val_std(name, *ORI); pm, ps = val_std(name, *POS)
        n = runs[name].get("_n")
        fig.text(x_rank, y, str(rank), fontsize=25, weight="bold", color=COLORS["navy"])
        fig.text(x_name, y, disp.get(name, name.replace(" · ", "  ·  ")), fontsize=15.5, color=COLORS["ink"])
        sub = subline(name, n)
        if sub: fig.text(x_name, y - 0.024, sub, fontsize=11, color=COLORS["grey"], style="italic")
        num(x_gap, y, gm, gs, " mm", 21, name == b_gap)
        num(x_ori, y, oa, oss, "%", 21, name == b_ori, dec=0)
        num(x_pos, y, pm, ps, " mm", 18, name == b_pos)

    # reference row
    yref = y0 - N*step - 0.006
    fig.add_artist(Rectangle((0.355, yref + 0.030), 0.625, 0.0014, transform=fig.transFigure, facecolor="#dde3e8"))
    fig.text(x_rank, yref, "·", fontsize=18, color=COLORS["grey"])
    fig.text(x_name, yref, "Reference (answer key)", fontsize=14, color=COLORS["grey"], style="italic")
    fig.text(x_gap, yref, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])
    fig.text(x_ori, yref, "100%", fontsize=15, ha="right", color=COLORS["grey"])
    fig.text(x_pos, yref, "0.0 mm", fontsize=15, ha="right", color=COLORS["grey"])
    fig.text(0.360, yref - 0.052,
             "GAP = error vs intended interface gap (~0 mm bolted, ~1 mm motion). Ranked by GAP median.",
             fontsize=12.5, color=COLORS["ink_dim"], style="italic")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor=COLORS["white"]); plt.close(fig)
    from PIL import Image; print("  saved:", out, Image.open(out).size)


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--view", default="iso")
    a = ap.parse_args(argv)
    print("sighted 3-panel:"); build_3panel(*SIGHTED, FIGS / "marb_sighted_3panel_16x9.png", a.view)
    print("local 3-panel:");   build_3panel(*LOCAL,   FIGS / "marb_local_3panel_16x9.png", a.view)
    print("scoreboard:");      build_scoreboard(FIGS / "marb_scoreboard_16x9.png")
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
