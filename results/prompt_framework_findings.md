# Prompt-framework mining — first three MARB runs (receipts)

*Source logs: each run's `run_log.yaml` (Opus 4.7 · CadQuery, 9 attempts;
Opus 4.7 · Fusion, 12 attempts; GPT-5 Codex · CadQuery, 1 attempt). Graded with MARB
v0.9 — see `marb_v0_9_grades.json`. This doc is the evidence; the distilled,
versioned framework lives in `../MARB_SCORING.md` §8.*

The goal is **not bench-maxxing** — it is finding the prompt additions that
*elicit the right CAD actions* from each model. We mine which logged actions
succeeded with **zero corrections** (working interventions to keep/encode) and
which generated **corrections** (failure modes a prompt addition could pre-empt).

---

## 1 · Effective actions (zero corrections in the log → keep / encode)

| Action (verbatim from log) | Run | Maps to metric | Why it worked |
|---|---|---|---|
| "Probed all 14 kit STEP parts (bbox, dims, centroid) to learn local axes/orientation; centroid sign used to find C-Beam channel opening." (A01) | Claude·CadQuery | ORIENT, GAP | Learns each part's local frame *from geometry* before placing — no guessed axes. |
| "Wrote/verified orientation helpers … with bbox asserts and channel-direction checks **before placing anything**." (A02) | Claude·CadQuery | ORIENT | Assert-before-place: orientation is proven against the bbox, not eyeballed. |
| "Extracted real plate hole pattern from the STEPs (circular edges) instead of guessing … snapped all wheel centerlines to these." (A06) | Claude·CadQuery | GAP, ORIENT | **Highest-confidence intervention.** Its plate clips came out ~5× smaller than Fusion's (which approximated). |
| "Caught by close-up render review per the brief's verify-as-you-go guidance." (A06 note) | Claude·CadQuery | ORIENT, POS | Per-part close-up inspection caught the mis-seated Z-post carriages; whole-assembly views hid it. |
| Re-import the exported STEP and re-count solids / bbox to confirm validity (A09) | Claude·CadQuery | (buildability gate) | Independent self-check of the deliverable. |

## 2 · Logged corrections (failure modes → a prompt addition could pre-empt)

| Correction (verbatim) | Run | Maps to metric | Candidate intervention |
|---|---|---|---|
| Spliced members + centered 2040 inserts placed **coincident instead of end-to-end**; posts/top-frame intersecting (dominant clip class: C-beam↔C-beam 11–12, C-beam↔2040 insert 10) | both Claude runs + Codex | GAP | **Explicit interface-type declaration** + **build inside-out** (sequence accommodating members). |
| "Z-post carriages were mistakenly placed … on the wrong face; re-seated them … facing inward toward the print volume." (A06) | Claude·CadQuery | ORIENT | **Verify each part's orientation vs the kit reference image** (per-part, not whole-assembly). |
| **Z-posts placed in the wrong rotation by *both* drivers** even with reference images present (comparison doc §"Orientation gap") | both Claude runs | ORIENT | Same — the generic "compare against the images" in the brief was not enough; needs a per-part orientation check. |
| "Wheel hole alignment and C-channel opening direction are **best-effort** (not extracted from authored hole geometry)." (assumptions) | Claude·Fusion | GAP, ORIENT | **"Extract hole positions from geometry, do not approximate."** The "best-effort" caveat correlates with worse plate clips + motion gap. |
| "as_ybeam(channel_plus_x=False) used rot_z(180) … which also flipped the rail length into −Y … Y span corrected from ~2015 mm to ~1030 mm." (A08) | Claude·CadQuery | POS, GAP | Orientation-helper axis bug; **assert-before-place on the resulting envelope** catches it (it did, at attempt 8). |
| "Visibility cascade: hiding a component's only master occurrence flips the whole component hidden …"; "Blank PNGs: set transparentBackground=false …" (A04b) | Claude·Fusion | (tooling, no geometry impact) | Backend-tooling friction, not a placement error — but it cost ~2 attempts. Worth a Fusion-stub warning. |
| "First envelope read was bogus (manual nested-matrix composition mis-placed the PINION sub-bodies in the measurement script); re-measured …" (A10) | Claude·Fusion | (tooling) | Measure via assembly-context proxies, not hand-composed matrices. |
| "Raised the four lower flat frame shims so their bottom faces sit at Z=0 …" (A01) | Codex | POS, GAP | Datum-to-ground for the static frame base; **build inside-out** anchors the base first. |

## 3 · Iteration vs. one-shot (the cost finding)

| | Claude·CadQuery | Claude·Fusion | Codex·CadQuery |
|---|---|---|---|
| Attempts / retries / corrections | 9 / 2 / 4 | 12 / 1 / 4 | **1 / 0 / 1** |
| Wall-clock | 48.8 min | 33.7 min | **13.0 min** |
| Billed / output tokens | 22.1M / 321K | 34.2M / 1.15M | n/a (Codex) |
| **GAP median** (v0.9, ↓) | **0.0 mm** | 2.0 mm | 7.8 mm |
| ORIENT aligned (↑) | 51% | 47% | 69% |
| POS rel median (↓) | 49.9 mm | 47.7 mm | 47.2 mm |

**Iteration clearly bought GAP accuracy here, and the highest-value corrections
landed *late*:** Claude·CadQuery's hole-extraction fix was attempt **6** and the
Y-axis-flip fix was attempt **8** — both *after* a naive ≤5 cap. So a hard cap
would likely have *removed* the two corrections that made it the GAP leader.

The honest cost lever is therefore **not a hard iteration cap** but a
**"no-improvement-in-2-consecutive-attempts" stop** plus reducing the
**cache-read** load (22–34 M tokens/run is mostly re-read context, the dominant
cost driver). Codex shows one confident pass is *fast* but *coarse* (worst GAP,
50 clips, motion gap 33.7 mm from never reserving running clearance).

## 4 · One-line distillation per model (n = 1 — treat as hypotheses)

- **Opus 4.7 + CadQuery** — already does geometry-probing and assert-before-place
  well; its remaining errors are *interface intent* (coincident splices) and
  *late-caught* orientation. Wins GAP when allowed to iterate.
- **Opus 4.7 + Fusion** — same reasoning, but stops at "best-effort" on hole
  geometry and loses attempts to viewer/visibility tooling. Telling it to extract
  holes + warning about the visibility cascade should close most of the gap.
- **GPT-5 Codex + CadQuery** — confident one-shot; best ORIENT, fastest, but
  skips iteration so its gaps/clips are largest. Benefits most from up-front
  interface-type + "extract holes" instructions (it won't self-correct into them).

## 5 · The recurring Fusion token sink (from the Opus 4.8 attempt, 2026-05-29)

The Opus 4.8 × Fusion attempt (non-comparable: resumed/compacted session, run
incognito, 40/100, no export) burned its budget almost entirely **re-deriving
Fusion's `addExistingComponent` placement law from scratch** — the same
derivation the Opus 4.7 Fusion run also did. Both independently discovered:

> `addExistingComponent(comp, M)` stores `M · T_bake`, so `world_center = M · C0`
> and `final_orientation = R · R_b`, where `C0` / `R_b` are the component's
> **current first-occurrence** center/rotation. Deleting a component's first
> occurrence silently **re-anchors** `C0`/`R_b` for all future placements (this
> flung re-placed plates ~1.4 m off). Fix: `M = [R_rel | target − R_rel·C0]`,
> `R_rel = R_desired · R_b⁻¹`; keep each import occurrence alive until its
> component is fully placed and verified.

**Highest-value, lowest-effort intervention (Fusion backend stub only, NOT the
fair core):** pre-supply this law as a "Fusion API quirks" appendix in the
Fusion driver stub. It is *tool mechanics*, not task guidance, so it does not
leak the answer — it just stops every Fusion run from re-paying the same
multi-attempt diagnostic tax. Expect a large token/cost reduction on Fusion runs
(folds into §4 of MARB_ROADMAP / cost reduction).

**Integrity finding (separate):** the run had to be executed **incognito**
because a normal Claude desktop session could reach the M3-CRETE answer
key / prior context. **Fusion benchmark runs must use a clean/incognito context**
or the fairness wall does not hold. Incognito desktop sessions also leave no
local usage transcript, so their token spend is not recoverable after the fact
(capture it from claude.ai usage at run time if it is needed).
