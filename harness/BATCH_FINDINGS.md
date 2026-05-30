# Local CadQuery track — n=10 batch findings (qwen3-coder-next, 2026-05-30)

Driver: `qwen3-coder-next:q4_K_M` on the local AI supercomputer ([redacted-host]), CadQuery 2.7.0, kit v1.1,
text-only, 8-turn cap. Two cohorts of 5, identical except the system-prompt guidance.
Raw catalogue: `local_batch_A_catalogue.csv` (and `[local-path-redacted]

**These are BUILDER metrics — buildability and BOM coverage, NOT grades.** A loadable
110-solid assembly can still be oversized/misoriented and score low. Grading is Session C.

## Result

| Cohort | prompt_variant | loadable STEP | solids (loadable) | dominant failure |
|---|---|---|---|---|
| A | `base` | **1 / 5** | 15 | dies at export |
| B | `cadquery_mechanics_v2` | **5 / 5** | 37, 82, 98, 103, 110 (median ~98) | oversizes envelope |

| run | variant | status | loadable | solids | bbox (mm) | total_tokens |
|---|---|---|---|---|---|---|
| 1 | base | no_valid_artifact | ✗ | 0 | — | 31.8K |
| 2 | base | complete | ✓ | 15 | 140×958×1028 | 47.9K |
| 3 | base | no_valid_artifact | ✗ | 0 | — | 16.5K |
| 4 | base | no_valid_artifact | ✗ | 0 | — | 24.7K |
| 5 | base | no_valid_artifact | ✗ | 0 | — | 41.3K |
| 6 | v2 | complete | ✓ | 82 | 2080×1462×2134 | 65.5K |
| 7 | v2 | complete | ✓ | 98 | 2064×1632×1050 | 80.6K |
| 8 | v2 | complete | ✓ | 110 | 2742×1742×1420 | 63.6K |
| 9 | v2 | complete | ✓ | 103 | 1927×1788×2459 | 61.3K |
| 10 | v2 | complete | ✓ | 37 | 1040×1342×1421 | 57.5K |

## General learning (mechanics, not answer — the Fusion-style fix)

Cohort A failed almost entirely on **one CadQuery mechanic, not on design**: the model
wrote plausible placement code, then passed a `cq.Assembly` straight to
`cq.exporters.export(asm, ...)` (which needs a Shape) and called `asm.val()` (doesn't
exist) — so the build died at the export line after burning its turns re-deriving
bbox/center idioms. This is the direct analogue of the Fusion `addExistingComponent`
token-drain.

`cadquery_mechanics_v2.md` supplies the correct `import → probe → place → asm.save()` /
`cq.exporters.export(asm.toCompound(), ...)` idioms plus an "export early, refine later"
strategy. Effect: loadable 20% → 100%, instance coverage 15 → ~98 median. It contains
**no placements, counts, or orientations** — verified line-by-line — so cohort B remains
a blind run; the brief + kit stay the only design inputs.

## Correction: the "oversizing" was NOT a defect (build volume ≠ machine size)

Originally this section flagged cohort B for "exceeding the 2000×1000×1000 target."
**That framing was wrong** (corrected 2026-05-30, per Nick): 2000×1000×1000 mm is the
achievable **print/build volume**, not the machine's outer size. The frame, rails,
gantry, Z-posts and end mounts must extend *beyond* it — a carriage cannot print to the
ends of its own travel — so an assembly larger than 2000×1000×1000 is **expected and
correct**. The grader confirms this is not a scoring bound (`benchmark.yaml` metadata
only; POS/ORIENT/GAP match parts to the reference, not to a size limit).

The real defect was the opposite: the brief's "target envelope/class" wording made the
model try to **shrink** the machine to fit — run 10 regressed to 37 solids doing exactly
that. Fix belongs in the spec/brief (build-volume wording), carried into v3 prompt
guidance. We still do NOT hand the model the numbers as a target to hit (gate-gaming,
which the brief forbids); v3 only corrects the *meaning* of the figure.

## Recursive progression A–D (added 2026-05-30) — more scaffolding backfires

| Cohort | prompt_variant | loadable | solids (median, loadable) | total_tokens (range) |
|---|---|---|---|---|
| A | base | 1/5 | 15 | 16–48K |
| B | cadquery_mechanics_v2 | **5/5** | **~98** | 50–81K |
| C | cadquery_mechanics_v3 (+build-volume) | 3/5 | 28 | 37–58K |
| D | design_requirements_v4 (+design modes) | 2/5 | ~24 | 35–72K |

The single export-idiom fix (v2) is the dominant lever and the buildability optimum.
Every block of text added after it (v3 build-volume note, v4 multi-mode design
objectives) **monotonically degraded** buildability and instance coverage. Logs show
why: with a longer system prompt the model spends its ~8 turns re-reading the brief and
re-deriving the same `BoundingBox()`/`Center()` access friction (batch-D runs 17 & 18
never exported), instead of building. The binding constraint at 8B-active / 8 turns is
**turn budget**, not capability or willingness — and the extra design asks buy no
*measurable* quality because the grader scores placement (POS/ORIENT/GAP), not the design
modes. n=5/cohort is noisy, but the v2≫v3≈v4 gap is consistent across 20 runs.

**Implication for the multi-mode (L0–L7) ambition:** asking a small local model to
*consider* rigidity/manufacturability/thermal/etc. in-prompt is counterproductive until
(a) it has the turn/compute budget to act on it and (b) the grader can score those modes.
Next experiment: hold v4 text fixed and raise the turn cap to separate budget-starvation
from capability.

### Cohort E — v4 text @ 14-turn budget (runs 21–25)

| metric | D (v4 @ 8) | E (v4 @ 14) |
|---|---|---|
| loadable | 2/5 | **4/5** |
| solids (median, loadable) | ~24 | ~30 |
| tokens (range) | 35–72K | 53–139K |

Raising the cap **partly confirms the budget hypothesis**: export rate recovered 2/5→4/5,
so v4 was partly budget-starved. But coverage stayed ~30 (vs v2's ~98) and no better
design appeared — the extra turns were consumed by two failure modes, not by building:

1. **The `BoundingBox()`/`Center()` access spiral is a hard failure.** Run 23 spent all 14
   turns repeating "try a different approach to get the center coordinates" (113K tokens,
   no export). The `.x` vs `.X` / `bb.center` confusion isn't reliably fixed by the crib
   when it's buried in the longer v4 prompt. More budget just means more wasted loops.
2. **The build-volume clarification doesn't stick.** Runs 21/22/24/25 still treated
   2000×1000×1000 as a size limit and burned late turns *shrinking* to it, despite v4
   stating it is the build volume. The bare number anchors the model regardless — arguing
   for removing the number from the prompt and forbidding any resize-to-fit, not explaining
   it (consistent with Nick's "don't add boundaries").

**Net:** the lean v2 export-fix remains the buildability optimum; budget recovers *export*
but not *coverage* or *quality*. The productive next prompt is leaner+sharper, not richer.

## Comparability + recommendation

`base` is the no-hint control; `cadquery_mechanics_v2` is a new cohort (like kit
v1.1 vs v1.2 for Fusion). Do not pool them on one leaderboard without noting the
variant. For future local runs, drive with
`--guidance-file harness/cadquery_mechanics_v2.md` (recorded as `prompt_variant`);
keep `base` only as the control.
