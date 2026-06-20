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
short_description: Can AI assemble a real machine? Mechanical assembly benchmark.
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
Each run builds the same ~100-part machine from the same blind kit and is graded
identically by the open-source [CADCLAW](https://github.com/sunnyday-technologies/CADCLAW)
engine, ranked by GAP median.

- Source of truth: [github.com/sunnyday-technologies/MARB](https://github.com/sunnyday-technologies/MARB)
- Benchmark input: [SunnydayTech/marb-m3-crete](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete)
- Answer key (gated): [SunnydayTech/marb-m3-crete-answer-key](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key)
- Project site: [marb.cadclaw.io](https://marb.cadclaw.io)

`board.json` mirrors the table in the MARB README. To refresh, regenerate the
numbers with the MARB grader and update `board.json`. Optionally drop a
`marb_scoreboard.png` next to `app.py` to show the scoreboard figure above the
table.

Developed by Sunnyday Technologies. Contact: info@sunn3d.com
