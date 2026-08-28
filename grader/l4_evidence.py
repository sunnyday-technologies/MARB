"""Fail-closed byte and schema validation for MARB L4 gate evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from grader.eco_invariant import EcoInvariantError, run_gate


PUBLIC_INVARIANT_SCHEMA = "marb_eco_public_invariant.v1"
REQUESTED_CHANGE_SCHEMA = "marb_l4_requested_change_attestation.v1"
GATED_REQUESTED_CHANGE_SCHEMA = "marb_l4_requested_change_grade.v1"
GATED_READ_TOKEN_ENV = "MARB_GATED_READ_TOKEN"
INVARIANT_VALIDATION_MODE = "recompute-registered-step-pair"
MAX_REPORT_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_RUN_LOG_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMMUTABLE_REVISION_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DATASET_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"
)
FROZEN_TOLERANCES = {
    "center_mm": 0.05,
    "axis_bbox_mm": 0.05,
    "bbox_volume_relative": 0.000001,
    "bbox_volume_absolute_mm3": 0.001,
    "requested_axis_bbox_mm": 0.5,
}
EXPECTED_CHECK_IDS = {
    "instance-count-delta",
    "baseline-aabb-pose-preservation",
    "added-stock-signature",
    "added-axis-orientation",
}
EXPECTED_LIMITATIONS = [
    "Anonymous AABB matching cannot observe swaps between geometrically identical instances.",
    "A rotation that preserves an axis-aligned bounding box may be unobservable.",
    "Coincident solids may be hidden by CADCLAW's 0.1 mm rounded-bbox deduplication.",
    "This gate does not prove topology, feature history, PMI, material, suppression state, or physical validity.",
]
EXPECTED_PUBLICATION_REQUIREMENT = (
    "private requested-placement grade and repeat-run policy apply separately"
)


class L4EvidenceError(ValueError):
    """Raised when public evidence cannot be authenticated without echoing data."""


def _resolve_bound_file(
    repo_root: Path,
    relative_path: Any,
    expected_sha256: Any,
    maximum_bytes: int,
) -> tuple[Path, int]:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise L4EvidenceError("evidence path is missing")
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise L4EvidenceError("evidence digest is malformed")
    if "\\" in relative_path:
        raise L4EvidenceError("evidence path is not repository-relative")
    posix_path = PurePosixPath(relative_path)
    if (
        posix_path.is_absolute()
        or posix_path.as_posix() != relative_path
        or any(
            part in {"", ".", ".."} or ":" in part
            for part in posix_path.parts
        )
    ):
        raise L4EvidenceError("evidence path is not repository-relative")
    rel = Path(*posix_path.parts)
    try:
        root = repo_root.resolve(strict=True)
        candidate = root.joinpath(*rel.parts)
        current = root
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise L4EvidenceError("evidence path contains a symbolic link")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        stat = resolved.stat()
    except L4EvidenceError:
        raise
    except (OSError, ValueError) as exc:
        raise L4EvidenceError("evidence file is unavailable") from exc
    if not resolved.is_file() or stat.st_size <= 0 or stat.st_size > maximum_bytes:
        raise L4EvidenceError("evidence file size is invalid")
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise L4EvidenceError("evidence file is unavailable") from exc
    if digest.hexdigest() != expected_sha256:
        raise L4EvidenceError("evidence digest does not match its file")
    return resolved, stat.st_size


def _read_bound_json(repo_root: Path, relative_path: Any, expected_sha256: Any) -> dict:
    resolved, _ = _resolve_bound_file(
        repo_root, relative_path, expected_sha256, MAX_REPORT_BYTES
    )
    try:
        payload = resolved.read_bytes()
    except OSError as exc:
        raise L4EvidenceError("evidence file is unavailable") from exc
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise L4EvidenceError("evidence file is not valid JSON") from exc
    if not isinstance(document, dict):
        raise L4EvidenceError("evidence document must be an object")
    return document


def _require_exact_keys(document: dict, expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise L4EvidenceError(f"{label} report has an unexpected schema")


def _validate_invariant_report(report: dict, run: dict, task: dict) -> None:
    _require_exact_keys(
        report,
        {
            "schema", "gate_version", "task", "task_revision",
            "cadclaw_audited_commit", "cadclaw_audited_version",
            "public_gate_status", "counts", "checks", "tolerances",
            "private_requested_placement_check", "publication_requirement",
            "limitations", "input_artifacts", "runtime_attestation",
            "gate_source",
        },
        "public invariant",
    )
    expected_scalars = {
        "schema": PUBLIC_INVARIANT_SCHEMA,
        "gate_version": task.get("invariant_gate_version"),
        "task": "L4-ECO",
        "task_revision": task.get("task_revision"),
        "cadclaw_audited_commit": task.get("cadclaw_commit"),
        "cadclaw_audited_version": task.get("cadclaw_version"),
        "public_gate_status": "pass",
        "private_requested_placement_check": "required_separately",
    }
    if any(report.get(key) != value for key, value in expected_scalars.items()):
        raise L4EvidenceError("public invariant report does not match the frozen task")

    artifacts = report.get("input_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "baseline_sha256", "changed_sha256"
    }:
        raise L4EvidenceError("public invariant artifact binding is invalid")
    if artifacts != {
        "baseline_sha256": run.get("baseline_artifact_sha256"),
        "changed_sha256": run.get("artifact_sha256"),
    }:
        raise L4EvidenceError("public invariant report is bound to different STEP artifacts")

    counts = report.get("counts")
    if not isinstance(counts, dict) or set(counts) != {
        "baseline_renderable_shapes", "changed_renderable_shapes",
        "matched_invariant_shapes", "missing_baseline_shapes",
        "unmatched_changed_shapes",
    }:
        raise L4EvidenceError("public invariant counts are invalid")
    values = list(counts.values())
    if any(type(value) is not int or value < 0 for value in values):
        raise L4EvidenceError("public invariant counts are invalid")
    if not (
        counts["changed_renderable_shapes"] == counts["baseline_renderable_shapes"] + 1
        and counts["matched_invariant_shapes"] == counts["baseline_renderable_shapes"]
        and counts["missing_baseline_shapes"] == 0
        and counts["unmatched_changed_shapes"] == 1
    ):
        raise L4EvidenceError("public invariant counts do not establish a passing delta")

    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(EXPECTED_CHECK_IDS):
        raise L4EvidenceError("public invariant checks are invalid")
    check_ids: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "status"}:
            raise L4EvidenceError("public invariant checks are invalid")
        check_id = check.get("id")
        if not isinstance(check_id, str):
            raise L4EvidenceError("public invariant checks are invalid")
        check_ids.add(check_id)
        if check.get("status") != "pass":
            raise L4EvidenceError("public invariant report contains a failing check")
    if check_ids != EXPECTED_CHECK_IDS:
        raise L4EvidenceError("public invariant checks are invalid")
    if report.get("tolerances") != FROZEN_TOLERANCES:
        raise L4EvidenceError("public invariant tolerances do not match the frozen gate")

    runtime = report.get("runtime_attestation")
    if not isinstance(runtime, dict) or set(runtime) != {
        "cadclaw_version", "cadclaw_commit", "cadclaw_provenance",
        "cadquery_version", "cadquery_ocp_version",
    }:
        raise L4EvidenceError("public invariant runtime attestation is invalid")
    if runtime.get("cadclaw_provenance") not in {
        "vcs-direct-url", "clean-local-git-checkout"
    }:
        raise L4EvidenceError("public invariant runtime attestation is invalid")
    for report_field, task_field in (
        ("cadclaw_version", "cadclaw_version"),
        ("cadclaw_commit", "cadclaw_commit"),
        ("cadquery_version", "cadquery_version"),
        ("cadquery_ocp_version", "cadquery_ocp_version"),
    ):
        if runtime.get(report_field) != task.get(task_field):
            raise L4EvidenceError("public invariant runtime does not match the frozen task")

    source = report.get("gate_source")
    expected_source = {
        "implementation_sha256": task.get("invariant_gate_implementation_sha256"),
        "requirements_sha256": task.get("invariant_gate_requirements_sha256"),
    }
    if source != expected_source:
        raise L4EvidenceError("public invariant source does not match the frozen task")
    if run.get("invariant_gate_implementation_sha256") != expected_source[
        "implementation_sha256"
    ] or run.get("invariant_gate_requirements_sha256") != expected_source[
        "requirements_sha256"
    ]:
        raise L4EvidenceError("run provenance does not bind the frozen invariant source")

    if report.get("limitations") != EXPECTED_LIMITATIONS:
        raise L4EvidenceError("public invariant limitations do not match the frozen gate")
    if report.get("publication_requirement") != EXPECTED_PUBLICATION_REQUIREMENT:
        raise L4EvidenceError("public invariant publication boundary does not match the frozen gate")


def _validate_requested_report(report: dict, run: dict, task: dict) -> None:
    _require_exact_keys(
        report,
        {
            "schema", "task", "task_revision", "scoring_version",
            "grade_method", "requested_change_status", "artifact_sha256",
            "attestation",
        },
        "requested-change",
    )
    expected = {
        "schema": REQUESTED_CHANGE_SCHEMA,
        "task": "L4-ECO",
        "task_revision": task.get("task_revision"),
        "scoring_version": task.get("scoring_version"),
        "grade_method": task.get("requested_change_grade_method"),
        "requested_change_status": "pass",
        "artifact_sha256": run.get("artifact_sha256"),
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise L4EvidenceError("requested-change report does not match the frozen run")
    revision = task.get("gated_distribution_revision")
    if not isinstance(revision, str) or not _IMMUTABLE_REVISION_RE.fullmatch(revision):
        raise L4EvidenceError("the task has no immutable gated answer-key revision")
    attestation = report.get("attestation")
    expected_attestation = {
        "source": "gated-dataset-immutable-readback",
        "dataset_id": task.get("gated_dataset_id"),
        "gated_distribution_revision": revision,
        "grade_report_path": run.get("gated_requested_grade_report_path"),
        "grade_report_sha256": run.get("gated_requested_grade_report_sha256"),
        "reference_step_sha256": task.get("answer_key_step_sha256"),
        "placement_spec_sha256": task.get("answer_key_spec_sha256"),
    }
    if attestation != expected_attestation:
        raise L4EvidenceError("requested-change report is not bound to the gated readback")
    gated_path = run.get("gated_requested_grade_report_path")
    if (
        not isinstance(gated_path, str)
        or not gated_path.strip()
        or Path(gated_path).is_absolute()
        or ".." in Path(gated_path).parts
    ):
        raise L4EvidenceError("gated requested-change report path is invalid")
    if not _SHA256_RE.fullmatch(str(run.get("gated_requested_grade_report_sha256", ""))):
        raise L4EvidenceError("gated requested-change report digest is malformed")
    for field, expected_value in (
        ("gated_distribution_revision", revision),
        ("answer_key_step_sha256", task.get("answer_key_step_sha256")),
        ("answer_key_spec_sha256", task.get("answer_key_spec_sha256")),
    ):
        if run.get(field) != expected_value:
            raise L4EvidenceError("run provenance is not bound to the frozen answer key")


def _fetch_gated_report_bytes(
    dataset_id: str,
    revision: str,
    relative_path: str,
) -> bytes:
    """Read one report from an exact gated Hugging Face revision."""
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise L4EvidenceError("gated dataset identity is invalid")
    if not _IMMUTABLE_REVISION_RE.fullmatch(revision):
        raise L4EvidenceError("gated dataset revision is not immutable")
    if "\\" in relative_path:
        raise L4EvidenceError("gated requested-change report path is invalid")
    posix_path = PurePosixPath(relative_path)
    if (
        not relative_path
        or posix_path.is_absolute()
        or any(part in {"", ".", ".."} for part in posix_path.parts)
        or posix_path.as_posix() != relative_path
    ):
        raise L4EvidenceError("gated requested-change report path is invalid")
    token = os.environ.get(GATED_READ_TOKEN_ENV)
    if not token:
        raise L4EvidenceError("authenticated gated readback credential is unavailable")
    if len(token) > 4096 or any(ord(char) < 0x20 or ord(char) == 0x7f for char in token):
        raise L4EvidenceError("authenticated gated readback credential is malformed")
    quoted_dataset = urllib.parse.quote(dataset_id, safe="/")
    quoted_path = urllib.parse.quote(relative_path, safe="/")
    url = (
        f"https://huggingface.co/datasets/{quoted_dataset}/resolve/"
        f"{revision}/{quoted_path}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "MARB-L4-evidence-validator/0.12",
        },
        method="GET",
    )
    class _StripCrossHostCredential(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            redirected = super().redirect_request(
                req, fp, code, msg, headers, newurl
            )
            if (
                redirected is not None
                and urllib.parse.urlsplit(req.full_url).netloc.lower()
                != urllib.parse.urlsplit(newurl).netloc.lower()
            ):
                redirected.remove_header("Authorization")
            return redirected

    opener = urllib.request.build_opener(_StripCrossHostCredential())
    try:
        with opener.open(request, timeout=30) as response:
            payload = response.read(MAX_REPORT_BYTES + 1)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise L4EvidenceError("authenticated gated readback failed") from exc
    if not payload or len(payload) > MAX_REPORT_BYTES:
        raise L4EvidenceError("gated requested-change report size is invalid")
    return payload


def _read_gated_requested_report(run: dict, task: dict) -> dict:
    if task.get("gated_read_credential_env") != GATED_READ_TOKEN_ENV:
        raise L4EvidenceError("gated readback credential contract is invalid")
    if (
        task.get("gated_requested_change_grade_schema")
        != GATED_REQUESTED_CHANGE_SCHEMA
    ):
        raise L4EvidenceError("gated requested-change schema contract is invalid")
    dataset_id = task.get("gated_dataset_id")
    revision = task.get("gated_distribution_revision")
    relative_path = run.get("gated_requested_grade_report_path")
    expected_sha256 = run.get("gated_requested_grade_report_sha256")
    if not isinstance(dataset_id, str) or not isinstance(revision, str):
        raise L4EvidenceError("gated readback identity is incomplete")
    if not isinstance(relative_path, str) or not isinstance(expected_sha256, str):
        raise L4EvidenceError("gated requested-change report binding is incomplete")
    payload = _fetch_gated_report_bytes(dataset_id, revision, relative_path)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise L4EvidenceError("gated requested-change report digest does not match readback")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise L4EvidenceError("gated requested-change report is not valid JSON") from exc
    if not isinstance(document, dict):
        raise L4EvidenceError("gated requested-change report must be an object")
    return document


def _validate_gated_requested_report(report: dict, run: dict, task: dict) -> None:
    _require_exact_keys(
        report,
        {
            "schema", "task", "task_revision", "scoring_version", "run_id",
            "eco_request_id", "grade_method", "requested_change_status",
            "artifact_sha256", "reference_step_sha256",
            "placement_spec_sha256",
        },
        "gated requested-change",
    )
    expected = {
        "schema": task.get("gated_requested_change_grade_schema"),
        "task": "L4-ECO",
        "task_revision": task.get("task_revision"),
        "scoring_version": task.get("scoring_version"),
        "run_id": run.get("run_id"),
        "eco_request_id": task.get("eco_request_id"),
        "grade_method": task.get("requested_change_grade_method"),
        "requested_change_status": "pass",
        "artifact_sha256": run.get("artifact_sha256"),
        "reference_step_sha256": task.get("answer_key_step_sha256"),
        "placement_spec_sha256": task.get("answer_key_spec_sha256"),
    }
    if report != expected:
        raise L4EvidenceError(
            "gated requested-change report does not match the frozen run"
        )


def validate_l4_evidence(run: dict, task: dict, repo_root: Path) -> None:
    """Recompute the public gate and authenticate the private gated grade."""
    if task.get("invariant_validation_mode") != INVARIANT_VALIDATION_MODE:
        raise L4EvidenceError("public invariant validation contract is invalid")
    validate_l4_attempt_files(run, repo_root)
    baseline_path, _ = _resolve_bound_file(
        repo_root,
        run.get("baseline_step"),
        run.get("baseline_artifact_sha256"),
        MAX_ARTIFACT_BYTES,
    )
    changed_path, _ = _resolve_bound_file(
        repo_root, run.get("step"), run.get("artifact_sha256"), MAX_ARTIFACT_BYTES
    )
    _resolve_bound_file(
        repo_root,
        run.get("changed_editable_source"),
        run.get("changed_editable_source_sha256"),
        MAX_ARTIFACT_BYTES,
    )
    invariant = _read_bound_json(
        repo_root, run.get("invariant_report"), run.get("invariant_report_sha256")
    )
    requested = _read_bound_json(
        repo_root,
        run.get("requested_change_report"),
        run.get("requested_change_report_sha256"),
    )
    _validate_invariant_report(invariant, run, task)
    try:
        recomputed_invariant = run_gate(baseline_path, changed_path)
    except EcoInvariantError as exc:
        raise L4EvidenceError(
            "public invariant could not be recomputed from the registered STEP files"
        ) from exc
    _validate_invariant_report(recomputed_invariant, run, task)
    if recomputed_invariant != invariant:
        raise L4EvidenceError(
            "public invariant report does not match trusted recomputation"
        )
    _validate_requested_report(requested, run, task)
    gated_requested = _read_gated_requested_report(run, task)
    _validate_gated_requested_report(gated_requested, run, task)


def validate_l4_attempt_files(run: dict, repo_root: Path) -> None:
    """Authenticate the versioned inputs and log required for every attempt."""
    for path_field, hash_field, maximum in (
        ("baseline_step", "baseline_artifact_sha256", MAX_ARTIFACT_BYTES),
        (
            "baseline_editable_source",
            "baseline_editable_source_sha256",
            MAX_ARTIFACT_BYTES,
        ),
        ("run_log", "run_log_sha256", MAX_RUN_LOG_BYTES),
    ):
        _resolve_bound_file(
            repo_root, run.get(path_field), run.get(hash_field), maximum
        )
