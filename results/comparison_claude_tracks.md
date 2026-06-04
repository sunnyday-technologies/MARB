# Frontier track: first three runs compared

Graded 2026-05-26. The same grader ran on every build: `grader/grade_native_step.py` for the native gates (inventory, interference, floating), scored under the v0.3 buildability gate that was current at the time. Three frontier-track drivers produced the builds: Claude Opus 4.7 on Autodesk Fusion, Claude Opus 4.7 on CadQuery, and GPT-5 Codex on CadQuery. Each ran a fresh, prompt-only session with no project memory. All three received the same kit and were graded by the same grader.

The buildability gate shown here is the retired v0.3 headline. It is reported only for historical context. The current MARB positional metrics are GAP, ORIENT, and POS. See `spec/MARB_SCORING.md` and `results/marb_v0_9_grades.json` for the v0.9 grades.

## Results

| | CADCLAW reference (answer key) | Claude-CadQuery | Claude-Fusion | GPT-5 Codex (CadQuery) |
|---|---|---|---|---|
| Parts placed | 100 / 100 | 100 / 100 | 100 / 100 | 100 / 100 |
| Inventory / Floating | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ |
| Interference | ✓ clean | ✗ 35 clips, 248 cm³ | ✗ 44 clips, 323 cm³ | ✗ 50 clips, 288 cm³ |
| Buildability (secondary gate) | 1.00 | 0.152 | 0.124 | 0.119 |
| L1 sub-grade | 100 / 100 | 15.2 / 100 | 12.4 / 100 | 11.9 / 100 |
| Full-stack (v0.3) | 15.0 / 100 | 6.52 / 100 | 6.24 / 100 | 6.19 / 100 |
| Wall-clock | (resolver) | 48.8 min | 33.7 min | 13.0 min |
| Attempts / retries / corrections | — | 9 / 2 / 4 | 12 / 1 / 4 | 1 / 0 / 1 |
| Human interventions | — | 0 | 0 | 0 |
| Tokens (billed / output) | — | 22.1M / 321K | 34.2M / 1.15M | n/a (Codex) |

A clean L1 sub-grade of 100 mapped to a full-stack score of 15 under v0.3, because only the L0 and L1 gates were verified at that time. The v0.3 buildability gate was `1 / (1 + clips/10 + overlap_cm³/120)`. That formula let interference dominate the score. A frame whose own parts overlap cannot be built, so it ranked far below a clean frame even with a perfect bill of materials.

## What the interference clips are

The grader skips accessories such as motors, belts, wheels, and pulleys. So every clip in this set is a structural part overlapping another structural part. These are real frame errors. Both Claude builds shared the same dominant failure.

| Clip pair | Fusion | CadQuery |
|---|---|---|
| C-beam to C-beam (splice joints, post-frame junctions) | 12 | 11 |
| C-beam to 2040 insert (splice insert overlapping its beams) | 10 | 10 |
| C-beam to gantry plate | 8 (about 6.1 cm³ each) | 10 (about 1.0 to 1.4 cm³ each) |
| C-beam to 2080 lower rail or flat shim | 8 | — |
| Other | 4 | 4 |

Both builds placed the spliced members and the centered 2040 inserts on top of each other rather than joined at their ends. Both also let the posts intersect the top frame. The CadQuery plate clips are about five times smaller, because that run extracted the real hole patterns. The Fusion clips are larger, but the Fusion run finished faster.

## Orientation: a real defect the v0.3 gates did not catch

Neither driver placed the vertical posts in the rotation shown in the answer-key image. The rotation is about the vertical axis, so it sets which way the channel face points. The v0.3 native gates check inventory, interference, and floating. None of them flags a part that is present, connected, and free of overlap but turned the wrong way. A rotated post passed all three gates. That is a real defect for buildability and for review, and the benchmark needs to measure it.

This finding motivated the orientation metric. MARB v0.9 adds ORIENT, the share of orientation-gradeable (asymmetric) parts placed in the correct rotation. It catches the rotated posts, separates builds that the old gates ranked as equal, and rewards correct datum and orientation reasoning. See `grader/marb_orient_metric.py` and the v0.9 spec in `spec/MARB_SCORING.md`.

## Takeaways

1. Both Claude workflows built a complete, fully connected machine of about 100 parts with no human intervention. That result is real and is the headline.
2. Neither build is buildable yet. Both share the same failure class: large overlaps at structural splices and junctions, plus posts turned the wrong way. The parts and the topology are right, but the precise, datum-correct placement is not. The CADCLAW-resolver reference build avoids this failure because its resolver places every part to its datum.
3. Ranking by the v0.3 score: Claude-CadQuery (15.2) is highest, then Claude-Fusion (12.4), then GPT-5 Codex (11.9). The effort behind each build matters as much as the rank. Claude-CadQuery iterated the most, with 9 attempts, and re-extracted the real hole patterns. It produced the cleanest build but was the slowest at 48.8 minutes. GPT-5 Codex finished in a single attempt with no retries, in 13.0 minutes. That is three to four times faster than either Claude run, but it produced the most clips (50) and the lowest score. More self-review buys accuracy and costs time and tokens, and the benchmark makes that trade-off visible. Token usage for the Codex run was not captured. The Claude token counts were recovered from the run transcripts.
4. Effort and autonomy are reported separately from the build score. Time, attempts, retries, and tokens are never folded into the L1 grade.

## Artifacts

- v0.9 positional grades for all three runs and the reference: [`results/marb_v0_9_grades.json`](marb_v0_9_grades.json).
- Native-gate grades (inventory, interference, floating): [`results/marb_native_grades.json`](marb_native_grades.json).
- Run registry and full provenance for every run, including timing, attempts, and recovered token counts: [`results/marb_runs.json`](marb_runs.json). The exported STEP files and per-run logs live in each run folder and are referenced from that registry.
- Scoring method: [`spec/MARB_SCORING.md`](../spec/MARB_SCORING.md).
