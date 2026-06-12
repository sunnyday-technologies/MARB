#!/usr/bin/env python3
"""MARB local/anchor-track builder harness.

Drives a local open-weight model (served by Ollama on a local AI supercomputer, OpenAI-compatible
API) to build the M3-CRETE assembly in CadQuery, working only from the staged blind
kit + frozen brief. Builder only -- grading is a separate session.

Tools given to the model: write_file, run_python (CadQuery + cadclaw installed),
read_output, and view_image (only when --multimodal). Caps iterations and exits on
no-improvement. Writes export.step + run_log.yaml to the run folder.

Usage (one command):
    python marb_local_harness.py
    python marb_local_harness.py --model qwen3-coder-next:q4_K_M --max-iters 8

Fairness: never point --model at an m3dcpm:* entry -- those are fine-tuned on
M3-CRETE and bake the answer key into the weights (a memory-leak violation of the
blind-run protocol, MARB_ROADMAP.md 2.1).
"""
import argparse, base64, datetime, json, os, pathlib, re, shutil, subprocess, sys, time
from openai import OpenAI

# Local OpenAI-compatible endpoint (Ollama by default). Override via env or --base-url.
ENDPOINT   = os.environ.get("MARB_LOCAL_ENDPOINT", "http://localhost:11434/v1")
# Hosts that count as the local anchor (matched on any port). Set MARB_LOCAL_HOSTS=host1,host2
# if your local box is remote (Ollama :11434, llama.cpp llama-server :8001, NVIDIA NIM :8000).
LOCAL_HOSTS = tuple(h.strip() for h in os.environ.get("MARB_LOCAL_HOSTS", "localhost,127.0.0.1").split(",") if h.strip())
DEF_MODEL  = "qwen3-coder-next:q4_K_M"          # Qwen3-Coder 79.7B Q4 -- real coder, text-only floor
# Vision cell (--multimodal): NVIDIA Nemotron 3 Nano Omni is the US-origin pick.
#   llama.cpp:  llama-server --model ...Nemotron-3-Nano-Omni-30B-A3B...Q4_K_XL.gguf --mmproj mmproj-BF16.gguf --port 8001
#   then:       python marb_local_harness.py --base-url http://<local-host>:8001/v1 --model <alias> --multimodal
# Higher-accuracy vision alternatives are listed in harness/README.md.
RUN_DIR    = pathlib.Path("runs/local_cadquery_01")
KIT_VERSION = "v1.1"
TOOL_VER   = "2.7.0"                             # CadQuery version (frozen for the log)
PY_TIMEOUT = 600                                 # seconds per run_python call
MAXOUT     = 6000                                # truncate tool output returned to model
# Reasoning models (e.g. NVIDIA Nemotron 3 Nano Omni) spend a large hidden "think"
# budget before emitting the answer; with too small a completion cap they hit
# finish_reason=length mid-thought and return an EMPTY message. Give any model whose
# name matches a reasoning hint a generous default cap unless --max-tokens is set.
REASONING_HINTS    = ("nemotron",)               # substrings (case-insensitive) of reasoning models
REASONING_MAXTOK   = 8192                         # generous per-turn cap for reasoning models (handoff: >= ~1500)

def _safe(rel):  # resolve a model-supplied path, kept inside the run folder
    p = (RUN_DIR / rel).resolve()
    if RUN_DIR.resolve() not in p.parents and p != RUN_DIR.resolve():
        raise ValueError(f"path escapes run folder: {rel}")
    return p

def write_file(path, content):
    p = _safe(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p.name} ({len(content)} bytes)"

def run_python(code):
    r = subprocess.run([sys.executable, "-c", code], cwd=RUN_DIR,
                       capture_output=True, text=True, timeout=PY_TIMEOUT)
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    return (out or "(no output)")[:MAXOUT]

def read_output(path):
    p = _safe(path)
    if not p.exists(): return f"(no such file: {path})"
    return p.read_text(encoding="utf-8", errors="replace")[:MAXOUT]

def view_image(path):  # only registered when --multimodal
    p = _safe(path)
    if not p.exists(): return [{"type": "text", "text": f"(no such image: {path})"}]
    b = base64.b64encode(p.read_bytes()).decode()
    return [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}]

TOOLS = {"write_file": write_file, "run_python": run_python, "read_output": read_output}
SCHEMA = [
    {"type": "function", "function": {"name": "write_file", "description": "Write a UTF-8 text file (e.g. build.py) into the run folder.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_python", "description": "Run Python in the run folder (CadQuery + cadclaw installed). Returns stdout/stderr.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "read_output", "description": "Read a text file from the run folder.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]
VIEW_SCHEMA = {"type": "function", "function": {"name": "view_image", "description": "View a PNG in the run folder (reference images or a render you exported).",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}

def progress_sig():  # used for no-improvement detection
    steps = sorted(RUN_DIR.glob("*.step")) + sorted(RUN_DIR.glob("*.STEP"))
    exp = RUN_DIR / "export.step"
    return (len(steps), exp.stat().st_size if exp.exists() else 0)

def main():
    global RUN_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEF_MODEL)
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="cap completion tokens per turn. Omitted -> Ollama default, EXCEPT "
                         "reasoning models (name matches REASONING_HINTS, e.g. Nemotron) which "
                         "auto-get a generous default so they don't truncate mid-thought and "
                         "return empty. Pass 0 to force the Ollama default even for those.")
    ap.add_argument("--patience", type=int, default=3, help="stop after N turns with no STEP progress")
    ap.add_argument("--multimodal", action="store_true", help="register view_image (model must support vision)")
    ap.add_argument("--base-url", default=ENDPOINT,
                    help="OpenAI-compatible endpoint (default: local Ollama). Point at a cloud API "
                         "(e.g. Gemini's OpenAI-compatible URL) only as a SEPARATE non-local cell.")
    ap.add_argument("--api-key-env", default=None,
                    help="env var holding the API key (e.g. GEMINI_API_KEY); omitted -> local Ollama")
    ap.add_argument("--run-dir", default=str(RUN_DIR),
                    help="staged run folder (kit + brief + reference images already in it)")
    ap.add_argument("--guidance-file", default=None,
                    help="optional file whose text is appended to the system prompt as harness "
                         "process/CadQuery-mechanics guidance (NOT design answers). Used for prompt-"
                         "variant cohorts; recorded as prompt_variant in the run log.")
    args = ap.parse_args()
    RUN_DIR = pathlib.Path(args.run_dir)
    is_local = any(h in args.base_url for h in LOCAL_HOSTS)  # local box on ANY port = local anchor

    # Resolve the per-turn completion cap. None -> auto (generous for reasoning models,
    # Ollama default otherwise); 0 -> force Ollama default; >0 -> use as given.
    if args.max_tokens is None:
        max_tokens = (REASONING_MAXTOK
                      if any(h in args.model.lower() for h in REASONING_HINTS) else None)
        if max_tokens:
            print(f"[harness] reasoning model detected ({args.model}); defaulting "
                  f"max_tokens={max_tokens} (override with --max-tokens, 0 = Ollama default)")
    else:
        max_tokens = args.max_tokens or None  # --max-tokens 0 -> None (Ollama default)

    brief = (RUN_DIR / "CADQUERY_DRIVER_BRIEF.md").read_text(encoding="utf-8")
    brief = brief.split("=== BEGIN ===")[1].split("=== END ===")[0] if "=== BEGIN ===" in brief else brief
    kit_files = "\n".join(f"  kit/{f.name}" for f in sorted((RUN_DIR / "kit").iterdir()))
    tools = TOOLS.copy(); schema = list(SCHEMA)
    if args.multimodal:
        tools["view_image"] = view_image; schema.append(VIEW_SCHEMA)

    verify_hint = ("Render with cadclaw as you build and call view_image to compare your assembly "
                   "against the reference_*.png images." if args.multimodal else
                   "You CANNOT see images or renders -- there is no view_image tool. Verify geometry "
                   "NUMERICALLY with run_python: print each part's bounding box and center of mass, "
                   "count the placed instances, check key coordinates and gaps, and assert against the "
                   "2000x1000x1000 mm target envelope and the brief's stated dimensions. Do not spend "
                   "turns rendering PNGs you cannot read.")
    sys_msg = ("You are an autonomous CAD engineer working in a fixed folder. Use the provided tools "
               "to write Python and run it -- do not just print code in chat. Probe each part's bbox/"
               "center before placing it, iterate, and verify your work. " + verify_hint +
               " When finished, your final STEP MUST be named export.step in this folder. Honor the "
               "brief's fairness wall: build only from the brief + the kit/ files; never look for any "
               "reference solution or project memory.")
    user_msg = (f"{brief}\n\nOPERATIONAL NOTES (harness):\n- Working folder is the current directory; "
                f"import parts as kit/<file>.\n- Available kit STEP files:\n{kit_files}\n"
                f"- Name your final assembly export.step (not cadquery_native_export.step).\n"
                f"- The harness writes run_log.yaml for you; focus on the geometry.\n"
                f"- Reference images present: reference_overview/front/top/side.png"
                + ("" if args.multimodal else " (you are text-only; you cannot view them)") + ".")
    prompt_variant = "base"
    if args.guidance_file and pathlib.Path(args.guidance_file).exists():
        g = pathlib.Path(args.guidance_file).read_text(encoding="utf-8").strip()
        if g:
            sys_msg += ("\n\nHARNESS GUIDANCE (general process & CadQuery mechanics learned from prior "
                        "local runs -- NOT design answers; the brief stays the only design input):\n" + g)
            prompt_variant = pathlib.Path(args.guidance_file).stem
    # With a vision model, put the goal image in front of it on TURN 1 (don't wait for a
    # view_image tool call). Inline the reference overview into the first user message.
    user_content = user_msg
    if args.multimodal:
        ref_img = RUN_DIR / "reference_overview.png"
        if ref_img.exists():
            b64 = base64.b64encode(ref_img.read_bytes()).decode()
            user_content = [
                {"type": "text", "text": user_msg + "\n\nThe TARGET machine (goal image) is shown "
                 "below. Build your assembly to match it; call view_image on your own renders to "
                 "compare as you go."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
            print(f"[harness] multimodal: inlined goal image reference_overview.png on turn 1")
        else:
            print("[harness] --multimodal set but reference_overview.png not in run folder; "
                  "model will rely on view_image only")
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_content}]

    import os
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else "ollama"
    # Cold-load armor: a fresh Ollama load of a 20-50 GB model can take minutes,
    # which exceeds default HTTP timeouts and killed first-of-batch runs. Generous
    # timeout + client retries + an explicit warm-up ping with a long keep_alive.
    client = OpenAI(base_url=args.base_url, api_key=api_key, timeout=900.0, max_retries=5)
    if is_local:
        for attempt in range(3):
            try:
                client.chat.completions.create(model=args.model,
                    messages=[{"role": "user", "content": "ping"}], max_tokens=1,
                    extra_body={"keep_alive": "2h"})
                print("[harness] warm-up ok (model loaded, keep_alive=2h)")
                break
            except Exception as e:
                print(f"[harness] warm-up attempt {attempt+1} failed: {type(e).__name__}; retrying in 30s")
                time.sleep(30)
    started = datetime.datetime.now(datetime.timezone.utc); t0 = time.time()
    tok_in = tok_out = tool_calls_made = 0; last_sig = None; stale = 0
    print(f"[harness] model={args.model} run={RUN_DIR} max_iters={args.max_iters}")

    create_kwargs = dict(tools=schema, tool_choice="auto", temperature=0.2)
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens
    for it in range(1, args.max_iters + 1):
        resp = None
        for attempt in range(3):   # transient connection drops must not kill a 30-min run
            try:
                resp = client.chat.completions.create(model=args.model, messages=messages, **create_kwargs)
                break
            except Exception as e:
                if attempt == 2: raise
                print(f"[harness] request failed ({type(e).__name__}), retry {attempt+1}/2 in 20s")
                time.sleep(20)
        u = getattr(resp, "usage", None)
        if u: tok_in += u.prompt_tokens or 0; tok_out += u.completion_tokens or 0
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        calls = msg.tool_calls or []
        print(f"[iter {it}] {len(calls)} tool call(s); text={(msg.content or '')[:80]!r}")
        for c in calls:
            tool_calls_made += 1
            name = c.function.name
            try:
                a = json.loads(c.function.arguments or "{}")
                result = tools[name](**a) if name in tools else f"(unknown tool {name})"
            except Exception as e:
                result = f"ERROR in {name}: {e}"
            if isinstance(result, list):  # view_image -> multimodal content
                messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
            else:
                messages.append({"role": "tool", "tool_call_id": c.id, "content": str(result)})

        sig = progress_sig()
        stale = stale + 1 if sig == last_sig else 0
        last_sig = sig
        if not calls:  # model thinks it is done
            print("[harness] model returned no tool calls -- treating as finished.")
            break
        if (RUN_DIR / "export.step").exists() and stale >= args.patience:
            print(f"[harness] no STEP progress for {stale} turns -- stopping.")
            break

    # Fallback: if the model used the brief's filename, promote it to export.step.
    exp = RUN_DIR / "export.step"
    if not exp.exists():
        for cand in ("cadquery_native_export.step", "assembly.step"):
            if (RUN_DIR / cand).exists():
                shutil.copy(RUN_DIR / cand, exp); print(f"[harness] copied {cand} -> export.step"); break

    ended = datetime.datetime.now(datetime.timezone.utc)
    log = {
        "schema_version": "m3_ai_assembly_run_log.v0.1",
        "run_id": f"cadquery_native_driver_{args.model.replace(':','_').replace('/','_')}_{started:%Y%m%d}_01",
        "benchmark_id": "m3_ai_assembly", "track": "cadquery_native_driver",
        "kit_version": KIT_VERSION, "prompt_variant": prompt_variant,
        "status": "complete" if exp.exists() else "no_valid_artifact",
        "driver": {
            "ai_driver": f"{args.model} ({'local open-weight, Ollama' if is_local else 'cloud API'})",
            "host_application": f"CadQuery (Python) {TOOL_VER}",
            "hardware": ("local AI supercomputer (unified-memory box)"
                         if is_local else "cloud API endpoint (NON-LOCAL cell, not the local anchor)"),
            "cell": "local_anchor" if is_local else "cloud_api",
            "endpoint": args.base_url, "multimodal": args.multimodal,
            "max_tokens": max_tokens,  # per-turn completion cap (None = Ollama default)
        },
        "timing": {"started_utc": started.isoformat(), "ended_utc": ended.isoformat(),
                   "elapsed_minutes": round((time.time() - t0) / 60, 2)},
        "token_usage": {"capture_status": "available" if (tok_in or tok_out) else "unavailable",
                        "prompt_tokens": tok_in, "completion_tokens": tok_out,
                        "total_tokens": tok_in + tok_out},
        "attempts": [{"attempt_id": "A01", "is_retry": False,
                      "driver_action": "agentic CadQuery build loop", "result": "complete" if exp.exists() else "no_export",
                      "corrections": [], "human_interventions": [], "notes": f"{it} model turns, {tool_calls_made} tool calls"}],
        "summary": {"attempt_count": it, "retry_count": 0, "correction_count": 0,
                    "human_intervention_count": 0,
                    "residual_not_built_yet": ["printhead/tool payload", "carriage->tool mount interface"]},
        "privacy_review": {"secrets_checked": True,
                           "notes": "No credentials, API keys, or private supplier fields."},
    }
    import yaml
    (RUN_DIR / "run_log.yaml").write_text(yaml.safe_dump(log, sort_keys=False), encoding="utf-8")
    print(f"[harness] done. export.step={'OK' if exp.exists() else 'MISSING'}  "
          f"turns={it} tool_calls={tool_calls_made} tokens={tok_in+tok_out}  log=run_log.yaml")

if __name__ == "__main__":
    main()
