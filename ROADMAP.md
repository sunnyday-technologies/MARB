# MARB roadmap

MARB is early and openly versioned. The aim is a benchmark that measures how much
better AI-assisted CAD assembly can get, across the levels mechanical design
actually requires, with results that stay reproducible as models and tools change.

## Now (v0.9)
- Three positional metrics: GAP, ORIENT, POS (see `spec/MARB_SCORING.md`).
- One reference task (the M3 gantry frame, ~100 parts) with versioned blind kits.
- Tool-independent grading on the exported STEP, via the CADCLAW engine.

## Next
- **Statistics.** Multiple seeds per model+tool cell; median / mean / std and
  error bars (`grader/marb_grade_all.py --manifest`). Awaiting repeat runs.
- **More drivers.** CadQuery, Autodesk Fusion, and additional tools
  (build123d and others) on the identical lens. Contributions welcome.
- **A second task.** A different machine type, to claim generality. Task 1 is
  `tasks/m3_crete/`; the layout leaves room for `tasks/<next>/`.
- **Held-out split.** Once there are multiple tasks, an optional held-out
  leaderboard split for contamination control.
- **Metric hardening.** A random-floor baseline; principal-axis (OBB/PCA)
  orientation; a separate manufacturing-tolerance axis.

## Method principles
- **Blind by construction.** Drivers receive only the kit and the goal image,
  never the answer key, and must run in a sealed, memoryless context.
- **Tight, fixed standard.** Tolerances are graded exact and never loosened to
  flatter a model; the headline is a median error that drops toward zero as models
  improve.
- **Comparable within a cohort.** Results compare only within a kit version and a
  fixed scoring version. Every run records its kit, model, tool, and environment.

Versioned scoring lives in `spec/MARB_SCORING.md`; kit versions in
`kits/KIT_VERSIONS.md`.
