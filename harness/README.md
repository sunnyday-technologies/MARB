# MARB local-anchor track builder harness

This harness drives a local open-weight model to build the M3-CRETE gantry frame (task 1)
in CadQuery. The model is served by Ollama through an OpenAI-compatible API. It works only
from the staged blind kit and the frozen task brief. This is the builder for the
local-anchor floor on the MARB capability curve.

The harness builds. It does not grade. Grading is a separate step that runs the MARB
grader on the exported STEP file: `grader/marb_grade_all.py` for the GAP, ORIENT, and POS
positional metrics, and `grader/grade_native_step.py` for the native gates (inventory,
interference, floating).

## Plan and execute a cohort slot

`cohort_runner.py` remains the H2a, standard-library-only planner for
`L1-ASSEMBLE`, `L2-RESOLVE`, and `L4-ECO`. Its `plan` command validates the
selected task's frozen public inputs against the current registry and emits
deterministic canonical JSON for at least three distinct seeds. Planning does
not read credentials, contact a provider, create run evidence, grade an
artifact, authorize spend, or mutate the registry or board. Model and driver
names in a plan are inert labels, and every slot remains `planned` with null
outcome and evidence digests. In particular, `cell_label` and `model.name` are
operator-supplied display labels, not identity evidence. A publishable model
identity must use the exact authorized `model.id` confirmed by the provider
response; H2b has no alias policy. This release accepts only the byte-bound
`prompt_variant` value `frozen-core`.

```powershell
$sourceRevision = git rev-parse HEAD
$pythonExe = "<absolute-python.exe>"
$planPath = Join-Path (Get-Location) "canonical-plan.json"
if ($PSVersionTable.PSVersion -lt [version]"7.4") { throw "PowerShell 7.4+ is required for byte-preserving native stdout redirection" }
& $pythonExe harness/cohort_runner.py plan `
  --task L4-ECO `
  --source-revision $sourceRevision `
  --cell-id l4-eco-example-cadquery `
  --cell-label "L4 ECO example - CadQuery" `
  --cohort-id l4-eco-example-v1 `
  --model-id example/model `
  --model-name "Example Model" `
  --driver cadquery `
  --driver-version 2.7.0 `
  --prompt-variant frozen-core `
  --seed-basis independent-run-ordinal `
  --seed 01 --seed 02 --seed 03 > $planPath
if ($LASTEXITCODE -ne 0) { throw "planner failed; discard the incomplete plan file" }
$planSha256 = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()
```

The full lowercase source commit must match the checked-out `HEAD`. The planner
binds the registry's canonical bytes, its own source, and each selected public
input. It rejects frozen-input drift, unsafe or linked paths, duplicate or
normalized-alias seeds such as `1` and `01`, and run-ID/path collisions. The L4
added part is hash-validated directly inside its ZIP without extraction.
The plan file must preserve the planner's exact canonical UTF-8 stdout bytes,
including its final newline. PowerShell 7.4+ preserves native byte streams for
the native `>` redirection above. Do not pipe through `Out-File`, `Set-Content`, or older
PowerShell native redirection, which can transcode or add a BOM. This capture is
external to H2a; the planner itself remains read-only and has no file-write path.

`cohort_executor.py` is the separate H2b executor. It executes exactly one
planned slot only after fail-closed readback of:

- the independently supplied plan SHA-256;
- a canonical `marb_execution_authorization.v2`, time-bounded and bound to that
  plan, slot, provider, settings, digest-pinned container, absolute Docker and
  Git executable identities, source revision, and exact planner, executor,
  provider-transport, isolation-module, and run-limiter blobs;
- the independently supplied authorization SHA-256 and exact confirmation
  literal `EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>`; and
- every frozen input from the authorized commit before provider construction.

For prompt fairness, the provider user message contains only the payload between
the single exact frozen-brief marker lines `` `=== BEGIN ===` `` and
`` `=== END ===` ``. Preamble/operator text and the grader suffix are excluded;
malformed or duplicate markers fail before provider construction. The trusted
system message is separate, and the delivered payload bytes and digest are
journaled.

The operator CLI separates preparation from execution:
`authorization-template` writes a complete local-no-charge payload for human
review, `seal-authorization` digest-wraps that unchanged canonical payload, and
`validate-authorization` checks it without Docker/provider/model activity. Only
`execute`, with the exact confirmation literal, can begin a run. Preparation
status reports contain output basenames only; execution reports a
repository-relative `runs/<attempt>` path, and retained failures return
structured JSON with exit code 2. See [`H2B_EXECUTOR.md`](H2B_EXECUTOR.md) for
the exact commands and their remaining full-preflight boundary.

The v2 authorization and schema validation bind the normalized absolute Git
path plus its file SHA-256, not a bare executable name.
`validate-authorization` validates those declared bindings but does not inspect
the host executable. Execution preflight verifies the actual host path chain
and executable file digest, rejecting symlink/reparse points or digest drift.
Production committed-blob reads use only `authorized_git`; no injected blob
reader is available on that path. The complete host check is repeated
immediately before every committed-blob spawn. On Windows, the executor opens
each path component and the executable with native handles and holds them across
the hash-to-process-creation interval, closing the hash-to-spawn replacement
window. Git uses only the authorized absolute path as `argv[0]`, never cwd or
ambient `PATH` resolution. Commit reads use `--no-replace-objects` with
`GIT_NO_REPLACE_OBJECTS=1` in a minimal explicit environment. The only
`safe.directory` value is the exact resolved repository supplied per command,
never `*`; stdout bytes and elapsed timeout are bounded and fail closed. The run log records only a neutral
Git executable basename/label and verified SHA-256, not the absolute
workstation path. The copy/paste no-call template in `H2B_EXECUTOR.md` requires
both `--git-executable` and `--git-executable-sha256`.

This release's execution policy is **local-no-charge only**. Authorization must
use `billing_mode: local-no-charge`, `max_cost_usd: null`, and an explicit
zero-cost attestation. A credential may be named only for an HTTPS endpoint;
transport is direct with ambient proxies disabled, redirects are rejected, and
only the sanitized origin is journaled. Metered provider execution is not
supported until MARB has a frozen provider-specific pre-call price/token
policy; a provider-reported cost that contradicts the zero-cost attestation
fails the run. `max_total_turns` is one aggregate ceiling shared by baseline
and change phases, not a per-phase allowance. Global tool-call, Python-call,
request/transcript-size, wall-clock, and output limits fail closed. Provider
`write_file` calls are mediated by the trusted host executor and persist
validated, bounded model-authored text in the checkout-local attempt workspace.
For `run_python`, the prior host workspace is mounted read-only and copied into
a size-capped tmpfs. The untrusted child writes only within tmpfs, and its only
writable host bind is one precreated, file-size-bounded export file. The image-owned limiter
additionally bounds entry count, path/depth, per-file size, and aggregate bytes
before a validated export can replace the read-only prior workspace. The full
staged input root is read-only at `/marb-input`, with its kit subtree also
read-only at `/workspace/kit` for frozen-brief compatibility. This is the
complete staged tree, including root brief/docs, reference images, license, and
other frozen members; `workspace_inputs.container_root` in the run log is
`/marb-input`. The authorized wall-clock covers normal work from retained
allocation through Docker inspect/create/readback/start, provider/tools,
artifact capture, and successful retained-inventory sealing. Docker
stop/remove/absence readback instead has a separate bounded 15-second safety
allowance after failure or deadline; minimal fail-closed journal fallback may
also run after breach.

Those limits and the authorization apply to one logical slot and its one
retained attempt, not to an entire cohort or campaign. That scope includes
`max_cost_usd`, which current local-no-charge policy requires to be null. An
N=3 or N=9 campaign still requires a separately approved aggregate campaign
authorization plus reviewed per-slot H2b authorizations. H2b remains a one-slot
executor; Nightwatch implements the serial local campaign ledger and
concurrency control without broadening any slot authorization.

## Nightwatch serial repeat-run controller

[`nightwatch.py`](nightwatch.py) implements the local serial wrapper specified
in [`NIGHTWATCH.md`](NIGHTWATCH.md). It consumes an immutable,
independently digest-approved `marb_nightwatch_campaign.v1` envelope. The
campaign binds a UUID, approval and execution window, one MARB source revision,
the LF-normalized Nightwatch controller identity, explicit execution and safety
policy blocks, aggregate slot/failure limits, and an ordered list of
repository-relative plan and authorization bindings. Multiple normal cohort
slots may reuse the same plan path only with an identical digest;
authorization paths remain unique and may not alias a plan path. Every slot
includes its independently retained plan and authorization digests, exact
planned run ID, and exact H2b literal
`EXECUTE_MARB_MODEL_CALLS:<plan-sha256>:<planned-run-id>`.

The automated policy is serial (`max_concurrency: 1`) and accepts only
loopback, credential-free, local-no-charge slots with an exact zero-cost spend
attestation. External, credentialed, metered, and otherwise potentially paid
providers stay manual-only under the one-slot H2b protocol. The controller's
dedicated fake/local tests exist and are included in board-policy CI. Those
tests do not invoke a real provider, model, Docker runtime, or network. No real
provider/model/Docker campaign or runtime qualification has been performed,
and no completed real campaign is claimed.

Read and reconcile state without writes or execution:

```powershell
python harness/nightwatch.py status `
  --campaign <campaign.json> `
  --expected-campaign-sha256 <campaign-sha256>
```

Run the currently approved campaign window with the exact aggregate
confirmation literal:

```powershell
python harness/nightwatch.py run `
  --campaign <campaign.json> `
  --expected-campaign-sha256 <campaign-sha256> `
  --confirmation-literal "EXECUTE_MARB_NIGHTWATCH:<campaign-sha256>"
```

Execution uses one OS-backed writer lock, atomically replaced canonical ledger
snapshots, and append-only sequenced events under
`runs/.nightwatch/<campaign-uuid>/`. Reconciliation treats H2b's permanent slot
claims and matching sealed run logs as authoritative. A claimed or retained
attempt is consumed and is never retried automatically; missing or
contradictory evidence requires manual review. Nightwatch's strongest success
state is `completed_ungraded`; it never grades, edits the registry or board,
rebuilds site source, publishes, or deploys.

For L2/L4, one provider session continues from baseline into the requested
change. The executor freezes the baseline STEP and deterministic editable-source
ZIP before revealing the change request, then retains changed equivalents and
a SHA-256-identified run journal. Failed and partial attempts are retained under
a UUID-backed run directory, with one permanent canonical claim at
`runs/.slot-claims/<planned-run-id>.json` preventing a second allocation only
within the same checkout. Because `runs/` is ignored and checkout-local, the
claim is not a global lock. Cross-clone and cross-host uniqueness remains the
responsibility of operator/campaign-ledger coordination and later
registry/publication validation. The executor does not grade, edit
`results/marb_runs.json`, update board data, rebuild the site, publish, or
deploy. L4's planned public-invariant and requested-change reports are
**grader-owned deferred outputs**; an executor result is
`completed_ungraded`, never publication evidence by itself.

The run-log `output_contract` uses schema `marb_plan_output_binding.v2`.
Its `executor_owned_outputs` map lists executor-reserved/bound output paths. It
does not assert that those files were produced: only `artifacts` entries attest
successfully captured outputs, and a failed or partial run can retain owned
paths without nonexistent artifacts.

Each deterministic editable-source ZIP retains every safe regular file in the
authored workspace regardless of a same-named file in the separate staged input
tree. Canonical STEP output is excluded; reserved source-manifest,
execution-status, and `.marb_*` identities fail closed. Capture binds initial
and final full-file inventories to device/inode/size/mtime plus SHA-256 and
aborts before target creation if a file is added, removed, replaced, or mutated.

Only text-input cells are currently qualified. The run journal records a
limited, negative `execution_modality` attestation: provider input is text, no
native image-view tool is exposed, staged image bytes may be inspected through
model-authored Python, and `vision_attested` is `false`. Do not label or compare
these runs as sighted or vision cells. The container implementation is currently
restricted to Windows Docker Desktop host semantics; Linux and rootless-host
bind ownership behavior has not been qualified. No image/host pair is qualified
until a real, no-provider/no-network manual smoke succeeds after an operator has
built and approved an image digest. CI uses injected fakes and makes no Docker,
provider, or model calls.

Plans truthfully report `blocked-before-execution` because planning never
authorizes a call. Task-specific blockers remain enforceable: L2/L4 cannot be
executed as benchmark attempts until their frozen run-evidence/key gates are
ready, and L4 cannot be graded until its immutable gated grading revision is
available. See [`H2B_EXECUTOR.md`](H2B_EXECUTOR.md) for the operator protocol
and [`container/README.md`](container/README.md) for the reproducible image
inputs and manual attestation gate.

## Legacy local-anchor builder (not H2b)

The commands below drive `marb_local_harness.py`. They predate the H2b
authorization, continuity, provenance, and container contract and must not be
used to claim an H2b or post-policy benchmark attempt.

```powershell
# Stage the blind kit once into a neutral folder. The kit has no CLAUDE.md and no answer key:
Expand-Archive ..\kits\m3_cadquery_blind_kit.zip -DestinationPath runs\local_cadquery_01 -Force

# Point the harness at your local Ollama endpoint (default: http://localhost:11434/v1):
$env:MARB_LOCAL_ENDPOINT = "http://<your-local-host>:11434/v1"   # or pass --base-url
python marb_local_harness.py
```

Outputs land in the run folder (`runs/local_cadquery_01/`):

- `export.step` — the single STEP assembly, graded later.
- `run_log.yaml` — driver, tool, `kit_version: v1.1`, timing, tokens, and attempts.

## Model selection

The local-anchor track runs on a local AI supercomputer. It has enough unified memory to
serve the models below on a single node. The largest open-weight models need a bridged pair
of nodes. We surveyed the current open-weight leaders against that limit.

| Model | Total / active | Q4 fit | Verdict |
|---|---|---|---|
| **`qwen3-coder-next:q4_K_M`** | 80B / 3B | ~52 GB, single node | **Default.** The newest dedicated Qwen coder (released 2026-02-04, built on Qwen3-Next, agentically RL-trained). Its 262K context fits the available memory. Text-only, so it sets an honest floor with no reference-image advantage. |
| `qwen3.6:35b` | 36B / 3B | ~24 GB, single node | Fits one node. A strong generalist, but no dedicated 3.6-Coder exists. |
| DeepSeek-V4-Flash | 284B / 13B | ~142 GB, bridged only | Rejected. It leaves too little KV-cache headroom for a long-context reasoning model. |
| Kimi K2.6 / DeepSeek-V4-Pro / GLM-5.1 | 1T / 1.6T / ~1.5 TB | Does not fit, even bridged | Out of reach on this hardware. |
| `m3dcpm:*` | 8B | — | **Do not use.** Fine-tuned on M3-CRETE. It bakes the answer key into the weights, which violates the blind-run protocol. |

There is no "Qwen3.5". The coder line runs Qwen2.5-Coder, then Qwen3-Coder, then
**Qwen3-Coder-Next**. `qwen3-coder-next` is both the newest coder and the best single-node
fit, so no bridge is needed.

### Vision-capable cells (optional)

A local vision model can power a sighted MARB cell that reads the goal image directly. This
is a separate cell from the text-only blind floor described below.

| Model | Maker / origin | Size (Q4) | Fit | Use |
|---|---|---|---|---|
| **`glm-4.5v`** | Zhipu AI / Z.ai (China) | 106B-A12B, ~62 GB | single node | Top document and chart reasoning plus Chinese OCR. The heaviest extractor. Leads more than 41 multimodal benchmarks. |
| **`qwen3-vl:32b`** | Alibaba Qwen (China) | 32B dense, ~21 GB | single node | Default workhorse. Best-in-class document and diagram parsing plus Chinese OCR. Use `:8b` (~6 GB) for bulk throughput, and the bridge-only `:235b-a22b` for maximum accuracy. |
| `nemotron-3-nano-omni` | NVIDIA (United States) | 30B-A3B, ~18 GB | single node | The US-origin option for the origin-sensitive MARB benchmark cell. Vision, audio, and text, with a C-RADIOv4-H encoder. |

**Serving.** Use Ollama 0.12.7 or newer for `qwen3-vl`. GLM-4.5V (zai-org/GLM-4.5V) may
need a community GGUF plus a Modelfile, because Ollama's official coverage is partial.
Nemotron 3 Nano Omni serves through NVIDIA NIM (OpenAI-compatible) or an HF GGUF. All
expose an OpenAI-compatible API, so `marb_local_harness.py --multimodal` drives any of them
unchanged.

For MARB, the designated vision cell is `nemotron-3-nano-omni`. It is US-origin and
NVIDIA-native, at 30B-A3B with vision, audio, and text. It is also the token-cheap
data-ingestion model. Serve the Q4 GGUF with its multimodal projector on an
OpenAI-compatible port, then drive it. It logs as `local_anchor` when the endpoint host is
listed in `MARB_LOCAL_HOSTS`.

```powershell
python marb_local_harness.py --base-url http://<your-local-host>:8001/v1 `
       --model nemotron-3-nano-omni --multimodal
```

The `--multimodal` path inlines the goal image on turn 1, so the model sees the target
immediately.

Override the default model and the caps:

```powershell
python marb_local_harness.py --model qwen3-coder-next:q4_K_M --max-iters 8 --patience 3
python marb_local_harness.py --model <vision-model> --multimodal   # enables view_image
```

### Optional: Gemini Flash as a separate cloud cell

This is not the local-anchor floor. The harness is OpenAI-compatible, so the same code can
drive a cloud API as a different MARB cell. Gemini Flash is capable and multimodal. With
`--multimodal` it can view the reference PNG images, which is an advantage the text-only
local run deliberately gives up. Run it tagged as a separate `cloud_api` cell in the run
log.

```powershell
$env:GEMINI_API_KEY = "..."
python marb_local_harness.py `
  --base-url https://generativelanguage.googleapis.com/v1beta/openai/ `
  --api-key-env GEMINI_API_KEY --model gemini-flash-latest --multimodal
```

## What the model gets

- **System prompt and brief.** The frozen `CADQUERY_DRIVER_BRIEF.md`, specifically the
  `=== BEGIN ===` to `=== END ===` section, plus the kit file listing and a short
  operational note.
- **Tools.** `write_file`, `run_python` (CadQuery and cadclaw are installed), `read_output`,
  and `view_image` (registered only with `--multimodal`).
- **Fairness.** The run folder is neutral and local. The model has no cross-session memory.
  Tools are sandboxed to the run folder. The model therefore has no path to any answer key
  or project memory.

### How a text-only model verifies (no vision)

A text-only model is the default. It cannot see renders or the reference PNG images.
`view_image` is not registered without `--multimodal`, and a PNG cannot be read back as
text. The harness therefore steers the model to verify numerically through `run_python`. It
probes each part's `BoundingBox()` and center of mass, counts placed instances, checks key
coordinates and gaps, and asserts against the brief's stated dimensions. The build volume is
2000 × 1000 × 1000 mm. This is the print volume, not an outer-size limit, so a correct
machine is larger than the build volume.

This blindness defines the floor. Build quality rests on coordinate reasoning alone, with no
visual feedback. To give the run sight, use `--multimodal` together with a vision-capable
model. That can be a local cell (`qwen3-vl:32b`, `glm-4.5v`, or NVIDIA
`nemotron-3-nano-omni`; see the vision-cells table above) or the Gemini cloud cell.
`qwen3-coder-next` is text-only.

## Loop control

`--max-iters` caps the number of model turns (default 8). The loop also exits early on
no-improvement. Once `export.step` exists, the loop stops if `--patience` consecutive turns
add no STEP progress (default 3). A turn with no tool calls is treated as finished.
`--max-tokens` caps completion tokens per turn. Reasoning models such as Nemotron get a
generous default cap so they do not truncate mid-thought.

## Requirements

Python 3.11 or newer, plus `openai`, `pyyaml`, `cadquery`, and `cadclaw`. Point the harness
at your local Ollama endpoint through `MARB_LOCAL_ENDPOINT` (default
`http://localhost:11434/v1`) or `--base-url`. Set `MARB_LOCAL_HOSTS` if your local AI
supercomputer is remote, so runs still log as the `local_anchor` cell.

## Related files in this folder

- `cohort_runner.py` — deterministic L1/L2/L4 plan generator; never executes a run.
- `cohort_executor.py` — explicit-authorization, one-slot H2b executor.
- `nightwatch.py` — serial, aggregate-approval-bound local campaign controller.
- `NIGHTWATCH.md` — exact campaign schema, CLI, ledger, and recovery contract.
- `isolated_container.py` — digest-pinned, networkless Python sandbox policy.
- `H2B_EXECUTOR.md` — operator authorization, evidence, and non-publication boundary.
- `container/` — offline runtime build inputs and attestation instructions.
- `marb_local_harness.py` — the builder described above.
- `run_batch.py` — runs a cohort of builds for a prompt-variant study.
- `BATCH_FINDINGS.md` — the batch and prompt-variant results.
