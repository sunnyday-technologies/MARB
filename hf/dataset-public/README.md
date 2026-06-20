---
pretty_name: "MARB — Mechanical Assembly Readiness Benchmark (M3-CRETE, task 1)"
license: other
license_name: mixed-mit-and-cc-by-sa-4.0
license_link: https://github.com/sunnyday-technologies/MARB/blob/main/kits/LICENSE.md
language:
  - en
task_categories:
  - other
size_categories:
  - n<1K
viewer: false
tags:
  - benchmark
  - cad
  - step-files
  - mechanical-engineering
  - spatial-reasoning
  - assembly
  - agents
  - evaluation
  - llm
---

# MARB — Mechanical Assembly Readiness Benchmark

*Can AI assemble a real machine?*

MARB is a tool-independent, automatically graded benchmark. It measures how
correctly AI-assisted CAD places the parts of a real machine: the right
position, the right orientation, and the right gap at every interface. The model
is given only the authored parts and a set of goal renders — a 3/4 isometric
overview plus front, top, and side views, which show how the parts relate in
space and reveal components a single view would hide. It receives no build
steps. Any tool or agent can be tested, including Autodesk Fusion, CadQuery, or a
custom agent.

MARB is developed by **Sunnyday Technologies**. It runs on the open-source
[CADCLAW](https://github.com/sunnyday-technologies/CADCLAW) verification engine.
Source of truth: [github.com/sunnyday-technologies/MARB](https://github.com/sunnyday-technologies/MARB).
Project home: [marb.cadclaw.io](https://marb.cadclaw.io). Live board:
[SunnydayTech/marb-leaderboard](https://huggingface.co/spaces/SunnydayTech/marb-leaderboard).

This dataset is the **public benchmark input**. It contains the blind kits, the
goal renders, the frozen task brief, and the scoring spec. It does **not** contain
the answer key. The answer key is a separate, access-gated dataset (gated to
prevent training-data contamination, not for secrecy):
[SunnydayTech/marb-m3-crete-answer-key](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key).

## What it grades (v0.9)

MARB grades three positional metrics against the answer key, under a fixed, tight
standard:

- **GAP** is the error between the actual and intended interface gap. The
  intended gap is about 0 mm where parts bolt together and about 1 to 2 mm where
  parts move. GAP is reported as a median in millimeters. It is the primary
  functional score.
- **ORIENT** is the share of orientation-gradeable (asymmetric) parts placed in
  the correct rotation, reported as a percent aligned. Rotationally symmetric
  parts are skipped.
- **POS** is the position error of each part versus the answer key after a
  best-fit rigid alignment. It is reported as a median in millimeters, both
  absolute and neighbor-relative.

Buildability stays on as a secondary gate.

## What is in this dataset

- `kits/` — versioned blind kits (one zip per version). A kit holds the authored
  parts, the goal renders (a 3/4 isometric overview plus front, top, and side
  views), and the task brief. No answer key. Versions are tracked in
  `kits/KIT_VERSIONS.md`.
- `prompts/` — the frozen task brief and the per-backend driver stubs.
- `spec/MARB_SCORING.md` — the canonical, versioned scoring method.
- `benchmark.yaml` — gate weights for the secondary buildability score.

The grader code lives on GitHub, depends on the `cadclaw` PyPI package, and is
kept there as the single source of truth.

## Run a model against it

1. Give the model a blind kit from `kits/`. It holds the authored parts, the goal
   image, and the task brief, with no answer key.
2. Run the model in a sealed, memoryless context so the answer key cannot leak.
   Use a neutral folder with cross-session memory turned off.
3. Hand the exported STEP file back and grade it.

To grade, install the engine and request the gated answer key:

```bash
pip install "cadclaw>=0.9.0"
# Download the gated answer key (see the answer-key dataset card for access).
python grader/marb_pose_metric.py   --ref m3_reference_round1.step --run your_export.step
python grader/marb_gap_metric.py    --ref m3_reference_round1.step --run your_export.step
python grader/marb_orient_metric.py --ref m3_reference_round1.step --run your_export.step
```

Grader source: [github.com/sunnyday-technologies/MARB/tree/main/grader](https://github.com/sunnyday-technologies/MARB/tree/main/grader).

## Versioning and comparability

Results are comparable only within a single kit cohort and a fixed scoring
version. Each run records its kit version, its model and tool versions, and the
client environment. Do not pool runs across kit versions without noting it.

## License

The MARB code, brief, and spec are MIT. Copyright (c) 2026 Sunnyday Technologies.
The CAD parts bundled in the kits are licensed separately: OpenBuilds-derived
parts are CC BY-SA 4.0, and the two Sunnyday-authored parts are MIT. Full notice:
[kits/LICENSE.md](https://github.com/sunnyday-technologies/MARB/blob/main/kits/LICENSE.md).
V-Slot, C-Beam, and V-Wheel are registered trademarks of OpenBuilds LLC.

## Citation

If you use MARB in published research or derivative work, please cite it. See the
[CITATION.cff](https://github.com/sunnyday-technologies/MARB/blob/main/CITATION.cff)
in the source repository.

Contact: info@sunn3d.com
