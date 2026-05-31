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

### Vision-capable cells (multimodal) + Chinese-literature extraction

A local vision model serves two purposes here: **(a)** a *sighted* MARB cell that reads the
goal image directly (vs the text-only blind floor below), and **(b)** **data extraction from
Chinese-language technical literature** (cement / 3DCP / materials papers) — where a
Chinese-trained VLM's native Chinese OCR + table/chart/document parsing is a real advantage.

| Model | Maker / origin | Size (Q4) | local AI supercomputer fit | Use |
|---|---|---|---|---|
| **`glm-4.5v`** | Zhipu AI / Z.ai (🇨🇳) | 106B-A12B, ~62 GB | single box | Top doc/chart reasoning + Chinese OCR; the heaviest extractor. Leads 41+ multimodal benchmarks. |
| **`qwen3-vl:32b`** | Alibaba Qwen (🇨🇳) | 32B dense, ~21 GB | single box | Default workhorse; best-in-class doc/diagram + Chinese OCR. `:8b` (~6 GB) for bulk throughput, `:235b-a22b` bridge-only for the ceiling. |
| `nemotron-3-nano-omni` | **NVIDIA (🇺🇸)** | 30B-A3B, ~18 GB | single box | **US-origin** option for the origin-sensitive MARB benchmark cell; native to the local AI supercomputer via NIM. vision+audio+text, C-RADIOv4-H encoder. |

**Origin note.** GLM-4.5V and Qwen3-VL are PRC-origin (Zhipu, Alibaba). Run **local/air-gapped**
on `[redacted-host]` the data-exfiltration risk is nil — the only axis is **customer optics** for the
MARB benchmark cell, and for *that* prefer NVIDIA Nemotron. For **Chinese-literature extraction**
the Chinese models are the *right* tool (the origin is the feature). The `m3dcpm:*` answer-key
ban still applies to any MARB blind run regardless of model.

**Serving.** Ollama **≥ 0.12.7** for `qwen3-vl`. GLM-4.5V (zai-org/GLM-4.5V) may need a community
GGUF + a Modelfile (Ollama's official coverage is partial). Nemotron 3 Nano Omni serves via
NVIDIA **NIM** (OpenAI-compatible, native to the local AI supercomputer/local AI supercomputer) or an HF GGUF. All expose an
OpenAI-compatible API, so `marb_local_harness.py --multimodal` drives any of them unchanged.
Pull on [redacted-host]: `ollama pull qwen3-vl:32b` (then `--model qwen3-vl:32b`).

**For MARB the designated vision cell is `nemotron-3-nano-omni`** (US-origin, NVIDIA-native,
30B-A3B, vision+audio+text — also the token-cheap **data-ingestion** model). The local AI supercomputer already
has the *text* Nemotrons (`nemotron-3-super:120b-a12b`, `nemotron-3-nano:4b`) but **not** the
Omni vision build. Get sight on it via llama.cpp (the confirmed vision path):

```
# on [redacted-host] — Q4 GGUF + the multimodal projector, OpenAI-compatible on :8001
llama-server --model NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-...-Q4_K_XL.gguf \
             --mmproj mmproj-BF16.gguf --alias nemotron-3-nano-omni --port 8001
# then, from the workstation (logs as local_anchor — is_local matches the local AI supercomputer host on any port):
python marb_local_harness.py --base-url http://[redacted-ip]:8001/v1 \
       --model nemotron-3-nano-omni --multimodal
```

(or serve it as an NVIDIA **NIM** on the local AI supercomputer — also OpenAI-compatible. The `--multimodal`
path now inlines the goal image on turn 1, so the model sees the target immediately.)

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
model — a local cell (`qwen3-vl:32b`, `glm-4.5v`, or NVIDIA `nemotron-3-nano-omni`; see the
vision-cells table above) or the Gemini cloud cell; `qwen3-coder-next` is text-only.

## Loop control

`--max-iters` caps model turns (default 8). The loop also exits early on
**no-improvement**: once `export.step` exists, if `--patience` (default 3) consecutive
turns add no STEP progress, it stops. A turn with no tool calls is treated as "finished."

## Requirements

Python 3.11+, `openai`, `pyyaml`, `cadquery`, `cadclaw` (all present on the workstation).
The local AI supercomputer must be reachable at `http://[redacted-ip]:11434/v1` (host `[redacted-host]`).
