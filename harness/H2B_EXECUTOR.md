# H2b cohort executor operator protocol

`cohort_executor.py` executes one MARB plan slot. It is intentionally separate
from the deterministic H2a planner and from every grader or publication path.
Importing either module performs no provider, Docker, model, registry, board,
site, or deployment action.

## Current qualification boundary

- Execution is opt-in and **local-no-charge only**. Metered calls are rejected
  before provider access until a frozen provider-specific pre-call price/token
  policy exists. A local-no-charge authorization uses `max_cost_usd: null` and
  `zero_cost_attested: true`. A credential may be named, but a credentialed
  endpoint must be HTTPS; transport is direct with ambient proxies disabled,
  redirects are rejected, and only the sanitized origin is journaled.
- The authorized model ID must equal the response model identity. No alias
  policy is declared. Provider seeds are bound to `provider-seed` plans;
  `independent-run-ordinal` plans require a null provider seed.
- Plan fields `cell_label` and `model.name` are operator-supplied display labels
  only, not model-identity evidence. Any later publishable identity must use the
  exact authorized `model.id` that the provider response confirms; H2b declares
  no model-alias policy.
- `max_total_turns` is one limit across the entire provider session. L2/L4 do
  not get a second allowance for the change phase. Separate global limits bound
  tool calls, `run_python` calls, request/transcript bytes, wall-clock time,
  and tool output. Provider `write_file` calls are mediated by the trusted host
  executor and persist bounded model-authored text in the checkout-local
  retained run workspace. For each `run_python` call, the prior workspace is
  mounted read-only and copied into a size-capped `/workspace` tmpfs. The
  untrusted child writes only within its tmpfs mounts, and its only writable
  host bind is the precreated, file-size-bounded export file. The image-owned limiter also enforces entry,
  path/depth, per-file, and aggregate-byte limits and kills all child processes
  on a violation. The authorized wall-clock deadline starts when the retained
  attempt is allocated and covers all normal work through Docker image
  inspect/create/readback/start, provider/tool work, artifact capture, and
  successful retained-inventory sealing. Docker stop/remove/absence readback has
  a separate bounded 15-second safety-cleanup allowance after failure or
  deadline; minimal fail-closed journal fallback may also run after breach.
  Neither is extra execution time under the authorized deadline.
- Every authorization and limit applies to exactly one logical slot and its one retained attempt;
  it is not a cohort or campaign budget. This includes
  `max_cost_usd`, which current local-no-charge policy requires to be null.
  Before any N=3 or N=9 cohort can call a provider, an operator must separately
  approve an aggregate campaign ledger with concurrency control. H2b does not
  implement that campaign ledger.
- Qualified cells have a limited, negative `execution_modality` attestation:
  provider input is text, no native image-view tool is exposed, staged image
  bytes may be inspected only through model-authored Python, and
  `vision_attested` is `false`. These runs must not be labeled or compared as
  sighted/vision cells.
- The implementation is currently restricted to Windows Docker Desktop host
  semantics. No image/host pair is qualified until the mandatory manual smoke
  succeeds. Linux and rootless-host bind/file ownership behavior for container
  UID/GID `65532:65532` remains unqualified.
- No real runtime image digest is shipped or implied by this repository. An
  operator must build or obtain the frozen image, record its registry
  `RepoDigest`, and complete the no-provider/no-network manual smoke in
  [`container/README.md`](container/README.md) before any real run.

## Required authorization boundary

A saved plan is not authorization. Before the provider object can be created,
the executor requires all of the following to agree exactly:

1. canonical plan bytes and an independently supplied plan SHA-256;
2. one unexpired `marb_execution_authorization.v2` payload naming the exact
   plan digest and planned run ID;
3. the authorization's independently supplied SHA-256;
4. the exact literal
   `EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>`;
5. explicit grants for execution, model calls, provider access, and the
   authorized zero-cost policy;
6. the exact provider protocol/model/endpoint/settings, optional credential
   environment-variable **name**, immutable container RepoDigest, normalized
   absolute `docker.exe` path plus its file SHA-256, normalized absolute Git
   executable path plus its file SHA-256, and exact run-limiter SHA-256;
7. the exact MARB source commit and SHA-256 identities of
   `harness/cohort_runner.py`, `harness/cohort_executor.py`,
   `harness/provider_transport.py`, `harness/isolated_container.py`, and
   `harness/container/run_limited.py`; and
8. commit-blob readback of every frozen plan input and implementation file.

The v2 authorization and schema validation bind the normalized absolute Git
path plus its file SHA-256 independently of the repository being authenticated.
`validate-authorization` validates those declared bindings but does not inspect
the host executable. Execution preflight verifies the actual host path chain
and executable file digest, rejecting symlink/reparse points or digest drift.
Production committed-blob reads use only `authorized_git`; no injected blob
reader is available on that path. The complete host check is repeated
immediately before every committed-blob spawn. On Windows, the executor opens
each path component and the executable with native handles and holds them across
the hash-to-process-creation interval, closing the hash-to-spawn replacement
window. Git is invoked only with the authorized absolute path as `argv[0]`,
never cwd or ambient `PATH` resolution. Every commit-blob read uses
`--no-replace-objects`, sets `GIT_NO_REPLACE_OBJECTS=1` in a minimal explicit
environment, and supplies only the exact resolved repository as a per-command
`safe.directory` value, never `*`. It bounds both stdout bytes and elapsed
timeout, failing closed on any breach. Run-log provenance does not expose the absolute workstation
path: `source.git_executable` records only a neutral executable basename/label
and the verified SHA-256.

The authorization records a credential key name and presence status only. It
must never contain the credential value. The executor scans retained artifacts
for the configured credential and common secret patterns before finalization.

## No-call authorization workflow

The three preparation commands below read local files only. They do not create
a provider session, invoke Docker, or call a model. First, write a complete
local-no-charge payload to a new path (existing outputs are rejected):

```powershell
$dockerExecutable = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
$gitExecutable = "C:/Program Files/Git/cmd/git.exe"
$dockerExecutableSha = (Get-FileHash -LiteralPath $dockerExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$gitExecutableSha = (Get-FileHash -LiteralPath $gitExecutable -Algorithm SHA256).Hash.ToLowerInvariant()

python harness/cohort_executor.py authorization-template `
  --plan <canonical-plan.json> `
  --expected-plan-sha256 <plan-sha256> `
  --slot <planned-run-id> `
  --approved-by <operator-identity> `
  --issued-utc <issued-utc> `
  --expires-utc <expires-utc> `
  --provider-endpoint "http://127.0.0.1:<port>/v1" `
  --container-image "<approved-repository>@sha256:<64-lowercase-hex>" `
  --docker-executable $dockerExecutable `
  --docker-executable-sha256 $dockerExecutableSha `
  --git-executable $gitExecutable `
  --git-executable-sha256 $gitExecutableSha `
  --max-output-tokens 4096 `
  --max-total-turns 8 `
  --max-total-tool-calls 32 `
  --max-run-python-calls 8 `
  --max-request-bytes 1000000 `
  --max-transcript-bytes 4000000 `
  --max-wall-clock-seconds 300 `
  --timeout-seconds 30 `
  --output <authorization-payload.json>
```

Use `--credential-env <DEDICATED_API_KEY_NAME>` only when separately approved;
it records a variable name, never its value, and requires an HTTPS endpoint.
The template hashes the currently executing H2b sources but is not approval or
proof that they belong to the authorized commit. Review every canonical payload
field out of band, then seal that unchanged reviewed payload to another new
path:

```powershell
python harness/cohort_executor.py seal-authorization `
  --payload <authorization-payload.json> `
  --output <sealed-authorization.json>
```

`seal-authorization` only canonicalizes and digest-wraps the reviewed payload;
it does not approve it. Read its `authorization_sha256` from the status JSON and
independently retain that digest. Then perform a no-call semantic, plan, digest,
slot, model-ID, and time-window check:

```powershell
python harness/cohort_executor.py validate-authorization `
  --authorization <sealed-authorization.json> `
  --expected-authorization-sha256 <authorization-sha256> `
  --plan <canonical-plan.json> `
  --expected-plan-sha256 <plan-sha256> `
  --slot <planned-run-id> `
  --at-utc <trusted-current-utc>
```

This validation is not an execution reservation and does not replace the full
commit-blob, Docker executable, image, staged-input, or expiry checks repeated
by `execute`. Only after independent review and the separate execution decision
may an operator run the one command that can create Docker/provider activity:

```powershell
python harness/cohort_executor.py execute `
  --plan <canonical-plan.json> `
  --expected-plan-sha256 <plan-sha256> `
  --authorization <sealed-authorization.json> `
  --expected-authorization-sha256 <authorization-sha256> `
  --authorize-execution "EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>" `
  --slot <planned-run-id>
```

Preparation status JSON reports output basenames only. Successful execution
reports only a repository-relative `runs/<attempt>` path and the run-log digest.
A retained execution failure emits structured JSON with status
`retained_failure`, a safe category, and the repository-relative run path on
standard error, then exits 2. Other rejected input/filesystem cases also emit
structured JSON and exit 2 without printing credentials or absolute local
paths.

Do not reuse an authorization for another plan, slot, implementation commit,
provider, model, setting, or image. Do not weaken a task-specific readiness
blocker to make a plan executable.

## Prompt fairness boundary

This release accepts only `prompt_variant: frozen-core`. From the frozen driver
brief, the executor delivers as the provider user message only the nonempty
payload strictly between the single exact marker lines `` `=== BEGIN ===` ``
and `` `=== END ===` ``. The brief preamble, operator instructions, and grader
suffix are excluded. Missing, duplicated, reversed, or nested marker text fails
before provider construction. The trusted executor system message remains a
separate message, and the delivered payload byte length and SHA-256 are recorded
in the retained run log.

## Isolation and retention

The provider conversation stays in the trusted host process. Only a selected
workspace-relative Python file runs inside the authorized OCI image. The
container is pre-inspected, created with an unpredictable name, and read back
before start. Policy requires no image pull, no network, a read-only root,
dropped capabilities, no-new-privileges, clean environment, resource limits,
no Docker socket, and verified stop/removal on every success, failure, or
timeout. The prior host workspace is read-only at `/marb-host-workspace`; the
full staged input root is read-only at `/marb-input`, and its `kit/` subtree is
also read-only-mounted at `/workspace/kit` for the frozen brief's compatible
geometry paths. The complete staged archive tree at `/marb-input` includes its
root brief/docs, reference images, license, and other frozen members; the run
log's `workspace_inputs` manifest records `container_root` as `/marb-input` and
the kit compatibility root as `/workspace/kit`. Within each untrusted OCI
child, the writable workspace and export parent are separate size-capped tmpfs
mounts. The single precreated `/marb-export/workspace.tar` file is the only
writable host bind exposed to that child, bounded by the container file-size
limit and host-side archive validation.
After the child exits, the host removes and verifies absence of the container
before validating that archive and atomically replacing the prior workspace.
The Docker CLI path itself is explicit and bound to
authorization/attestation; ambient `PATH` lookup is not an authenticity
boundary.

Each logical slot is atomically and permanently claimed within the current
checkout before its new UUIDv4 attempt directory is created. A successfully
sealed attempt has this shape; an early retained failure can contain only the
files reached before failure:

```text
runs/.slot-claims/<planned-run-id>.json
runs/<planned-run-id>--<attempt-uuid>/
  workspace/
  inputs/
  evidence/
  events.jsonl
  artifact_inventory.json
  run_log.json
  run_log.sha256
```

The canonical JSON claim records the logical slot and attempt UUID; it is not
removed when setup or execution fails. It prevents a second allocation only
within the same checkout. Because `runs/` is ignored and the claim is
checkout-local, this mechanism is not a global lock. Cross-clone and
cross-host uniqueness remains the responsibility of operator/campaign-ledger
coordination and later registry/publication validation. The executor rejects
registered and unregistered local collisions,
including Windows-normalized aliases. Frozen kit members are staged read-only
and their commit and staged hashes are checked again during execution. STEP and
deterministic editable-source ZIP artifacts are journaled when created; failed
and partial attempts are retained rather than overwritten. Run logs bind the plan,
authorization, implementation, committed inputs, request/response and
transcript hashes, actual provider response identity where available, timing,
usage status, container attestation, events, and the final retained inventory.
Failed or timed-out provider attempts are `attempted_not_reported`; they are
never reported as `not_incurred`.

Canonical STEP discovery and evidence capture are one identity-bound operation,
even though the executor performs them in two calls. Discovery freezes the
regular file's device, inode, mode, size, nanosecond mtime, byte count, and
SHA-256 from one stable open handle. Capture then reopens that exact path,
checks the open handle and pathname against the frozen identity before and
after copying, and requires the copied digest and byte count to match. A
same-path rewrite or atomic replacement after discovery therefore aborts with
`artifact_failure` and removes any partial evidence target.

In `run_log.json`, `output_contract` uses schema
`marb_plan_output_binding.v2`. Its `executor_owned_outputs` map records the
executor-reserved/bound output paths, not a claim that those files were
produced; only `artifacts` entries attest successfully captured outputs. Failed
and partial runs may list owned paths without listing nonexistent artifacts.

Editable-source ZIP capture is a content-bound snapshot and is not extension-
or staged-name-filtered: it keeps
every safe regular file retained in the authored workspace, even when the same
relative name exists under the separate staged input root. It excludes the
canonical STEP output and fails closed on reserved
`MARB_SOURCE_MANIFEST.json`, `MARB_EXECUTION_STATUS.json`, or `.marb_*`
identities rather than silently omitting them. Initial and final inventories
bind every workspace file to device, inode, size, nanosecond mtime, and SHA-256;
captured bytes must match that identity. Any addition, removal, replacement, or
mutation observed during capture aborts before a ZIP target is created.

## Task routing and grading boundary

| Task | Provider continuity | Executor-owned evidence | Grader-owned deferred evidence |
|---|---|---|---|
| `L1-ASSEMBLE` | One baseline session | baseline/final STEP and editable-source ZIP | normal L1 grading outputs |
| `L2-RESOLVE` | One continuous baseline-to-change session | baseline/changed STEP and editable-source ZIP | task-local requested-change grade |
| `L4-ECO` | One continuous baseline-to-change session | baseline/changed STEP and editable-source ZIP | public invariant report and gated requested-change attestation |

The L2/L4 change request is withheld until the baseline STEP and source ZIP are
captured. L4 planned report paths are output reservations for the later trusted
grader; the executor does not fabricate them and completes only as
`completed_ungraded`.

The executor never writes the run registry, board, task definitions, grading
keys, `publishing/`, or deployment workflows. Publication requires separate
trusted grading, registry registration, board-policy validation, review, and
deployment authorization. Existing historical runs are never overwritten or
silently rescored.

## CADCLAW runtime pin

H2b authorizes CADCLAW 0.10.0 commit
`60fc271f68c8a794a4741f856b2dd4c9878416a6`, CadQuery 2.7.0, and
cadquery-ocp 7.8.1.1.post1 under pin basis
`marb_v0.12_frozen_functional_core`. The CADCLAW commit is the audited
functional core frozen by the MARB v0.12 L4 invariant contract; it is not a
claim that this is the latest CADCLAW `main`. Newer upstream or qualification
commits, including a fresh candidate, must be independently calibrated and the
MARB contract versioned before replacing this pin.

## CI and manual gates

Board-policy CI runs `tests.test_cohort_runner`, `tests.test_cohort_executor`,
`tests.test_provider_transport`, `tests.test_isolated_container`,
`tests.test_run_limiter`, and the static `tests.test_container_recipe` contract
checks with fake provider, HTTP, sandbox, and Docker command runners. Those
tests make no provider, model, or Docker calls. CI does not qualify a real
image. Before the first actual attempt, an operator must record the approved
image RepoDigest and pass the manual no-provider/no-network runtime smoke
described in the container build notes.
