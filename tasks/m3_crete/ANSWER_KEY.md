# M3-CRETE answer key (gated)

The MARB answer key for task 1 is **not** in this repository. It is distributed
through a gated Hugging Face dataset so it stays out of the open file tree that
crawlers and training pipelines ingest.

Two files make up the answer key:

- `m3_reference_round1.step` — the reference assembled STEP (the intended machine).
- `m3_reference_assembly.yaml` — the placement spec (per-part pose, gaps, labels).

## Get it

1. Request access: https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key
2. Download both files and place them in this directory (`tasks/m3_crete/`).

The grader defaults point here, so once the files are in place the commands in
the top-level README work unchanged. The two files are gitignored, so they will
not be re-committed.

The benchmark input you hand the model (kits, brief, scoring spec) is open and
needs no gate: https://huggingface.co/datasets/SunnydayTech/marb-m3-crete
