# M3-CRETE answer key (gated)

The MARB answer key for task 1 is **not** in this repository. It is distributed
through a gated Hugging Face dataset so it stays out of the open file tree that
crawlers and training pipelines ingest.

The gate prevents inadvertent training-data contamination. It is not a secrecy or
security measure: the files are openly licensed and access is granted for any
legitimate use.

Two files make up the answer key:

- `m3_reference_round1.step` — the reference assembled STEP (the intended machine).
- `m3_reference_assembly.yaml` — the placement spec (per-part pose, gaps, labels).

## Get it

1. Request access: https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key
2. Download both files and place them in this directory (`tasks/m3_crete/`).

The grader defaults point here, so once the files are in place the commands in
the top-level README work unchanged. The two files are gitignored, so they will
not be re-committed — and a pre-push hook plus the `key-guard` CI workflow
reject any commit that tries.

## Integrity (sha256)

Verify your downloaded key matches the canonical revision the published
grades were produced against:

```
b64d77a24e1339a4842d117fa877be84d97724f55a7636a0d9ba9e4f443dae57  m3_reference_round1.step    (38,397,070 bytes)
18bca4395c63a217896a8043ab9fe1f8f72c016368b35938dcac16f27d5dfea7  m3_reference_assembly.yaml  (76,505 bytes)
```

`sha256sum <file>` (or `Get-FileHash <file>` in PowerShell) must reproduce
these digests. If a key file is ever revised, a new revision is pushed to the
gated dataset and the hashes here are updated in the same commit — so this
file always pins exactly which key the graders ran against.

The benchmark input you hand the model (kits, brief, scoring spec) is open and
needs no gate: https://huggingface.co/datasets/SunnydayTech/marb-m3-crete
