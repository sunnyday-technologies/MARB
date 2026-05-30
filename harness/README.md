# MARB local/anchor-track builder harness

Drives a **local open-weight model** (served by Ollama on the **local AI supercomputer**, OpenAI-compatible
API) to build the M3-CRETE assembly in **CadQuery**, working only from the staged blind
kit + frozen brief. This is the **builder** for the local/anchor point on the
"ARB score vs model capability" curve. **It does not grade** — grading is a separate
session (`grade_native_step.py` → `score_report.py` in the CADCLAW repo).

## One-command run

```powershell
# Stage the blind kit once (neutral folder, no CLAUDE.md, no answer key):
Expand-Archive ..\kits\m3_cadquery_blind_kit.zip -DestinationPath [local-path-redacted] -Force

python marb_local_harness.py
```

Outputs land in `[local-path-redacted]
- `export.step` — the single STEP assembly (graded later)
- `run_log.yaml` — driver / tool / `kit_version: v1.1` / timing / tokens / attempts

## Model selection (local AI supercomputer, queried 2026-05-30)

The local AI supercomputer / local AI supercomputer has **128 GB unified memory** (practical inference to ~150 B params
Q4); two units bridge over **ConnectX-7** (200 Gbps direct peer, no switch) into a
**256 GB** pool (~405 B). We surveyed the current open-weight leaders against that ceiling:

| Model | Total/active | Q4 fit | Verdict |
|---|---|---|---|
| **`qwen3-coder-next:q4_K_M`** | 80B / 3B | ~52 GB → **single box** | **Default.** Newest dedicated Qwen coder (released 2026-02-04, built on Qwen3-Next, agentically RL-trained). 262K context fits in the 76 GB headroom. Text-only → the genuine floor (no reference-image advantage). |
| `qwen3.6:35b` | 36B / 3B | ~24 GB → single box | On the box; strong **generalist**, but no dedicated 3.6-Coder exists. |
| DeepSeek-V4-Flash | 284B / 13B | ~142 GB → bridged only | Rejected: 142 GB of a 256 GB pool leaves too little KV-cache headroom for a long-context reasoning model. |
| Kimi K2.6 / DeepSeek-V4-Pro / GLM-5.1 | 1T / 1.6T / ~1.5 TB | ✗ even bridged | Out of reach on this hardware. |
| `m3dcpm:*` | 8B | — | **DO NOT USE.** Fine-tuned on M3-CRETE — bakes the answer key into the weights, a memory-leak violation of the blind-run protocol (MARB_ROADMAP.md §2.1). |

There is **no "Qwen3.5"**; the coder line is Qwen2.5-Coder → Qwen3-Coder → **Qwen3-Coder-Next**.
`qwen3-coder-next` is both the newest coder and the best single-box fit, so **no bridge is needed**.

Override the default and caps:

```powershell
python marb_local_harness.py --model qwen3-coder-next:q4_K_M --max-iters 8 --patience 3
python marb_local_harness.py --model <vision-model> --multimodal   # enables view_image
```

### Optional: Gemini Flash as a SEPARATE cloud cell (not the local anchor)

The harness is OpenAI-compatible, so the same code can drive a cloud API as a **different
MARB cell** (cloud/API, not the local/anchor point this track exists to plant). Gemini Flash
is capable and multimodal — with `--multimodal` it can actually *view* the reference PNGs,
which is an advantage the local text-only run deliberately forgoes. Run it tagged as a
separate `cloud_api` cell in the run log:

```powershell
$env:GEMINI_API_KEY = "..."
python marb_local_harness.py `
  --base-url https://generativelanguage.googleapis.com/v1beta/openai/ `
  --api-key-env GEMINI_API_KEY --model gemini-flash-latest --multimodal
```

## What the model gets

- **System + brief**: the frozen `CADQUERY_DRIVER_BRIEF.md` (the `=== BEGIN/END ===`
  section), plus the kit file listing and a short operational note.
- **Tools**: `write_file`, `run_python` (CadQuery 2.7.0 + cadclaw 0.8.0 installed),
  `read_output`, and `view_image` (only with `--multimodal`).
- **Fairness wall holds by construction**: the run folder is neutral and local, the
  model has no cross-session memory, and tools are sandboxed to the run folder — there
  is no path to any reference solution or project memory.

### How a text-only model verifies (no vision)

A text-only model (the default) **cannot see renders or the reference PNGs** — `view_image`
is not registered without `--multimodal`, and a PNG can't be read back as text. So the
harness steers it to verify **numerically** via `run_python`: probe each part's
`BoundingBox()` / center of mass, count placed instances, check key coordinates and gaps,
and `assert` against the 2000×1000×1000 mm envelope and the brief's dimensions. That
blindness is the genuine floor — build quality rides on coordinate reasoning alone, with
no visual feedback. To give the run sight, use `--multimodal` **and** a vision-capable
model (e.g. the Gemini cloud cell above); `qwen3-coder-next` is text-only.

## Loop control

`--max-iters` caps model turns (default 8). The loop also exits early on
**no-improvement**: once `export.step` exists, if `--patience` (default 3) consecutive
turns add no STEP progress, it stops. A turn with no tool calls is treated as "finished."

## Requirements

Python 3.11+, `openai`, `pyyaml`, `cadquery`, `cadclaw` (all present on the workstation).
The local AI supercomputer must be reachable at `http://[redacted-ip]:11434/v1` (host `[redacted-host]`).
