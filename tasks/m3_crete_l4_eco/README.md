# M3-CRETE L4-ECO

- Task ID: `L4-ECO`
- Task revision: `second-top-cross-spreader-midright-r1`
- MARB method: `v0.12`
- Status: `defined_unmeasured`

L4-ECO measures whether a CAD driver can apply one frozen engineering change
to its editable M3-CRETE build without collateral changes. The request adds one
existing authored 20x40x1000 rail as a second top-frame cross-spreader, halfway
between the center spreader and top-right side rail. See
[`ECO_REQUEST.md`](ECO_REQUEST.md) for the normative boundary.

## Run contract

One independent attempt is one uninterrupted engineering-change loop:

1. Produce the baseline M3-CRETE assembly with the selected model and driver.
2. Preserve the baseline editable source and STEP export.
3. Give the same driver session the frozen ECO request.
4. Preserve the changed editable source, changed STEP, and run log.
5. Run `grader/eco_invariant.py` with the CADCLAW commit pinned in
   `requirements-l4-eco.txt` and preserve its aggregate JSON report. Board
   validation reruns that gate against the registered STEP pair and requires
   byte-for-byte equivalent report content.
6. Grade the requested placement against the task-specific private answer key;
   store that per-run aggregate grade report in the immutable gated revision
   and record only its readback path and SHA-256 in public evidence. Board
   validation authenticates and downloads those exact bytes with the protected
   `MARB_GATED_READ_TOKEN` credential before accepting the attestation.

The model/tool version, blind kit, prompt, and harness cohort are frozen within
a published cell. Three genuine independent attempts and three gradeable
changed outputs are required. Retries or alternate exports inside one driver
session do not increase `n`.

Each registered attempt binds all before/after artifacts, the ECO request,
public invariant report, authenticated gated requested-change readback, and run
log through paths and lowercase SHA-256 digests. The public attestation is not
a recomputation of private geometry; its trust boundary is an authenticated
download from the exact immutable gated dataset revision, followed by digest
and strict aggregate-schema validation. The opaque continuity
ID must not contain a credential, token, user identifier, or other secret.
SHA-256 identities for
tracked text use canonical UTF-8/LF bytes; binary identities use raw bytes.

## Public invariant gate

The public gate uses only `cadclaw.roundtrip.snapshot_geometry` from audited
CADCLAW 0.10.0 commit `60fc271f68c8a794a4741f856b2dd4c9878416a6`. A
deterministic maximum-cardinality match requires every baseline renderable
shape to retain its rounded signature, center, all six AABB coordinates,
per-axis dimensions, and bounding-box volume within the frozen tolerances. It
also requires exactly one unmatched changed shape with the rail's rounded
20x40x1000 AABB-dimension signature and requested 20x1000x40 axis orientation.
That dimension signature does not establish source-asset identity or topology.
Reports contain aggregate counts, raw input digests, and statuses only, never
per-part poses.

The task's 100-to-101 count is the authored-instance contract. CADCLAW's
snapshot count follows its bounded renderable-shape loader and is not assumed
to equal the authored-instance count; the public gate requires only a +1 shape
delta while preserving every baseline snapshot shape.

This is deliberately narrower than a full CAD semantic diff. Anonymous AABB
matching cannot observe swaps between geometrically identical instances, and
some symmetric rotations preserve the same extents. The gate does not prove
topology, feature history, PMI, material, suppression state, manufacturing
fitness, safety, or physical validity. Coincident solids can also be hidden by
CADCLAW's rounded-bbox deduplication. The private reference grade separately
checks the requested midpoint/top-flush placement.

## Current evidence boundary

This release freezes the task, a locally validated private-key revision, and
the public gate. The private key has not been uploaded/read back through a
gated dataset revision, the registry contains zero `L4-ECO` runs, and no
three-run cohort exists. The key-authoring/team session is non-blind and cannot
count as a benchmark attempt. Accordingly no L4 score or board row is published, no
historical run is relabeled or rescored, and the ladder continues to score only
L0 and L1. See
[`results/evidence/l4_eco/v0.12/blocker.json`](../../results/evidence/l4_eco/v0.12/blocker.json).
