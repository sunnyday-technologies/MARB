# -*- coding: utf-8 -*-
"""MARB v0.9 unified grader -- runs gap + position + orientation on every named
run plus the reference-vs-itself ceiling baseline, prints a comparison table,
and writes a single JSON the leaderboard / scoreboard graphic consumes.

Two modes:

* SINGLE-RUN (default, unchanged) -- grades the four canonical runs in RUNS and
  writes the flat per-run schema to results/marb_v0_9_grades.json. New repeat
  runs stay directly comparable to these.

* AGGREGATE (--config / --runs-dir) -- grades many seeds per cell, aggregates
  median / mean / std per headline metric per cell, and writes the *stats*
  schema (each cell carries a flat representative block + an `_agg` block with
  the spread). Use this once the repeat runs land.

USAGE
  # single-run (the original four):
  python marb_grade_all.py [--ref ...] [--spec ...] [--json results.json]

  # aggregate from an explicit config {cell_name: [step_paths, ...]}:
  python marb_grade_all.py --config runs.json --json results/marb_v0_9_stats.json

  # aggregate by auto-discovering [local-path-redacted]
  python marb_grade_all.py --runs-dir [local-path-redacted] --json results/marb_v0_9_stats.json
"""
from __future__ import annotations
import argparse, json, re, statistics, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "grader"))
from marb_pose_metric import grade as grade_pose, parts as pose_parts, _label_map  # noqa: E402
from marb_gap_metric import grade as grade_gap                                       # noqa: E402
from marb_orient_metric import grade as grade_orient                                 # noqa: E402

DEFAULT_REF = REPO / "tasks" / "m3_crete" / "m3_reference_round1.step"
DEFAULT_SPEC = REPO / "tasks" / "m3_crete" / "m3_reference_assembly.yaml"
RUNS = {
    "Reference (self / ceiling)": DEFAULT_REF,
    "Claude · CadQuery": REPO / "runs" / "claude_cadquery" / "export.step",
    "Claude · Fusion":   REPO / "runs" / "claude_fusion" / "export.step",
    "Codex · CadQuery":  REPO / "runs" / "codex_cadquery" / "export.step",
}

# Headline scalars aggregated per cell. (key, drill-path, lower_is_better)
HEADLINES = [
    ("gap_median_mm",      ("gap", "overall", "median_mm"),                 True),
    ("gap_le1_pct",        ("gap", "overall", "bands_pct", "<= 1.0 mm"),    False),
    ("pos_rel_mm",         ("position", "relative", "median_mm"),           True),
    ("pos_abs_mm",         ("position", "absolute", "median_mm"),           True),
    ("orient_aligned_pct", ("orientation", "aligned_pct"),                  False),
]

# Pretty display names for auto-discovered <model>_<driver> folder keys.
CELL_ALIASES = {
    "opus48_cadquery": "Opus 4.8 · CadQuery",
    "opus48_fusion":   "Opus 4.8 · Fusion",
    "opus47_cadquery": "Opus 4.7 · CadQuery",
    "opus47_fusion":   "Opus 4.7 · Fusion",
    "codex_cadquery":  "GPT-5 Codex · CadQuery",
    "codex_fusion":    "GPT-5 Codex · Fusion",
}


def _drill(d, path):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def grade_one(ref: Path, run: Path, spec: Path):
    label = _label_map(spec)
    pose = grade_pose(pose_parts(ref, label), pose_parts(run, label))
    gap = grade_gap(ref, run, spec)
    ori = grade_orient(ref, run, spec)
    return {"position": pose, "gap": gap, "orientation": ori}


def _stats(values):
    """median / mean / population-std (0 at n=1, always defined) + raw values."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "median": None, "mean": None, "std": None, "values": []}
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 2),
        "mean": round(statistics.fmean(vals), 2),
        "std": round(statistics.pstdev(vals), 2),  # population std: 0 for n=1
        "values": [round(v, 2) for v in vals],
    }


def aggregate(grades: list[dict], provenance: list[dict] | None = None) -> dict:
    """Aggregate a cell's per-seed grade_one dicts into a stats block plus a flat
    representative block (median across seeds) so the existing flat-schema figure
    builders keep working when pointed at the stats file. `provenance` (optional,
    from the manifest) is the per-seed version/timing/token record carried through
    for traceability and for the scatter's build-time axis."""
    agg = {key: _stats([_drill(g, path) for g in grades]) for key, path, _ in HEADLINES}
    # Representative flat block: pick the seed whose gap median is the cell median
    # (a real run, so every nested field stays self-consistent for the builders).
    gaps = [(_drill(g, ("gap", "overall", "median_mm")), i) for i, g in enumerate(grades)]
    gaps = [(v, i) for v, i in gaps if v is not None] or [(0, 0)]
    gaps.sort()
    rep = grades[gaps[len(gaps) // 2][1]]
    out = dict(rep)            # flat schema: position / gap / orientation
    out["_agg"] = agg
    out["_n"] = len(grades)
    if provenance:
        out["_provenance"] = provenance
        mins = [p.get("timing", {}).get("elapsed_minutes") for p in provenance]
        mins = [m for m in mins if m is not None]
        if mins:
            out["_build_min"] = round(statistics.median(mins), 1)
    return out


def _load_manifest(path: Path):
    """Read the run registry (results/marb_runs.json). Returns (cells, toolchain,
    ref, spec) where cells maps cell_name -> [(step_path, provenance_dict), ...]."""
    m = json.loads(path.read_text(encoding="utf-8"))
    cells: dict[str, list] = {}
    for r in m.get("runs", []):
        prov = {k: r[k] for k in ("seed", "model", "driver", "timing", "tokens", "effort") if k in r}
        cells.setdefault(r["cell"], []).append((Path(r["step"]), prov))
    return (cells, m.get("grader_toolchain", {}),
            m.get("reference_step"), m.get("spec"))


def _discover(runs_dir: Path) -> dict:
    """Group [local-path-redacted] by <model>_<driver>."""
    cells: dict[str, list[Path]] = {}
    for sub in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        step = sub / "export.step"
        if not step.exists():
            cands = list(sub.glob("*.step")) + list(sub.glob("*.STEP"))
            if not cands:
                print(f"  SKIP {sub.name}: no STEP inside"); continue
            step = cands[0]
        key = re.sub(r"_(\d+|seed\d+)$", "", sub.name)   # strip trailing _<seed>
        name = CELL_ALIASES.get(key, key.replace("_", " · "))
        cells.setdefault(name, []).append(step)
    return cells


def _print_single(rows):
    print(f"\n{'Run':30} {'GAP med':>8} {'GAP <=1mm':>10} {'POS rel med':>12} "
          f"{'POS abs med':>12} {'ORIENT aligned':>14}")
    print("-" * 90)
    for name, r in rows.items():
        gm = r["gap"]["overall"]["median_mm"]
        gp = r["gap"]["overall"]["bands_pct"]["<= 1.0 mm"]
        pr = r["position"]["relative"]["median_mm"]
        pa = r["position"]["absolute"]["median_mm"]
        oa = r["orientation"]["aligned_pct"]
        print(f"{name:30} {str(gm)+'mm':>8} {str(gp)+'%':>10} "
              f"{str(pr)+'mm':>12} {str(pa)+'mm':>12} {str(oa)+'%':>14}")


def _print_agg(rows):
    print(f"\n{'Cell':26} {'n':>2} {'GAP med±sd':>14} {'POS rel±sd':>14} "
          f"{'ORIENT %±sd':>14}")
    print("-" * 76)
    for name, r in rows.items():
        a = r["_agg"]
        def cell(k, unit):
            s = a[k]
            if s["median"] is None:
                return "—"
            return f"{s['median']}{unit}±{s['std']}"
        print(f"{name:26} {r['_n']:>2} {cell('gap_median_mm','mm'):>14} "
              f"{cell('pos_rel_mm','mm'):>14} {cell('orient_aligned_pct','%'):>14}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MARB v0.9 unified grader.")
    ap.add_argument("--ref", default=str(DEFAULT_REF))
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--json", default=None)
    ap.add_argument("--manifest", default=None,
                    help="run registry JSON (results/marb_runs.json): cells + provenance + toolchain")
    ap.add_argument("--config", default=None,
                    help="JSON file mapping cell_name -> [step_path, ...] (aggregate mode)")
    ap.add_argument("--runs-dir", default=None,
                    help=r"auto-discover <model>_<driver>_<seed>\export.step (aggregate mode)")
    a = ap.parse_args(argv)
    ref = Path(a.ref); spec = Path(a.spec)

    toolchain = {}
    prov_by_cell: dict[str, list] = {}
    aggregate_mode = bool(a.manifest or a.config or a.runs_dir)

    if not aggregate_mode:
        rows = {}
        for name, p in RUNS.items():
            if not p.exists():
                print(f"  SKIP {name}: missing {p}"); continue
            rows[name] = grade_one(ref, p, spec)
        _print_single(rows)
        out = {"schema": "marb_v0_9_single", "reference_step": str(ref),
               "spec": str(spec), "runs": rows}
    else:
        if a.manifest:
            mcells, toolchain, mref, mspec = _load_manifest(Path(a.manifest))
            if mref and a.ref == str(DEFAULT_REF): ref = Path(mref)
            if mspec and a.spec == str(DEFAULT_SPEC): spec = Path(mspec)
            cells = {}
            for name, items in mcells.items():
                cells[name] = [step for step, _ in items]
                prov_by_cell[name] = [prov for _, prov in items]
        elif a.config:
            cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
            cells = {k: [Path(p) for p in v] for k, v in cfg.items()}
        else:
            cells = _discover(Path(a.runs_dir))
        # Always include the reference ceiling as a single-seed cell for context.
        cells = {"Reference (self / ceiling)": [ref], **cells}

        rows = {}
        for name, paths in cells.items():
            provs = prov_by_cell.get(name, [])
            grades, used_prov = [], []
            for i, p in enumerate(paths):
                if not p.exists():
                    print(f"  SKIP {name}: missing {p}"); continue
                print(f"  grading {name}: {p}")
                grades.append(grade_one(ref, p, spec))
                if i < len(provs):
                    used_prov.append(provs[i])
            if grades:
                rows[name] = aggregate(grades, used_prov or None)
        _print_agg(rows)
        out = {"schema": "marb_v0_9_stats", "reference_step": str(ref),
               "spec": str(spec), "toolchain": toolchain, "runs": rows}

    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    raise SystemExit(main())
