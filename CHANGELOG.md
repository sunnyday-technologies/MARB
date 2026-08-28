# MARB changelog

Scoring-method versions describe the method and publication contract. Every
published cell keeps the version that was used when it was graded; a method
release never relabels an older result.

## v0.11 — 2026-08-28

- Freeze the public `L2-RESOLVE` task revision `top-spreader-x+200-r1`: continue
  from the same driver's editable M3-CRETE build and move the existing
  top-center-spreader plus its four mounting plates +200.0 mm along global X,
  while preserving the other 95 instances.
- Add task-local registry routing, a same-driver before/after provenance
  contract, and publication validation for editable-source, STEP, run-log, and
  frozen-change identities.
- Record the task as `defined_unmeasured` with zero registered runs and publish
  a versioned blocker record. The task-specific private resolver key is locally
  validated, while gated distribution, qualifying same-driver editable
  before/after source, and at least three genuine gradeable attempts remain
  pending.

This is a method-only release. It adds no score row, does not rescore any
historical run, and does not add L2 to the currently scored ladder.

## v0.10 — 2026-08-28

- Require every new frontier cell to include at least three independent runs
  attempted and three gradeable outputs before publication.
- Report repeat-run cells as the median plus population standard deviation;
  retain the eight existing frontier cells as explicitly dated, allowlisted
  single-run observations under v0.9.
- Add stable task/cell identifiers and task-aware registry routing so future
  L1, L2, and L4 results cannot be blended or graded against the wrong key.
- Document what the current single-reference grader already treats as
  equivalent and where potential alternate topologies require independent
  validation and a separately declared complete answer class.
- Add an executable publication-policy validator and synthetic regression
  tests. Post-policy cells must reconcile stable run IDs, run-log digests,
  graded STEP digests, and grade-source provenance. Metric definitions and
  tolerance bands are unchanged from v0.9.

This release publishes method and reporting controls only. It does not publish
an L2 or L4 result and does not establish manufacturability, safety,
certification, or readiness evidence.
