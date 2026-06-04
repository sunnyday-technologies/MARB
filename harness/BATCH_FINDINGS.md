# Local CadQuery track: 10-run batch findings (qwen3-coder-next, 2026-05-30)

Builder: `qwen3-coder-next:q4_K_M` on a local AI supercomputer, CadQuery 2.7.0, kit v1.1,
text-only, 8-turn cap. Two cohorts of five runs each. The cohorts are identical except for
the system-prompt guidance. The raw run log is `harness/local_batch_A_catalogue.csv`.

These are builder metrics: buildability and bill-of-materials coverage, not grades. A loadable
110-solid assembly can still be oversized or misoriented and still score low. Grading is a
separate session. The local-anchor study reports the GAP, ORIENT, and POS grades.

## Result

| Cohort | prompt_variant | loadable STEP | solids (loadable) | dominant failure |
|---|---|---|---|---|
| A | `base` | **1 / 5** | 15 | fails at export |
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

## What fixed cohort A: a CadQuery mechanic, not the design

Cohort A failed almost entirely on one CadQuery mechanic, not on the design. The model wrote
plausible placement code. It then passed a `cq.Assembly` straight to
`cq.exporters.export(asm, ...)`, which needs a `Shape`, and called `asm.val()`, which does not
exist. The build failed at the export line. The model then spent its remaining turns
re-deriving the bounding-box and center idioms. This is the CadQuery analogue of the Autodesk
Fusion `addExistingComponent` failure that consumes turns.

`harness/cadquery_mechanics_v2.md` supplies the correct sequence: import, probe, place, then
`asm.save()` or `cq.exporters.export(asm.toCompound(), ...)`. It also adds an "export early,
refine later" strategy. The effect was clear: the loadable rate rose from 1 of 5 to 5 of 5, and
median instance coverage rose from 15 to about 98 solids. The guidance contains no placements,
no part counts, and no orientations. This was verified line by line. Cohort B therefore remains
a blind run. The brief and the kit stay the only design inputs.

## Correction: the oversizing was not a defect

The machine build volume is 2000 × 1000 × 1000 mm. This is the volume the machine can print, not
the machine's outer size. The frame, rails, gantry, vertical posts, and end mounts must extend
beyond it, because a moving carriage cannot reach the far ends of its own travel without
structure past those ends. An assembly larger than 2000 × 1000 × 1000 mm is therefore expected
and correct.

An earlier draft of this document flagged cohort B for exceeding 2000 × 1000 × 1000 mm. That was
wrong. The grader confirms this is not a scoring bound. The figure lives in `benchmark.yaml` as
metadata only. The GAP, ORIENT, and POS metrics match parts to the answer key, not to a size
limit.

The real defect was the opposite. The brief's "target envelope" wording made the model shrink
the machine to fit the figure. Run 10 regressed to 37 solids doing exactly that. The fix belongs
in the spec and the brief, in how the build volume is described. It carries into the v3 prompt
guidance. We still do not hand the model the build-volume numbers as a target to hit, because the
brief forbids optimizing to the gate. The v3 fix only corrects the meaning of the figure.

## Recursive progression A to D: more guidance text reduced buildability

| Cohort | prompt_variant | loadable | solids (median, loadable) | total_tokens (range) |
|---|---|---|---|---|
| A | base | 1/5 | 15 | 16–48K |
| B | cadquery_mechanics_v2 | **5/5** | **~98** | 50–81K |
| C | cadquery_mechanics_v3 (+build-volume) | 3/5 | 28 | 37–58K |
| D | design_requirements_v4 (+design modes) | 2/5 | ~24 | 35–72K |

The single export-idiom fix in v2 is the dominant lever and the buildability optimum. Every
block of text added after it reduced buildability and instance coverage. This includes the v3
build-volume note and the v4 multi-mode design objectives.

The logs explain why. With a longer system prompt, the model spends its eight turns re-reading
the brief and re-deriving the same `BoundingBox()` and `Center()` access friction. Cohort D runs
17 and 18 never exported. The binding constraint at 3B-active and eight turns is the turn budget,
not capability or willingness. The extra design requests buy no measurable quality, because the
grader scores placement through GAP, ORIENT, and POS, not the design modes.

n = 5 per cohort is noisy. The gap is still consistent across all 20 runs: v2 is far ahead of v3,
and v3 and v4 are about equal.

This affects the multi-mode design ambition (the L0 to L7 ladder). Asking a small local model to
weigh rigidity, manufacturability, thermal behavior, and similar concerns inside the prompt is
counterproductive until two conditions hold. First, the model needs the turn budget and compute
budget to act on the request. Second, the grader needs to be able to score those modes. The next
experiment holds the v4 text fixed and raises the turn cap, to separate budget starvation from
capability.

### Cohort E: v4 text at a 14-turn budget (runs 21 to 25)

| metric | D (v4 @ 8) | E (v4 @ 14) |
|---|---|---|
| loadable | 2/5 | **4/5** |
| solids (median, loadable) | ~24 | ~30 |
| tokens (range) | 35–72K | 53–139K |

Raising the turn cap partly confirms the budget hypothesis. The export rate recovered from 2 of 5
to 4 of 5, so v4 was partly starved for turns. But coverage stayed near 30 solids, against about
98 for v2, and no better design appeared. The extra turns went to two failure modes, not to
building.

1. **The `BoundingBox()` and `Center()` access loop is a hard failure.** Run 23 spent all 14
   turns repeating "try a different approach to get the center coordinates" and used 113K tokens
   with no export. The confusion between `.x` and `.X`, and between forms of `bb.center`, is not
   reliably fixed by the crib note when that note is buried in the longer v4 prompt. More budget
   only produces more wasted loops.
2. **The build-volume clarification did not hold.** Runs 21, 22, 24, and 25 still treated
   2000 × 1000 × 1000 mm as a size limit and spent late turns shrinking to it, even though v4
   states it is the build volume. The figure draws the model toward it regardless of the
   explanation. This argues for removing the number from the prompt and forbidding any
   resize-to-fit, rather than explaining the number.

In summary: the lean v2 export fix remains the buildability optimum. A larger budget recovers the
export rate but not coverage or quality. The next prompt should be leaner and sharper, not richer.

### Cohort F: lean v5 at 8 turns (runs 26 to 30), the consolidating step

`harness/cadquery_lean_v5.md` distills every prior finding into a lean prompt. It states the exact
lowercase `.x` idiom, "probe at most once, never loop", the correct Assembly export, and "never
resize to fit a number". It drops the design-mode list that reduced buildability.

| metric | B (v2 @8) | F (v5 @8) |
|---|---|---|
| loadable | 5/5 | **5/5** |
| solids (median, loadable) | 98 | 84 (102, 88, 84, 30, 26) |
| `.x` probe loop | n/a | **eliminated** (the "probe once" rule held) |

v5 matches v2 on buildability at 5 of 5. It also removes the center-access loop that consumed a
full 14-turn run in cohort E. v5 is therefore the more robust prompt, even though v2's median
coverage is marginally higher within n = 5 noise. The instinct to shrink toward the build-volume
figure still appears: runs 26, 28, and 30 note the assembly is "too large". But with the "never
resize" rule, the model repositions parts instead of deleting them. Run 26 kept 102 solids while
shrinking.

## Full progression and recommendation

| Cohort | prompt_variant | budget | loadable | solids (median) |
|---|---|---|---|---|
| A | base | 8 | 1/5 | 15 |
| B | cadquery_mechanics_v2 | 8 | 5/5 | 98 |
| C | cadquery_mechanics_v3 | 8 | 3/5 | 28 |
| D | design_requirements_v4 | 8 | 2/5 | 24 |
| E | design_requirements_v4 | 14 | 4/5 | 30 |
| F | **cadquery_lean_v5** | 8 | **5/5** | 84 |

Recommended local-track default: `--guidance-file harness/cadquery_lean_v5.md`. It ties the best
buildability, eliminates the worst failure mode, and is the leanest prompt that still works. Keep
`base` as the no-hint control. These are distinct cohorts, comparable to kit v1.1 versus kit v1.2
for Fusion. Always note the `prompt_variant`, and never combine variants on one leaderboard.

## What remains unsolved here (belongs upstream or in future runs)

- **Structural quality.** Across all 30 runs the output is loosely placed parts, not a rigid,
  jointed frame. Measuring this needs the grader's L1 and higher interface and kinematics metrics.
  Coaching it inside the prompt leaks design intent, and as cohorts C and D show, it reduces
  buildability at this model scale.
- **The build-volume figure pulls the model toward shrinking.** No prompt wording fully overrode
  it. The real fix is to reframe the brief and the spec so 2000 × 1000 × 1000 mm is not presented
  as a box to fit inside.
- **Multi-mode design (L0 to L7).** Defer this to two conditions. First, the upstream brief once
  the grader can score those modes. Second, a run with a higher budget or a larger model. At
  3B-active and eight turns, these requests only trade away the artifact.
