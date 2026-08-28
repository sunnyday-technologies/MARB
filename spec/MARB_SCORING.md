# MARB — Mechanical Assembly Readiness Benchmark: scoring spec (v0.10)

This is the canonical scoring reference for MARB. All grader and figure builders
live in `grader/` and are listed in Section 5. Update this document whenever a
builder changes, so the method stays versioned and reproducible.

Status: v0.10. This version retains the v0.9 metrics and fixed bands, adds
repeat-run publication controls, and makes the current answer-key equivalence
limits explicit. Existing board cells keep their original v0.9 tag.

---

## 1. What MARB measures, and why the metric changed

The v0.3 headline was a single buildability score, dominated by interference. It
collapsed every run to about 12 to 15 out of 100 and barely separated models. For
an early-stage test, that is the wrong signal. The useful question, and the one
that separates models, is this:

> How many parts did the AI place in the correct position and the correct
> orientation, and are the gaps between parts functionally correct?

MARB v0.10 grades positional accuracy with the same three metrics introduced in
v0.9:

- GAP: the error between the actual and intended interface gap. This is the
  primary functional score. (In earlier v0.3 wording, GAP did not yet exist as a
  named metric.)
- POS (formerly LOCATED): the position error of each part versus the answer key,
  after a best-fit rigid alignment.
- ORIENT (formerly ORIENTED): the share of orientation-gradeable parts placed in
  the correct rotation.

The configured native inventory, interference, and floating-part checks remain
as secondary artifact gates; they are no longer the headline. Passing these
digital checks does not establish physical assemblability or manufacturability.

## 2. Tolerance philosophy

Two points define the standard and do not change.

First, digital placement is graded exactly. A perfect digital model has no
placement error: a part is either in the correct spot or it is not. Real machines
function at fractions of a millimeter, so the bar is tight. A part within 5 mm of
its target counts as located. A part within 0.01 mm counts as exact. The only
allowance is floating-point and export noise, which is far tighter than commercial
CAD requires. The band is never loosened to favor a model.

Second, manufacturing tolerance is a separate axis. Manufacturing tolerance is
the plus-or-minus of material added or removed when a real part is made from the
perfect digital model. That is the purpose of the CADCLAW tolerance gate, and it
is not exercised here. Today MARB grades only whether the AI can place the parts
correctly. Production tolerance is graded later (see Section 7).

## 3. The functional-gap metric (GAP)

Absolute coordinates are a proxy. What actually distinguishes a functional system
from a single fused body is the gap at each interface. GAP grades that gap
directly.

- Attachment interfaces: intended gap about 0 mm. Parts that bolt or fasten
  together must touch. GAP checks whether the gap is about 0 mm where it should be.
- Motion interfaces: intended gap about 1 to 2 mm. Parts that move relative to
  each other, such as wheels in slots or gantry carriages, need a clearance. GAP
  checks whether that clearance is present and in range where movement is required.

A correct functional assembly reproduces the intended gap at each interface.
Collapsing every gap to 0 mm fuses the machine into one body. Missing the
attachment contacts leaves a set of separated parts. GAP maps directly onto the
CADCLAW adjacency check (parts that should touch) and the interference and
clearance check (parts that should not overlap and that should clear). GAP is the
target metric. The position and orientation measures in Section 4 are the
foundation that GAP builds on.

## 4. Scales and bands

Every part-level measure is reported at two scales.

- POS absolute: each part's pose versus the answer key in a common frame, after a
  best-fit rigid alignment (Kabsch or ICP on label-matched centroids). The runs
  are authored in different global frames; some are centered, and some use a
  corner origin. The alignment puts them in one frame before scoring.
- POS relative (neighbor-relative): each part's pose relative to its neighbors.
  This scale is frame-invariant. It separates whether the model understood how
  parts connect from whether it anchored the whole machine in the right place. It
  is less sensitive to a global size or frame difference.

The headline number per scale is the median error: millimeters for position, and
degrees for orientation. The median drops toward 0 as models improve, and it
never requires the band to be loosened.

The bands are tight and fixed.

| Metric | Bands |
|---|---|
| Position | exact at 0.01 mm or less; within 1 mm; located at 5 mm or less; then 25, 50, and 100 mm bins |
| Orientation | exact at 0.5 degrees or less; aligned at 5 degrees or less; slightly off at 15 degrees or less; otherwise wrong |

## 5. Builders

All builders live in `grader/` and depend on the CADCLAW grading engine.

| Builder | Role |
|---|---|
| `marb_pose_metric.py` | POS grader. Loads the answer key and run STEP files, labels solids by bounding-box signature, rigid-aligns (Kabsch or ICP), matches parts (SciPy assignment), and computes the position bands and median, both absolute and relative. |
| `marb_gap_metric.py` | GAP grader (Section 3). Classifies each answer-key interface as attachment (0 mm), motion (1 to 2 mm), or standoff, then grades actual gap versus intended gap. This is the primary functional score. |
| `marb_orient_metric.py` | ORIENT grader (Section 4). Uses world-axis-aligned bounding-box extents as an orientation proxy and bins each gradeable part as aligned, rotated, or wrong. Parts with two near-equal extents are skipped because the proxy cannot distinguish their rotation. |
| `marb_grade_all.py` | The task-aware grader driver. Legacy single-run mode remains available. Registry mode selects one task and one scoring version, groups by stable cell ID, and reports median, mean, population standard deviation, and raw per-run values. It refuses to blend scoring versions. |
| `grade_native_step.py` | The native gates: inventory, interference, and floating. This is the secondary buildability gate. |
| `brand_figs.py` | The Sunnyday brand kit shared by all figures: display and body fonts, brand colors, and large legible sizes. Figures use as few words as possible. |
| `build_marb_scoreboard.py` | Leaderboard graphic. Reads the grade schema and renders the standard-deviation bars and per-seed clusters from the aggregate block. |
| `build_marb_scatter.py` | GAP-versus-time scatter plot. Reads the grade schema and renders per-seed clusters. |
| `build_marb_gap_closeup.py` | Close-up figure of the GAP metric at an interface. |
| `build_marb_local_3panel.py` | Three-panel figure for the local-anchor floor results. |
| `build_marb_linkedin_hero_v09.py` | Hero figure for the v0.9 results summary. |
| `scripts/validate_frontier_publication.py` | Publication gate. Reconciles board cells with registered run ordinals, allows only the exact legacy single-run list, and requires at least three attempted and three graded independent runs plus median and spread for every later frontier cell. |

### 5.1 Acceptable solution classes and current limitation

The current M3-CRETE key is one resolver-built geometric answer. The graders
already accept two limited equivalences without a second key:

- POS, GAP, and ORIENT use optimal assignment within the same part label, so
  exchanging indistinguishable repeated instances is not penalized merely for
  instance order.
- ORIENT skips parts when two near-equal axis-aligned bounding-box extents make
  rotation unobservable to the current proxy. This is a scoring-observability
  rule, not a declaration that every skipped part is functionally
  rotation-equivalent.

Those rules do not make every potentially acceptable topology equivalent. GAP
derives its interface graph from the selected reference, and POS relative uses
that reference's neighbor graph. A submission with a different part count,
handed layout, or interface topology can therefore be penalized by the
single-reference comparison. MARB has not established whether any such
alternative is functionally acceptable. Axis-aligned bounding-box orientation
can also miss rotations that preserve the same extents.

The v0.10 current-kit audit found no independently validated second complete
geometric answer class, so none is declared. Potential alternate layouts remain
unvalidated scoring cases, not accepted equivalents and not evidence that one
feature tree or topology is uniquely valid. A future task may declare multiple
resolver-built classes, but one complete class must be selected for the whole
grade; MARB must not cherry-pick GAP from one reference and POS or ORIENT from
another.

## 6. First-run findings (three frontier runs, 2026-05-26)

Median placement error under the tight standard:

| Run | POS absolute median | located (5 mm) | POS relative median | located (5 mm) | within 50 mm |
|---|---|---|---|---|---|
| Claude Opus 4.7 · CadQuery | 118 mm | 0% | 50 mm | 11% | 50% |
| Claude Opus 4.7 · Autodesk Fusion | 97 mm | 0% | 48 mm | 11% | 50% |
| GPT-5 Codex · CadQuery | 110 mm | 0% | 47 mm | 3% | 57% |

At the 5 mm precision a real machine needs, today's AI places about 0% of parts
correctly on the absolute scale. The median part is about 10 cm off in absolute
terms, and about 5 cm off relative to its neighbors. Parts land in roughly the
correct region, but not at functional precision. The median number tracks
progress without loosening the standard.

The answer-key frame is about 2175 mm wide, and the runs' frames are about 2048
mm wide. The AIs built a slightly smaller machine. This inflates the absolute
figures. The relative metric factors that difference out.

### 6.1 Frontier publication provenance

The eight frontier cells already published between 2026-05-26 and 2026-06-11
remain dated, v0.9 single-run observations. They are descriptive and are not
retrospectively promoted into repeat-run estimates.

Effective 2026-08-28, a new frontier cell is publishable only when:

1. at least three independent runs were attempted and at least three produced
   gradeable outputs;
2. every attempt remains registered under a distinct provider seed or, when a
   provider exposes no seed control, a distinct independent-run ordinal;
3. every attempt has a unique run ID plus a distinct run-log path and SHA-256
   digest; every graded output records its STEP SHA-256 digest, and the grade
   source identifies the exact graded run IDs;
4. attempts and retries inside one session are not counted as separate runs;
5. the published center is the median and the spread is population standard
   deviation for GAP, ORIENT, and POS; and
6. task, scoring version, kit, prompt, model/tool, and harness cohort are not
   mixed inside a cell.

The validator applies the same minimum and provenance checks to every
post-policy non-reference row, so changing a board track label cannot bypass
the frontier rule. Reference and legacy exceptions are exact cell-ID
allowlists.

The local and sighted cells already use repeat-run summaries. Their attempted
and graded counts are reported separately so failed exports do not disappear.

## 7. Planned work

1. Populate new frontier cells under the v0.10 repeat-run rule. Re-running the
   historical v0.9 cells is a separate, budgeted study rather than a condition
   of this method release.
2. Random-floor baseline. Grade shuffled positions to establish a true floor
   beneath the current POS relative median of about 47 mm.
3. Principal-axes orientation refinement. The current ORIENT metric uses
   axis-aligned bounding-box extents. Principal axes (OBB or PCA) would catch
   off-axis rotations that the current metric misses.
4. A second task on a different machine type, to support a claim of generality.
5. The manufacturing-tolerance axis, to grade the real-world plus-or-minus of
   made parts. This is a separate axis that the AI is not tested on yet (see
   Section 2).
6. Keep the buildability gate as the secondary gate.

## 8. Prompt framework: eliciting the right CAD actions

The goal is a reusable framework that elicits the most effective CAD actions from
each model. It is meant for any future interaction between a language model and a
CAD tool, not only MARB. This section grows as models and tasks are added. It
records the prompt elements that help and the ones that hurt, with the metric each
one moves and the supporting evidence.

### 8.1 Protocol (keeps MARB fair)

The driver-facing brief (`prompts/standard_prompt.md`) is the frozen, fair
control. It states the target (the what), never a build method or order (the how),
and it is never edited to make a run pass.

Prompt-framework interventions are tested as a separate experimental arm. Each
intervention is an additive coaching overlay, appended after the frozen core or
held in a sibling prompt file, and clearly labeled. Runs under an overlay are not
directly comparable to the frozen-core baseline. They are a different cell. Each
variant is graded with the current MARB spec and reported as the change per metric per dollar
versus the frozen-core control, for the same model and driver.

### 8.2 Evidence base (first three runs, n = 1: hypotheses, not conclusions)

These notes are mined from the run logs. The supporting detail is in
`results/prompt_framework_findings.md`.

Actions that worked, with zero corrections needed:

- Probing each part's local frame geometry first.
- Asserting orientation before placing a part, proven against the bounding box.
- Extracting hole patterns from the STEP edges instead of guessing.
- Reviewing a per-part close-up render.

Actions that backfired or were missing:

- Spliced members placed end-to-end were instead placed overlapping. This was the
  most common interference class.
- Both drivers placed certain vertical posts in the wrong rotation, even with the
  reference images present.
- A best-effort approximation, rather than values extracted from geometry,
  correlated with worse plate interference and worse motion gap.
- Orientation-helper axis flips.
- Repeated viewer and visibility churn in Autodesk Fusion.

### 8.3 Candidate interventions, ranked by evidence

| # | Intervention (overlay text idea) | Target metric | Evidence | Predicted yield |
|---|---|---|---|---|
| 1 | Extract hole positions and channel-opening direction from the part geometry; do not approximate. | GAP, ORIENT | CadQuery extracted holes; its plate interference was about 5 times smaller than Autodesk Fusion, which used a best-effort approximation. | High, high confidence. |
| 2 | Declare each interface type: static frame joints are flush at 0 mm and meet end-to-end, not overlapping; V-slot axis hand-offs reserve a 1 mm running gap. | GAP | Overlapping splices and inserts were the most common interference class in all three runs. The GPT-5 Codex motion gap was 33.7 mm, with no clearance reserved. | High. |
| 3 | Verify each placed part's orientation against the kit reference image, per part, not for the whole assembly at once. | ORIENT | Certain vertical posts were placed wrong in both Claude runs even with images present. A generic instruction to compare against the images was not enough. | Medium to high. |
| 4 | Build in dependency order: X-gantry spacing first, then Y, then the vertical posts, then the frame members that accommodate them. | GAP, POS | Frame-junction interference dominates. An explicit dependency order sequences the accommodating members and anchors the base datum. | Medium. General; test before relying on it. |
| 5 | Iteration budget, not a hard cap of 5 attempts. Prefer stopping when there is no improvement across 2 consecutive attempts. | cost, GAP | The two highest-value Claude CadQuery corrections occurred at attempts 6 and 8. A cap of 5 would have removed them. Iteration improved GAP accuracy. | A cost lever, not a quality lever. |

### 8.4 Cost and token reduction

Iteration helped here, so the lever is not to truncate it. The two levers are: (a)
the stop-after-two-with-no-improvement rule above, and (b) reducing cache-read
load. Cache reads run from 22 million to 34 million tokens per run, mostly
re-read context, and are the dominant cost driver. One confident pass (GPT-5
Codex, 13 minutes) was the fastest but the least accurate: it had the worst GAP
and the most interference. As repeat runs land, quantify per model where
iteration pays and where it wastes tokens.
