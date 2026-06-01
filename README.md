# MARB — Mechanical Assembly Readiness Benchmark

**MARB measures how well AI-assisted CAD places the parts of a real machine.** Given
the authored parts and a single goal image (no build steps), how correctly does an
AI locate every part: right position, right orientation, and the right gap at every
interface? It is tool-independent (Fusion, CadQuery, or any agent) and graded
automatically.

MARB is developed by **Sunnyday Technologies**. It runs on the open-source
[CADCLAW](https://github.com/sunnyday-technologies/CADCLAW) verification engine.
Project home: [cadclaw.io/benchmark](https://cadclaw.io/benchmark).

---

## What it grades (v0.9)

Three positional metrics against the answer key, under a tight, fixed standard
(see [`spec/MARB_SCORING.md`](spec/MARB_SCORING.md)):

- **GAP** — error vs the intended interface gap (about 0 mm where parts bolt
  together, about 1 mm where they move). The primary functional score.
- **ORIENT** — share of asymmetric parts in the correct rotation.
- **POS** — position error vs the answer key, reported absolute and
  neighbour-relative.

Buildability / interference stays on as a secondary gate.

## First results

Three frontier AI workflows, one ~100-part machine each, graded identically:

| Model · tool | GAP median | ORIENT aligned | POS rel median |
|---|---|---|---|
| Claude Opus 4.7 · CadQuery | **0.0 mm** | 51% | 49.9 mm |
| Claude Opus 4.7 · Fusion | 2.0 mm | 47% | 47.7 mm |
| GPT-5 Codex · CadQuery | 7.8 mm | **69%** | 47.2 mm |
| *Reference (answer key)* | *0.0 mm* | *100%* | *0.0 mm* |

None is buildable yet. The bar is a machine you could actually bolt together; that
is what the metrics measure. Full write-up:
[`results/comparison_claude_tracks.md`](results/comparison_claude_tracks.md) and
[`results/prompt_framework_findings.md`](results/prompt_framework_findings.md).

## The local-anchor floor

The frontier table is the ceiling; this is the floor. A single **80B open-weight coder**
(`qwen3-coder-next`, text-only, on one local AI supercomputer) building the same machine blind — the model
an air-gapped shop could run, no internet, no hosted API.

| Cell (n=5) | GAP median | ORIENT aligned | POS rel median |
|---|---|---|---|
| Local · qwen3-coder-next (mechanics v2) | 308 ± 96 mm | 30% | 104 ± 43 mm |
| Local · qwen3-coder-next (lean v5) | 410 ± 72 mm | 29% | 353 ± 157 mm |

It reliably imports the **right parts** but seats them as **loosely-placed pieces, not a jointed
frame** (parts land 100–400 mm off on a 2000 mm machine). The native gates agree: the part *mix*
is wrong and 20–28 part-pairs *clip*, while nothing floats free — a pile, not a machine. One
CadQuery export-mechanic fix takes it 1/5 → 5/5 buildable; more prompt scaffolding *backfires*;
budget ≠ quality. Write-up: [`results/local_anchor_study.md`](results/local_anchor_study.md) ·
figure: [`results/figures/marb_local_3panel.png`](results/figures/marb_local_3panel.png).

## Quickstart

```bash
pip install "cadclaw>=0.9.0"  # the grading engine + STEP I/O (see requirements.txt: 0.9.0 is on
pip install -r requirements.txt   # CADCLAW `main`; if not yet on PyPI, install it from the repo)

# grade the reference against itself (the ceiling) plus the bundled runs
python grader/marb_grade_all.py --json results/marb_v0_9_grades.json

# grade a set of runs via the run registry (median / mean / std per cell)
python grader/marb_grade_all.py --manifest results/marb_runs.json \
    --json results/marb_v0_9_stats.json
```

A single run can be graded directly:

```bash
python grader/marb_pose_metric.py  --ref tasks/m3_crete/m3_reference_round1.step --run your_export.step
python grader/marb_gap_metric.py   --ref tasks/m3_crete/m3_reference_round1.step --run your_export.step
python grader/marb_orient_metric.py --ref tasks/m3_crete/m3_reference_round1.step --run your_export.step
```

## Run a model against it

1. Give the model a **blind kit** from [`kits/`](kits/) (authored parts + the goal
   image + the task brief, no answer key). Versions are tracked in
   [`kits/KIT_VERSIONS.md`](kits/KIT_VERSIONS.md).
2. Run it in a **sealed, memoryless context** so the answer key cannot leak (a
   neutral folder, cross-session memory off; see the blind-run protocol in the spec).
3. Hand the exported STEP back and grade it with the commands above.

## Layout

```
spec/MARB_SCORING.md     the canonical scoring method (versioned)
grader/                  the metrics + figure builders (depend on the cadclaw package)
tasks/m3_crete/          the reference answer key (STEP + spec) for task 1
kits/                    versioned blind kits handed to the driver, + KIT_VERSIONS.md
prompts/                 the frozen task brief + per-backend driver stubs + generator
results/                 grades, run registry, findings, and figures
benchmark.yaml           gate weights for the secondary buildability score
```

## Versioning and comparability

Results are comparable only within a **kit cohort** and a fixed scoring version.
Each run records its kit version, the model + tool versions, and the client
environment (see [`results/marb_runs.json`](results/marb_runs.json)). Do not pool
runs across kit versions without noting it.

## Citation

If you use MARB in published research or derivative work, please cite it (see
[`CITATION.cff`](CITATION.cff)).

## License

MIT (the MARB code). Copyright (c) 2026 Sunnyday Technologies. See [`LICENSE`](LICENSE).
The CAD parts bundled in the blind kits are licensed separately — OpenBuilds-derived parts
under CC BY-SA 4.0, Sunnyday-authored parts under the repo MIT; see
[`kits/LICENSE.md`](kits/LICENSE.md). Product and company names used to identify the tools
tested are trademarks of their respective owners.
