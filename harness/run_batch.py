#!/usr/bin/env python3
"""Batch-run the MARB local harness N times and catalogue the results.

Each run gets a fresh staged folder (kit + brief + reference images unzipped from the
blind kit), runs marb_local_harness.py, then we inspect the produced export.step
(loadable? solid count? bbox) and the run_log.yaml (status, tokens, turns, elapsed),
appending a row to catalogue.csv + catalogue.md in the batch folder.

Usage:
    python run_batch.py --n 5 --start 1 --batch-dir runs/qwen3-coder-next_cadquery
    python run_batch.py --n 5 --start 6 --batch-dir runs/qwen3-coder-next_cadquery --guidance-file v2_guidance.md
"""
import argparse, csv, json, pathlib, subprocess, sys, zipfile
import cadquery as cq
import yaml

HARNESS = pathlib.Path(__file__).with_name("marb_local_harness.py")
KIT_ZIP = pathlib.Path(__file__).resolve().parents[1] / "kits" / "m3_cadquery_blind_kit.zip"
COLS = ["run", "prompt_variant", "status", "loadable", "solids", "bbox_x", "bbox_y",
        "bbox_z", "turns", "prompt_tokens", "completion_tokens", "total_tokens",
        "elapsed_min", "error"]

def inspect_step(p):
    if not p.exists():
        return {"loadable": False, "solids": 0, "bbox_x": "", "bbox_y": "", "bbox_z": "", "error": "no export.step"}
    try:
        shp = cq.importers.importStep(str(p))
        bb = shp.val().BoundingBox()
        return {"loadable": True, "solids": len(shp.solids().vals()),
                "bbox_x": round(bb.xlen), "bbox_y": round(bb.ylen), "bbox_z": round(bb.zlen), "error": ""}
    except Exception as e:
        return {"loadable": False, "solids": 0, "bbox_x": "", "bbox_y": "", "bbox_z": "", "error": f"load:{e}"[:80]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--model", default="qwen3-coder-next:q4_K_M")
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--guidance-file", default=None)
    args = ap.parse_args()

    batch = pathlib.Path(args.batch_dir); batch.mkdir(parents=True, exist_ok=True)
    csv_path, md_path = batch / "catalogue.csv", batch / "catalogue.md"
    rows = list(csv.DictReader(open(csv_path))) if csv_path.exists() else []

    for i in range(args.start, args.start + args.n):
        rd = batch / f"run_{i:02d}"
        if rd.exists():
            import shutil; shutil.rmtree(rd)
        rd.mkdir(parents=True)
        with zipfile.ZipFile(KIT_ZIP) as z:
            z.extractall(rd)
        cmd = [sys.executable, "-u", str(HARNESS), "--model", args.model,
               "--max-iters", str(args.max_iters), "--run-dir", str(rd)]
        if args.guidance_file:
            cmd += ["--guidance-file", str(pathlib.Path(args.guidance_file).resolve())]
        print(f"\n===== run {i:02d} -> {rd} =====", flush=True)
        try:
            subprocess.run(cmd, timeout=1800, check=False)
        except subprocess.TimeoutExpired:
            print(f"run {i:02d} TIMEOUT", flush=True)

        row = {c: "" for c in COLS}
        row["run"] = i
        row.update(inspect_step(rd / "export.step"))
        log = rd / "run_log.yaml"
        if log.exists():
            d = yaml.safe_load(log.read_text(encoding="utf-8"))
            row["prompt_variant"] = d.get("prompt_variant", "")
            row["status"] = d.get("status", "")
            row["turns"] = d.get("summary", {}).get("attempt_count", "")
            t = d.get("token_usage", {})
            row["prompt_tokens"], row["completion_tokens"], row["total_tokens"] = (
                t.get("prompt_tokens", ""), t.get("completion_tokens", ""), t.get("total_tokens", ""))
            row["elapsed_min"] = d.get("timing", {}).get("elapsed_minutes", "")
        else:
            row["status"] = "no_run_log"
        rows = [r for r in rows if str(r.get("run")) != str(i)] + [row]
        rows.sort(key=lambda r: int(r["run"]))

        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
        with open(md_path, "w") as f:
            f.write(f"# Local CadQuery batch — {args.model}\n\n")
            f.write("| " + " | ".join(COLS) + " |\n|" + "---|" * len(COLS) + "\n")
            for r in rows:
                f.write("| " + " | ".join(str(r.get(c, "")) for c in COLS) + " |\n")
            ok = [r for r in rows if str(r.get("loadable")) == "True"]
            f.write(f"\n**{len(ok)}/{len(rows)} produced a loadable STEP.** ")
            if ok:
                sv = [int(r["solids"]) for r in ok]
                f.write(f"Solids among loadable: min {min(sv)}, max {max(sv)}, "
                        f"median {sorted(sv)[len(sv)//2]} (target ~100 instances).")
        print(f"run {i:02d}: status={row['status']} loadable={row['loadable']} "
              f"solids={row['solids']} tokens={row['total_tokens']}", flush=True)

    print(f"\n[batch] wrote {csv_path} and {md_path}", flush=True)

if __name__ == "__main__":
    main()
