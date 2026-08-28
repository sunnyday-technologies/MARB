#!/usr/bin/env python3
"""Fail-closed, aggregate-only public invariant gate for MARB L4-ECO.

The gate deliberately imports only ``cadclaw.roundtrip.snapshot_geometry``
from CADCLAW.  It proves that every baseline AABB pose has a compatible match
after the ECO and that exactly one rail-shaped part was added.  The private
answer-key grade remains responsible for the requested midpoint/top-flush
placement; this public gate never substitutes for that task-local check.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname


TASK_ID = "L4-ECO"
TASK_REVISION = "second-top-cross-spreader-midright-r1"
CADCLAW_AUDITED_COMMIT = "60fc271f68c8a794a4741f856b2dd4c9878416a6"
CADCLAW_AUDITED_VERSION = "0.10.0"
CADQUERY_AUDITED_VERSION = "2.7.0"
CADQUERY_OCP_AUDITED_VERSION = "7.8.1.1.post1"
REPORT_SCHEMA = "marb_eco_public_invariant.v1"
GATE_VERSION = "marb_l4_eco_invariant.v0.12.0"
MAX_BASELINE_PARTS = 256


@dataclass(frozen=True)
class InvariantTolerances:
    center_mm: float = 0.05
    axis_bbox_mm: float = 0.05
    bbox_volume_relative: float = 1e-6
    bbox_volume_absolute_mm3: float = 1e-3
    requested_axis_bbox_mm: float = 0.5

    def __post_init__(self) -> None:
        for name, value in (
            ("center_mm", self.center_mm),
            ("axis_bbox_mm", self.axis_bbox_mm),
            ("bbox_volume_relative", self.bbox_volume_relative),
            ("bbox_volume_absolute_mm3", self.bbox_volume_absolute_mm3),
            ("requested_axis_bbox_mm", self.requested_axis_bbox_mm),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class _Part:
    stable_order: int
    bbox_mm: tuple[float, float, float, float, float, float]
    center_mm: tuple[float, float, float]
    axis_bbox_mm: tuple[float, float, float]
    signature_mm: tuple[float, float, float]
    bbox_volume_mm3: float


class EcoInvariantError(ValueError):
    """Raised for invalid or unsafe gate inputs without echoing input data."""


FROZEN_TOLERANCES = InvariantTolerances()


def _finite_triplet(value: Iterable[Any], field: str) -> tuple[float, float, float]:
    try:
        values = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EcoInvariantError(f"invalid {field}") from exc
    if len(values) != 3 or not all(math.isfinite(item) for item in values):
        raise EcoInvariantError(f"invalid {field}")
    return values  # type: ignore[return-value]


def _parts(snapshot: Any) -> tuple[_Part, ...]:
    try:
        raw_parts = tuple(snapshot.parts)
        declared_count = int(snapshot.part_count)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise EcoInvariantError("invalid geometry snapshot") from exc
    if declared_count != len(raw_parts) or declared_count < 1:
        raise EcoInvariantError("inconsistent geometry snapshot part count")
    if declared_count > MAX_BASELINE_PARTS + 1:
        raise EcoInvariantError("geometry snapshot exceeds the bounded part limit")

    result: list[_Part] = []
    seen_orders: set[int] = set()
    for raw in raw_parts:
        try:
            stable_order = int(raw.index)
            bbox = tuple(float(value) for value in raw.bbox_mm)
            center = _finite_triplet(raw.center_mm, "part center")
            signature = _finite_triplet(raw.signature_mm, "part signature")
            volume = float(raw.bbox_volume_mm3)
        except EcoInvariantError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise EcoInvariantError("invalid part geometry") from exc
        if (
            len(bbox) != 6
            or not all(math.isfinite(value) for value in bbox)
            or not math.isfinite(volume)
            or volume < 0.0
        ):
            raise EcoInvariantError("invalid part geometry")
        if stable_order in seen_orders:
            # CADCLAW normally supplies unique indices. Refuse ambiguous input
            # rather than silently using tuple order as an identity.
            raise EcoInvariantError("duplicate part index in geometry snapshot")
        seen_orders.add(stable_order)
        axis_bbox = (
            bbox[3] - bbox[0],
            bbox[4] - bbox[1],
            bbox[5] - bbox[2],
        )
        if any(value < 0.0 for value in axis_bbox):
            raise EcoInvariantError("invalid part bounding box")
        result.append(_Part(stable_order, bbox, center, axis_bbox, signature, volume))
    return tuple(sorted(result, key=lambda part: part.stable_order))


def _within(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _compatible(before: _Part, after: _Part, limits: InvariantTolerances) -> bool:
    if before.signature_mm != after.signature_mm:
        return False
    if not all(
        _within(actual, expected, limits.axis_bbox_mm)
        for actual, expected in zip(after.bbox_mm, before.bbox_mm)
    ):
        return False
    if not all(
        _within(actual, expected, limits.center_mm)
        for actual, expected in zip(after.center_mm, before.center_mm)
    ):
        return False
    if not all(
        _within(actual, expected, limits.axis_bbox_mm)
        for actual, expected in zip(after.axis_bbox_mm, before.axis_bbox_mm)
    ):
        return False
    volume_tolerance = max(
        limits.bbox_volume_absolute_mm3,
        limits.bbox_volume_relative
        * max(abs(before.bbox_volume_mm3), abs(after.bbox_volume_mm3)),
    )
    return _within(after.bbox_volume_mm3, before.bbox_volume_mm3, volume_tolerance)


def _maximum_matching(
    before: Sequence[_Part],
    after: Sequence[_Part],
    limits: InvariantTolerances,
) -> tuple[tuple[int, int], ...]:
    """Return a deterministic maximum-cardinality bipartite matching."""
    adjacency = [
        tuple(
            index
            for index, candidate in enumerate(after)
            if _compatible(part, candidate, limits)
        )
        for part in before
    ]
    right_to_left: dict[int, int] = {}

    def augment(left: int, seen: set[int]) -> bool:
        for right in adjacency[left]:
            if right in seen:
                continue
            seen.add(right)
            previous = right_to_left.get(right)
            if previous is None or augment(previous, seen):
                right_to_left[right] = left
                return True
        return False

    for left in range(len(before)):
        augment(left, set())
    return tuple(sorted((left, right) for right, left in right_to_left.items()))


def evaluate_snapshots(
    baseline: Any,
    changed: Any,
    *,
    limits: InvariantTolerances = FROZEN_TOLERANCES,
) -> dict[str, Any]:
    """Evaluate public ECO invariants without returning per-part geometry."""
    if limits != FROZEN_TOLERANCES:
        raise EcoInvariantError("the versioned gate requires its exact frozen tolerances")
    before = _parts(baseline)
    after = _parts(changed)
    if len(before) > MAX_BASELINE_PARTS:
        raise EcoInvariantError("baseline exceeds the bounded part limit")

    matching = _maximum_matching(before, after, limits)
    matched_before = {left for left, _ in matching}
    matched_after = {right for _, right in matching}
    added = [part for index, part in enumerate(after) if index not in matched_after]
    missing_count = len(before) - len(matched_before)
    expected_signature = (20.0, 40.0, 1000.0)
    expected_axis_bbox = (20.0, 1000.0, 40.0)
    # CADCLAW signatures are already sorted and rounded to 0.1 mm. Requiring
    # exact equality prevents the placement tolerance from admitting a
    # different stock size.
    source_shape_ok = len(added) == 1 and added[0].signature_mm == expected_signature
    requested_orientation_ok = len(added) == 1 and all(
        _within(actual, expected, limits.requested_axis_bbox_mm)
        for actual, expected in zip(added[0].axis_bbox_mm, expected_axis_bbox)
    )
    count_ok = len(after) == len(before) + 1
    invariants_ok = missing_count == 0 and len(matching) == len(before)
    public_gate_ok = (
        count_ok and invariants_ok and source_shape_ok and requested_orientation_ok
    )

    checks = (
        ("instance-count-delta", count_ok),
        ("baseline-aabb-pose-preservation", invariants_ok),
        ("added-stock-signature", source_shape_ok),
        ("added-axis-orientation", requested_orientation_ok),
    )
    return {
        "schema": REPORT_SCHEMA,
        "gate_version": GATE_VERSION,
        "task": TASK_ID,
        "task_revision": TASK_REVISION,
        "cadclaw_audited_commit": CADCLAW_AUDITED_COMMIT,
        "cadclaw_audited_version": CADCLAW_AUDITED_VERSION,
        "public_gate_status": "pass" if public_gate_ok else "fail",
        "counts": {
            "baseline_renderable_shapes": len(before),
            "changed_renderable_shapes": len(after),
            "matched_invariant_shapes": len(matching),
            "missing_baseline_shapes": missing_count,
            "unmatched_changed_shapes": len(added),
        },
        "checks": [
            {"id": check_id, "status": "pass" if passed else "fail"}
            for check_id, passed in checks
        ],
        "tolerances": {
            "center_mm": limits.center_mm,
            "axis_bbox_mm": limits.axis_bbox_mm,
            "bbox_volume_relative": limits.bbox_volume_relative,
            "bbox_volume_absolute_mm3": limits.bbox_volume_absolute_mm3,
            "requested_axis_bbox_mm": limits.requested_axis_bbox_mm,
        },
        "private_requested_placement_check": "required_separately",
        "publication_requirement": "private requested-placement grade and repeat-run policy apply separately",
        "limitations": [
            "Anonymous AABB matching cannot observe swaps between geometrically identical instances.",
            "A rotation that preserves an axis-aligned bounding box may be unobservable.",
            "Coincident solids may be hidden by CADCLAW's 0.1 mm rounded-bbox deduplication.",
            "This gate does not prove topology, feature history, PMI, material, suppression state, or physical validity.",
        ],
    }


def _validated_input(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise EcoInvariantError(f"{label} STEP must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise EcoInvariantError(f"{label} STEP is unavailable") from exc
    if not resolved.is_file() or stat.st_size <= 0:
        raise EcoInvariantError(f"{label} STEP must be a nonempty regular file")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_text_sha256(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EcoInvariantError("the frozen gate source is unavailable") from exc
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _run_git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            [
                "git", "-c", f"safe.directory={repo.as_posix()}",
                "-C", str(repo), *args,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EcoInvariantError("CADCLAW runtime provenance could not be verified") from exc
    return completed.stdout.strip()


def _attest_cadclaw_runtime(roundtrip_module: Any) -> dict[str, str]:
    """Fail closed unless the imported CADCLAW is the audited immutable code."""
    try:
        distribution = importlib.metadata.distribution("cadclaw")
        cadclaw_version = distribution.version
        direct_url = json.loads(distribution.read_text("direct_url.json") or "null")
        cadquery_version = importlib.metadata.version("cadquery")
        ocp_version = importlib.metadata.version("cadquery-ocp")
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError) as exc:
        raise EcoInvariantError("CADCLAW runtime provenance could not be verified") from exc

    if cadclaw_version != CADCLAW_AUDITED_VERSION:
        raise EcoInvariantError("the installed CADCLAW version is not the audited version")
    if cadquery_version != CADQUERY_AUDITED_VERSION or ocp_version != CADQUERY_OCP_AUDITED_VERSION:
        raise EcoInvariantError("the CAD geometry runtime does not match the frozen dependency lock")
    if not isinstance(direct_url, dict):
        raise EcoInvariantError("CADCLAW runtime provenance could not be verified")

    vcs = direct_url.get("vcs_info")
    if isinstance(vcs, dict):
        commit = str(vcs.get("commit_id", "")).lower()
        if vcs.get("vcs") != "git" or commit != CADCLAW_AUDITED_COMMIT:
            raise EcoInvariantError("the installed CADCLAW commit is not the audited commit")
        try:
            module_path = Path(roundtrip_module.__file__).resolve(strict=True)
            expected_modules = {
                Path(distribution.locate_file(item)).resolve(strict=True)
                for item in (distribution.files or ())
                if str(item).replace("\\", "/") == "cadclaw/roundtrip.py"
            }
        except (OSError, TypeError) as exc:
            raise EcoInvariantError("the imported CADCLAW module does not match its distribution") from exc
        if module_path not in expected_modules:
            raise EcoInvariantError("the imported CADCLAW module does not match its distribution")
        provenance = "vcs-direct-url"
    else:
        # Local validation may use an editable checkout, but only when the
        # imported module is inside a clean checkout at the exact commit.
        parsed = urlparse(str(direct_url.get("url", "")))
        editable = (direct_url.get("dir_info") or {}).get("editable") is True
        if parsed.scheme != "file" or not editable:
            raise EcoInvariantError("CADCLAW runtime provenance could not be verified")
        try:
            repo = Path(url2pathname(parsed.path)).resolve(strict=True)
            module_path = Path(roundtrip_module.__file__).resolve(strict=True)
            module_path.relative_to(repo)
        except (OSError, ValueError, TypeError) as exc:
            raise EcoInvariantError("the imported CADCLAW module does not match its distribution") from exc
        commit = _run_git(repo, "rev-parse", "HEAD").lower()
        if commit != CADCLAW_AUDITED_COMMIT:
            raise EcoInvariantError("the installed CADCLAW commit is not the audited commit")
        if _run_git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise EcoInvariantError("the audited CADCLAW checkout has tracked modifications")
        provenance = "clean-local-git-checkout"

    return {
        "cadclaw_version": cadclaw_version,
        "cadclaw_commit": commit,
        "cadclaw_provenance": provenance,
        "cadquery_version": cadquery_version,
        "cadquery_ocp_version": ocp_version,
    }


def _gate_source_attestation() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[1]
    return {
        "implementation_sha256": _canonical_text_sha256(Path(__file__).resolve()),
        "requirements_sha256": _canonical_text_sha256(
            repo_root / "requirements-l4-eco.txt"
        ),
    }


def _validated_new_report_path(
    path: Path,
    baseline_path: Path,
    changed_path: Path,
) -> Path:
    """Resolve a new report path without overwriting evidence or inputs."""
    if path.is_symlink():
        raise EcoInvariantError("report path must be new and must not be a symbolic link")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise EcoInvariantError("report directory is unavailable") from exc
    if not parent.is_dir():
        raise EcoInvariantError("report directory is unavailable")
    resolved = parent / path.name
    protected = {
        baseline_path.resolve(strict=False),
        changed_path.resolve(strict=False),
    }
    if resolved in protected:
        raise EcoInvariantError("report path must differ from both STEP inputs")
    if path.exists():
        raise EcoInvariantError("report path must be new and must not be a symbolic link")
    return resolved


def run_gate(baseline_path: Path, changed_path: Path) -> dict[str, Any]:
    baseline_input = _validated_input(baseline_path, "baseline")
    changed_input = _validated_input(changed_path, "changed")
    if baseline_input == changed_input:
        raise EcoInvariantError("baseline and changed STEP paths must differ")
    input_hashes = {
        "baseline_sha256": _sha256(baseline_input),
        "changed_sha256": _sha256(changed_input),
    }
    try:
        import cadclaw.roundtrip as roundtrip
    except (ImportError, ModuleNotFoundError) as exc:
        raise EcoInvariantError("the pinned CADCLAW gate dependency is unavailable") from exc
    runtime = _attest_cadclaw_runtime(roundtrip)
    try:
        baseline = roundtrip.snapshot_geometry(baseline_input)
        changed = roundtrip.snapshot_geometry(changed_input)
    except Exception as exc:  # CADCLAW/OCP failures must close the gate.
        raise EcoInvariantError("CADCLAW could not snapshot both STEP artifacts") from exc
    if input_hashes != {
        "baseline_sha256": _sha256(baseline_input),
        "changed_sha256": _sha256(changed_input),
    }:
        raise EcoInvariantError("a STEP input changed while the gate was running")
    report = evaluate_snapshots(baseline, changed)
    report["input_artifacts"] = input_hashes
    report["runtime_attestation"] = runtime
    report["gate_source"] = _gate_source_attestation()
    return report


def serialize_report(report: dict[str, Any]) -> bytes:
    """Return stable canonical report bytes for versioned evidence hashing."""
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--changed", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report_path: Path | None = None
    try:
        if args.report:
            report_path = _validated_new_report_path(
                args.report, args.baseline, args.changed
            )
        report = run_gate(args.baseline, args.changed)
    except EcoInvariantError as exc:
        report = {
            "schema": REPORT_SCHEMA,
            "gate_version": GATE_VERSION,
            "task": TASK_ID,
            "task_revision": TASK_REVISION,
            "cadclaw_audited_commit": CADCLAW_AUDITED_COMMIT,
            "cadclaw_audited_version": CADCLAW_AUDITED_VERSION,
            "public_gate_status": "error",
            "error": str(exc),
            "private_requested_placement_check": "required_separately",
            "publication_requirement": "private requested-placement grade and repeat-run policy apply separately",
        }
    payload = serialize_report(report)
    if report_path:
        try:
            with report_path.open("xb") as stream:
                stream.write(payload)
        except OSError as exc:
            raise EcoInvariantError("report could not be created safely") from exc
    else:
        print(payload.decode("utf-8"), end="")
    return 0 if report.get("public_gate_status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
