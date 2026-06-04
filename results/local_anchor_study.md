# MARB local-anchor study: an 80B open-weight model builds M3-CRETE blind

**Track:** `cadquery_native_driver`, cell `local_anchor`. **Builder:** `qwen3-coder-next:q4_K_M`
(qwen3-coder-next, 79.7B total parameters, 3B active, Q4_K_M, text-only) on a local AI
supercomputer, served through Ollama, with an 8-turn cap. **Kit:** v1.1. **Blind:** the model
receives the task brief and the staged kit only, with no answer key and no project memory.
**Grader:** MARB v0.9 (`marb_grade_all.py`), cadclaw 0.9.0. **n = 5 per cohort.**

The frontier track measures how well the best hosted models assemble a real machine
(Claude Opus 4.7 and GPT-5 Codex, each on CadQuery and Autodesk Fusion). This local-anchor
track measures the local-anchor floor: the result from a single 80B open-weight coder that is
text-only and runs entirely on one local AI supercomputer. It marks the low end of the current
capability curve, and it is the kind of model a shop with no external network could run on its
own hardware.

## Main caveat: read this first

The local builds place roughly the correct number of parts, but they do not seat those parts
into a connected, box-beam-jointed frame. The model reliably imports the correct kit parts and
reaches the right overall scale. It does not connect them into a rigid structure. Read the
buildability rate first (loadable STEP and part coverage) as the primary signal. The positional
grades below quantify how far each part sits from its intended place. They do not show a
machine that is nearly assembled, because it is not.
See [the 3-panel figure](figures/marb_local_3panel.png): the goal image next to the two best
local builds.

## Buildability: the primary signal

The study ran 30 builds across 6 recursive-prompt cohorts. **20 of 30 produced a loadable
STEP.** The two cohorts graded here are the ones that export reliably.

| Cohort (n=5) | Prompt | Loadable | Solids (median) | Notes |
|---|---|---|---|---|
| **mechanics v2** | `cadquery_mechanics_v2` | **5/5** | 98 (37–110) | the single change that fixed export |
| **lean v5** | `cadquery_lean_v5` | **5/5** | 84 (26–102) | sharpened default, with the probe loop removed |

The answer key contains **about 101 placed instances**. Both winning cohorts reach the right
order of magnitude on part count. The model has the inventory roughly right. It is the assembly
that is wrong.

## Positional grades vs the answer key (n = 5 per cohort)

These cells use the same GAP, POS, and ORIENT rubric as the frontier track, so the numbers are
directly comparable. The answer key graded against itself gives the 0.0 mm and 100% ceiling row,
included as a sanity check.

| Cell | GAP median (mm) | gap ≤ 1 mm | POS relative (mm) | POS absolute (mm) | ORIENT aligned |
|---|---|---|---|---|---|
| Reference (ceiling) | 0.0 | 100% | 0.0 | 0.0 | 100% |
| **Local · mechanics v2** | **308 ± 96** | 14% | **104 ± 43** | 498 ± 126 | **30% ± 14** |
| **Local · lean v5** | **410 ± 72** | 11% | **353 ± 157** | 571 ± 115 | **29% ± 9** |

GAP is the nearest-surface gap on interfaces that should touch, with a target of 0 mm.
POS relative is the part position error after a best-fit alignment. POS absolute is the same
error measured in the raw exported frame. ORIENT is the share of orientation-gradeable parts
placed in the correct rotation.

**What the numbers show:**

1. **Parts land 100 to 400 mm off on a 2000 mm machine.** Joints that should sit flush are
   about 300 to 400 mm apart at the median. Only about 11 to 14% of interfaces fall within
   1 mm. This is the numeric form of parts that are placed but not jointed.
2. **Orientation is below 50% correct.** About 30% of asymmetric parts are aligned correctly,
   and the rest are rotated wrong. The model picks the right part but not the right pose.
3. **Relative position error is far smaller than absolute error** (104 mm vs 498 mm for
   mechanics v2). The model captures local clustering better than it anchors the whole
   assembly. The frame has roughly the right shape but is shifted as a whole.
4. **Buildability is not the same as placement accuracy.** Lean v5 wins on buildability, with
   the cleanest export and no probe loop. Mechanics v2 places parts more accurately (GAP 308 mm
   vs 410 mm, POS relative 104 mm vs 353 mm). Maximizing export success and part count is a
   different objective from getting the geometry right. This is a useful caution for anyone
   tuning a local agent only on whether it builds.

## Native gates: inventory, interference, floating (complementary track)

The positional metrics ask whether each part is in the right place. The native gates
([`grader/grade_native_step.py`](../grader/grade_native_step.py), graded directly from the
single exported STEP) ask three structural questions the positional track cannot. They check
the part mix, whether any solids clip through each other, and whether any part is detached from
the structure. The answer key graded against itself is the ceiling.
Full data: [`results/marb_native_grades.json`](marb_native_grades.json).

| Cell | parts | inventory | interference | floating | findings |
|---|---|---|---|---|---|
| Reference (ceiling) | 101 | ✅ | ✅ | ✅ | 0 |
| Local · mechanics v2 (run_08) | 106 | ❌ | ❌ | ✅ | 6 inventory + **28 clipping pairs** |
| Local · lean v5 (run_26) | 100 | ❌ | ❌ | ✅ | 6 inventory + **20 clipping pairs** |

- **Inventory fails on the mix, not the count.** Both builds land near 100 parts, but the
  composition is wrong. Run_08 over-produces plates (16 vs 10) and wheels (40 vs 32), and
  under-produces belts (7 vs 13). Run_26 is short on C-beams (10 vs 14) and long on 20×80
  V-slot (6 vs 2). The model selects plausible parts in plausible totals, but not the correct
  set.
- **Interference fails clearly.** Between 20 and 28 pairs of solids clip through each other.
  Combined with the positional track, the result is precise: parts are both mis-placed and
  overlapping, not nearly assembled.
- **Floating passes, which is itself informative.** Nothing is detached because the placed
  parts sit close together, so every part is within the gap threshold of another. A passing
  floating gate together with a failing interference gate is the signature of parts grouped
  together rather than a connected frame. A future L1 or higher connectivity gate, which would
  check that parts join into one rigid structure, is what would separate a loose group of parts
  from an assembled machine.

## Durable findings (recursive-prompt cohorts A through F, 30 runs)

1. **One tool mechanic controls buildability.** With the base prompt, only 1 of 5 runs were
   loadable. Most failed at export, because the model passed a `cq.Assembly` to
   `cq.exporters.export()`, which requires a `Shape`. The fix (using `asm.save()` or
   `asm.toCompound()`) alone moved the model from 1 of 5 to **5 of 5** loadable (cohort B).
2. **Adding more guidance reduced results** at this small model size and low turn budget.
   Adding the build-volume note (v3, 3 of 5) and design-mode objectives (v4, 2 of 5) steadily
   hurt an 8-turn, 3B-active model. The model runs out of turns, and extra prose consumes turns
   it needs for geometry.
3. **A higher turn budget did not raise quality.** Raising the turn cap to 14 (cohort E)
   restored the export rate but not coverage. The extra turns were spent on a `BoundingBox`
   `.x` versus `.X` probe loop (one run repeated 14 times, used 113K tokens, and produced no
   export) and on reducing the model toward the build-volume figure.
4. **A short, failure-targeted prompt works best.** A short prompt that fixes only the proven
   failure (`cadquery_lean_v5`) restored 5 of 5 loadable and removed the probe loop. This is the
   recommended local default.

Fairness held throughout. Guidance covered process, CadQuery mechanics, and spec
clarifications only. It never included the answer key. The `m3dcpm` models, which are
fine-tuned on M3-CRETE, are banned from blind runs.

## Open items for the CADCLAW grader and brief

- **Scoring connection, not just per-part pose.** Telling loosely-placed parts from a jointed
  frame needs L1 or higher interface and kinematics metrics that score connection. The
  positional metrics here confirm the gap but cannot yet reward a model for seating a joint.
- **The `2000 × 1000 × 1000 mm` figure is the build volume, not a size limit.** Treating it as
  a size limit drives the model to shrink the assembly. The brief should state this clearly.
- **Multi-mode design** (structural, manufacturability, thermal, fatigue, and kinematics, the
  L0 to L7 ladder) needs a larger model and budget, plus grader support, before it is worthwhile.

## Reproduce

```bash
# Build one local run with the lean v5 default:
python harness/marb_local_harness.py --model qwen3-coder-next:q4_K_M \
    --guidance-file harness/cadquery_lean_v5.md --run-dir runs/<name>

# Grade the cohort (n seeds, aggregated stats) against the answer key:
python grader/marb_grade_all.py --config <cells.json> --json results/marb_local_grades.json
```

Builder provenance (per-seed model, tokens, and timing) is registered in
[`marb_runs.json`](marb_runs.json) under the two `Local · qwen3-coder-next (…)` cells.
