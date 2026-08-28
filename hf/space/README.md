---
title: MARB — Mechanical Assembly Readiness Benchmark
emoji: 🦾
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 6.19.0
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
short_description: Can AI assemble a real machine? Mechanical CAD benchmark.
tags:
  - benchmark
  - leaderboard
  - mechanical-engineering
  - cad
  - assembly
  - spatial-reasoning
  - step-files
  - agents
  - evaluation
---

# MARB Leaderboard

The published board for **MARB**, the Mechanical Assembly Readiness Benchmark.
Every row targets the same ~100-part machine, but the runs span versioned
blind-kit and harness cohorts. Results use the scoring version shown per row and
are comparable only within a matching kit, prompt, tool, and harness cohort.
The open-source [CADCLAW](https://github.com/sunnyday-technologies/CADCLAW)
engine grades the exported assemblies, and the board is ranked by GAP median.

- Source of truth: [github.com/sunnyday-technologies/MARB](https://github.com/sunnyday-technologies/MARB)
- Benchmark input: [SunnydayTech/marb-m3-crete](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete)
- Answer key (gated): [SunnydayTech/marb-m3-crete-answer-key](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key)
- Project site: [marb.cadclaw.io](https://marb.cadclaw.io)

`board.json` mirrors the table in the MARB README. To refresh, regenerate the
numbers with the MARB grader and update `board.json`. Optionally drop a
`marb_scoreboard.png` next to `app.py` to show the scoreboard figure above the
table.

The visible table and `board.json` carry per-cell task, scoring version,
publication date, attempted and graded counts, and seed/run ordinals. Existing
frontier cells are dated single runs. Every new frontier cell after 2026-08-28
must have at least three attempted and three graded independent runs and show
median plus population standard deviation. Validate the curated snapshot with
`python scripts/validate_frontier_publication.py` in the source repository.
The gate also reconciles stable run IDs, per-attempt run-log digests, graded
STEP digests, and grade-source provenance for every post-policy cell.

MARB v0.11 also defines `L2-RESOLVE` revision
`top-spreader-x+200-r1`, but it is `defined_unmeasured`: the private resolver
key is locally validated while gated distribution and qualifying runs remain
pending, so there is no L2 score or board row. The visible board and historical
rows are unchanged by that method-only release.

MARB v0.12 also defines `L4-ECO` revision
`second-top-cross-spreader-midright-r1` as `defined_unmeasured`. Its public
CADCLAW snapshot invariant gate is synthetically tested and the task-specific
private requested-placement key is locally validated, but gated upload/readback
has not been performed and no qualifying runs exist. There is no L4 score or
board row, and the visible historical rows remain unchanged.

Developed by Sunnyday Technologies. Contact: info@sunn3d.com
