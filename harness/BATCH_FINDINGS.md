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

## New failure mode exposed (do NOT patch by hinting the envelope)

With export fixed, cohort B builds the whole machine but commonly **exceeds the
2000×1000×1000 target** (4/5 oversized in ≥1 axis) and run 10 *regressed* to 37 solids
while shrinking to fit. The brief explicitly says "do not tune toward any gate," so we
deliberately leave this alone — telling the model the envelope numbers (already in the
brief) or how to hit them would be gate-gaming, not a fair mechanics hint.

## Comparability + recommendation

`base` is the no-hint control; `cadquery_mechanics_v2` is a new cohort (like kit
v1.1 vs v1.2 for Fusion). Do not pool them on one leaderboard without noting the
variant. For future local runs, drive with
`--guidance-file harness/cadquery_mechanics_v2.md` (recorded as `prompt_variant`);
keep `base` only as the control.
