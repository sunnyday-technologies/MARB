# MARB changelog

Scoring-method versions describe the method and publication contract. Every
published cell keeps the version that was used when it was graded; a method
release never relabels an older result.

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
