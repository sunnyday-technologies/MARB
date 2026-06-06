# MARB local-anchor track builder harness

This harness drives a local open-weight model to build the M3-CRETE gantry frame (task 1)
in CadQuery. The model is served by Ollama through an OpenAI-compatible API. It works only
from the staged blind kit and the frozen task brief. This is the builder for the
local-anchor floor on the MARB capability curve.

The harness builds. It does not grade. Grading is a separate step that runs the MARB
grader on the exported STEP file: `grader/marb_grade_all.py` for the GAP, ORIENT, and POS
positional metrics, and `grader/grade_native_step.py` for the native gates (inventory,
interference, floating).

## One-command run

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

- `marb_local_harness.py` — the builder described above.
- `run_batch.py` — runs a cohort of builds for a prompt-variant study.
- `BATCH_FINDINGS.md` — the batch and prompt-variant results.
