"""Build self-digesting MARB cohort plans without executing benchmark runs.

This module is intentionally separate from the legacy execution harness.  It
does not import provider clients, inspect credentials, access the network,
launch subprocesses, create directories, or write files.  Its only CLI action
prints a deterministic plan to stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Sequence


PLAN_SCHEMA = "marb_cohort_plan.v1"
REGISTRY_PATH = "results/marb_runs.json"
MIN_SHA256 = re.compile(r"[0-9a-f]{64}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
IMMUTABLE_REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}[a-z0-9]|[a-z0-9]")
SAFE_SEED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
SAFE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}[A-Za-z0-9]|[A-Za-z0-9]")
SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+()-]{0,62}[A-Za-z0-9)]|[A-Za-z0-9]")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
PROTECTED_PATH_WORDS = re.compile(
    r"(?:^|[_-])(?:answer[_-]?key|private|credential|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)


TASKS: dict[str, dict[str, Any]] = {
    "L2-RESOLVE": {
        "contract": "l2_change_loop.v1",
        "rung": "L2",
        "scoring_version": "v0.11",
        "task_definition": "tasks/m3_crete_l2_resolve/task.yaml",
        "request_path": "tasks/m3_crete_l2_resolve/CHANGE_REQUEST.md",
        "request_path_key": "change_request",
        "request_hash_key": "change_request_sha256",
        "request_id_key": "change_request_id",
    },
    "L4-ECO": {
        "contract": "l4_eco.v1",
        "rung": "L4",
        "scoring_version": "v0.12",
        "task_definition": "tasks/m3_crete_l4_eco/task.yaml",
        "request_path": "tasks/m3_crete_l4_eco/ECO_REQUEST.md",
        "request_path_key": "eco_request",
        "request_hash_key": "eco_request_sha256",
        "request_id_key": "eco_request_id",
    },
}

DRIVERS: dict[str, dict[str, str]] = {
    "cadquery": {
        "tool": "CadQuery",
        "kit": "kits/m3_cadquery_blind_kit.zip",
        "kit_version": "v1.1",
        "brief": "prompts/CADQUERY_DRIVER_BRIEF.md",
        "editable_suffix": ".py",
    },
    "fusion": {
        "tool": "Autodesk Fusion",
        "kit": "kits/m3_fusion_blind_kit_v1.3.zip",
        "kit_version": "v1.3",
        "brief": "prompts/FUSION_DRIVER_BRIEF.md",
        "editable_suffix": ".f3d",
    },
}


class PlanError(ValueError):
    """A fail-closed cohort-plan validation error."""


def _fail(message: str) -> NoReturn:
    raise PlanError(message)


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    _fail("JSON contains a non-finite number")


def _decode_json(raw: bytes, label: str) -> tuple[Any, bytes]:
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(f"{label} must not contain a UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail(f"{label} is not valid UTF-8")
    canonical_text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        value = json.loads(
            canonical_text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"{label} is not valid JSON at line {exc.lineno}")
    return value, canonical_text.encode("utf-8")


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return encoded + (b"\n" if newline else b"")


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _repo_relative_parts(relative: str, label: str, *, public_only: bool = True) -> list[str]:
    if not isinstance(relative, str) or not relative or CONTROL.search(relative):
        _fail(f"{label} has an invalid repository path")
    if "\\" in relative or ":" in relative or relative.startswith("/"):
        _fail(f"{label} must be a normalized repository-relative POSIX path")
    if relative.startswith("//") or "//" in relative or relative.endswith("/"):
        _fail(f"{label} must be a normalized repository-relative POSIX path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{label} contains path traversal")
    if any(
        part.endswith((".", " "))
        or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        for part in parts
    ):
        _fail(f"{label} contains a Windows-ambiguous path component")
    if PurePosixPath(relative).as_posix() != relative:
        _fail(f"{label} is not normalized")
    if public_only and any(PROTECTED_PATH_WORDS.search(part) for part in parts):
        _fail(f"{label} crosses the public-input boundary")
    return parts


def _validated_repo_file(repo_root: Path, relative: str, label: str) -> Path:
    parts = _repo_relative_parts(relative, label)

    root = repo_root.resolve(strict=True)
    candidate = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if _is_link_like(cursor):
                _fail(f"{label} must not traverse a symlink or junction")
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        _fail(f"{label} does not exist")
    if root != resolved and root not in resolved.parents:
        _fail(f"{label} escapes the repository")
    try:
        mode = candidate.lstat().st_mode
    except OSError:
        _fail(f"{label} cannot be inspected")
    if not stat.S_ISREG(mode):
        _fail(f"{label} is not a regular file")
    return candidate


def _stable_read(path: Path, label: str) -> bytes:
    try:
        before = path.lstat()
        with path.open("rb") as handle:
            raw = handle.read()
        after = path.lstat()
    except OSError:
        _fail(f"{label} cannot be read")
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(raw) != after.st_size:
        _fail(f"{label} changed while it was being read")
    return raw


def _digest_public_input(
    repo_root: Path,
    relative: str,
    expected_sha256: str,
    label: str,
    *,
    text: bool,
) -> dict[str, Any]:
    if not isinstance(expected_sha256, str) or not MIN_SHA256.fullmatch(expected_sha256):
        _fail(f"{label} has a malformed frozen SHA-256")
    path = _validated_repo_file(repo_root, relative, label)
    raw = _stable_read(path, label)
    if text:
        if raw.startswith(b"\xef\xbb\xbf"):
            _fail(f"{label} must not contain a UTF-8 BOM")
        try:
            normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        except UnicodeDecodeError:
            _fail(f"{label} is not valid UTF-8")
        hashed = normalized
        hash_mode = "utf8-lf"
    else:
        hashed = raw
        hash_mode = "raw-bytes"
    digest = hashlib.sha256(hashed).hexdigest()
    if digest != expected_sha256:
        _fail(f"{label} does not match its frozen SHA-256")
    return {
        "path": relative,
        "sha256": digest,
        "bytes": len(hashed),
        "hash_mode": hash_mode,
    }


def _observed_public_input(
    repo_root: Path, relative: str, label: str, *, text: bool
) -> dict[str, Any]:
    path = _validated_repo_file(repo_root, relative, label)
    raw = _stable_read(path, label)
    if text:
        if raw.startswith(b"\xef\xbb\xbf"):
            _fail(f"{label} must not contain a UTF-8 BOM")
        try:
            raw = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        except UnicodeDecodeError:
            _fail(f"{label} is not valid UTF-8")
    return {
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "hash_mode": "utf8-lf" if text else "raw-bytes",
    }


def _read_git_head(repo_root: Path) -> str:
    dot_git = repo_root / ".git"
    if dot_git.is_dir() and not _is_link_like(dot_git):
        git_dir = dot_git
    elif dot_git.is_file() and not _is_link_like(dot_git):
        try:
            marker = dot_git.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            _fail("Git metadata cannot be read")
        if not marker.startswith("gitdir: "):
            _fail("Git metadata is malformed")
        git_dir = (repo_root / marker[8:]).resolve(strict=True)
    else:
        _fail("repository Git metadata is unavailable")

    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        _fail("Git HEAD cannot be read")
    if FULL_COMMIT.fullmatch(head):
        return head
    if not head.startswith("ref: "):
        _fail("Git HEAD is malformed")
    ref = head[5:]
    if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", ref) or ".." in ref:
        _fail("Git HEAD reference is malformed")

    common_dir = git_dir
    common_marker = git_dir / "commondir"
    if common_marker.is_file():
        try:
            common_dir = (git_dir / common_marker.read_text(encoding="ascii").strip()).resolve(strict=True)
        except (OSError, UnicodeDecodeError):
            _fail("Git common directory cannot be read")
    for base in (git_dir, common_dir):
        ref_path = base.joinpath(*ref.split("/"))
        if ref_path.is_file() and not _is_link_like(ref_path):
            try:
                revision = ref_path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeDecodeError):
                _fail("Git HEAD reference cannot be read")
            if FULL_COMMIT.fullmatch(revision):
                return revision
            _fail("Git HEAD reference is malformed")
    packed = common_dir / "packed-refs"
    if packed.is_file() and not _is_link_like(packed):
        try:
            lines = packed.read_text(encoding="ascii").splitlines()
        except (OSError, UnicodeDecodeError):
            _fail("Git packed references cannot be read")
        for line in lines:
            if line.startswith(("#", "^")) or " " not in line:
                continue
            revision, packed_ref = line.split(" ", 1)
            if packed_ref == ref and FULL_COMMIT.fullmatch(revision):
                return revision
    _fail("Git HEAD revision cannot be resolved")


def _validate_archive_member(
    repo_root: Path,
    kit_path: str,
    expected_kit_sha256: str,
    member_path: str,
    expected_sha256: str,
    expected_bytes: int,
) -> dict[str, Any]:
    kit = _validated_repo_file(repo_root, kit_path, "selected kit")
    if not isinstance(expected_kit_sha256, str) or not MIN_SHA256.fullmatch(expected_kit_sha256):
        _fail("selected kit has a malformed frozen SHA-256")
    if not isinstance(member_path, str) or not member_path or "\\" in member_path:
        _fail("L4 added source has an invalid archive path")
    member_parts = member_path.split("/")
    if any(part in {"", ".", ".."} for part in member_parts):
        _fail("L4 added source has an invalid archive path")
    if not MIN_SHA256.fullmatch(str(expected_sha256)):
        _fail("L4 added source has a malformed frozen SHA-256")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 1
    ):
        _fail("L4 added source has a malformed frozen byte count")
    kit_raw = _stable_read(kit, "selected kit")
    if hashlib.sha256(kit_raw).hexdigest() != expected_kit_sha256:
        _fail("selected kit does not match its frozen SHA-256")
    try:
        with zipfile.ZipFile(io.BytesIO(kit_raw)) as archive:
            infos = archive.infolist()
            seen: set[str] = set()
            selected = None
            for info in infos:
                name = info.filename
                if (
                    not name
                    or CONTROL.search(name)
                    or "\\" in name
                    or ":" in name
                    or name.startswith("/")
                    or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                ):
                    _fail("selected kit contains an unsafe archive entry")
                folded = name.casefold()
                if folded in seen:
                    _fail("selected kit contains duplicate archive entries")
                seen.add(folded)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(unix_mode):
                    _fail("selected kit contains a symbolic-link archive entry")
                if info.flag_bits & 0x1:
                    _fail("selected kit contains an encrypted archive entry")
                if name == member_path:
                    selected = info
            if selected is None:
                _fail("L4 added source is absent from the selected kit")
            if selected.file_size != expected_bytes:
                _fail("L4 added source does not match its frozen byte count")
            with archive.open(selected, "r") as handle:
                raw = handle.read(expected_bytes + 1)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        _fail("selected kit is not a readable safe ZIP archive")
    if len(raw) != expected_bytes:
        _fail("L4 added source does not match its frozen byte count")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail("L4 added source does not match its frozen SHA-256")
    return {
        "archive_path": kit_path,
        "member_path": member_path,
        "sha256": expected_sha256,
        "bytes": expected_bytes,
        "hash_mode": "raw-bytes",
    }


def _required_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        _fail(f"{label} is missing {key}")
    return value


def _safe_slug(value: str, label: str) -> str:
    if not isinstance(value, str) or not SAFE_SLUG.fullmatch(value):
        _fail(f"{label} must be a lowercase ASCII slug")
    return value


def _safe_display(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        _fail(f"{label} must be a nonempty public label of at most 100 characters")
    if not value.isascii() or CONTROL.search(value) or "://" in value:
        _fail(f"{label} must be plain ASCII and must not contain a URL")
    return value


def _safe_config_token(value: Any, label: str, pattern: re.Pattern[str] = SAFE_VERSION) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or "://" in value:
        _fail(f"{label} is not a safe frozen public value")
    return value


def _safe_seed_list(seeds: Sequence[str], minimum: int) -> list[str]:
    if len(seeds) < minimum:
        _fail(f"cohort requires at least {minimum} planned seeds")
    result: list[str] = []
    identities: set[str] = set()
    for seed in seeds:
        if not isinstance(seed, str) or not SAFE_SEED.fullmatch(seed):
            _fail("every seed must be a safe ASCII identifier")
        if seed.endswith("."):
            _fail("every seed must avoid Windows-ambiguous trailing characters")
        identity = f"numeric:{int(seed)}" if seed.isdecimal() else f"text:{seed.casefold()}"
        if identity in identities:
            _fail("cohort seeds contain a duplicate or normalized alias")
        identities.add(identity)
        result.append(seed)
    return result


def _read_registry(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _validated_repo_file(repo_root, REGISTRY_PATH, "run registry")
    raw = _stable_read(path, "run registry")
    value, canonical = _decode_json(raw, "run registry")
    if not isinstance(value, dict) or value.get("schema") != "marb_runs.v2":
        _fail("run registry has an unsupported schema")
    return value, {
        "path": REGISTRY_PATH,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "bytes": len(canonical),
        "hash_mode": "utf8-lf",
    }


def _frozen_inputs(
    repo_root: Path, task_id: str, task: dict[str, Any], driver: dict[str, str]
) -> dict[str, Any]:
    task_rule = TASKS[task_id]
    task_definition = _required_string(task, "task_definition", "task registry entry")
    prompt = _required_string(task, "prompt", "task registry entry")
    request = _required_string(task, task_rule["request_path_key"], "task registry entry")
    _repo_relative_parts(task_definition, "task definition")
    _repo_relative_parts(prompt, "frozen prompt")
    _repo_relative_parts(request, "frozen change request")
    if task_definition != task_rule["task_definition"]:
        _fail("task definition path is not the canonical public path")
    if request != task_rule["request_path"]:
        _fail("change request path is not the canonical public path")
    if prompt != "prompts/standard_prompt.md":
        _fail("frozen prompt path is not the canonical public path")
    allowed_kits = task.get("allowed_kits")
    allowed_briefs = task.get("allowed_driver_briefs")
    if not isinstance(allowed_kits, dict) or driver["kit"] not in allowed_kits:
        _fail("selected driver kit is not frozen for this task")
    if not isinstance(allowed_briefs, dict) or driver["brief"] not in allowed_briefs:
        _fail("selected driver brief is not frozen for this task")
    frozen = {
        "task_definition": _digest_public_input(
            repo_root,
            task_definition,
            _required_string(task, "task_definition_sha256", "task registry entry"),
            "task definition",
            text=True,
        ),
        "kit": _digest_public_input(
            repo_root,
            driver["kit"],
            _required_string(allowed_kits, driver["kit"], "allowed kits"),
            "selected kit",
            text=False,
        ),
        "prompt": _digest_public_input(
            repo_root,
            prompt,
            _required_string(task, "prompt_sha256", "task registry entry"),
            "frozen prompt",
            text=True,
        ),
        "driver_brief": _digest_public_input(
            repo_root,
            driver["brief"],
            _required_string(allowed_briefs, driver["brief"], "allowed driver briefs"),
            "selected driver brief",
            text=True,
        ),
        "change_request": _digest_public_input(
            repo_root,
            request,
            _required_string(task, task_rule["request_hash_key"], "task registry entry"),
            "frozen change request",
            text=True,
        ),
    }
    if task_id == "L4-ECO":
        connector = _required_string(task, "connector_metadata", "L4 task registry entry")
        if connector != "tasks/m3_crete/m3_connector_metadata.yaml":
            _fail("connector metadata path is not the canonical public path")
        if task.get("added_source_path") != "kit/V-Slot 20x40x1000 Linear Rail.step":
            _fail("L4 added source path is not the canonical kit member")
        frozen["connector_metadata"] = _digest_public_input(
            repo_root,
            connector,
            _required_string(task, "connector_metadata_sha256", "L4 task registry entry"),
            "connector metadata",
            text=True,
        )
        frozen["invariant_gate_implementation"] = _digest_public_input(
            repo_root,
            "grader/eco_invariant.py",
            _required_string(task, "invariant_gate_implementation_sha256", "L4 task registry entry"),
            "L4 invariant gate implementation",
            text=True,
        )
        frozen["invariant_gate_requirements"] = _digest_public_input(
            repo_root,
            "requirements-l4-eco.txt",
            _required_string(task, "invariant_gate_requirements_sha256", "L4 task registry entry"),
            "L4 invariant gate requirements",
            text=True,
        )
        frozen["added_source"] = _validate_archive_member(
            repo_root,
            driver["kit"],
            _required_string(allowed_kits, driver["kit"], "allowed kits"),
            _required_string(task, "added_source_path", "L4 task registry entry"),
            _required_string(task, "added_source_sha256", "L4 task registry entry"),
            task.get("added_source_bytes"),
        )
    return frozen


def _windows_path_identity(value: str) -> str:
    return "/".join(
        part.rstrip(". ").casefold() for part in value.replace("\\", "/").split("/")
    )


def _planned_outputs(run_id: str, task_id: str, driver: dict[str, str]) -> dict[str, str]:
    base = f"runs/{run_id}"
    suffix = driver["editable_suffix"]
    outputs = {
        "baseline_step": f"{base}/baseline.step",
        "baseline_editable_source": f"{base}/baseline_source{suffix}",
        "changed_step": f"{base}/changed.step",
        "changed_editable_source": f"{base}/changed_source{suffix}",
        "run_log": f"{base}/run_log.yaml",
    }
    if task_id == "L4-ECO":
        outputs.update(
            {
                "public_invariant_report": f"{base}/public_invariant.json",
                "public_requested_change_attestation": f"{base}/requested_change_attestation.json",
            }
        )
    return outputs


def _existing_run_checks(
    registry: dict[str, Any],
    *,
    task_id: str,
    task: dict[str, Any],
    cell_id: str,
    cell_label: str,
    cohort_id: str,
    model_id: str,
    model_name: str,
    driver: dict[str, str],
    driver_version: str,
    prompt_variant: str,
    seeds: list[str],
    generated_run_ids: list[str],
    generated_output_paths: Sequence[str],
) -> int:
    runs = registry.get("runs")
    if not isinstance(runs, list):
        _fail("run registry runs must be a list")
    existing_ids: set[str] = set()
    existing_ids_folded: set[str] = set()
    existing_seed_identities: set[str] = set()
    existing_path_identities: set[str] = set()
    registered_path_fields = (
        "step",
        "run_log",
        "baseline_step",
        "baseline_editable_source",
        "changed_editable_source",
        "invariant_report",
        "requested_change_report",
        "gated_requested_grade_report_path",
    )
    matched = 0
    for run in runs:
        if not isinstance(run, dict):
            _fail("run registry contains a non-object run")
        run_id = run.get("run_id")
        if isinstance(run_id, str) and run_id:
            folded = run_id.rstrip(". ").casefold()
            if run_id in existing_ids or folded in existing_ids_folded:
                _fail("run registry contains duplicate run identities")
            existing_ids.add(run_id)
            existing_ids_folded.add(folded)
        for field in registered_path_fields:
            value = run.get(field)
            if isinstance(value, str) and value:
                _repo_relative_parts(
                    value, "registered run evidence path", public_only=False
                )
                existing_path_identities.add(_windows_path_identity(value))
        if run.get("cell_id") != cell_id:
            continue
        matched += 1
        task_rule = TASKS[task_id]
        allowed_kits = task["allowed_kits"]
        allowed_briefs = task["allowed_driver_briefs"]
        identity_fields = {
            "cell": cell_label,
            "task": task_id,
            "scoring_version": task_rule["scoring_version"],
            "track": "frontier",
            "cohort_id": cohort_id,
            "model.id": model_id,
            "model.name": model_name,
            "driver.tool": driver["tool"],
            "driver.version": driver_version,
            "kit": driver["kit"],
            "kit_sha256": allowed_kits[driver["kit"]],
            "kit_version": driver["kit_version"],
            "prompt": task["prompt"],
            "prompt_sha256": task["prompt_sha256"],
            "prompt_variant": prompt_variant,
            "driver_brief": driver["brief"],
            "driver_brief_sha256": allowed_briefs[driver["brief"]],
            "task_definition": task["task_definition"],
            "task_definition_sha256": task["task_definition_sha256"],
            task_rule["request_path_key"]: task[task_rule["request_path_key"]],
            task_rule["request_hash_key"]: task[task_rule["request_hash_key"]],
            task_rule["request_id_key"]: task[task_rule["request_id_key"]],
        }
        if task_id == "L4-ECO":
            for key in (
                "cadclaw_commit",
                "cadclaw_version",
                "cadquery_version",
                "cadquery_ocp_version",
                "invariant_gate_version",
                "invariant_gate_implementation_sha256",
                "invariant_gate_requirements_sha256",
                "requested_change_grade_method",
                "gated_distribution_revision",
                "answer_key_step_sha256",
                "answer_key_spec_sha256",
            ):
                identity_fields[key] = task.get(key)
        missing = object()
        for dotted, expected in identity_fields.items():
            value: Any = run
            for part in dotted.split("."):
                if not isinstance(value, dict) or part not in value:
                    value = missing
                    break
                value = value[part]
            if value != expected:
                _fail("cell_id already belongs to a different frozen cohort identity")
        seed = run.get("seed")
        if isinstance(seed, str) and SAFE_SEED.fullmatch(seed):
            identity = f"numeric:{int(seed)}" if seed.isdecimal() else f"text:{seed.casefold()}"
            existing_seed_identities.add(identity)
    for run_id in generated_run_ids:
        if run_id in existing_ids or run_id.rstrip(". ").casefold() in existing_ids_folded:
            _fail("planned run_id collides with the run registry")
    for path in generated_output_paths:
        if _windows_path_identity(path) in existing_path_identities:
            _fail("planned output path collides with registered run evidence")
    for seed in seeds:
        identity = f"numeric:{int(seed)}" if seed.isdecimal() else f"text:{seed.casefold()}"
        if identity in existing_seed_identities:
            _fail("planned seed collides with an existing cell attempt")
    return matched


def build_plan(
    repo_root: Path,
    *,
    task_id: str,
    source_revision: str,
    cell_id: str,
    cell_label: str,
    cohort_id: str,
    model_id: str,
    model_name: str,
    driver_id: str,
    driver_version: str,
    prompt_variant: str,
    seed_basis: str,
    seeds: Sequence[str],
) -> dict[str, Any]:
    """Return a deterministic, self-digesting plan envelope."""
    repo_root = repo_root.resolve(strict=True)
    if task_id not in TASKS:
        _fail("task is not supported by the plan-only cohort runner")
    if driver_id not in DRIVERS:
        _fail("driver is not supported by the plan-only cohort runner")
    if not isinstance(source_revision, str) or not FULL_COMMIT.fullmatch(source_revision):
        _fail("source revision must be a full lowercase 40-character commit")
    if _read_git_head(repo_root) != source_revision:
        _fail("source revision does not match the checked-out Git HEAD")
    cell_id = _safe_slug(cell_id, "cell_id")
    cohort_id = _safe_slug(cohort_id, "cohort_id")
    prompt_variant = _safe_slug(prompt_variant, "prompt_variant")
    cell_label = _safe_display(cell_label, "cell label")
    model_name = _safe_display(model_name, "model name")
    if not isinstance(model_id, str) or not SAFE_MODEL_ID.fullmatch(model_id) or "://" in model_id:
        _fail("model_id must be a safe inert public identifier")
    if not isinstance(driver_version, str) or not SAFE_VERSION.fullmatch(driver_version):
        _fail("driver version must be a safe public version label")
    if seed_basis not in {"provider-seed", "independent-run-ordinal"}:
        _fail("seed basis is unsupported")

    registry, registry_identity = _read_registry(repo_root)
    policy = registry.get("publication_policy")
    if not isinstance(policy, dict):
        _fail("run registry publication policy is missing")
    minimum = policy.get("frontier_min_distinct_runs")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 3:
        _fail("run registry publication minimum must be an integer of at least three")
    seeds = _safe_seed_list(seeds, minimum)

    task_map = registry.get("tasks")
    if not isinstance(task_map, dict) or not isinstance(task_map.get(task_id), dict):
        _fail("selected task is absent from the run registry")
    task = task_map[task_id]
    task_rule = TASKS[task_id]
    if task.get("provenance_contract") != task_rule["contract"]:
        _fail("selected task has an unsupported provenance contract")
    if task.get("rung") != task_rule["rung"]:
        _fail("selected task has an unexpected rung")
    if task.get("scoring_version") != task_rule["scoring_version"]:
        _fail("selected task has an unexpected scoring version")
    if task.get("status") not in {"defined_unmeasured", "measured"}:
        _fail("selected task is not in a supported active state")
    if policy.get("summary") != "median" or policy.get("spread") != "population_std":
        _fail("run registry publication statistics are unsupported")
    driver = DRIVERS[driver_id]
    if task_id == "L4-ECO" and driver_id == "cadquery":
        if driver_version != task.get("cadquery_version"):
            _fail("L4 CadQuery driver version must match the frozen runtime contract")
    frozen = _frozen_inputs(repo_root, task_id, task, driver)

    generated_run_ids = [f"{cohort_id}-{seed}" for seed in seeds]
    if len({run_id.casefold() for run_id in generated_run_ids}) != len(generated_run_ids):
        _fail("planned run IDs are not unique")
    generated_output_paths = [
        path
        for run_id in generated_run_ids
        for path in _planned_outputs(run_id, task_id, driver).values()
    ]
    existing_cell_attempts = _existing_run_checks(
        registry,
        task_id=task_id,
        task=task,
        cell_id=cell_id,
        cell_label=cell_label,
        cohort_id=cohort_id,
        model_id=model_id,
        model_name=model_name,
        driver=driver,
        driver_version=driver_version,
        prompt_variant=prompt_variant,
        seeds=seeds,
        generated_run_ids=generated_run_ids,
        generated_output_paths=generated_output_paths,
    )

    request_path_key = task_rule["request_path_key"]
    request_hash_key = task_rule["request_hash_key"]
    request_id_key = task_rule["request_id_key"]
    runtime_contract: dict[str, Any] = {}
    if task_id == "L4-ECO":
        for key in (
            "cadclaw_version",
            "cadquery_version",
            "cadquery_ocp_version",
            "invariant_gate_version",
            "requested_change_grade_method",
        ):
            runtime_contract[key] = _safe_config_token(
                task.get(key), f"L4 runtime field {key}"
            )
        runtime_contract["cadclaw_commit"] = _safe_config_token(
            task.get("cadclaw_commit"), "L4 runtime field cadclaw_commit", FULL_COMMIT
        )

    planned_runs = []
    for run_id, seed in zip(generated_run_ids, seeds):
        outputs = _planned_outputs(run_id, task_id, driver)
        planned_runs.append(
            {
                "run_id": run_id,
                "seed": seed,
                "status": "planned",
                "outcome": None,
                "driver_continuity_id": None,
                "planned_outputs": outputs,
                "artifact_sha256": None,
                "run_log_sha256": None,
                "model_calls_performed": 0,
            }
        )

    blockers = [
        "this plan does not authorize execution, provider access, or spend",
        "planned slots are not benchmark attempts or run evidence",
    ]
    if task.get("evidence_status") != "complete":
        blockers.append("task run evidence is not complete")
    if task.get("answer_key_status") != "ready":
        blockers.append("task-local grading-key distribution is not ready")
    gated_revision = task.get("gated_distribution_revision")
    if task_id == "L4-ECO" and (
        not isinstance(gated_revision, str)
        or not IMMUTABLE_REVISION.fullmatch(gated_revision)
    ):
        blockers.append("immutable gated requested-change grading revision is missing")

    plan = {
        "mode": "plan-only",
        "execution_status": "blocked-before-execution",
        "safety": {
            "execution_authorized": False,
            "network_allowed": False,
            "provider_calls_allowed": False,
            "spend_authorized": False,
            "model_calls_performed": 0,
            "provider_calls_performed": 0,
            "registry_mutated": False,
            "run_artifacts_created": False,
        },
        "source": {
            "revision": source_revision,
            "revision_verified_against_local_head": True,
            "working_tree_membership_in_revision_verified": False,
            "registry": registry_identity,
            "planner": _observed_public_input(
                repo_root, "harness/cohort_runner.py", "cohort planner", text=True
            ),
        },
        "task": {
            "id": task_id,
            "rung": task_rule["rung"],
            "scoring_version": task_rule["scoring_version"],
            "task_revision": _safe_config_token(
                task.get("task_revision"), "task revision"
            ),
            "provenance_contract": task_rule["contract"],
            "change_request_id": _safe_config_token(
                task.get(request_id_key), "change request id"
            ),
            "change_request": {
                "path": _required_string(task, request_path_key, "task registry entry"),
                "sha256": _required_string(task, request_hash_key, "task registry entry"),
            },
            "runtime_contract": runtime_contract,
        },
        "cohort": {
            "cell_id": cell_id,
            "cell_label": cell_label,
            "cohort_id": cohort_id,
            "track": "frontier",
            "model": {"id": model_id, "name": model_name},
            "driver": {
                "id": driver_id,
                "tool": driver["tool"],
                "version": driver_version,
            },
            "kit_version": driver["kit_version"],
            "prompt_variant": prompt_variant,
            "seed_basis": seed_basis,
            "publication_contract": {
                "minimum_independent_attempts": minimum,
                "minimum_gradeable_outputs": minimum,
                "center": policy.get("summary"),
                "spread": policy.get("spread"),
            },
            "existing_registered_attempts_for_cell": existing_cell_attempts,
            "planned_attempts": len(planned_runs),
        },
        "frozen_public_inputs": frozen,
        "readiness": {
            "status": "blocked",
            "registered_task_runs": sum(
                1
                for run in registry["runs"]
                if isinstance(run, dict) and run.get("task") == task_id
            ),
            "blockers": blockers,
        },
        "runs": planned_runs,
    }
    digest = hashlib.sha256(_canonical_json(plan)).hexdigest()
    return {"schema": PLAN_SCHEMA, "plan_sha256": digest, "plan": plan}


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(f"{label} has unknown or missing fields")
    return value


def _verify_file_identity(value: Any, label: str, *, archive_member: bool = False) -> None:
    if archive_member:
        record = _exact_keys(
            value,
            {"archive_path", "member_path", "sha256", "bytes", "hash_mode"},
            label,
        )
        _repo_relative_parts(record.get("archive_path"), f"{label} archive path")
        _repo_relative_parts(record.get("member_path"), f"{label} member path")
    else:
        record = _exact_keys(
            value, {"path", "sha256", "bytes", "hash_mode"}, label
        )
        _repo_relative_parts(record.get("path"), f"{label} path")
    if not isinstance(record.get("sha256"), str) or not MIN_SHA256.fullmatch(record["sha256"]):
        _fail(f"{label} has a malformed SHA-256")
    byte_count = record.get("bytes")
    if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 1:
        _fail(f"{label} has a malformed byte count")
    if record.get("hash_mode") not in {"utf8-lf", "raw-bytes"}:
        _fail(f"{label} has an unsupported hash mode")


def _verify_plan_schema(plan: dict[str, Any]) -> None:
    _exact_keys(
        plan,
        {
            "mode",
            "execution_status",
            "safety",
            "source",
            "task",
            "cohort",
            "frozen_public_inputs",
            "readiness",
            "runs",
        },
        "cohort plan payload",
    )
    safety = _exact_keys(
        plan.get("safety"),
        {
            "execution_authorized",
            "network_allowed",
            "provider_calls_allowed",
            "spend_authorized",
            "model_calls_performed",
            "provider_calls_performed",
            "registry_mutated",
            "run_artifacts_created",
        },
        "cohort plan safety block",
    )
    required_safety = {
        "execution_authorized": False,
        "network_allowed": False,
        "provider_calls_allowed": False,
        "spend_authorized": False,
        "model_calls_performed": 0,
        "provider_calls_performed": 0,
        "registry_mutated": False,
        "run_artifacts_created": False,
    }
    if (
        plan.get("mode") != "plan-only"
        or plan.get("execution_status") != "blocked-before-execution"
        or safety != required_safety
    ):
        _fail("cohort plan safety assertions are not fail-closed")

    source = _exact_keys(
        plan.get("source"),
        {
            "revision",
            "revision_verified_against_local_head",
            "working_tree_membership_in_revision_verified",
            "registry",
            "planner",
        },
        "cohort plan source block",
    )
    if (
        not isinstance(source.get("revision"), str)
        or not FULL_COMMIT.fullmatch(source["revision"])
        or source.get("revision_verified_against_local_head") is not True
        or source.get("working_tree_membership_in_revision_verified") is not False
    ):
        _fail("cohort plan source assertions are malformed")
    _verify_file_identity(source.get("registry"), "cohort plan registry identity")
    _verify_file_identity(source.get("planner"), "cohort plan planner identity")
    if (
        source["registry"].get("path") != REGISTRY_PATH
        or source["registry"].get("hash_mode") != "utf8-lf"
    ):
        _fail("cohort plan registry path is not canonical")
    if (
        source["planner"].get("path") != "harness/cohort_runner.py"
        or source["planner"].get("hash_mode") != "utf8-lf"
    ):
        _fail("cohort plan planner path is not canonical")

    task = _exact_keys(
        plan.get("task"),
        {
            "id",
            "rung",
            "scoring_version",
            "task_revision",
            "provenance_contract",
            "change_request_id",
            "change_request",
            "runtime_contract",
        },
        "cohort plan task block",
    )
    task_id = task.get("id")
    if task_id not in TASKS:
        _fail("cohort plan task is unsupported")
    task_rule = TASKS[task_id]
    if (
        task.get("rung") != task_rule["rung"]
        or task.get("scoring_version") != task_rule["scoring_version"]
        or task.get("provenance_contract") != task_rule["contract"]
    ):
        _fail("cohort plan task identity is inconsistent")
    _safe_config_token(task.get("task_revision"), "cohort plan task revision")
    _safe_config_token(task.get("change_request_id"), "cohort plan change request id")
    change_request = _exact_keys(
        task.get("change_request"), {"path", "sha256"}, "cohort plan change request"
    )
    _repo_relative_parts(change_request.get("path"), "cohort plan change request path")
    if not isinstance(change_request.get("sha256"), str) or not MIN_SHA256.fullmatch(change_request["sha256"]):
        _fail("cohort plan change request has a malformed SHA-256")
    runtime = task.get("runtime_contract")
    if task_id == "L2-RESOLVE":
        if runtime != {}:
            _fail("L2 cohort plan must not invent a runtime contract")
    else:
        runtime = _exact_keys(
            runtime,
            {
                "cadclaw_version",
                "cadclaw_commit",
                "cadquery_version",
                "cadquery_ocp_version",
                "invariant_gate_version",
                "requested_change_grade_method",
            },
            "L4 cohort plan runtime contract",
        )
        for key, value in runtime.items():
            _safe_config_token(
                value,
                f"L4 cohort plan runtime field {key}",
                FULL_COMMIT if key == "cadclaw_commit" else SAFE_VERSION,
            )

    cohort = _exact_keys(
        plan.get("cohort"),
        {
            "cell_id",
            "cell_label",
            "cohort_id",
            "track",
            "model",
            "driver",
            "kit_version",
            "prompt_variant",
            "seed_basis",
            "publication_contract",
            "existing_registered_attempts_for_cell",
            "planned_attempts",
        },
        "cohort plan cohort block",
    )
    cell_id = _safe_slug(cohort.get("cell_id"), "cohort plan cell_id")
    cohort_id = _safe_slug(cohort.get("cohort_id"), "cohort plan cohort_id")
    _safe_slug(cohort.get("prompt_variant"), "cohort plan prompt_variant")
    _safe_display(cohort.get("cell_label"), "cohort plan cell label")
    if cohort.get("track") != "frontier":
        _fail("cohort plan track must be frontier")
    model = _exact_keys(cohort.get("model"), {"id", "name"}, "cohort plan model")
    if (
        not isinstance(model.get("id"), str)
        or not SAFE_MODEL_ID.fullmatch(model["id"])
        or "://" in model["id"]
    ):
        _fail("cohort plan model id is unsafe")
    _safe_display(model.get("name"), "cohort plan model name")
    driver = _exact_keys(
        cohort.get("driver"), {"id", "tool", "version"}, "cohort plan driver"
    )
    driver_id = driver.get("id")
    if driver_id not in DRIVERS or driver.get("tool") != DRIVERS[driver_id]["tool"]:
        _fail("cohort plan driver identity is inconsistent")
    _safe_config_token(driver.get("version"), "cohort plan driver version")
    if (
        task_id == "L4-ECO"
        and driver_id == "cadquery"
        and driver.get("version") != runtime.get("cadquery_version")
    ):
        _fail("cohort plan CadQuery driver version contradicts the L4 runtime contract")
    if cohort.get("kit_version") != DRIVERS[driver_id]["kit_version"]:
        _fail("cohort plan kit version is inconsistent")
    if cohort.get("seed_basis") not in {"provider-seed", "independent-run-ordinal"}:
        _fail("cohort plan seed basis is unsupported")
    publication = _exact_keys(
        cohort.get("publication_contract"),
        {
            "minimum_independent_attempts",
            "minimum_gradeable_outputs",
            "center",
            "spread",
        },
        "cohort plan publication contract",
    )
    minimum = publication.get("minimum_independent_attempts")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 3
        or publication.get("minimum_gradeable_outputs") != minimum
        or publication.get("center") != "median"
        or publication.get("spread") != "population_std"
    ):
        _fail("cohort plan publication contract is inconsistent")
    for key in ("existing_registered_attempts_for_cell", "planned_attempts"):
        value = cohort.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _fail(f"cohort plan {key} is malformed")

    frozen = plan.get("frozen_public_inputs")
    expected_frozen = {
        "task_definition",
        "kit",
        "prompt",
        "driver_brief",
        "change_request",
    }
    if task_id == "L4-ECO":
        expected_frozen.update(
            {
                "connector_metadata",
                "invariant_gate_implementation",
                "invariant_gate_requirements",
                "added_source",
            }
        )
    frozen = _exact_keys(frozen, expected_frozen, "cohort plan frozen inputs")
    for key, value in frozen.items():
        _verify_file_identity(value, f"cohort plan frozen input {key}", archive_member=key == "added_source")
    canonical_paths = {
        "task_definition": task_rule["task_definition"],
        "kit": DRIVERS[driver_id]["kit"],
        "prompt": "prompts/standard_prompt.md",
        "driver_brief": DRIVERS[driver_id]["brief"],
        "change_request": task_rule["request_path"],
    }
    if task_id == "L4-ECO":
        canonical_paths.update(
            {
                "connector_metadata": "tasks/m3_crete/m3_connector_metadata.yaml",
                "invariant_gate_implementation": "grader/eco_invariant.py",
                "invariant_gate_requirements": "requirements-l4-eco.txt",
            }
        )
    for key, expected_path in canonical_paths.items():
        if frozen[key].get("path") != expected_path:
            _fail(f"cohort plan frozen input {key} path is not canonical")
    if frozen["kit"].get("hash_mode") != "raw-bytes":
        _fail("cohort plan selected kit hash mode is inconsistent")
    for key in set(canonical_paths) - {"kit"}:
        if frozen[key].get("hash_mode") != "utf8-lf":
            _fail(f"cohort plan frozen input {key} hash mode is inconsistent")
    if frozen["kit"].get("path") != DRIVERS[driver_id]["kit"]:
        _fail("cohort plan selected kit is inconsistent")
    if frozen["driver_brief"].get("path") != DRIVERS[driver_id]["brief"]:
        _fail("cohort plan selected driver brief is inconsistent")
    if change_request != {
        "path": frozen["change_request"].get("path"),
        "sha256": frozen["change_request"].get("sha256"),
    }:
        _fail("cohort plan change request identities are inconsistent")
    if task_id == "L4-ECO":
        added_source = frozen["added_source"]
        if (
            added_source.get("archive_path") != frozen["kit"].get("path")
            or added_source.get("member_path")
            != "kit/V-Slot 20x40x1000 Linear Rail.step"
            or added_source.get("hash_mode") != "raw-bytes"
        ):
            _fail("cohort plan L4 added-source identity is inconsistent")

    readiness = _exact_keys(
        plan.get("readiness"),
        {"status", "registered_task_runs", "blockers"},
        "cohort plan readiness block",
    )
    registered = readiness.get("registered_task_runs")
    blockers = readiness.get("blockers")
    if (
        readiness.get("status") != "blocked"
        or not isinstance(registered, int)
        or isinstance(registered, bool)
        or registered < 0
        or not isinstance(blockers, list)
        or len(blockers) < 2
    ):
        _fail("cohort plan readiness is not fail-closed")
    for blocker in blockers:
        _safe_display(blocker, "cohort plan blocker")

    runs = plan.get("runs")
    if not isinstance(runs, list) or not runs:
        _fail("cohort plan has no planned runs")
    if cohort.get("planned_attempts") != len(runs) or len(runs) < minimum:
        _fail("cohort plan run count does not satisfy its publication minimum")
    seed_values: list[str] = []
    seen_run_ids: set[str] = set()
    suffix = DRIVERS[driver_id]["editable_suffix"]
    output_names = {
        "baseline_step": "baseline.step",
        "baseline_editable_source": f"baseline_source{suffix}",
        "changed_step": "changed.step",
        "changed_editable_source": f"changed_source{suffix}",
        "run_log": "run_log.yaml",
    }
    if task_id == "L4-ECO":
        output_names.update(
            {
                "public_invariant_report": "public_invariant.json",
                "public_requested_change_attestation": "requested_change_attestation.json",
            }
        )
    for run in runs:
        run = _exact_keys(
            run,
            {
                "run_id",
                "seed",
                "status",
                "outcome",
                "driver_continuity_id",
                "planned_outputs",
                "artifact_sha256",
                "run_log_sha256",
                "model_calls_performed",
            },
            "cohort plan run slot",
        )
        seed = run.get("seed")
        if not isinstance(seed, str):
            _fail("cohort plan run seed is malformed")
        seed_values.append(seed)
        expected_run_id = f"{cohort_id}-{seed}"
        if run.get("run_id") != expected_run_id or expected_run_id.casefold() in seen_run_ids:
            _fail("cohort plan run identity is inconsistent or duplicated")
        seen_run_ids.add(expected_run_id.casefold())
        outputs = _exact_keys(
            run.get("planned_outputs"), set(output_names), "cohort plan planned outputs"
        )
        expected_outputs = {
            key: f"runs/{expected_run_id}/{name}" for key, name in output_names.items()
        }
        if outputs != expected_outputs:
            _fail("cohort plan planned output paths are inconsistent")
        for value in outputs.values():
            _repo_relative_parts(value, "cohort plan planned output", public_only=False)
        if (
            run.get("status") != "planned"
            or run.get("outcome") is not None
            or run.get("driver_continuity_id") is not None
            or run.get("artifact_sha256") is not None
            or run.get("run_log_sha256") is not None
            or run.get("model_calls_performed") != 0
        ):
            _fail("cohort plan contains fabricated execution evidence")
    _safe_seed_list(seed_values, minimum)


def verify_plan_envelope(raw: bytes, *, expected_sha256: str | None = None) -> dict[str, Any]:
    """Verify canonical encoding, self-digest, and immutable safety assertions."""
    value, _canonical_input = _decode_json(raw, "cohort plan")
    if not isinstance(value, dict) or set(value) != {"schema", "plan_sha256", "plan"}:
        _fail("cohort plan envelope has unknown or missing fields")
    if value.get("schema") != PLAN_SCHEMA or not isinstance(value.get("plan"), dict):
        _fail("cohort plan envelope has an unsupported schema")
    if raw != _canonical_json(value, newline=True):
        _fail("cohort plan is not canonical JSON with one LF terminator")
    digest = hashlib.sha256(_canonical_json(value["plan"])).hexdigest()
    if value.get("plan_sha256") != digest:
        _fail("cohort plan payload digest does not match")
    if expected_sha256 is not None:
        if not MIN_SHA256.fullmatch(expected_sha256) or digest != expected_sha256:
            _fail("cohort plan does not match the independently approved digest")
    _verify_plan_schema(value["plan"])
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a deterministic MARB cohort plan; never execute runs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="validate frozen inputs and print a plan")
    plan.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    plan.add_argument("--task", required=True, choices=sorted(TASKS))
    plan.add_argument("--source-revision", required=True)
    plan.add_argument("--cell-id", required=True)
    plan.add_argument("--cell-label", required=True)
    plan.add_argument("--cohort-id", required=True)
    plan.add_argument("--model-id", required=True)
    plan.add_argument("--model-name", required=True)
    plan.add_argument("--driver", required=True, choices=sorted(DRIVERS))
    plan.add_argument("--driver-version", required=True)
    plan.add_argument("--prompt-variant", required=True)
    plan.add_argument(
        "--seed-basis",
        required=True,
        choices=("provider-seed", "independent-run-ordinal"),
    )
    plan.add_argument("--seed", required=True, action="append")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "plan":
        _fail("unsupported command")
    try:
        envelope = build_plan(
            args.repo_root,
            task_id=args.task,
            source_revision=args.source_revision,
            cell_id=args.cell_id,
            cell_label=args.cell_label,
            cohort_id=args.cohort_id,
            model_id=args.model_id,
            model_name=args.model_name,
            driver_id=args.driver,
            driver_version=args.driver_version,
            prompt_variant=args.prompt_variant,
            seed_basis=args.seed_basis,
            seeds=args.seed,
        )
        raw = _canonical_json(envelope, newline=True)
        verify_plan_envelope(raw)
    except PlanError as exc:
        print(f"cohort plan rejected: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
