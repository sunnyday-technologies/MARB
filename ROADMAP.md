# MARB roadmap

MARB is early and openly versioned. The goal is a benchmark that measures how much
AI-assisted CAD assembly improves over time. It grades at the levels mechanical
design actually requires, and it keeps results reproducible as models and tools
change.

## Now (v0.9)

- Three positional metrics: GAP, ORIENT, and POS. See [`spec/MARB_SCORING.md`](spec/MARB_SCORING.md).
- One reference task: the M3-CRETE gantry frame (task 1), about 100 parts, with versioned blind kits.
- Tool-independent grading on the exported STEP, through the CADCLAW engine.

## Next

- **Statistics.** Run multiple seeds per model and tool cell. Report median, mean,
  standard deviation, and error bars through [`grader/marb_grade_all.py`](grader/marb_grade_all.py) `--manifest`.
  This work awaits repeat runs.
- **More drivers.** CadQuery and Autodesk Fusion are covered today. Add more tools
  such as build123d, all graded the same way. Contributions are welcome.
- **A second task.** Add a different machine type to show the benchmark generalizes.
  Task 1 lives in [`tasks/m3_crete/`](tasks/m3_crete/), and the layout leaves room for `tasks/<next>/`.
- **Held-out split.** Once several tasks exist, add an optional held-out leaderboard
  split to control for contamination.
- **Metric hardening.** Add a random-floor baseline. Add principal-axis (OBB or PCA)
  orientation. Add a separate manufacturing-tolerance axis.
- **Occlusion-aware goal views.** Today the goal is shown as surface renders — a 3/4
  isometric overview plus front, top, and side views — which already expose more
  spatial relationships and components than a single view. But in denser assemblies,
  parts nested inside the machine are hidden in every surface view, so their placement
  cannot be read from the renders alone. Add a way to reveal occluded parts —
  transparency stacking, exploded views, or section cuts — so the goal stays legible
  for accurate placement as assemblies grow more complex. Contributions welcome.

## Method principles

- **Blind by design.** Drivers receive only the kit and the goal renders, never the
  answer key. Each driver runs in a sealed context with no memory between runs.
- **Tight, fixed standard.** Tolerances are graded exactly and stay the same for
  every model. The headline number is a median error that gets smaller as models
  improve.
- **Comparable within a cohort.** Results compare only within one kit version and one
  fixed scoring version. Every run records its kit, model, tool, and environment.

Versioned scoring lives in [`spec/MARB_SCORING.md`](spec/MARB_SCORING.md). Kit versions
live in [`kits/KIT_VERSIONS.md`](kits/KIT_VERSIONS.md).
