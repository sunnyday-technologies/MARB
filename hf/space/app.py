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
    ("gap_display", "GAP median"),
    ("orient_pct", "ORIENT aligned %"),
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
        out.append({label: ("" if r.get(key) is None else r.get(key)) for key, label in COLUMNS})
    return pd.DataFrame(out, columns=[label for _, label in COLUMNS])


board = load_board()
df = board_dataframe(board)

scoreboard_png = os.path.join(HERE, "marb_scoreboard.png")

header = f"""
# MARB — Mechanical Assembly Readiness Benchmark

**Can AI assemble a real machine?** Each run builds the same ~100-part machine
({board['task']}) from the same blind kit and is graded identically by the
open-source [CADCLAW]({CADCLAW}) engine. Ranked by **GAP** median, the primary
functional score.

Scoring **{board['scoring_version']}**. {board['comparability_note']}

[Source repo]({GITHUB}) · [Project site]({SITE}) · [Benchmark input dataset]({DATASET}) · [Answer key (gated)]({ANSWER_KEY})
"""

metrics = """
### Metrics
- **GAP** — error between the actual and intended interface gap (median mm). About
  0 mm where parts bolt together, about 1 to 2 mm where parts move. Primary score.
- **ORIENT** — share of orientation-gradeable (asymmetric) parts in the correct
  rotation (percent aligned). Symmetric parts are skipped.
- **POS** — per-part position error versus the answer key after a best-fit rigid
  alignment (median mm, neighbor-relative).

The **frontier** rows are hosted models. The **local** rows are open-weight models
a shop could run offline; the **sighted** row adds the goal image in-loop. Local and
sighted cells are anchors in their own cohorts, not head-to-head with the frontier.
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
        gr.Image(scoreboard_png, label="MARB v0.9 scoreboard", show_label=False)
    gr.Dataframe(value=df, interactive=False, wrap=True)
    gr.Markdown(metrics)
    gr.Markdown(run_it)
    gr.Markdown("MARB is developed by Sunnyday Technologies. Contact: info@sunn3d.com")

if __name__ == "__main__":
    demo.launch()
