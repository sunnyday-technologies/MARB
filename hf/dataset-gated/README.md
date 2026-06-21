---
pretty_name: "MARB Answer Key (M3-CRETE, task 1) — gated"
license: other
license_name: mixed-mit-and-cc-by-sa-4.0
license_link: https://github.com/sunnyday-technologies/MARB/blob/main/kits/LICENSE.md
language:
  - en
task_categories:
  - image-to-3d
  - text-to-3d
  - other
size_categories:
  - n<1K
viewer: false
tags:
  - benchmark
  - cad
  - step-files
  - mechanical-engineering
  - answer-key
  - held-out
  - evaluation
extra_gated_heading: "Request access to the MARB answer key"
extra_gated_description: >
  This dataset is the answer key (the reference assembled STEP and the placement
  spec) for MARB task 1, M3-CRETE. It is gated to prevent inadvertent
  training-data contamination, not for secrecy or security: the data is openly
  licensed and access is granted for any legitimate use (grading runs, research).
  Access is logged. The public benchmark input (kits, brief, scoring spec) is in
  the open dataset and does not require this gate.
extra_gated_fields:
  Name: text
  Affiliation or project: text
  Intended use: text
  I will not include this answer key in any model training or pretraining corpus: checkbox
  I will not redistribute this answer key outside my team: checkbox
  I will cite MARB in any published results: checkbox
extra_gated_button_content: "Request access"
---

# MARB — Mechanical Assembly Readiness Benchmark

*Can AI assemble a real machine?*

## Answer key (gated) — M3-CRETE, task 1

This is the **answer key** for MARB task 1, the M3-CRETE gantry frame. It holds
the reference assembled geometry and the placement spec that the MARB grader
scores runs against:

- `m3_reference_round1.step` — the reference assembled STEP (the intended machine).
- `m3_reference_assembly.yaml` — the placement spec: per-part intended pose,
  interface gaps, and orientation labels.

The stock parts the model places (beams, belts, motor, spacers, pinion) are not
here. Those are inputs and ship in the open blind kit.

## Why this is gated

The gate is about **contamination, not secrecy or security**. The answer key is
openly licensed (see License below) and is not confidential. Gating only keeps it
out of the crawlable file tree that feeds model training data, so the benchmark
keeps measuring capability rather than memorization. Access is granted for any
legitimate use; manual approval is a light check, not a barrier.

MARB grades how well an AI places parts against this reference. If the answer key
is freely crawlable, it leaks into model training data and the benchmark stops
measuring capability. Gating this dataset keeps a logged, terms-bound channel for
legitimate use (grading runs, research) while keeping it out of the open file
tree that crawlers and training pipelines ingest.

This is not a guarantee against contamination. Anyone granted access can leak the
key. The durable defense is task rotation: new machines are added as held-out
tasks whose keys are released only through this gated channel. See the
[MARB roadmap](https://github.com/sunnyday-technologies/MARB/blob/main/ROADMAP.md).

## How to use it

1. Get access through the request form above.
2. Download the reference STEP.
3. Install the engine and grade your exported run:

```bash
pip install "cadclaw>=0.9.0"
python grader/marb_pose_metric.py   --ref m3_reference_round1.step --run your_export.step
python grader/marb_gap_metric.py    --ref m3_reference_round1.step --run your_export.step
python grader/marb_orient_metric.py --ref m3_reference_round1.step --run your_export.step
```

The grader and metrics live in the
[MARB repository](https://github.com/sunnyday-technologies/MARB). The public
benchmark input (the kits you hand the model) is the open dataset
[SunnydayTech/marb-m3-crete](https://huggingface.co/datasets/SunnydayTech/marb-m3-crete).

## License

The placement spec is MIT. The reference geometry includes OpenBuilds-derived
parts under CC BY-SA 4.0 and two Sunnyday-authored parts under MIT. Full notice:
[kits/LICENSE.md](https://github.com/sunnyday-technologies/MARB/blob/main/kits/LICENSE.md).
V-Slot, C-Beam, and V-Wheel are registered trademarks of OpenBuilds LLC.

Contact: info@sunn3d.com
