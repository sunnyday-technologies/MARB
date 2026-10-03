# Hugging Face-first local model profiles

MARB's automated model path is Hugging Face-first, local, credential-free, and
revision-bound. `local_model_adapter.py` inventories an existing Hugging Face
cache and creates reviewable model profiles. It does **not** download model
weights, start a server, make a completion request, authorize a benchmark,
grade evidence, publish a row, or incur provider spend.

Status as of 2026-08-29: no MARB-suitable language model has been downloaded
or qualified in the currently inspected Windows Hugging Face cache, no DGX
Spark model server has been qualified through this contract, and no benchmark
attempt has been run with it. The profile and same-host attestation software is
still being integrated and operationally qualified. A sealed profile is an
identity record, not proof that a server exists or that a run is ready.

## Supported v1 envelope

A canonical `marb_hf_local_model_profile.v1` envelope binds all of these
identities:

- a Hugging Face `owner/repository` and full lowercase 40-character commit;
- a tokenizer commit, which may equal the model commit but is still explicit;
- the exact digest of the structured launch configuration;
- offline loading with `trust_remote_code: false`;
- context, concurrency, GPU-memory, dtype, quantization, parser, chat-template,
  and API-port settings;
- the exact vLLM version and immutable OCI `RepoDigest`;
- one single-node Docker API container on hardware class
  `nvidia-dgx-spark-gb10`; and
- an exact credential-free `http://127.0.0.1:<port>/v1` or
  `http://[::1]:<port>/v1` endpoint.

Profile v1 deliberately supports one DGX Spark node with tensor and pipeline
parallel sizes of one. A two-node profile is not accepted. Qualify a single
Spark first; a bridged 2x-GX10 topology needs a new independently attested
schema before it can produce MARB evidence.

The served model name is derived from the repository-name SHA-256, model
commit, and launch-configuration SHA-256. It is not a mutable Hugging Face
branch, tag, friendly alias, or server default. H2a, H2b, the provider
transport, the retained run log, and Nightwatch bind that same identity and
profile digest.

## Read-only cache inventory

Inventory an already populated cache without following weight blobs or using
the network:

```powershell
python harness/local_model_adapter.py inventory-cache `
  --cache-root "<existing-hugging-face-cache>"
```

The canonical result uses `marb_hf_cache_inventory.v1`, reports repository,
snapshot, and ref identities, and states `network_accessed: false`. It does not
prove that every required model file is complete, safe, licensed for the
intended use, or runnable. Downloading or updating weights is a separate,
explicit operator action and is outside this command.

## Create and seal a profile without a model call

Use full commit identities obtained and reviewed separately. The output paths
must be new files in existing real directories; the commands do not overwrite
files.

```powershell
python harness/local_model_adapter.py profile-template `
  --repository-id "<owner/repository>" `
  --revision "<40-lowercase-hex-model-commit>" `
  --tokenizer-revision "<40-lowercase-hex-tokenizer-commit>" `
  --endpoint "http://127.0.0.1:<port>/v1" `
  --engine vllm `
  --engine-version "<exact-safe-version-token>" `
  --container-image "<registry/repository>@sha256:<64-lowercase-hex>" `
  --host-label "<reviewed-host-label>" `
  --container-name "marb-model-<reviewed-name>" `
  --max-model-len <reviewed-context-limit> `
  --max-num-seqs 1 `
  --gpu-memory-utilization "0.80" `
  --dtype "<reviewed-dtype>" `
  --container-port <port> `
  --output <new-profile-payload.json>
```

Optional `--quantization`, `--tool-call-parser`, `--reasoning-parser`, and
`--chat-template-sha256` values are part of the launch digest. Omitting them is
also a bound choice. The endpoint port must equal the container API port.

Review the canonical payload and its reported served-model ID out of band.
Then seal the unchanged payload and independently retain the returned profile
digest:

```powershell
python harness/local_model_adapter.py seal-profile `
  --payload <profile-payload.json> `
  --output <new-sealed-profile.json>

python harness/local_model_adapter.py validate-profile `
  --profile <sealed-profile.json> `
  --expected-profile-sha256 <independently-retained-profile-sha256>
```

These operations are profile preparation only. They perform no Docker action,
endpoint probe, model call, benchmark allocation, or approval.

## Runtime identity gate

`attest_runtime(...)` is the same-host verification primitive. Given a sealed
profile and a narrow Docker observer, it requires the running container name,
container ID, immutable image RepoDigest and image ID, exact derived vLLM
command, engine/version, hardware class, repository/revision, launch digest,
read-only model mount, and loopback port binding to agree. It accepts only
environment-variable **names**, rejects credential-like names, and never asks
the observer for values.

It then performs only a bounded `GET /v1/models` against the exact loopback
endpoint and requires the revision/config-derived served-model identity. The
result is `marb_local_model_runtime_attestation.v1` with
`model_call_performed: false`; it is not a completion, benchmark result, or
quality claim.

The adapter does not yet provide a generally qualified launcher or remote
attestation path. Until H2b's trusted Docker observer and re-attestation checks
are integrated, fake-tested, and passed on the intended host, a profile must
remain blocked before execution. A remote hostname, SSH hop, port-forward
claim, Docker label alone, or operator declaration is not a substitute for
the same-host observation.

## Planning and campaign binding

After profile review, use its derived served-model ID as H2a `--model-id` and
its digest as `--model-profile-sha256`. The canonical plan schema is
`marb_cohort_plan.v2`. Every H2b `marb_execution_authorization.v3` additionally
binds the repository-relative profile path, schema, and digest. Nightwatch
`marb_nightwatch_campaign.v2` repeats those exact path/digest bindings for each
ordered slot and binds the adapter source identity itself.

Profile reuse is allowed only when the path and digest agree. One plan digest
cannot be paired with different profile digests, and profile, plan, and
authorization paths may not alias. See [`H2B_EXECUTOR.md`](H2B_EXECUTOR.md)
and [`NIGHTWATCH.md`](NIGHTWATCH.md) for the remaining authorization chain.

## Candidate and repeat policy

Hugging Face is the primary discovery and revision source. A candidate still
needs a license review, full commit pin, compatible immutable vLLM image,
hardware-fit check, parser/template review, and clean runtime attestation.
Marketing names, `latest`, mutable branches, community quantizations, and a
successful manual chat are not sufficient provenance.

Start with a bounded single-node smoke after runtime qualification. Repeated
runs are useful for measuring variability only after each logical slot has a
distinct H2a seed/run ID, per-slot H2b authorization, and an aggregate
Nightwatch campaign approval. Nightwatch is serial and stops at the approved
failure threshold. It never grades or publishes the resulting attempts.

External Grok, GPT, Claude, Gemini, or other metered/credentialed providers are
never a fallback for this local automation. The current profile and Nightwatch
schemas reject them. Any future external run must be a separate, explicitly
approved, one-slot protocol with a provider-specific pre-call token/price cap;
it must not be placed in an overnight local campaign merely because an API key
is available.
