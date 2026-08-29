# MARB changelog

Scoring-method versions describe the method and publication contract. Every
published cell keeps the version that was used when it was graded; a method
release never relabels an older result.

## Unreleased — H2a planner, H2b executor, and Nightwatch controller

- Add the standalone, deterministic H2a `L1-ASSEMBLE` / `L2-RESOLVE` /
  `L4-ECO` cohort planner that
  validates frozen public inputs and allocates at least three independent run
  slots without provider, network, subprocess, credential, file-write, or model
  access.
- Keep every generated plan blocked before execution and free of fabricated
  outcomes, scores, gate results, or artifact identities. Current L4 plans also
  disclose the missing immutable gated grading revision.
- Add the separate H2b one-slot executor with explicit plan, authorization,
  slot, implementation-blob, frozen-input, provider/model/settings, and
  digest-pinned runtime bindings before provider access. The current execution
  policy is text-only and local-no-charge; metered execution is fail-closed
  pending a frozen provider-specific pre-call pricing policy.
- Advance execution authorization to schema v2, binding an absolute normalized
  Git executable path and the SHA-256 of its raw bytes. Schema validation checks
  those declared bindings; execution preflight, not `validate-authorization`,
  verifies the actual host path chain and file digest. For every process
  creation, lock the host path chain with Windows handles and reverify the file
  digest while those handles remain held across the hash-to-spawn interval,
  closing executable replacement. Use only the authenticated path as
  `argv[0]`, never a bare executable resolved through the working directory or
  `PATH`; reject symlink/reparse path chains; invoke Git with
  `--no-replace-objects` and `GIT_NO_REPLACE_OBJECTS=1`; authorize only the
  exact resolved repository with per-command `safe.directory` (never `*`); and fail closed on
  timeout or bounded-stdout overflow.
- Add the active `marb-v0.13-h2b` execution-runtime contract and advance active
  authorization/run-log schemas to `marb_execution_authorization.v3` and
  `marb_executor_run_log.v2`. The contract pins CADCLAW 0.10.0 commit
  `fad0dd552a49a0b32336f1845c2b82873ad6360a`, gate-spec `0.13.0`, gate registry
  `harness-gates.v1`, its package-source manifest, and executable calibration
  evidence. Authorization, `/opt/marb/runtime.json`, image labels, and
  `marb_h2b_image_build_provenance.v3` carry the same versioned identities and
  fail closed on stale or mismatched pins.
- Preserve the L4 v0.12 grade contract at CADCLAW commit
  `60fc271f68c8a794a4741f856b2dd4c9878416a6`. The v0.13 H2b execution contract
  records a narrow calibration-backed compatibility relation; it does not
  rewrite the L4 task, grader, answer key, method identity, or historical
  evidence.
- Preserve one provider session across L2/L4 baseline and change phases, freeze
  baseline/changed STEP and deterministic editable-source ZIP evidence, retain
  failed and partial UUID-backed attempts, and record bounded provenance without
  mutating the registry, board, task definitions, site, or deployment workflow.
- Bind each canonical workspace STEP to the device, inode, mode, size,
  nanosecond mtime, byte count, and SHA-256 accepted during discovery. Evidence
  capture reopens the file and requires that exact identity and content through
  the completed copy; rewrites or path replacements fail closed and leave no
  retained STEP target.
- Freeze cohort execution to `prompt_variant: frozen-core` and deliver only the
  payload between the exact backticked BEGIN/END marker lines; exclude driver
  preamble and grader suffix text and journal the delivered payload identity.
- Treat `cell_label` and `model.name` as operator display labels only; bind any
  publishable model identity to the exact authorized ID returned by the
  provider, with no H2b alias policy.
- Scope every authorization and limit to one logical slot/attempt, not an
  aggregate cohort budget. N=3/N=9 execution requires a separately approved
  aggregate Nightwatch campaign authorization plus reviewed per-slot H2b
  authorizations; Nightwatch supplies the serial local ledger and concurrency
  controller without broadening slot authority. Record the limited negative
  modality attestation: text provider input, no native image-view tool,
  Python-only access to staged image bytes, and `vision_attested: false`.
- Add the Nightwatch local repeat-run controller and document its exact
  `marb_nightwatch_campaign.v1`, `marb_nightwatch_ledger.v1`, and
  `marb_nightwatch_event.v1` contracts. The immutable digest-approved campaign
  binds a UUID, approval window, MARB revision, controller source identity,
  exact execution/safety policy, aggregate limits, and reviewed per-slot H2b
  plan and authorization paths, digests, run IDs, and literals. Normal cohort
  slots may share one plan path only with the same digest; authorization paths
  remain unique and may not alias plan paths. `status` is read-only; `run`
  requires
  `EXECUTE_MARB_NIGHTWATCH:<campaign-sha256>`. Automated execution is serial
  (`max_concurrency: 1`), loopback, credential-free, and local-no-charge;
  external, credentialed, metered, and otherwise potentially paid providers
  remain manual-only. Use an OS-backed writer lock, atomic ledger snapshots,
  append-only sequenced events, and claim/sealed-log reconciliation. Claimed or
  retained attempts are never retried automatically, and the strongest success
  state remains `completed_ungraded` with no grading, registry, board, site,
  publication, or deployment authority. Dedicated fake/local Nightwatch tests
  exist and are included in board-policy CI without making real provider,
  model, Docker, or network calls. No real provider/model/Docker campaign or
  runtime qualification has been performed, and no completed real campaign is
  claimed here.
- Clarify that the permanent `.slot-claims` record prevents duplicate claims
  only within one checkout because `runs/` is ignored and checkout-local.
  Cross-clone and cross-host uniqueness remains an operator/campaign-ledger and
  later registry/publication validation responsibility; it is not a global
  lock.
- Add no-call authorization template, seal, and validation CLI steps before
  `execute`, with basename-only preparation output, repository-relative run
  paths, and structured retained-failure reporting.
- Define `output_contract.executor_owned_outputs` as executor-reserved and bound
  paths, not proof of production; only captured artifact records prove a file
  exists, including for failed and partial attempts.
- Add fake-only executor and isolation regressions to board-policy CI. They
  exercise authorization, limits, provenance, cleanup, and non-mutation without
  making Docker, provider, or model calls.
- Add an offline OCI build contract whose base image, wheelhouse manifest,
  CADCLAW wheel, runtime-contract and calibration files, Dockerfile, effective
  temporary-context `.dockerignore`, complete context manifest, and final
  runtime are digest-attested. The active provenance schema is
  `marb_h2b_image_build_provenance.v3`. No real image RepoDigest or passing
  no-provider/no-network runtime smoke is claimed by this source release; the
  image remains unqualified.
- Mediate provider `write_file` requests through the trusted host executor into
  the checkout-local retained workspace. For `run_python`, confine untrusted
  child-process writes to size-capped tmpfs mounts and expose only one
  precreated file-size-bounded writable host export-file bind to the child. Layer
  entry/path/file/byte checks in the image-owned limiter, mount prior state plus
  the full input root and its kit compatibility subtree read-only, and validate
  the export only after verified container cleanup.
- Make editable-source evidence a content-bound full-file snapshot: initial and
  final inventories plus captured bytes must agree on path, device, inode, size,
  nanosecond mtime, and SHA-256, or capture fails before creating the ZIP.

This source release adds H2a planning, H2b execution, and Nightwatch serial
campaign plumbing but creates no benchmark attempt, score, board row, task
revision, scoring-method version, model call, site rebuild, or deployment. H2
remains open for independently authorized, genuinely graded cohorts. L4 planned
grade reports remain grader-owned deferred outputs, not executor-produced
evidence.

## v0.12 — 2026-08-28

- Freeze the public `L4-ECO` task revision
  `second-top-cross-spreader-midright-r1`: add one existing authored
  20x40x1000 rail as a second top cross-spreader at the requested midpoint,
  while preserving all 100 baseline instances.
- Add the fail-closed `marb_l4_eco_invariant.v0.12.0` public gate, built only on
  `cadclaw.roundtrip.snapshot_geometry` at audited CADCLAW 0.10.0 commit
  `60fc271f68c8a794a4741f856b2dd4c9878416a6`. It uses deterministic maximum
  matching and emits aggregate-only evidence.
- Require both a passing public invariant report and a task-local private-key
  requested-change grade attested by immutable gated-dataset readback in every
  graded L4 run record, with frozen methods, paths,
  and SHA-256 provenance.
- Record L4 as `defined_unmeasured`, with a locally validated private key whose
  gated distribution remains pending, zero registered runs, and a versioned
  blocker. The public gate has synthetic regression evidence only; the non-
  blind key-authoring/team session is not a benchmark run.

This is a method-only release. It adds no score row, does not rescore any
historical run, does not add L4 to the currently scored ladder, and does not
establish manufacturability, safety, certification, physical validation, or
increased readiness.

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
