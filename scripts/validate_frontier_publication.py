#!/usr/bin/env python3
"""Fail closed when a published MARB board violates run-provenance policy.

The board is curated, but its run counts and seed identifiers must agree with
the run registry. Existing frontier observations are grandfathered only by an
exact cell-id allowlist. Every later frontier cell must report at least three
attempted *and* three graded independent runs as a median with spread; the same
minimum applies to every post-policy non-reference row so a track label cannot
bypass the gate.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import date
from pathlib import Path
from typing import Any


class PublicationPolicyError(ValueError):
    """Raised when a board is not eligible for publication."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationPolicyError(f"cannot read valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PublicationPolicyError(f"expected a JSON object: {path}")
    return data


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PublicationPolicyError(f"{field} must be YYYY-MM-DD") from exc


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _nested(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


GRADE_METRICS = (
    ("gap_median_mm", "gap_spread", "gap_median_mm", "gap.overall.median_mm"),
    ("orient_pct", "orient_spread", "orient_aligned_pct", "orientation.aligned_pct"),
    ("pos_rel_median_mm", "pos_spread", "pos_rel_mm", "position.relative.median_mm"),
)

DISPLAY_METRICS = (
    ("gap_median_mm", "gap_spread", "gap_display", "mm"),
    ("orient_pct", "orient_spread", "orient_display", "%"),
    ("pos_rel_median_mm", "pos_spread", "pos_display", "mm"),
)

_DISPLAY_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:±\s*([+-]?\d+(?:\.\d+)?))?\s*(mm|%)\s*$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _close(actual: Any, expected: Any, tolerance: float) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def _validate_displays(row: dict[str, Any], reporting: dict[str, Any],
                       label: str, errors: list[str]) -> None:
    repeat = reporting.get("mode") == "repeat-run"
    for value_field, spread_field, display_field, expected_unit in DISPLAY_METRICS:
        display = str(row.get(display_field, ""))
        parsed = _DISPLAY_RE.fullmatch(display)
        _require(parsed is not None,
                 f"{label}: {display_field} has an invalid published format", errors)
        if parsed is None:
            continue
        center, spread, unit = parsed.groups()
        _require(unit == expected_unit,
                 f"{label}: {display_field} has the wrong unit", errors)
        _require(_close(center, row.get(value_field), 0.51),
                 f"{label}: {display_field} center does not match {value_field}", errors)
        if repeat:
            _require(spread is not None,
                     f"{label}: {display_field} must show spread", errors)
            _require(spread is not None and _close(spread, row.get(spread_field), 0.51),
                     f"{label}: {display_field} spread does not match {spread_field}", errors)
        else:
            _require(spread is None,
                     f"{label}: non-repeat {display_field} must not imply a spread", errors)


def load_grade_sources(board: dict[str, Any], repo_root: Path) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for row in board.get("rows") or []:
        source = row.get("grade_source")
        if not source or source in sources:
            continue
        sources[source] = _read_json(repo_root / source)
    return sources


def _validate_grade_source(row: dict[str, Any], reporting: dict[str, Any],
                           grade_sources: dict[str, dict[str, Any]],
                           label: str, minimum: int, errors: list[str]) -> None:
    source_name = row.get("grade_source")
    grade_name = row.get("grade_cell")
    _require(bool(source_name) and bool(grade_name),
             f"{label}: missing grade_source or grade_cell", errors)
    source = grade_sources.get(source_name)
    _require(source is not None, f"{label}: grade source was not loaded", errors)
    if source is None:
        return
    grade = (source.get("runs") or {}).get(grade_name)
    _require(isinstance(grade, dict), f"{label}: grade_cell not found in source", errors)
    if not isinstance(grade, dict):
        return

    expected_n = grade.get("_n", 1)
    _require(expected_n == reporting.get("n_graded"),
             f"{label}: n_graded does not match grade source", errors)
    aggregate = grade.get("_agg")
    if reporting.get("mode") == "repeat-run":
        _require(isinstance(aggregate, dict) and bool(aggregate),
                 f"{label}: repeat-run grade source must include aggregate statistics", errors)
    for board_value, board_spread, aggregate_key, flat_path in GRADE_METRICS:
        if aggregate:
            stats = aggregate.get(aggregate_key) or {}
            values = stats.get("values") or []
            sample_n = stats.get("n")
            _require(isinstance(sample_n, int) and sample_n == len(values),
                     f"{label}: {aggregate_key} sample count is inconsistent", errors)
            if row.get("track") == "frontier" and reporting.get("mode") == "repeat-run":
                _require(sample_n >= minimum,
                         f"{label}: {aggregate_key} requires at least {minimum} samples", errors)
            if values:
                calc_median = round(statistics.median(values), 2)
                calc_std = round(statistics.pstdev(values), 2)
                _require(_close(stats.get("median"), calc_median, 0.011),
                         f"{label}: {aggregate_key} median is not reproducible", errors)
                _require(_close(stats.get("std"), calc_std, 0.011),
                         f"{label}: {aggregate_key} spread is not reproducible", errors)
            expected_value = stats.get("median")
            expected_spread = stats.get("std")
            if reporting.get("mode") == "repeat-run":
                _require(board_spread in row,
                         f"{label}: board omits numeric {board_spread}", errors)
                if board_spread in row and expected_spread is not None:
                    _require(_close(row[board_spread], expected_spread, 0.011),
                             f"{label}: board {board_spread} does not match grade source", errors)
        else:
            expected_value = _nested(grade, flat_path)
        if expected_value is not None:
            _require(_close(row.get(board_value), expected_value, 0.51),
                     f"{label}: board {board_value} does not match grade source", errors)


def _validate_independent_runs(
    row: dict[str, Any],
    reporting: dict[str, Any],
    matched: list[dict[str, Any]],
    grade_sources: dict[str, dict[str, Any]] | None,
    label: str,
    errors: list[str],
) -> None:
    """Require auditable attempt and artifact identities for post-policy cells."""
    attempted = reporting.get("n_attempted")
    graded = reporting.get("n_graded")
    run_ids = [run.get("run_id") for run in matched]
    _require(
        len(run_ids) == attempted
        and all(isinstance(run_id, str) and run_id.strip() for run_id in run_ids)
        and len(set(run_ids)) == len(run_ids),
        f"{label}: post-policy attempts require unique nonempty run_id values",
        errors,
    )
    _require(
        {run.get("track") for run in matched} == {row.get("track")},
        f"{label}: board track does not match registry runs",
        errors,
    )
    outcomes = [run.get("outcome") for run in matched]
    _require(
        all(outcome in {"graded", "failed-export"} for outcome in outcomes),
        f"{label}: each post-policy attempt needs a graded or failed-export outcome",
        errors,
    )
    graded_runs = [run for run in matched if run.get("outcome") == "graded"]
    _require(
        len(graded_runs) == graded,
        f"{label}: registry graded outcomes do not match n_graded",
        errors,
    )

    log_paths = [run.get("run_log") for run in matched]
    log_hashes = [run.get("run_log_sha256") for run in matched]
    _require(
        all(isinstance(path, str) and path.strip() for path in log_paths)
        and len(set(log_paths)) == len(log_paths),
        f"{label}: attempts require distinct nonempty run_log paths",
        errors,
    )
    _require(
        all(isinstance(value, str) and _SHA256_RE.fullmatch(value) for value in log_hashes)
        and len(set(log_hashes)) == len(log_hashes),
        f"{label}: attempts require distinct lowercase SHA-256 run-log digests",
        errors,
    )

    step_paths = [run.get("step") for run in graded_runs]
    artifact_hashes = [run.get("artifact_sha256") for run in graded_runs]
    _require(
        all(isinstance(path, str) and path.strip() for path in step_paths)
        and len(set(step_paths)) == len(step_paths),
        f"{label}: graded runs require distinct nonempty STEP paths",
        errors,
    )
    _require(
        all(isinstance(value, str) and _SHA256_RE.fullmatch(value)
            for value in artifact_hashes),
        f"{label}: graded runs require lowercase SHA-256 artifact digests",
        errors,
    )

    if grade_sources is None:
        return
    source = grade_sources.get(row.get("grade_source"))
    grade_record = None
    if isinstance(source, dict):
        grade_record = (source.get("runs") or {}).get(row.get("grade_cell"))
    provenance = grade_record.get("_provenance") if isinstance(grade_record, dict) else None
    _require(
        isinstance(provenance, list) and len(provenance) == graded,
        f"{label}: grade source must identify every graded run",
        errors,
    )
    if not isinstance(provenance, list):
        return
    registry_by_id = {run.get("run_id"): run for run in graded_runs}
    provenance_ids = [item.get("run_id") for item in provenance if isinstance(item, dict)]
    _require(
        len(provenance_ids) == graded
        and len(set(provenance_ids)) == len(provenance_ids)
        and set(provenance_ids) == set(registry_by_id),
        f"{label}: grade-source run identities do not match graded registry runs",
        errors,
    )
    for item in provenance:
        if not isinstance(item, dict) or item.get("run_id") not in registry_by_id:
            continue
        registered = registry_by_id[item["run_id"]]
        for field in ("step", "artifact_sha256", "run_log_sha256"):
            _require(
                item.get(field) == registered.get(field),
                f"{label}: grade-source {field} does not match registry provenance",
                errors,
            )


def validate(board: dict[str, Any], registry: dict[str, Any],
             grade_sources: dict[str, dict[str, Any]] | None = None) -> list[str]:
    errors: list[str] = []
    policy = board.get("publication_policy") or {}
    effective = _parse_date(policy.get("effective_date"), "publication_policy.effective_date")
    minimum = policy.get("new_frontier_min_seeded_runs")
    _require(isinstance(minimum, int) and minimum >= 3,
             "new_frontier_min_seeded_runs must be an integer >= 3", errors)
    legacy_allowlist = policy.get("legacy_frontier_cell_ids") or []
    _require(len(legacy_allowlist) == len(set(legacy_allowlist)),
             "legacy frontier allowlist contains duplicate cell ids", errors)
    reference_allowlist = policy.get("reference_cell_ids") or []
    _require(len(reference_allowlist) == len(set(reference_allowlist)),
             "reference allowlist contains duplicate cell ids", errors)
    registry_policy = registry.get("publication_policy") or {}
    _require(registry_policy.get("effective_date") == policy.get("effective_date"),
             "board and registry policy effective dates differ", errors)
    _require(registry_policy.get("frontier_min_distinct_runs") == minimum,
             "board and registry frontier minimums differ", errors)
    _require(registry_policy.get("summary") == policy.get("center"),
             "board and registry center policies differ", errors)
    _require(registry_policy.get("spread") == policy.get("spread"),
             "board and registry spread policies differ", errors)
    _require(set(registry_policy.get("legacy_frontier_cell_ids") or []) == set(legacy_allowlist),
             "board and registry legacy allowlists differ", errors)
    _require(set(registry_policy.get("reference_cell_ids") or []) == set(reference_allowlist),
             "board and registry reference allowlists differ", errors)

    legacy_defaults = registry.get("legacy_defaults") or {}
    legacy_task = legacy_defaults.get("task", "L1-ASSEMBLE")
    legacy_version = legacy_defaults.get(
        "scoring_version", registry.get("marb_scoring_version", "v0.9")
    )
    legacy_cell_map = registry.get("legacy_cell_map") or {}
    _require(isinstance(legacy_cell_map, dict),
             "legacy_cell_map must be an object", errors)
    if not isinstance(legacy_cell_map, dict):
        legacy_cell_map = {}
    mapped_legacy_cells = {
        key: value for key, value in legacy_cell_map.items()
        if not str(key).startswith("_")
    }
    _require(len(mapped_legacy_cells.values()) == len(set(mapped_legacy_cells.values())),
             "legacy_cell_map display labels must be unique", errors)
    registry_runs = registry.get("runs") or []
    seen_cells: set[str] = set()
    actual_legacy: set[str] = set()
    actual_references: set[str] = set()
    used_legacy_mappings: set[str] = set()

    for row in board.get("rows") or []:
        cell_id = row.get("cell_id")
        label = cell_id or row.get("model") or "<unnamed>"
        _require(bool(cell_id), f"{label}: missing stable cell_id", errors)
        _require(cell_id not in seen_cells, f"{label}: duplicate cell_id", errors)
        seen_cells.add(cell_id)
        _require(bool(row.get("task")), f"{label}: missing task", errors)
        _require(bool(row.get("spec_version")), f"{label}: missing spec_version", errors)
        published = _parse_date(row.get("published_date"), f"{label}.published_date")

        reporting = row.get("reporting") or {}
        attempted = reporting.get("n_attempted")
        graded = reporting.get("n_graded")
        seeds = reporting.get("seed_ids") or []
        _require(isinstance(attempted, int) and attempted >= 1,
                 f"{label}: n_attempted must be >= 1", errors)
        _require(isinstance(graded, int) and 1 <= graded <= attempted,
                 f"{label}: n_graded must be between 1 and n_attempted", errors)
        _require(len(seeds) == len(set(seeds)), f"{label}: seed_ids must be unique", errors)
        _require(len(seeds) == attempted,
                 f"{label}: seed_ids count must equal n_attempted", errors)
        _validate_displays(row, reporting, label, errors)
        if grade_sources is not None:
            _validate_grade_source(row, reporting, grade_sources, label, minimum, errors)

        track = row.get("track")
        _require(track in {"frontier", "local", "sighted", "reference"},
                 f"{label}: unsupported track", errors)
        matched: list[dict[str, Any]] = []
        if track != "reference":
            registry_cell = row.get("registry_cell")
            task = row.get("task")
            legacy_cell = mapped_legacy_cells.get(cell_id)
            matched = []
            for run in registry_runs:
                run_task = run.get("task", legacy_task)
                if run_task != task:
                    continue
                if run.get("cell_id") == cell_id:
                    matched.append(run)
                elif "cell_id" not in run and legacy_cell == run.get("cell"):
                    matched.append(run)
            if matched and all("cell_id" not in run for run in matched):
                used_legacy_mappings.add(cell_id)
                _require(registry_cell == legacy_cell,
                         f"{label}: registry_cell does not match the stable legacy mapping", errors)
            matched_labels = {run.get("cell") for run in matched}
            _require(matched_labels == {registry_cell},
                     f"{label}: registry display label does not match stable cell identity", errors)
            registry_seeds = [str(run.get("seed")) for run in matched]
            _require(len(matched) == attempted,
                     f"{label}: board n_attempted does not match registry", errors)
            _require(set(registry_seeds) == set(str(seed) for seed in seeds),
                     f"{label}: board seed_ids do not match registry", errors)
            run_versions = {
                run.get("scoring_version", legacy_version if "task" not in run else
                        (registry.get("tasks", {}).get(task, {}) or {}).get("scoring_version"))
                for run in matched
            }
            _require(run_versions == {row.get("spec_version")},
                     f"{label}: board spec_version does not match registry runs", errors)
            identity_fields = (
                ("model_id", "model.id"),
                ("driver_tool", "driver.tool"),
                ("driver_version", "driver.version"),
            )
            for board_field, run_field in identity_fields:
                expected_identity = row.get(board_field)
                _require(bool(expected_identity),
                         f"{label}: missing board {board_field}", errors)
                identities = {_nested(run, run_field) for run in matched}
                _require(identities == {expected_identity},
                         f"{label}: board {board_field} does not match registry runs", errors)
            model_names = {_nested(run, "model.name") for run in matched}
            board_model = str(row.get("model", "")).casefold()
            _require(
                len(model_names) == 1
                and None not in model_names
                and board_model.startswith(str(next(iter(model_names))).casefold()),
                f"{label}: published model label does not match registry runs",
                errors,
            )
            board_tool = str(row.get("tool", "")).casefold()
            driver_tool = str(row.get("driver_tool", "")).casefold()
            _require(
                bool(board_tool)
                and (board_tool.startswith(driver_tool) or driver_tool.endswith(board_tool)),
                f"{label}: published tool label does not match registry runs",
                errors,
            )
            if matched and all("builder_metrics" in run for run in matched):
                loadable = sum(bool(run["builder_metrics"].get("loadable")) for run in matched)
                _require(loadable == graded,
                         f"{label}: n_graded does not match registry loadable count", errors)

        mode = reporting.get("mode")
        post_policy_cell = track != "reference" and published >= effective
        if post_policy_cell:
            _require(mode == "repeat-run",
                     f"{label}: every post-policy non-reference cell must use repeat-run reporting", errors)
            _require(attempted >= minimum and graded >= minimum,
                     f"{label}: post-policy cell requires at least {minimum} attempted and graded runs", errors)
            _require(reporting.get("center") == policy.get("center") == "median",
                     f"{label}: post-policy center must be median", errors)
            _require(reporting.get("spread") == policy.get("spread"),
                     f"{label}: post-policy spread must match board policy", errors)
            _require(reporting.get("seed_basis") in {"provider-seed", "independent-run-ordinal"},
                     f"{label}: post-policy seed_basis is missing or unsupported", errors)
            for field in (
                "model.id", "driver.tool", "driver.version", "kit_version",
                "prompt_variant", "cohort_id",
            ):
                values = {_nested(run, field) for run in matched}
                _require(len(values) == 1 and None not in values,
                         f"{label}: post-policy cell mixes or omits {field}", errors)
            _validate_independent_runs(
                row, reporting, matched, grade_sources, label, errors
            )
        if track == "frontier":
            if mode == "legacy-single-run":
                actual_legacy.add(cell_id)
                _require(cell_id in legacy_allowlist,
                         f"{label}: legacy single run is not allowlisted", errors)
                _require(attempted == graded == 1,
                         f"{label}: legacy single run must be exactly 1/1", errors)
                _require(published < effective,
                         f"{label}: legacy single run must predate policy", errors)
            elif mode == "repeat-run":
                _require(published >= effective,
                         f"{label}: repeat-run frontier publication date predates policy", errors)
                _require(attempted >= minimum and graded >= minimum,
                         f"{label}: new frontier cell requires at least {minimum} attempted and graded runs", errors)
                _require(reporting.get("center") == policy.get("center") == "median",
                         f"{label}: repeat-run center must be median", errors)
                _require(reporting.get("spread") == policy.get("spread"),
                         f"{label}: repeat-run spread must match board policy", errors)
                _require(reporting.get("seed_basis") in {"provider-seed", "independent-run-ordinal"},
                         f"{label}: repeat-run seed_basis is missing or unsupported", errors)
                for field in ("gap_display", "orient_display", "pos_display"):
                    _require("±" in str(row.get(field, "")),
                             f"{label}: {field} must show spread", errors)
                for field in (
                    "model.id", "driver.tool", "driver.version", "kit_version",
                    "prompt_variant", "cohort_id",
                ):
                    values = {_nested(run, field) for run in matched}
                    _require(len(values) == 1 and None not in values,
                             f"{label}: repeat-run cell mixes or omits {field}", errors)
            else:
                errors.append(f"{label}: frontier reporting mode must be legacy-single-run or repeat-run")
        elif mode == "repeat-run":
            _require(reporting.get("center") == "median",
                     f"{label}: repeat-run center must be median", errors)
            _require(bool(reporting.get("spread")),
                     f"{label}: repeat-run spread is required", errors)
            for field in ("gap_display", "orient_display", "pos_display"):
                _require("±" in str(row.get(field, "")),
                         f"{label}: {field} must show spread", errors)
        elif track == "reference":
            actual_references.add(cell_id)
            _require(cell_id in reference_allowlist,
                     f"{label}: reference row is not allowlisted", errors)
            _require(mode == "reference", f"{label}: reference row mode must be reference", errors)
        elif mode == "legacy-single-run":
            errors.append(f"{label}: legacy-single-run mode is reserved for allowlisted frontier rows")

    _require(set(legacy_allowlist) == actual_legacy,
             "legacy frontier allowlist must exactly match published legacy rows", errors)
    _require(set(reference_allowlist) == actual_references,
             "reference allowlist must exactly match published reference rows", errors)
    _require(set(mapped_legacy_cells) == used_legacy_mappings,
             "legacy_cell_map must exactly match published taskless registry cells", errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="hf/space/board.json")
    parser.add_argument("--registry", default="results/marb_runs.json")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    board = _read_json(Path(args.board))
    errors = validate(
        board,
        _read_json(Path(args.registry)),
        load_grade_sources(board, Path(args.repo_root)),
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Frontier publication policy: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
