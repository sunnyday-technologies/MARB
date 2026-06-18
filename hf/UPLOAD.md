# Publishing MARB to Hugging Face — runbook

Three HF repos, mirrored from this GitHub repo (which stays the source of truth):

| HF repo | Type | Visibility | Contents |
|---|---|---|---|
| `SunnydayTech/marb-m3-crete` | dataset | public | blind kits, prompts, scoring spec, benchmark.yaml |
| `SunnydayTech/marb-m3-crete-answer-key` | dataset | public but **gated** | reference STEP + placement spec |
| `SunnydayTech/marb-leaderboard` | space (gradio) | public | the board (`hf/space/`) |

> Replace `SunnydayTech` everywhere if your HF org or username differs.
> Run all commands from the repo root: `D:\SunnydayTech\MARB`.
> Large binaries (the 1.2 MB kit zips, the 37 MB reference STEP) upload over the
> LFS protocol automatically when you use the `hf` CLI or `HfApi`. No manual
> `git lfs track` is needed.

## 0. Prereqs (once)

You do NOT need the `/mcp` connector for this. `/mcp` is a Claude Code chat
command, not a Windows command. Uploading is done locally with a write token.

**0a. Get a write token (browser, already logged in as SunnydayTech):**
1. Open https://huggingface.co/settings/tokens
2. Click **Create new token** → Token type **Write** → name it `marb-upload` → **Create**.
3. Copy the token (starts with `hf_...`).

**0b. Install the CLI and log in (PowerShell):**

```powershell
python -m pip install -U huggingface_hub
hf auth login --token hf_PASTE_YOUR_TOKEN_HERE
hf auth whoami     # should print: SunnydayTech
```

If `hf` is not recognized: close and reopen PowerShell (PATH refresh) and retry.
Still not found? Use `huggingface-cli` in place of `hf` (older alias, same
subcommands except login is `huggingface-cli login`), or run the Python script at
the end of this file (Alternative: HfApi) instead.

**0c. Move into the repo (every command below runs from here):**

```powershell
Set-Location D:\SunnydayTech\MARB
```

## 1. Create the repos

```powershell
hf repo create SunnydayTech/marb-m3-crete            --repo-type dataset
hf repo create SunnydayTech/marb-m3-crete-answer-key --repo-type dataset
hf repo create SunnydayTech/marb-leaderboard         --repo-type space --space_sdk gradio
```

## 2. Public benchmark-input dataset

```powershell
hf upload SunnydayTech/marb-m3-crete hf/dataset-public/README.md README.md        --repo-type dataset
hf upload SunnydayTech/marb-m3-crete kits         kits         --repo-type dataset
hf upload SunnydayTech/marb-m3-crete prompts      prompts      --repo-type dataset
hf upload SunnydayTech/marb-m3-crete spec         spec         --repo-type dataset
hf upload SunnydayTech/marb-m3-crete benchmark.yaml benchmark.yaml --repo-type dataset
```

## 3. Gated answer-key dataset

Upload only the answer key (reference STEP + placement spec):

```powershell
hf upload SunnydayTech/marb-m3-crete-answer-key hf/dataset-gated/README.md README.md --repo-type dataset
hf upload SunnydayTech/marb-m3-crete-answer-key tasks/m3_crete/m3_reference_round1.step   m3_reference_round1.step   --repo-type dataset
hf upload SunnydayTech/marb-m3-crete-answer-key tasks/m3_crete/m3_reference_assembly.yaml m3_reference_assembly.yaml --repo-type dataset
```

**Turn the gate on.** The `extra_gated_*` block in the card enables gating on push.
Confirm it, and pick manual approval, here:
`https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key/settings`
→ Access requests → **Manual** (you approve each request) or Automatic (click-through only).
Manual is the stronger choice for an answer key.

## 4. Leaderboard Space

```powershell
hf upload SunnydayTech/marb-leaderboard hf/space . --repo-type space
```

The Space builds from `app.py` + `requirements.txt`. Optional: drop a
`marb_scoreboard.png` into `hf/space/` (copy from `results/figures/`) and re-upload
to show the scoreboard figure above the table.

## 5. Verify

- Public dataset card renders, kits download without login.
- Answer-key dataset shows the request-access gate to a logged-out user, and the
  files are not reachable without approval.
- Space builds green and the board sorts by GAP.
- Cross-links resolve (each card links the other two + GitHub + marb.cadclaw.io).

## Alternative: one Python script (HfApi)

Version-independent; same effect as the CLI calls above.

```python
from huggingface_hub import HfApi
api = HfApi()
ORG = "SunnydayTech"

api.create_repo(f"{ORG}/marb-m3-crete", repo_type="dataset", exist_ok=True)
api.create_repo(f"{ORG}/marb-m3-crete-answer-key", repo_type="dataset", exist_ok=True)
api.create_repo(f"{ORG}/marb-leaderboard", repo_type="space", space_sdk="gradio", exist_ok=True)

# Public dataset
api.upload_file(path_or_fileobj="hf/dataset-public/README.md", path_in_repo="README.md",
                repo_id=f"{ORG}/marb-m3-crete", repo_type="dataset")
for folder in ("kits", "prompts", "spec"):
    api.upload_folder(folder_path=folder, path_in_repo=folder,
                      repo_id=f"{ORG}/marb-m3-crete", repo_type="dataset")
api.upload_file(path_or_fileobj="benchmark.yaml", path_in_repo="benchmark.yaml",
                repo_id=f"{ORG}/marb-m3-crete", repo_type="dataset")

# Gated answer key
api.upload_file(path_or_fileobj="hf/dataset-gated/README.md", path_in_repo="README.md",
                repo_id=f"{ORG}/marb-m3-crete-answer-key", repo_type="dataset")
api.upload_file(path_or_fileobj="tasks/m3_crete/m3_reference_round1.step",
                path_in_repo="m3_reference_round1.step",
                repo_id=f"{ORG}/marb-m3-crete-answer-key", repo_type="dataset")
api.upload_file(path_or_fileobj="tasks/m3_crete/m3_reference_assembly.yaml",
                path_in_repo="m3_reference_assembly.yaml",
                repo_id=f"{ORG}/marb-m3-crete-answer-key", repo_type="dataset")

# Space
api.upload_folder(folder_path="hf/space", path_in_repo=".",
                  repo_id=f"{ORG}/marb-leaderboard", repo_type="space")
```
