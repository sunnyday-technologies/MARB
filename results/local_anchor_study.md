# MARB local-anchor study — an 80B open-weight model builds M3-CRETE blind

**Track:** `cadquery_native_driver`, cell `local_anchor` · **Builder:** `qwen3-coder-next:q4_K_M`
(Qwen3-Coder 79.7B / 3B-active, Q4, **text-only**) on a local AI supercomputer, Ollama, 8-turn cap ·
**Kit:** v1.1 · **Blind:** brief + staged kit only, no reference solution, no project memory ·
**Grader:** MARB v0.9 (`marb_grade_all.py`), cadclaw 0.9.0 · **n = 5 / cohort.**

The frontier track (Opus / Codex × CadQuery / Fusion) answers *"how well do the best hosted
models assemble a real machine?"* This local-anchor track plants the **floor**: a single 80B
open-weight coder, text-only, no internet, on one box — the honest low end of the capability
curve, and the model an air-gapped shop could actually run.

## The headline caveat — read this first

**The local builds are loosely-placed parts at roughly the right part *count*, not a rigid,
box-beam-jointed frame.** The model reliably *imports the correct kit parts* and gets the rough
scale, but it does not seat them into a connected structure. Treat the buildability rate
(loadable STEP, part coverage) as the primary signal; the positional grades below quantify
*how far off* the placement is, not whether the machine is "almost assembled" (it is not).
See [the 3-panel figure](figures/marb_local_3panel.png): GOAL vs the two best local builds.

## Buildability — the primary signal (catalogue: `[local-path-redacted]

30 runs across 6 recursive prompt cohorts. **20/30 produced a loadable STEP.** The two cohorts
graded here are the ones that reliably export:

| Cohort (n=5) | Prompt | Loadable | Solids (median) | Notes |
|---|---|---|---|---|
| **mechanics v2** | `cadquery_mechanics_v2` | **5/5** | 98 (37–110) | the one lever that fixed export |
| **lean v5** | `cadquery_lean_v5` | **5/5** | 84 (26–102) | sharpened, spiral-free default |

The reference target is **~101 placed instances**. Both winning cohorts land in the right
order of magnitude on part count — the model is not missing the inventory, it is missing the
*assembly*.

## Positional grades — vs the reference answer key (n=5/cohort)

Graded with the same GAP / POSITION / ORIENTATION rubric as the frontier track, so the cells are
directly comparable. (Reference-vs-itself is the 0 mm / 100 % ceiling, included as a sanity row.)

| Cell | GAP median (mm) | gap ≤1 mm | POS rel (mm) | POS abs (mm) | ORIENT aligned |
|---|---|---|---|---|---|
| Reference (ceiling) | 0.0 | 100 % | 0.0 | 0.0 | 100 % |
| **Local · mechanics v2** | **308 ± 96** | 14 % | **104 ± 43** | 498 ± 126 | **30 % ± 14** |
| **Local · lean v5** | **410 ± 72** | 11 % | **353 ± 157** | 571 ± 115 | **29 % ± 9** |

*GAP = nearest-surface gap on interfaces that should touch (target 0 mm). POS rel = part position
error after best-fit alignment; POS abs = error in the raw exported frame. ORIENT = share of
orientation-gradeable parts pointing the right way.*

**What the numbers say:**

1. **Parts land 100–400 mm off on a 2000 mm machine.** Joints meant to be flush sit ~300–400 mm
   apart at the median. Only ~11–14 % of interfaces are within 1 mm. This is the quantitative
   form of "loosely placed, not jointed."
2. **Orientation is a coin-flip-and-worse:** ~30 % of asymmetric parts are correctly aligned;
   the rest are rotated wrong. The model picks the right part but not the right pose.
3. **Relative ≪ absolute position error** (104 vs 498 mm for mechanics v2): the model captures
   *local* clustering better than it anchors the *whole* assembly — the frame is roughly shaped
   but globally mis-seated/shifted.
4. **Buildability ≠ placement accuracy.** Lean v5 is the *buildability* winner (cleanest export,
   spiral-free) but mechanics v2 places parts **more accurately** (GAP 308 vs 410 mm, POS rel
   104 vs 353 mm). Maximizing export success and part count is a different objective from getting
   the geometry right — a useful caution for anyone optimizing a local agent on "did it build."

## Native gates — inventory / interference / floating (complementary track)

The positional metrics ask *"is each part in the right place?"* The native gates
([`grader/grade_native_step.py`](../grader/grade_native_step.py), graded black-box from the
single exported STEP) ask three structural questions the positional track can't: right part
**mix**, no **clipping**, nothing **floating** off the structure. Reference-vs-itself is the
ceiling. (Full data: [`results/marb_native_grades.json`](marb_native_grades.json).)

| Cell | parts | inventory | interference | floating | findings |
|---|---|---|---|---|---|
| Reference (ceiling) | 101 | ✅ | ✅ | ✅ | 0 |
| Local · mechanics v2 (run_08) | 106 | ❌ | ❌ | ✅ | 6 inventory + **28 clipping pairs** |
| Local · lean v5 (run_26) | 100 | ❌ | ❌ | ✅ | 6 inventory + **20 clipping pairs** |

- **Inventory fails on the *mix*, not the count.** Both builds land near 100 parts, but the
  composition is off: run_08 over-produces plates (16 vs 10) and wheels (40 vs 32) and
  under-produces belts (7 vs 13); run_26 is short on C-beams (10 vs 14) and long on 20×80
  V-slot (6 vs 2). The model picks plausible parts in plausible totals but not the right recipe.
- **Interference fails hard** — 20–28 pairs of solids clip through each other. Combined with the
  positional track, the picture is precise: parts are both *mis-placed* **and** *overlapping*,
  not "almost assembled."
- **Floating passes** — a telling artifact: nothing is detached because the loosely-placed parts
  are *piled together* (everything is within the gap threshold of something). Floating-OK +
  interference-FAIL is the signature of a jumble, not a frame. A future L1+ *connectivity* gate
  (parts joined into one rigid structure) is what would actually separate "pile" from "machine."

## Durable findings (recursive-prompt cohorts A–F, 30 runs)

1. **One tool-mechanic gates buildability.** Base prompt: 1/5 loadable — most failed at *export*,
   passing a `cq.Assembly` to `cq.exporters.export()` (which needs a `Shape`). The fix
   (`asm.save()` / `asm.toCompound()`) alone took the model 1/5 → **5/5** (cohort B).
2. **More scaffolding backfires** at small-model / low-budget. Adding the build-volume note (v3,
   3/5) and design-mode objectives (v4, 2/5) *monotonically hurt* an 8-turn, 3B-active model —
   it is turn-starved, and extra prose spends turns it needs for geometry.
3. **Budget ≠ quality.** Raising the turn cap to 14 (cohort E) recovered export *rate* but not
   coverage; spare turns were burned on a `BoundingBox` `.x`-vs-`.X` probe spiral (one run looped
   14×, 113K tokens, no export) and on shrinking-to-the-envelope.
4. **Lean + failure-targeted wins.** A short prompt that fixes only the proven failure
   (`cadquery_lean_v5`) restored 5/5 with the spiral eliminated — the recommended local default.

Fairness held throughout: guidance was process / CadQuery-mechanics and spec clarifications only,
**never the reference solution**. `m3dcpm:*` models (fine-tuned on M3-CRETE) are banned from blind
runs.

## Unsolved → upstream (CADCLAW grader / brief)

- **Loosely-placed vs jointed** needs L1+ interface/kinematics metrics to score *connection*, not
  just per-part pose — the positional metrics here confirm the gap but can't yet reward a model
  for *seating* a joint.
- **The `2000×1000×1000` figure is the build volume, not a size limit** — it anchors
  counterproductive shrinking; reframe the brief.
- **Multi-mode design** (structural / manufacturability / thermal / fatigue / kinematics, the
  L0–L7 ladder) needs a larger model + budget **and** grader support before it pays off.

## Reproduce

```
# build (one 80B local run, lean-v5 default):
python harness/marb_local_harness.py --model qwen3-coder-next:q4_K_M \
    --guidance-file harness/cadquery_lean_v5.md --run-dir [local-path-redacted]

# grade the cohort (n seeds -> aggregate stats), vs the reference answer key:
python grader/marb_grade_all.py --config <cells.json> --json results/marb_local_grades.json
```

Builder provenance (per-seed model / tokens / timing) is registered in
[`marb_runs.json`](marb_runs.json) under the two `Local · qwen3-coder-next (…)` cells.
