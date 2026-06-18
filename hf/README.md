# hf/ — Hugging Face publishing staging

Mirror of MARB onto Hugging Face. GitHub stays the source of truth; these files
define the HF surfaces and how to push them.

- `dataset-public/README.md` — card for the public benchmark-input dataset
  (`SunnydayTech/marb-m3-crete`): kits, prompts, scoring spec.
- `dataset-gated/README.md` — card for the gated answer-key dataset
  (`SunnydayTech/marb-m3-crete-answer-key`): reference STEP + placement spec,
  behind request-access. Gating is configured by the card's `extra_gated_*` block.
- `space/` — the Gradio leaderboard Space (`SunnydayTech/marb-leaderboard`):
  `app.py`, `requirements.txt`, `README.md`, and `board.json` (published board
  snapshot, mirrors the MARB README table).
- `UPLOAD.md` — the step-by-step push runbook (`hf` CLI and an HfApi script).

When the board or kits change, update the matching file here and re-run the
relevant `hf upload` from `UPLOAD.md`.
