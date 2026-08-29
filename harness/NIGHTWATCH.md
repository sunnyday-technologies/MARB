# Nightwatch local repeat-run controller

`nightwatch.py` is the serial, approval-bound controller around the existing
H2a cohort planner and H2b one-slot executor. It advances already planned H2a
slots using already reviewed H2b authorizations. It does not create either
approval, grade runs, mutate the registry, publish results, or deploy anything.
Importing the module performs no file, environment, subprocess, provider,
model, Docker, or network action.

Dedicated fake/local Nightwatch tests exist and are included in board-policy
CI. They do not invoke a real provider, model, Docker runtime, or network. No
real provider/model/Docker campaign or runtime qualification has been
performed, and no completed real campaign is claimed here.

## Authority chain

Nightwatch execution requires all three layers below. Nightwatch does not
create, infer, renew, or weaken any of them:

1. One or more canonical `marb_cohort_plan.v1` envelopes, each verified against
   its independently retained plan SHA-256. Normal N-slot cohorts may reuse one
   plan path across slots only when every reuse binds the identical digest.
2. One reviewed and sealed `marb_execution_authorization.v3` per slot. Each
   authorization remains bound to exactly one plan digest, planned run ID,
   model, provider configuration, source revision, implementation, runtime,
   settings, and validity window.
3. One independently reviewed canonical `marb_nightwatch_campaign.v1`
   envelope. Its digest approves the controller identity, aggregate window and
   limits, exact automation policy, ordered slots, and every per-slot plan,
   authorization, digest, and H2b confirmation literal.

Each slot authorization must bind the active `marb-v0.13-h2b`
execution-runtime contract, exact CADCLAW
`fad0dd552a49a0b32336f1845c2b82873ad6360a` source and calibration identities,
and a qualified image RepoDigest. The unchanged L4
v0.12/`60fc271f68c8a794a4741f856b2dd4c9878416a6` grade contract remains a
separate historical identity; Nightwatch does not alter either contract.

`make_campaign_envelope(payload)` digest-wraps reviewed campaign payload bytes;
it does not approve execution. The envelope is canonical JSON with one LF
terminator and exactly these top-level fields:

```json
{
  "schema": "marb_nightwatch_campaign.v1",
  "campaign_sha256": "<sha256-of-canonical-campaign-payload>",
  "campaign": {
    "campaign_id": "11111111-1111-4111-8111-111111111111",
    "approved_by": "<public-operator-label>",
    "issued_utc": "2026-08-29T01:00:00Z",
    "window": {
      "not_before_utc": "2026-08-29T01:00:00Z",
      "expires_utc": "2026-08-29T07:00:00Z"
    },
    "source_revision": "<full-lowercase-40-character-marb-commit>",
    "controller": {
      "path": "harness/nightwatch.py",
      "sha256": "<lf-normalized-controller-source-sha256>"
    },
    "execution": {
      "execute": true,
      "max_concurrency": 1
    },
    "policy": {
      "billing_mode": "local-no-charge",
      "zero_cost_attested": true,
      "max_cost_usd": null,
      "loopback_only": true,
      "credentials_allowed": false,
      "external_providers_allowed": false,
      "grading_allowed": false,
      "registry_mutation_allowed": false,
      "publication_allowed": false,
      "deployment_allowed": false
    },
    "limits": {
      "max_slots": 3,
      "failure_stop_threshold": 1
    },
    "slots": [
      {
        "ordinal": 1,
        "plan_path": "runs/nightwatch-inputs/slot-01-plan.json",
        "plan_sha256": "<independently-retained-plan-sha256>",
        "planned_run_id": "<exact-plan-slot>",
        "authorization_path": "runs/nightwatch-inputs/slot-01-authorization.json",
        "authorization_sha256": "<independently-retained-authorization-sha256>",
        "authorization_literal": "EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>"
      }
    ]
  }
}
```

The UUID shown above is illustrative; `campaign_id` must be a canonical UUIDv4.
All timestamps use exact whole-second UTC text `YYYY-MM-DDTHH:MM:SSZ`.
`window.not_before_utc` may not precede `issued_utc`, and `expires_utc` must be
later than `not_before_utc`. `campaign_sha256` is the SHA-256 of the canonical
`campaign` payload, not the whole envelope, and must match the independently
supplied expected digest. `approved_by` is a nonempty public ASCII label of at
most 100 characters. `source_revision` is exactly 40 lowercase hexadecimal
characters.

The campaign's `controller` identity must exactly match the executing
`harness/nightwatch.py` path and its LF-normalized source SHA-256. The execution
block must be exactly `{"execute":true,"max_concurrency":1}`, and every policy
field must equal the value shown above. Unknown or missing fields fail closed.

`limits.max_slots` must equal the number of slots and be between 1 and 256.
`failure_stop_threshold` must be between 1 and the number of slots. Slot
ordinals are contiguous and one-based. Plan and authorization paths are safe,
normalized repository-relative POSIX paths of at most 500 characters: they do
not contain backslashes, control characters, absolute roots, `.` or `..`
components, or colon-bearing components. Authorization paths are
case-insensitively unique. A plan path may be reused by multiple slots only
with the same `plan_sha256`; the same path with a conflicting digest is
rejected. A plan path and authorization path may never alias each other.
Planned run IDs are unique and match
`[A-Za-z0-9][A-Za-z0-9._-]{0,191}`. Every slot's literal is exactly:

```text
EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>
```

Each slot must have exactly one matching run ID in its bound plan, and every
plan must bind the campaign's `source_revision`. One shared plan may therefore
carry the normal N>=3 cohort while each slot retains its own unique reviewed
authorization file. Each authorization digest, plan digest, run ID, and model
ID is verified through H2a/H2b before dispatch. Campaign and ledger files
contain paths and digests, never credential values.

## Automated-provider boundary

Nightwatch automation is narrower than the general H2b schema. For every slot,
the verified H2b authorization must have:

- `billing_mode: local-no-charge`;
- `credential_env: null`;
- an endpoint whose parsed host is `localhost` or an IP address classified as
  loopback;
- spend exactly `{"currency":"USD","max_cost_usd":null,"zero_cost_attested":true}`;
  and
- the campaign-wide exact policy and `max_concurrency: 1` shown above.

An external endpoint, a credentialed endpoint, a metered authorization, or any
other potentially paid provider is **manual-only**. Nightwatch rejects it during
input validation before acquiring the campaign writer lock or constructing a
provider. A paid or external run remains a separately reviewed one-slot action
under the H2b operator protocol. Nightwatch passes `environ={}` to H2b so an
automated run cannot obtain a provider credential from the ambient environment.

## Python API

The module exposes these orchestration entry points:

```python
controller_identity() -> dict[str, str]
make_campaign_envelope(payload: dict[str, Any]) -> dict[str, Any]
verify_campaign_envelope(raw: bytes, *, expected_sha256: str) -> dict[str, Any]
run_campaign(
    repo_root: Path,
    *,
    campaign_raw: bytes,
    expected_campaign_sha256: str,
    execute: bool = False,
    confirmation_literal: str | None = None,
    executor: Callable[..., dict[str, Any]] = cohort_executor.execute_plan,
    now: Callable[[], dt.datetime] | None = None,
) -> dict[str, Any]
```

`controller_identity` returns exactly `{"path":"harness/nightwatch.py",
"sha256":"<lf-normalized-source-sha256>"}`. `make_campaign_envelope` wraps a
payload but does not approve it. `verify_campaign_envelope` verifies canonical
bytes, exact fields, and the independently supplied digest without touching
runtime state. `run_campaign` is read-only by default because `execute` defaults
to `False`; `execute=True` additionally requires the exact Nightwatch
confirmation literal. The injectable executor and clock are explicit test
seams, not alternate production authority paths.

## CLI

Nightwatch has exactly two commands: `status` and `run`. Both require the
campaign file and the independently retained campaign digest. `--repo-root` is
optional and defaults to the repository containing `harness/nightwatch.py`.

### Read-only status

```powershell
python harness/nightwatch.py status `
  --campaign runs/nightwatch-inputs/campaign.json `
  --expected-campaign-sha256 <campaign-sha256>
```

`status` is the non-executing default behavior of `run_campaign`. It verifies
the campaign envelope, controller identity, every plan, and every authorization
without requiring the campaign or authorizations to be current at the time of
inspection. Authorization semantics are checked at the midpoint of each saved
authorization window. It then reads and reconciles any existing ledger, event
journal, slot claims, and sealed H2b logs.

Status does not create the Nightwatch directory, acquire the writer lock, write
a ledger or event, read credential values, invoke H2b execution, construct a
provider, invoke Docker, or call a model. Its canonical stdout schema is
`marb_nightwatch_status.v1`, with `mode: read_only`, the campaign digest,
campaign status, the full in-memory ledger view, and `would_execute` containing
the still-pending run IDs. This view is not an execution reservation.

### Serial run

```powershell
python harness/nightwatch.py run `
  --campaign runs/nightwatch-inputs/campaign.json `
  --expected-campaign-sha256 <campaign-sha256> `
  --confirmation-literal "EXECUTE_MARB_NIGHTWATCH:<campaign-sha256>"
```

`run` requires the exact confirmation literal:

```text
EXECUTE_MARB_NIGHTWATCH:<campaign-sha256>
```

It checks the campaign window and current validity of every authorization before
the writer lock, then repeats the campaign-window and selected-slot input checks
under the lock immediately before dispatch. Each pending slot is passed to
`cohort_executor.execute_plan` with its exact saved plan and authorization
bytes, independent digests, planned run ID, and saved H2b authorization literal.
Only one slot runs at a time, and the controller reconciles its retained state
before moving to the next slot.

The canonical success output uses `marb_nightwatch_result.v1` with
`mode: executed`, campaign digest, terminal or current campaign status,
repository-relative ledger and event paths, and the ledger. Policy, input, or
lock errors are written safely to stderr and exit with status 2.

## Local ledger, events, and writer lock

Nightwatch state is checkout-local at:

```text
runs/.nightwatch/<campaign-uuid>/
  writer.lock
  ledger.json
  events.jsonl
```

`ledger.json` uses `marb_nightwatch_ledger.v1`. It binds the campaign UUID and
digest, source revision, aggregate limits, event sequence, timestamps, campaign
status, and the exact immutable identity of every slot. Mutable slot fields are
status, attempt UUID, repository-relative run directory, sealed run-log digest,
and a safe failure category.

Supported slot statuses are `pending`, `running`, `completed_ungraded`,
`failed`, `partial`, and `manual_review`. Supported campaign statuses are
`ready`, `running`, `stopped`, `completed_ungraded`,
`completed_with_failures`, and `manual_review`. Reaching the approved
`failure_stop_threshold` sets the campaign to `stopped`; a manual-review slot
sets the campaign to `manual_review`.

Execution holds one nonblocking OS-backed exclusive lock for all reconciliation
and ledger writes. Windows uses `msvcrt.locking`; POSIX uses `fcntl.flock`.
Ownership comes from the live file handle, not the existence of `writer.lock`,
so the OS releases ownership when the process ends. Failure to acquire the lock
blocks dispatch.

Ledger snapshots are canonical JSON written to a unique UUID-suffixed temporary
file, flushed, `fsync`ed, and atomically replaced with `os.replace`.
`events.jsonl` uses `marb_nightwatch_event.v1`; each canonical event records a
strictly contiguous sequence, campaign UUID and digest, event name, optional
slot ordinal and run ID, status, and UTC timestamp. The event is appended,
flushed, and `fsync`ed before the corresponding ledger snapshot is replaced.

The loader rejects an incomplete trailing event, malformed event, sequence gap,
ledger ahead of its event journal, nonzero ledger sequence with a missing event
journal, or an event journal present without a ledger snapshot. If complete
events are ahead of the snapshot, it advances the ledger's event sequence and
derives current run state from claims and sealed logs; an event alone is not run
evidence. Ledger and event records contain no provider credentials, response
bodies, prompts, model-authored source, or absolute workstation paths.

## Reconciliation and no-retry rule

H2b's permanent slot claim and sealed attempt log are more authoritative than
Nightwatch's operational ledger. Reconciliation:

1. Reads `runs/.slot-claims/<planned-run-id>.json` and verifies its exact
   `marb_logical_slot_claim.v1` identity and canonical attempt UUID.
2. Requires exactly one matching `runs/<planned-run-id>--<attempt-uuid>`
   directory.
3. Verifies canonical `run_log.json` against `run_log.sha256`.
4. Requires the H2b run-log schema, run and attempt IDs, MARB revision, plan and
   authorization digests, sealed journal, and terminal status to agree.
5. Requires every publication flag—graded, registry, board, site, and
   deployment—to remain false.

A valid sealed attempt becomes `completed_ungraded`, `failed`, or `partial` in
the Nightwatch ledger. Failed and partial attempts also retain their safe H2b
failure category. Any claimed attempt whose directory, digest, log, seal, or
identity is missing or contradictory becomes `manual_review` with a safe local
category.

Once a permanent claim exists, the slot is consumed even if execution failed
early, timed out, was cancelled, retained partial artifacts, or lost its attempt
directory. Nightwatch never deletes a claim, overwrites an attempt, or retries
that logical run ID. A new attempt requires a new H2a slot and new approvals.
Likewise, a non-pending nonterminal ledger state without a matching claim is
routed to manual review rather than automatic redispatch.

If Nightwatch stops after H2b seals a run but before the local ledger update,
the next reconciliation imports the sealed terminal result and does not invoke
H2b again. An unclaimed slot remains eligible only while its ledger state is
still `pending` and all campaign and per-slot bindings remain valid.

## Terminal boundary

The strongest successful campaign and slot state is `completed_ungraded`. It
means the authorized H2b artifacts and run journal were retained and sealed. It
is not a score, gate result, registry record, board row, publication decision,
or deployment authorization.

Nightwatch's exact approved policy forbids grading, registry mutation,
publication, and deployment. The controller also verifies that reconciled H2b
logs report every publication flag as false. Nightwatch never runs a grader,
creates grader-owned deferred evidence, edits `results/marb_runs.json` or board
data, rebuilds site source, publishes, deploys, mutates existing attempts, or
silently rescales historical results.

The OS lock, ledger, events, and H2b claims protect only one local checkout.
They do not provide cross-clone or cross-host uniqueness; that remains an
operator coordination responsibility.
