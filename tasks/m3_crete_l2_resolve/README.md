# M3-CRETE L2-RESOLVE

- Task ID: `L2-RESOLVE`
- Task revision: `top-spreader-x+200-r1`
- MARB method: `v0.11`
- Status: `defined_unmeasured`

L2-RESOLVE measures whether a CAD driver can continue from its own initial
M3-CRETE build, apply one frozen parameter change, and export the changed
assembly without starting over. The frozen request moves the existing
top-center-spreader subassembly +200.0 mm along global X. See
[`CHANGE_REQUEST.md`](CHANGE_REQUEST.md) for the normative change boundary.

## Run contract

One independent attempt is one uninterrupted change loop:

1. Produce the initial M3-CRETE assembly with the selected model and driver.
2. Preserve the baseline editable source and baseline STEP export.
3. Give the same driver session the frozen change request.
4. Preserve the changed editable source, changed STEP export, and run log.
5. Grade only after the task-specific resolver key is available.

The model/tool version, blind kit, prompt, and harness cohort are frozen within
a published cell. Three genuine independent attempts and three gradeable
changed outputs are required. Retries or alternate exports inside one driver
session do not increase `n`.

Each registered attempt must carry the baseline and changed artifact paths and
SHA-256 digests, the frozen change-request path and digest, and an opaque
driver-continuity ID. The continuity ID is provenance for the same-session
claim; it must not contain a token, credential, user identifier, or other
secret.

SHA-256 identities for tracked text use the repository's canonical UTF-8/LF
bytes, independent of a checkout's platform line-ending conversion. Binary
artifact identities use their raw bytes.

## Current evidence boundary

This release freezes the task and validation contract only. The checked-in
registry contains zero `L2-RESOLVE` runs. The task-specific private resolver key
is locally authored and validated, but it has not been uploaded to a gated
dataset revision. No qualifying same-driver editable baseline source or
three-run gradeable cohort was available for this release. Accordingly:

- no L2 score or leaderboard row is published;
- no historical L1 run is relabeled or rescored as L2; and
- the full-stack ladder continues to score only L0 and L1.

The machine-readable blocker record is
[`results/evidence/l2_resolve/v0.11/blocker.json`](../../results/evidence/l2_resolve/v0.11/blocker.json).
