# MARB — Mechanical Assembly Readiness Benchmark · scoring spec (v0.9, draft)

*Canonical scoring reference. All grader/figure builders live in `scripts/` and
are listed below; update this doc whenever a builder changes so the method stays
versioned and reproducible.*

Status: **draft v0.9** — supersedes the v0.3 "buildability factor" headline.

---

## 1. What we measure, and why it changed

The v0.3 headline was a single **buildability** score (interference-dominated),
which collapsed everything to ~12–15/100 and barely differentiated models. For an
*early* test that is the wrong signal. The honest, differentiating question is:

> **How many parts did the AI put in the right place, the right way around — and
> are the gaps between parts functionally correct?**

So MARB v0.9 grades **positional accuracy**, reported two ways:

- **LOCATED** — is each part in the correct *position*?
- **ORIENTED** — is each part in the correct *rotation*?

Buildability / interference stays on as a **secondary gate** ("could you actually
bolt it together"), no longer the headline.

## 2. Tolerance philosophy (non-negotiable)

- **Digital placement is graded EXACT.** A perfect digital model has no "slop" —
  a part is in the right spot or it is not. Real machines run on fractions of a
  mm, so the bar is tight: **≤ 5 mm = located**, and **< 0.01 mm = exact** (the
  only allowance is float/export noise, far tighter than commercial CAD permits).
  We never loosen the band to flatter a model.
- **Manufacturing tolerance is a *separate* axis.** Real-world tolerance is the
  ± of material added/removed when a real part is made from the perfect digital
  model. That is what the CADCLAW *tolerance* gate is for — and it is **not yet
  exercised** here. Today we are still grading whether the AI can "stack the
  blocks"; production tolerance comes later.

## 3. The functional-gap refinement (the heart of v0.9)

Absolute coordinates are a proxy. What actually makes a **functional system**
(vs. a monolithic blob) is the **gap at each interface**:

- **Attachment interfaces → intended gap ≈ 0 mm.** Parts that bolt/fasten
  together must touch. Grade: is the gap ~0 where it should be?
- **Motion interfaces → intended gap ≈ 1–2 mm.** Parts that move relative to
  each other (wheels in slots, gantry carriages) need a clearance. Grade: is the
  clearance present and in range where movement is required?

A correct functional assembly reproduces the **intended gap per interface**;
collapsing all gaps to 0 makes a monolith, and missing the attachment contacts
makes a pile of parts. This maps directly onto CADCLAW's **adjacency**
(should-touch) and **interference/clearance** (should-not-overlap, should-clear)
gates. **This is the target metric;** the position/orientation deltas in §4 are
the precursor that the gap grade builds on.

## 4. Scales and bands

Every part-level measure is reported at two scales (per Sunnyday decision):

- **System (absolute)** — pose vs the answer key in a common frame, after a
  best-fit rigid alignment (Kabsch/ICP on label-matched centroids; the runs are
  authored in different global frames — some centered, some corner-origin).
- **Relative (neighbor)** — each part's pose relative to its neighbors; frame-
  invariant, isolates "understood how parts connect" from "anchored the whole
  machine." Less sensitive to a global size/frame difference.

Headline number per scale: **median error** (mm for position, ° for orientation)
— honest, drops toward 0 as models improve, and never needs the band loosened.

Tiered bands (tight, fixed):

| Position | exact ≤ 0.01 mm · within 1 mm · **located ≤ 5 mm** · then 25 / 50 / 100 mm bins |
|---|---|
| Orientation | exact ≤ 0.5° · **aligned ≤ 5°** · slightly-off ≤ 15° · wrong |

## 5. Builders (all in `benchmarks/m3_ai_assembly/scripts/`)

| Builder | Role |
|---|---|
| `brand_figs.py` | Hard-coded Sunnyday brand kit for **all** figures (Nulshock display + Segoe UI body + brand colors + large legible sizes). Figures use as few words as possible. |
| `marb_pose_metric.py` | **v0.9 positional grader (LOCATED).** Loads reference + run STEP, labels solids by bbox signature, rigid-aligns (Kabsch/ICP), matches parts (scipy assignment), computes position bands + median, absolute & relative. |
| `marb_gap_metric.py` | **v0.9 functional-gap grader (§3).** Classifies each reference interface attachment (0 mm) / motion (1–2 mm) / standoff and grades actual-vs-intended gap. The primary functional score. |
| `marb_orient_metric.py` | **v0.9 orientation grader (§4).** Per-part rotation vs the answer key, binned aligned / rotated / wrong; skips rotationally-symmetric parts. Catches the "Z-posts turned the wrong way" defect. |
| `marb_grade_all.py` | Unified driver. **Single-run mode** (default) grades the canonical runs → flat `marb_v0_9_grades.json`. **Aggregate mode** (`--config` map, or `--runs-dir` auto-discovering `<model>_<driver>_<seed>\export.step`) grades N seeds/cell, reports median/mean/std per metric, → stats schema `marb_v0_9_stats.json`. |
| `build_marb_scoreboard.py`, `build_marb_scatter.py` | Leaderboard graphic + GAP-vs-time scatter. Read either schema (flat or stats); render ±std and per-seed clusters from the `_agg` block. Optional argv `[data.json] [out.png]`. |
| `grade_native_step.py` | Black-box gates: inventory / interference / floating. Buildability (secondary gate). |
| `score_report.py` | Rolls grader output into the report/score schema. |
| `make_reference_images.py`, `package_testkit.py`, `summarize_run_log.py`, `run_grader.py` | Kit prep, reference renders, run-log summary, harness driver. |

## 6. First-run findings (3 frontier runs, 2026-05-26)

Median placement error, under the tight standard:

| Run | Absolute median | ≤5 mm | Relative (neighbor) median | ≤5 mm | ≤50 mm |
|---|---|---|---|---|---|
| Claude · CadQuery | 118 mm | 0% | 50 mm | 11% | 50% |
| Claude · Fusion | 97 mm | 0% | 48 mm | 11% | 50% |
| OpenAI Codex · CadQuery | 110 mm | 0% | 47 mm | 3% | 57% |

Honest read: at the ≤5 mm a real machine needs, today's AI locates ~**0%** of
parts (absolute); the median part is ~**10 cm** off (~**5 cm** relative to its
neighbors). It gets parts into the right neighborhood but nowhere near real
precision — "stacking blocks like a toddler," quantified. The median number
tracks progress without ever loosening the standard.

(Note: the answer-key frame is ~2175 mm wide vs the runs' ~2048 mm — the AIs
built a slightly smaller machine — which inflates the *absolute* figures; the
*relative* metric factors that out.)

## 7. Open work

1. ~~**Functional-gap grade (§3)**~~ — **done** (`marb_gap_metric.py`): each
   interface classified attachment (0 mm) / motion (1–2 mm) / standoff; actual
   vs intended graded. The primary functional score.
2. ~~**Orientation metric**~~ — **done** (`marb_orient_metric.py`): per-part
   rotation binned aligned / rotated / wrong; rotationally-symmetric parts
   skipped. Catches the "Z-posts turned the wrong way" defect.
3. ~~**Re-version the presentation**~~ — **done**: leaderboard + scatter rebuilt
   around GAP / ORIENT / POS via `brand_figs.py`; deployed to cadclaw.io.
4. **Statistics (n > 1)** — `marb_grade_all.py` aggregate mode is built (median /
   mean / std per cell, std-bar leaderboard + per-driver scatter clusters);
   awaiting the repeat STEP runs to populate `marb_v0_9_stats.json`.
5. **Random-floor baseline** — shuffled positions, to put a real floor under the
   ~47 mm POS-rel number.
6. **Principal-axes (OBB/PCA) orientation refinement** — current ORIENT uses
   axis-aligned bbox extents; principal axes would catch off-axis rotations the
   current metric misses.
7. **2nd task (different machine type)** + **manufacturing-tolerance axis** — to
   claim generality and to grade the real-world ± of made parts (a separate axis
   the AI is not tested on yet; see §2).
8. Keep buildability/interference as the secondary gate.

## 8. Prompt framework — eliciting the right CAD actions (regenerative)

The aim is **not bench-maxxing.** It is a reusable framework — usable for *any*
future LLM↔CAD interaction, not just MARB — that elicits the most effective CAD
actions from each model. We grow this section as models and tasks are added:
record the prompt elements that **win** and the ones that **backfire**, with the
metric each moves and the evidence.

### 8.1 Protocol (keeps MARB fair)

The driver-facing brief (`prompts/standard_prompt.md`) is the **frozen, fair
control** — it states the *target* ("what"), never a build method or order
("how"), and is **never edited to make a run pass**. Prompt-framework
interventions are therefore tested as a **separate experimental arm**: an
additive *coaching overlay* appended after the frozen core (or a sibling prompt
file), clearly labelled. Runs under an overlay are **not** directly comparable to
the frozen-core baseline — they are a different cell. Each variant is graded with
MARB v0.9 and reported as **Δ per metric per dollar** vs the frozen-core control
for the same model+driver.

### 8.2 Evidence base (first three runs, n = 1 — hypotheses, not conclusions)

Mined from the run logs; receipts in
`results/prompt_framework_findings.md`. Working actions (zero corrections):
geometry-probing each part's local frame first; **assert-before-place**
(orientation proven against the bbox); **extracting hole patterns from STEP
edges instead of guessing**; per-part close-up render review. Backfiring /
missing: spliced members placed **coincident instead of end-to-end** (the
dominant clip class); **Z-posts rotated the wrong way by both drivers despite
reference images**; the **"best-effort, not extracted from geometry"** caveat
(correlates with worse plate clips + motion gap); orientation-helper axis flips;
Fusion viewer/visibility-cascade churn.

### 8.3 Candidate interventions, ranked by evidence

| # | Intervention (overlay text idea) | Target metric | Evidence | Predicted yield |
|---|---|---|---|---|
| 1 | **"Extract hole positions (and channel-opening direction) from the part geometry; do not approximate."** | GAP, ORIENT | CadQuery extracted holes → plate clips ~5× smaller than Fusion, which logged "best-effort". | **High / high-confidence.** |
| 2 | **Explicit interface-type declaration:** "Static frame joints are flush (0 mm) and meet **end-to-end, not coincident**; V-slot axis hand-offs reserve a 1 mm running gap." | GAP | The coincident-splice + insert overlaps are the dominant clip class in all three runs; Codex motion gap was 33.7 mm (no clearance reserved). | High. |
| 3 | **"Verify each placed part's orientation against the kit reference image"** (per-part, not whole-assembly). | ORIENT | Z-posts wrong in *both* Claude runs even with images present; the generic "compare to the images" was insufficient. | Medium–high. |
| 4 | **"Build inside-out"** — X-gantry spacing first → Y → Z-posts → frame members that accommodate them (the resolver invariant). | GAP, POS | Frame-junction clips dominate; an explicit dependency order sequences the accommodating members and anchors the base datum. | Medium (general; test before trusting). |
| 5 | **Iteration budget** — *not* a hard ≤5 cap. Prefer "stop when no improvement in 2 consecutive attempts." | cost, GAP | The two highest-value Claude·CadQuery corrections landed at attempts **6 and 8** — a ≤5 cap would have removed them. Iteration *bought* GAP accuracy. | Cost lever, not quality. |

### 8.4 Cost / token reduction

Iteration helped here, so the lever is **not** truncating it — it is (a) the
"no-improvement-in-2" stop above, and (b) reducing **cache-read** load (22–34 M
tokens/run, mostly re-read context, is the dominant cost driver). One confident
pass (Codex, 13 min) is *fast but coarse* — worst GAP, most clips. Quantify
per-model where iteration pays vs. wastes tokens as repeats land.

### 8.5 Backend-stub notes (not prompt-framework, but cost real attempts)

Fusion runs lost ~2 attempts to a **visibility cascade** (hiding a master
occurrence hides the whole component) and **blank PNG** auto-fit. A one-line
warning in the Fusion backend stub (not the fair core) would reclaim them
without affecting comparability.
