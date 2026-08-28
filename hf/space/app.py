"""MARB leaderboard — Hugging Face Space (Gradio).

Renders the published MARB board from board.json. board.json mirrors the table in
the MARB README, which is the reviewed source of truth. To refresh, regenerate
the numbers with the MARB grader and update board.json.
"""

import json
import os

import gradio as gr
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

GITHUB = "https://github.com/sunnyday-technologies/MARB"
SITE = "https://marb.cadclaw.io"
DATASET = "https://huggingface.co/datasets/SunnydayTech/marb-m3-crete"
ANSWER_KEY = "https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key"
CADCLAW = "https://github.com/sunnyday-technologies/CADCLAW"

COLUMNS = [
    ("rank", "#"),
    ("model", "Model"),
    ("tool", "Tool"),
    ("cohort", "Effort / cohort"),
    ("provenance_display", "Task / spec / published"),
    ("reporting_display", "Reporting / runs"),
    ("gap_display", "GAP median"),
    ("orient_display", "ORIENT aligned"),
    ("pos_display", "POS relative median"),
]


def load_board():
    with open(os.path.join(HERE, "board.json"), encoding="utf-8") as fh:
        return json.load(fh)


def board_dataframe(board):
    rows = sorted(
        board["rows"],
        key=lambda r: (r["track"] == "reference", r["gap_median_mm"]),
    )
    out = []
    for r in rows:
        display = dict(r)
        display["provenance_display"] = " · ".join(
            str(value) for value in (
                r.get("task"), r.get("spec_version"), r.get("published_date")
            ) if value is not None
        )
        reporting = r.get("reporting") or {}
        mode = {
            "legacy-single-run": "single run",
            "repeat-run": "median ± population SD",
            "reference": "reference",
        }.get(reporting.get("mode"), reporting.get("mode"))
        reporting_bits = [mode] if mode else []
        attempted = reporting.get("n_attempted")
        graded = reporting.get("n_graded")
        if attempted is not None and graded is not None:
            reporting_bits.append(f"attempted {attempted} · graded {graded}")
        seed_ids = reporting.get("seed_ids") or []
        if seed_ids:
            reporting_bits.append("runs " + ", ".join(str(seed) for seed in seed_ids))
        display["reporting_display"] = " · ".join(reporting_bits)
        out.append({
            label: ("" if display.get(key) is None else display.get(key))
            for key, label in COLUMNS
        })
    return pd.DataFrame(out, columns=[label for _, label in COLUMNS])


board = load_board()
df = board_dataframe(board)

scoreboard_png = os.path.join(HERE, "marb_scoreboard.png")

header = f"""
# MARB — Mechanical Assembly Readiness Benchmark

**Can AI assemble a real machine?** Every row targets the same ~100-part machine
({board['task']}), but the runs span versioned blind-kit and harness cohorts.
Results use the scoring version shown per row and are comparable only within a
matching kit, prompt, tool, and harness cohort. The open-source
[CADCLAW]({CADCLAW}) engine grades the exported assemblies. Ranked by **GAP**
median, the primary functional score.

Current method **{board['scoring_version']}**; each row retains its own scoring
version. Results through **{board['results_through']}**. {board['comparability_note']}

[Source repo]({GITHUB}) · [Project site]({SITE}) · [Benchmark input dataset]({DATASET}) · [Answer key (gated)]({ANSWER_KEY})
"""

metrics = """
### Metrics
- **GAP** — error between the actual and intended interface gap (median mm). About
  0 mm where parts bolt together, about 1 to 2 mm where parts move. Primary score.
- **ORIENT** — share of parts whose rotation the current axis-aligned
  bounding-box extent proxy can grade and that are in the correct rotation
  (percent aligned). Parts with two near-equal extents are skipped because the
  proxy cannot distinguish their rotation.
- **POS** — per-part position error versus the answer key after a best-fit rigid
  alignment (median mm, neighbor-relative).

The **frontier** rows are hosted models. The **local** rows are open-weight models
a shop could run offline; the **sighted** row adds the goal image in-loop. Local and
sighted cells are anchors in their own cohorts, not head-to-head with the frontier.

The eight existing frontier cells are dated single runs under v0.9. New frontier
cells published from 2026-08-28 onward require at least three attempted and three
graded independent runs and report median ± population standard deviation. Failed
attempts remain registered; attempts or retries within one session are not seeds.
The publication gate also reconciles stable run IDs, per-attempt run-log digests,
graded STEP digests, and the exact graded runs carried into the aggregate source.
"""

run_it = f"""
### Run a model against it
1. Hand the model a blind kit from the [public dataset]({DATASET}): authored parts,
   four goal renders (overview + front/top/side), the task brief. No answer key, no build steps.
2. Run it in a sealed, memoryless context so the answer key cannot leak.
3. Export a STEP and grade it with the [MARB grader]({GITHUB}/tree/main/grader)
   (`pip install "cadclaw>=0.9.0"`). The answer key is access-gated: [request it here]({ANSWER_KEY}).
"""

with gr.Blocks(title="MARB Leaderboard", theme=gr.themes.Soft()) as demo:
    gr.Markdown(header)
    if os.path.exists(scoreboard_png):
        gr.Image(scoreboard_png, label="MARB scoreboard with run provenance", show_label=False)
    gr.Dataframe(value=df, interactive=False, wrap=True)
    gr.Markdown(metrics)
    gr.Markdown(run_it)
    gr.Markdown("MARB is developed by Sunnyday Technologies. Contact: info@sunn3d.com")

if __name__ == "__main__":
    demo.launch()
