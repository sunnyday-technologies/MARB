# Prompt-framework evidence — first three MARB runs

This document records the raw evidence behind the MARB prompt framework. Each run kept a log (`run_log.yaml`). The three runs are Claude Opus 4.7 with CadQuery (9 attempts), Claude Opus 4.7 with Autodesk Fusion (12 attempts), and GPT-5 Codex with CadQuery (1 attempt). All three were graded with MARB scoring v0.9. The grades are in `results/marb_v0_9_grades.json`. The distilled, versioned framework lives in `spec/MARB_SCORING.md`, section 8. This document is the source evidence behind that section.

The goal is to find the prompt additions that elicit the correct CAD actions from each model. We are not tuning prompts to inflate scores. We read each log for two things. First, the logged actions that produced no corrections: these are the working steps worth keeping and encoding. Second, the logged corrections: these are failure modes that a prompt addition could prevent.

MARB grades three metrics. GAP is the error between the actual and intended interface gap, reported as a median in millimeters, and it is the primary functional score. ORIENT is the share of orientation-gradeable parts placed in the correct rotation, reported as a percent aligned. POS is the position error of each part versus the answer key after a best-fit rigid alignment, reported as a median in millimeters. POS relative is neighbor-relative; POS absolute is the raw exported frame.

---

## 1. Effective actions (no corrections in the log — keep and encode)

| Action (from the log) | Run | Maps to metric | Why it worked |
|---|---|---|---|
| Probed all 14 kit STEP parts (bounding box, dimensions, centroid) to learn each part's local axes and orientation. Used the centroid sign to find the C-Beam channel opening. (A01) | Claude, CadQuery | ORIENT, GAP | The model learned each part's local frame from its geometry before placing it. No axes were guessed. |
| Wrote and verified orientation helpers with bounding-box assertions and channel-direction checks before placing anything. (A02) | Claude, CadQuery | ORIENT | Assert before place. Orientation is proven against the bounding box, not judged by eye. |
| Extracted the real plate hole pattern from the STEP files (circular edges) instead of guessing, then snapped all wheel centerlines to those holes. (A06) | Claude, CadQuery | GAP, ORIENT | The highest-confidence action. Its plate clips were about 5 times smaller than the Fusion run, which approximated the holes. |
| Caught a defect during close-up render review, following the brief's verify-as-you-go guidance. (A06 note) | Claude, CadQuery | ORIENT, POS | Per-part close-up inspection caught mis-seated Z-post carriages. Whole-assembly views hid the defect. |
| Re-imported the exported STEP and re-counted solids and bounding box to confirm validity. (A09) | Claude, CadQuery | buildability (secondary gate) | An independent self-check of the deliverable. |

## 2. Logged corrections (failure modes a prompt addition could prevent)

| Correction (from the log) | Run | Maps to metric | Candidate intervention |
|---|---|---|---|
| Spliced members and centered 2040 inserts were placed at the same point rather than joined at their ends. Posts and the top frame intersected. The dominant clip class was C-beam against C-beam (11 to 12 pairs) and C-beam against 2040 insert (10 pairs). | Both Claude runs and Codex | GAP | Declare the interface type explicitly, then build base-first so that members are placed in dependency order. |
| Z-post carriages were placed on the wrong face, then re-seated to face inward toward the print volume. (A06) | Claude, CadQuery | ORIENT | Verify each part's orientation against the kit reference image, per part rather than per whole assembly. |
| Both drivers placed the Z-posts in the wrong rotation even with reference images present. (comparison document, orientation section) | Both Claude runs | ORIENT | Same fix. The generic instruction to compare against the images was not enough. A per-part orientation check is needed. |
| Wheel hole alignment and C-channel opening direction were marked best-effort and were not extracted from the authored hole geometry. (assumptions) | Claude, Fusion | GAP, ORIENT | Extract hole positions from geometry; do not approximate. The best-effort caveat correlates with worse plate clips and a worse motion gap. |
| An orientation helper used `rot_z(180)`, which also flipped the rail length into negative Y. The Y span was corrected from about 2015 mm to about 1030 mm. (A08) | Claude, CadQuery | POS, GAP | An orientation-helper axis bug. An assert-before-place check on the resulting envelope catches it. Here it did, at attempt 8. |
| Hiding a component's only master occurrence flipped the whole component hidden. Blank PNG renders were fixed by setting `transparentBackground=false`. (A04b) | Claude, Fusion | tooling, no geometry impact | This is backend-tooling friction, not a placement error, but it cost about 2 attempts. It is worth a warning in the Fusion stub. |
| The first envelope read was wrong because a manual nested-matrix composition mis-placed the PINION sub-bodies in the measurement script. It was re-measured. (A10) | Claude, Fusion | tooling | Measure through assembly-context proxies, not hand-composed matrices. |
| Raised the four lower flat frame shims so their bottom faces sit at Z = 0. (A01) | Codex | POS, GAP | Reference the static frame base to the ground. Building base-first anchors the base datum. |

## 3. Iteration versus a single pass (the cost finding)

| | Claude, CadQuery | Claude, Fusion | Codex, CadQuery |
|---|---|---|---|
| Attempts / retries / corrections | 9 / 2 / 4 | 12 / 1 / 4 | 1 / 0 / 1 |
| Wall-clock | 48.8 min | 33.7 min | 13.0 min |
| Billed / output tokens | 22.1M / 321K | 34.2M / 1.15M | not available (Codex) |
| GAP median (v0.9, lower is better) | 0.0 mm | 2.0 mm | 7.8 mm |
| ORIENT aligned (higher is better) | 51% | 47% | 69% |
| POS relative median (lower is better) | 49.9 mm | 47.7 mm | 47.2 mm |

Iteration improved GAP accuracy here, and the highest-value corrections landed late. The hole-extraction fix in the Claude CadQuery run was attempt 6. The Y-axis flip fix was attempt 8. Both came after a naive cap of 5 attempts would have stopped the run. A hard cap would likely have removed the two corrections that made this run the GAP leader.

The useful cost control is therefore not a hard iteration cap. It is a stop rule that ends the run after two consecutive attempts with no improvement, combined with a lower cache-read load. Each run billed 22 to 34 million tokens, most of which is re-read context, and that is the dominant cost driver. The Codex run shows that a single pass is fast but less precise. It had the worst GAP, 50 interference clips, and a motion gap of 33.7 mm, because it never reserved running clearance.

## 4. One-line read per model (n = 1, treat as hypotheses)

- **Opus 4.7 with CadQuery.** Already probes geometry and asserts before placing. Its remaining errors are interface intent (coincident splices) and orientation caught late. It wins GAP when allowed to iterate.
- **Opus 4.7 with Fusion.** Same reasoning, but it stops at best-effort on hole geometry and loses attempts to viewer and visibility tooling. Telling it to extract holes and warning it about the visibility cascade should close most of the gap.
- **GPT-5 Codex with CadQuery.** A confident single pass with the best ORIENT and the fastest run, but it skips iteration, so its gaps and clips are the largest. It benefits most from up-front instructions to declare interface types and extract holes, because it will not self-correct into them.

## 5. The recurring Fusion token cost (from the Opus 4.8 attempt, 2026-05-29)

The Opus 4.8 Fusion attempt is not directly comparable. It used a resumed and compacted session, ran in an incognito context, reached 40 of 100 parts, and produced no export. It spent almost its entire budget re-deriving the placement rule for Fusion's `addExistingComponent` from scratch. The Opus 4.7 Fusion run had performed the same derivation. Both runs independently arrived at the same result:

> `addExistingComponent(comp, M)` stores `M · T_bake`, so `world_center = M · C0` and `final_orientation = R · R_b`, where `C0` and `R_b` are the component's current first-occurrence center and rotation. Deleting a component's first occurrence silently re-anchors `C0` and `R_b` for all future placements. In this run that displaced re-placed plates by about 1.4 m. The fix is `M = [R_rel | target − R_rel·C0]` with `R_rel = R_desired · R_b⁻¹`. Keep each import occurrence alive until its component is fully placed and verified.

The highest-value, lowest-effort intervention is to supply this rule in advance as a Fusion API appendix in the Fusion driver stub. It belongs to the stub only, not to the fair task core. It is tool mechanics, not task guidance, so it does not reveal the answer. It only stops every Fusion run from re-deriving the same rule over several attempts. Expect a large token and cost reduction on Fusion runs. This work is tracked under cost reduction in `ROADMAP.md`.

A separate integrity note: benchmark runs must use a clean, memoryless context so the answer key and prior context cannot leak into the session, or the fairness control fails. A clean context may leave no local transcript, so capture token spend at run time if it is needed.
