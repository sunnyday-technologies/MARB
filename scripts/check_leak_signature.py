"""Detect a submission that reproduced superseded reference data.

MARB grades how well a model assembles a machine in CAD. GAP/ORIENT/POS say how
CLOSE a submission landed. They cannot say HOW it got there. This says how.

The task subject is open hardware, and earlier versions of related open
artifacts carried partial placement data. Archival copies of those are
immutable, so some fraction of a superseded reference is permanently
recoverable by a well-crawled model. That is a normal condition for a
benchmark built on published hardware. It is not a reason to stop grading; it
is a reason to be able to tell the two cases apart.

The signature exploits a property of the superseded data: it is WRONG. Every
marker is an instance whose pose CHANGED between the superseded artifact and
the current reference, because the placement was corrected. Reproducing a
corrected-away value cannot result from solving the task. It can only result
from reading the old artifact. Instances that did NOT change are excluded --
those are correct answers, and a submission is supposed to land on them.

The marker table is GATED and never lives in this repository. Publishing what
the detector looks for would let an entrant route around it, leaving a filter
that only catches the honest.

Usage:
    python scripts/check_leak_signature.py <submission.yaml>
    python scripts/check_leak_signature.py <submission.yaml> --json
    python scripts/check_leak_signature.py <submission.yaml> \\
        --signature path/to/leak_signature.yaml

Exit codes: 0 clean, 1 contamination detected, 2 usage/setup error.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("pyyaml is required: pip install pyyaml\n")
    raise SystemExit(2)


DEFAULT_SIGNATURE = Path(__file__).resolve().parent.parent / "tasks" / "m3_crete" / "leak_signature.yaml"

# A marker only counts when the submission sits on the superseded value this
# tightly. Loose enough to survive float formatting and unit round-trips,
# far tighter than the smallest correction in the table (1.5 mm).
MATCH_TOL_MM = 0.05

# A marker whose correction moved this far is structural rather than numeric
# drift -- a solver cannot land on it by being slightly wrong.
STRUCTURAL_DELTA_MM = 100.0

# Verdict thresholds. One structural marker is already very hard to explain
# innocently; the bands exist so a human reviewer can triage, not so the
# script can accuse.
CONFIRMED_STRUCTURAL = 2
CONFIRMED_TOTAL = 4


def _poses(spec: dict) -> dict:
    """instance id -> {translate_mm, rotate_deg}, defaults filled in."""
    out = {}
    for inst in spec.get("instances") or []:
        if not isinstance(inst, dict) or "id" not in inst:
            continue
        t = inst.get("transform") or {}
        out[inst["id"]] = {
            "translate_mm": [float(v) for v in (t.get("translate_mm") or [0.0, 0.0, 0.0])],
            "rotate_deg": [float(v) for v in (t.get("rotate_deg") or [0.0, 0.0, 0.0])],
        }
    return out


def _close(a, b, tol=MATCH_TOL_MM) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def check(submission_path, signature_path) -> dict:
    sig = yaml.safe_load(io.open(signature_path, encoding="utf-8"))
    markers = sig.get("markers") or []
    if not markers:
        raise ValueError(f"signature file has no markers: {signature_path}")

    sub = _poses(yaml.safe_load(io.open(submission_path, encoding="utf-8")))

    hits, near_misses = [], []
    for m in markers:
        inst = m["instance_id"]
        if inst not in sub:
            continue
        got = sub[inst]
        old, cur = m["superseded"], m["current"]

        on_superseded = _close(got["translate_mm"], old["translate_mm"])
        on_current = _close(got["translate_mm"], cur["translate_mm"])

        # Landing on the CURRENT value is the correct answer, never a hit --
        # even if the rotation happens to match the old one.
        if on_current:
            continue

        if on_superseded:
            delta = m.get("max_delta_mm", 0.0)
            hits.append({
                "instance_id": inst,
                "structural": delta >= STRUCTURAL_DELTA_MM,
                "correction_delta_mm": delta,
                "diverging_axes": m.get("diverging_axes", ""),
            })
        elif _close(got["rotate_deg"], old["rotate_deg"]) and not _close(
            got["rotate_deg"], cur["rotate_deg"]
        ):
            near_misses.append({"instance_id": inst, "reason": "superseded rotation only"})

    structural = sum(1 for h in hits if h["structural"])
    if structural >= CONFIRMED_STRUCTURAL or len(hits) >= CONFIRMED_TOTAL:
        verdict = "CONTAMINATED"
    elif hits:
        verdict = "REVIEW"
    else:
        verdict = "CLEAN"

    return {
        "verdict": verdict,
        "submission": str(submission_path),
        "markers_available": len(markers),
        "markers_applicable": sum(1 for m in markers if m["instance_id"] in sub),
        "hits": hits,
        "structural_hits": structural,
        "near_misses": near_misses,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("submission", help="submitted assembly spec (YAML)")
    ap.add_argument("--signature", default=str(DEFAULT_SIGNATURE),
                    help="gated marker table (default: tasks/m3_crete/leak_signature.yaml)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if not Path(args.submission).exists():
        sys.stderr.write(f"error: submission not found: {args.submission}\n")
        return 2
    if not Path(args.signature).exists():
        sys.stderr.write(
            f"error: signature not found: {args.signature}\n"
            "The marker table is gated and is not in this repository. Request "
            "access alongside the answer key, then place it at the path above "
            "or pass --signature.\n"
        )
        return 2

    try:
        result = check(args.submission, args.signature)
    except Exception as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"provenance: {result['verdict']}")
        print(f"  markers applicable : {result['markers_applicable']}"
              f"/{result['markers_available']}")
        print(f"  matched superseded : {len(result['hits'])}"
              f" ({result['structural_hits']} structural)")
        for h in result["hits"]:
            kind = "structural" if h["structural"] else "numeric"
            print(f"    - {h['instance_id']} [{kind},"
                  f" corrected by {h['correction_delta_mm']}mm"
                  f" on {h['diverging_axes']}]")
        for n in result["near_misses"]:
            print(f"    ~ {n['instance_id']} ({n['reason']})")
        if result["verdict"] != "CLEAN":
            print("\nA flag is a prompt for human review, not a determination.")
            print("Confirm before any leaderboard action.")

    return 1 if result["verdict"] == "CONTAMINATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
