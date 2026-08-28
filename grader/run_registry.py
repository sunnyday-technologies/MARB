"""Dependency-light loading and routing for MARB run registries."""
from __future__ import annotations

import json
import re
from pathlib import Path


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_L2_CHANGE_LOOP_FIELDS = (
    "task_definition",
    "task_definition_sha256",
    "kit",
    "kit_sha256",
    "prompt",
    "prompt_sha256",
    "driver_brief",
    "driver_brief_sha256",
    "baseline_step",
    "baseline_artifact_sha256",
    "baseline_editable_source",
    "baseline_editable_source_sha256",
    "changed_editable_source",
    "changed_editable_source_sha256",
    "change_request",
    "change_request_sha256",
    "change_request_id",
    "driver_continuity_id",
    "artifact_sha256",
    "run_log",
    "run_log_sha256",
)


def _validate_l2_change_loop(run: dict, task_cfg: dict) -> None:
    """Fail closed on incomplete same-driver change-loop provenance.

    Values are never included in an exception: a malformed manifest reports
    only the run identity and field names.
    """
    run_id = run.get("run_id", "<unnamed>")
    missing = [field for field in _L2_CHANGE_LOOP_FIELDS if not run.get(field)]
    if missing:
        raise ValueError(
            f"L2 change-loop run {run_id!r} is missing provenance fields: "
            + ", ".join(missing)
        )
    hash_fields = (
        "task_definition_sha256",
        "kit_sha256",
        "prompt_sha256",
        "driver_brief_sha256",
        "baseline_artifact_sha256",
        "baseline_editable_source_sha256",
        "changed_editable_source_sha256",
        "change_request_sha256",
        "artifact_sha256",
        "run_log_sha256",
    )
    malformed = [
        field for field in hash_fields
        if not _SHA256_RE.fullmatch(str(run.get(field, "")))
    ]
    if malformed:
        raise ValueError(
            f"L2 change-loop run {run_id!r} has malformed SHA-256 fields: "
            + ", ".join(malformed)
        )
    frozen_fields = (
        ("task_definition", "task_definition"),
        ("task_definition_sha256", "task_definition_sha256"),
        ("change_request", "change_request"),
        ("change_request_sha256", "change_request_sha256"),
        ("change_request_id", "change_request_id"),
        ("prompt", "prompt"),
        ("prompt_sha256", "prompt_sha256"),
    )
    mismatched = [
        run_field for run_field, task_field in frozen_fields
        if run.get(run_field) != task_cfg.get(task_field)
    ]
    if mismatched:
        raise ValueError(
            f"L2 change-loop run {run_id!r} does not match the frozen task: "
            + ", ".join(mismatched)
        )
    allowed_kits = task_cfg.get("allowed_kits") or {}
    if allowed_kits.get(run.get("kit")) != run.get("kit_sha256"):
        raise ValueError(
            f"L2 change-loop run {run_id!r} does not match a frozen kit"
        )
    allowed_briefs = task_cfg.get("allowed_driver_briefs") or {}
    if allowed_briefs.get(run.get("driver_brief")) != run.get("driver_brief_sha256"):
        raise ValueError(
            f"L2 change-loop run {run_id!r} does not match a frozen driver brief"
        )
    if run["baseline_step"] == run["step"]:
        raise ValueError(
            f"L2 change-loop run {run_id!r} must preserve distinct before/after STEP paths"
        )
    if run["baseline_editable_source"] == run["changed_editable_source"]:
        raise ValueError(
            f"L2 change-loop run {run_id!r} must preserve distinct before/after source paths"
        )


def load_manifest(path: Path, task_id: str | None = None,
                  scoring_version: str | None = None):
    """Read a v1/v2 run registry without blending tasks or stable cell ids.

    Taskless legacy records inherit ``legacy_defaults.task`` (or
    ``L1-ASSEMBLE`` when that compatibility field is absent). V2 records are
    filtered by task and grouped by ``cell_id``. Two stable ids may not reuse
    one display label inside a task.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    legacy_defaults = manifest.get("legacy_defaults") or {}
    legacy_task = legacy_defaults.get("task", "L1-ASSEMBLE")
    default_task = manifest.get("default_task", legacy_task)
    selected_task = task_id or default_task
    legacy_version = legacy_defaults.get(
        "scoring_version", manifest.get("marb_scoring_version", "v0.9")
    )
    tasks = manifest.get("tasks", {})
    if tasks and selected_task not in tasks:
        raise ValueError(f"unknown task in run registry: {selected_task}")
    task_cfg = tasks.get(selected_task, {})
    if selected_task != legacy_task:
        missing_routes = [
            field for field in ("reference_step", "spec")
            if not task_cfg.get(field)
        ]
        if missing_routes:
            fields = ", ".join(missing_routes)
            raise ValueError(
                f"task {selected_task!r} must declare task-local routing: {fields}"
            )

    by_id: dict[str, list] = {}
    display_by_id: dict[str, str] = {}
    included_versions: set[str] = set()
    for run in manifest.get("runs", []):
        run_task = run.get("task", legacy_task)
        if run_task != selected_task:
            continue
        if "task" in run and not run.get("cell_id"):
            raise ValueError(
                f"task-aware run {run.get('run_id', '<unnamed>')!r} is missing cell_id"
            )
        if task_cfg.get("provenance_contract") == "l2_change_loop.v1":
            _validate_l2_change_loop(run, task_cfg)
        run_version = run.get("scoring_version")
        if run_version is None:
            run_version = legacy_version if "task" not in run else task_cfg.get(
                "scoring_version", manifest.get("marb_scoring_version", "v0.9")
            )
        if scoring_version and run_version != scoring_version:
            continue
        included_versions.add(run_version)
        display = run["cell"]
        cell_id = run.get("cell_id", display)
        previous = display_by_id.setdefault(cell_id, display)
        if previous != display:
            raise ValueError(f"cell_id {cell_id!r} has inconsistent display labels")
        provenance_fields = (
            "run_id", "cell_id", "task", "seed", "model", "driver",
            "kit_version", "prompt_variant", "cohort_id", "track",
            "outcome", "artifact_sha256", "run_log", "run_log_sha256",
            "task_definition", "task_definition_sha256",
            "kit", "kit_sha256", "prompt", "prompt_sha256",
            "driver_brief", "driver_brief_sha256",
            "baseline_step", "baseline_artifact_sha256",
            "baseline_editable_source", "baseline_editable_source_sha256",
            "changed_editable_source", "changed_editable_source_sha256",
            "change_request", "change_request_sha256", "change_request_id",
            "driver_continuity_id", "timing", "tokens", "effort",
        )
        provenance = {key: run[key] for key in provenance_fields if key in run}
        provenance["step"] = run["step"]
        provenance.setdefault("task", run_task)
        provenance.setdefault("cell_id", cell_id)
        provenance.setdefault("scoring_version", run_version)
        by_id.setdefault(cell_id, []).append((Path(run["step"]), provenance))

    if not by_id:
        selector = f"task {selected_task!r}"
        if scoring_version:
            selector += f" and scoring version {scoring_version!r}"
        raise ValueError(f"no runs match {selector}")

    cells: dict[str, list] = {}
    for cell_id, items in by_id.items():
        display = display_by_id[cell_id]
        if display in cells:
            raise ValueError(
                f"task {selected_task!r} reuses display label {display!r} "
                "for more than one cell_id"
            )
        cells[display] = items

    reference_step = task_cfg.get("reference_step", manifest.get("reference_step"))
    spec = task_cfg.get("spec", manifest.get("spec"))
    if len(included_versions) > 1:
        versions = ", ".join(sorted(included_versions))
        raise ValueError(
            f"task {selected_task!r} contains multiple scoring versions ({versions}); "
            "select one with --scoring-version"
        )
    selected_version = next(iter(included_versions), None)
    selected_version = selected_version or scoring_version or task_cfg.get(
        "scoring_version", manifest.get("marb_scoring_version", "v0.9")
    )
    return (
        cells,
        manifest.get("grader_toolchain", {}),
        reference_step,
        spec,
        selected_task,
        selected_version,
    )


# Preserve the private helper name for existing internal callers.
_load_manifest = load_manifest
