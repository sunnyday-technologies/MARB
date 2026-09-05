"""Deterministic, provider-free H2b runtime qualification smoke runner.

Importing this module is inert.  Docker is reached only through an injected
engine after :func:`run_smoke` is called; tests supply a fake engine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Protocol, Sequence

try:  # package import in tests; script import for the CLI
    from . import isolated_container as isolation
    from . import runtime_smoke_probes as probes
except ImportError:  # pragma: no cover - exercised by CLI tests
    import isolated_container as isolation  # type: ignore[no-redef]
    import runtime_smoke_probes as probes  # type: ignore[no-redef]


EVIDENCE_SCHEMA = "marb_runtime_smoke_evidence.v1"
ENVELOPE_SCHEMA = "marb_runtime_smoke_evidence_envelope.v1"
MAX_MANIFEST_HASH_BYTES = 2 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SAFE_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
SAFE_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


class SmokeRunnerError(RuntimeError):
    """Safe, fixed-text smoke input/evidence failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "smoke_runner_error",
        stage: str = "validation",
        prior: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        prior_evidence = getattr(prior, "evidence", None)
        if isinstance(prior_evidence, Mapping):
            # A post-case wrapper must preserve only the already-sanitized
            # output identities. Child-output bodies were disposed by the
            # isolation error and must not be reconstructed here.
            self.evidence = {
                key: prior_evidence.get(key) for key in ("stdout", "stderr")
            }
        for field in (
            "cleanup_attempted",
            "cleanup_error_type",
            "cleanup_verified",
            "container_absence_verified",
            "container_id",
            "container_name",
            "export_staging_removed",
            "image",
            "image_id",
            "primary_error_type",
            "returncode",
            "stderr",
            "stderr_truncated",
            "stdout",
            "stdout_truncated",
        ):
            if prior is not None and hasattr(prior, field):
                setattr(self, field, getattr(prior, field))


class SmokeEngine(Protocol):
    docker_command_count: int

    def execute(
        self,
        workspace: Path,
        relative_script: str,
        *,
        inspect_timeout: float | None = 30.0,
        execution_timeout: float | None = 600.0,
        attached_run_timeout: float | None = None,
    ) -> Any: ...

    def inspect_image_provenance(self) -> Mapping[str, Any]: ...


EngineFactory = Callable[[probes.ProbeCase, int, Path], SmokeEngine]
SourceVerifier = Callable[
    [Path, str, str, str, str, Mapping[str, Any]], dict[str, Any]
]
SOURCE_FILES = (
    "harness/cohort_executor.py",
    "harness/isolated_container.py",
    "harness/runtime_smoke_probes.py",
    "harness/runtime_smoke_runner.py",
    "harness/container/Dockerfile",
    "harness/container/.dockerignore",
    "harness/container/cadclaw-calibration.fad0dd55.json",
    "harness/container/native-debs.lock.json",
    "harness/container/requirements.lock",
    "harness/container/run_limited.py",
    "harness/container/runtime-contract.v0.13.json",
    "harness/container/verify_native_bundle.py",
)
_LABEL_PREFIX = "org.sunnyday.marb."
_LABEL_TO_PROVENANCE_KEY = {
    _LABEL_PREFIX + "base-image": "base_image",
    _LABEL_PREFIX + "requirements-lock-sha256": "requirements_lock_sha256",
    _LABEL_PREFIX + "wheelhouse-manifest-sha256": "wheelhouse_manifest_sha256",
    _LABEL_PREFIX + "cadclaw-wheel-sha256": "cadclaw_wheel_sha256",
    _LABEL_PREFIX + "run-limiter-sha256": "run_limiter_sha256",
    _LABEL_PREFIX + "native-deb-lock-sha256": "native_deb_lock_sha256",
    _LABEL_PREFIX + "native-deb-manifest-sha256": "native_deb_manifest_sha256",
    _LABEL_PREFIX + "native-bundle-verifier-sha256": "native_bundle_verifier_sha256",
    _LABEL_PREFIX + "dockerfile-sha256": "dockerfile_sha256",
    _LABEL_PREFIX + "context-dockerignore-sha256": "context_dockerignore_sha256",
    _LABEL_PREFIX + "build-context-manifest-sha256": "build_context_manifest_sha256",
}


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


def _utc_text(value: dt.datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SmokeRunnerError("smoke clock must return an aware UTC datetime")
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text_identity(path: Path, public_path: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SmokeRunnerError("tracked smoke source is not valid UTF-8") from exc
    return {
        "bytes": len(raw),
        "hash_mode": "raw",
        "path": public_path,
        "sha256": _sha256(raw),
    }


def _verify_docker_executable(path_text: str, expected_sha256: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_absolute():
        raise SmokeRunnerError("Docker executable path is not absolute")
    try:
        isolation._assert_path_chain_is_plain(path, "Docker executable")
    except isolation.IsolationError as exc:
        raise SmokeRunnerError("Docker executable is unavailable or unsafe") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise SmokeRunnerError("Docker executable is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 256 * 1024 * 1024:
        raise SmokeRunnerError("Docker executable is unavailable or unsafe")
    digest = hashlib.sha256()
    measured = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            measured += len(chunk)
            digest.update(chunk)
    try:
        readback = path.lstat()
    except OSError as exc:
        raise SmokeRunnerError("Docker executable changed during hashing") from exc
    identity = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if (
        any(getattr(info, key, None) != getattr(readback, key, None) for key in identity)
        or measured != info.st_size
        or digest.hexdigest() != expected_sha256
    ):
        raise SmokeRunnerError("Docker executable digest does not match")
    return {"bytes": measured, "sha256": digest.hexdigest()}


def _manifest(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    candidates: list[Path] = []

    def walk_error(_error: OSError) -> None:
        raise SmokeRunnerError("smoke workspace manifest cannot be inventoried")

    for current, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort(key=lambda item: item.encode("utf-8"))
        files.sort(key=lambda item: item.encode("utf-8"))
        current_path = Path(current)
        candidates.extend(current_path / name for name in directories)
        candidates.extend(current_path / name for name in files)
    candidates.sort(key=lambda item: item.relative_to(root).as_posix().encode("utf-8"))
    for candidate in candidates:
        relative = candidate.relative_to(root).as_posix()
        info = candidate.lstat()
        if stat.S_ISDIR(info.st_mode):
            entries.append({"bytes": 0, "path": relative, "sha256": None, "type": "directory"})
            continue
        if not stat.S_ISREG(info.st_mode) or candidate.is_symlink():
            raise SmokeRunnerError("smoke workspace manifest contains a non-regular entry")
        total_bytes += info.st_size
        digest: str | None = None
        if info.st_size <= MAX_MANIFEST_HASH_BYTES:
            digest = _sha256(candidate.read_bytes())
        entries.append(
            {
                "bytes": info.st_size,
                "path": relative,
                "sha256": digest,
                "type": "file",
            }
        )
    return {
        "entries": entries,
        "entry_count": len(entries),
        "manifest_sha256": _sha256(_canonical_json(entries)),
        "total_file_bytes": total_bytes,
    }


def _write_exact(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        written = handle.write(raw)
        if written != len(raw):
            raise SmokeRunnerError("smoke input write was incomplete")


def _materialize_inputs(root: Path) -> None:
    root.mkdir()
    for relative, raw in probes.RUNTIME_SMOKE_INPUTS:
        _write_exact(root.joinpath(*relative.split("/")), raw)


def _expected_input_manifest() -> dict[str, Any]:
    directories: set[str] = set()
    files: dict[str, bytes] = {}
    for relative, raw in probes.RUNTIME_SMOKE_INPUTS:
        parts = relative.split("/")
        for length in range(1, len(parts)):
            directories.add("/".join(parts[:length]))
        files[relative] = raw
    entries = [
        {"bytes": 0, "path": path, "sha256": None, "type": "directory"}
        for path in directories
    ] + [
        {"bytes": len(raw), "path": path, "sha256": _sha256(raw), "type": "file"}
        for path, raw in files.items()
    ]
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    return {
        "entries": entries,
        "entry_count": len(entries),
        "manifest_sha256": _sha256(_canonical_json(entries)),
        "total_file_bytes": sum(len(raw) for raw in files.values()),
    }


def _output_summary(value: Any, *, limit: int, truncated: bool) -> dict[str, Any]:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    elif value is None:
        raw = b""
    else:
        return {"bytes": None, "sha256": None, "truncated": bool(truncated), "within_bound": False}
    within_bound = len(raw) <= limit
    return {
        "bytes": len(raw),
        "sha256": _sha256(raw) if within_bound else None,
        "truncated": bool(truncated),
        "within_bound": within_bound,
    }


def _container_name(value: Any) -> str | None:
    if not isinstance(value, str) or not isolation.CONTAINER_NAME_RE.fullmatch(value):
        return None
    return value


def _container_id(value: Any) -> str | None:
    if not isinstance(value, str) or not isolation.CONTAINER_ID_RE.fullmatch(value):
        return None
    return value


def _image_id(value: Any) -> str | None:
    if not isinstance(value, str) or not isolation.IMAGE_ID_RE.fullmatch(value):
        return None
    return value


def _engine_count(engine: SmokeEngine) -> int:
    value = getattr(engine, "docker_command_count", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _exception_field(exc: BaseException, key: str) -> Any:
    if hasattr(exc, key):
        return getattr(exc, key)
    if key == "container_absence_verified" and hasattr(exc, "container_absent_verified"):
        return getattr(exc, "container_absent_verified")
    raw = getattr(exc, "evidence", None)
    if isinstance(raw, Mapping):
        return raw.get(key)
    return None


def _safe_type(value: Any) -> str | None:
    return value if isinstance(value, str) and SAFE_TYPE.fullmatch(value) else None


def _failure_output_summary(exc: BaseException, key: str, limit: int) -> dict[str, Any]:
    evidence = getattr(exc, "evidence", None)
    if isinstance(evidence, Mapping):
        metric = evidence.get(key)
        if isinstance(metric, Mapping):
            size = metric.get("bytes")
            digest = metric.get("sha256")
            truncated = metric.get("truncated")
            if (
                isinstance(size, int)
                and not isinstance(size, bool)
                and 0 <= size <= limit
                and isinstance(digest, str)
                and HEX64.fullmatch(digest)
                and isinstance(truncated, bool)
            ):
                return {
                    "bytes": size,
                    "sha256": digest,
                    "truncated": truncated,
                    "within_bound": True,
                }
    return _output_summary(
        _exception_field(exc, key),
        limit=limit,
        truncated=bool(_exception_field(exc, f"{key}_truncated")),
    )


def _failure_summary(exc: BaseException) -> dict[str, Any]:
    code = _exception_field(exc, "code")
    stage = _exception_field(exc, "stage")
    if not isinstance(code, str) or not SAFE_TOKEN.fullmatch(code):
        code = "unstructured_failure"
    if not isinstance(stage, str) or not SAFE_TOKEN.fullmatch(stage):
        stage = "unknown"
    return {
        "cleanup_attempted": bool(_exception_field(exc, "cleanup_attempted")),
        "cleanup_error_type": _safe_type(_exception_field(exc, "cleanup_error_type")),
        "cleanup_verified": _exception_field(exc, "cleanup_verified"),
        "code": code,
        "container_absence_verified": _exception_field(exc, "container_absence_verified"),
        "container_id": _container_id(_exception_field(exc, "container_id")),
        "container_name": _container_name(_exception_field(exc, "container_name")),
        "export_staging_removed": _exception_field(exc, "export_staging_removed"),
        "image": _exception_field(exc, "image"),
        "image_id": _exception_field(exc, "image_id"),
        "primary_error_type": _safe_type(_exception_field(exc, "primary_error_type")),
        "returncode": _exception_field(exc, "returncode"),
        "stage": stage,
        "stderr": _failure_output_summary(exc, "stderr", isolation.MAX_STDERR_BYTES),
        "stdout": _failure_output_summary(exc, "stdout", isolation.MAX_STDOUT_BYTES),
    }


def _result_summary(result: Any) -> dict[str, Any]:
    return {
        "cleanup_verified": bool(getattr(result, "cleanup_verified", False)),
        "container_absence_verified": bool(
            getattr(result, "container_absence_verified", False)
        ),
        "container_id": _container_id(getattr(result, "container_id", None)),
        "container_name": _container_name(getattr(result, "container_name", None)),
        "export_staging_removed": bool(
            getattr(result, "export_staging_removed", False)
        ),
        "image": getattr(result, "image", None),
        "image_id": getattr(result, "image_id", None),
        "returncode": getattr(result, "returncode", None),
        "stderr": _output_summary(
            getattr(result, "stderr", ""),
            limit=isolation.MAX_STDERR_BYTES,
            truncated=bool(getattr(result, "stderr_truncated", False)),
        ),
        "stdout": _output_summary(
            getattr(result, "stdout", ""),
            limit=isolation.MAX_STDOUT_BYTES,
            truncated=bool(getattr(result, "stdout_truncated", False)),
        ),
    }


def _parse_canonical_stdout(result: Any) -> dict[str, Any] | None:
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str) or bool(getattr(result, "stdout_truncated", False)):
        return None
    raw = stdout.encode("utf-8")
    if len(raw) > isolation.MAX_STDOUT_BYTES:
        return None
    try:
        value = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(value, dict) or raw != _canonical_json(value, newline=True):
        return None
    return value


def _positive_attestation_ok(value: dict[str, Any] | None, limiter_sha256: str) -> bool:
    if value is None:
        return False
    expected = {
        **probes.EXPECTED_RUNTIME,
        "cadclaw_pin_basis": probes.EXPECTED_PIN_BASIS,
        "capabilities_zero": True,
        "build_provenance_sha256": value.get("build_provenance_sha256"),
        "cwd": "/workspace",
        "docker_socket_absent": True,
        "environment_keys": sorted(probes.EXPECTED_ENVIRONMENT),
        "gid": 65532,
        "kit_read_only": True,
        "network_interfaces": ["lo"],
        "no_new_privileges": True,
        "root_read_only": True,
        "run_limiter_sha256": limiter_sha256,
        "seccomp_filtered": True,
        "staged_input_root": "/marb-input",
        "staged_inputs_read_only": True,
        "uid": 65532,
    }
    return bool(
        isinstance(value.get("build_provenance_sha256"), str)
        and HEX64.fullmatch(value["build_provenance_sha256"])
        and value == expected
    )


def _source_sha256s(source_manifest: Mapping[str, Any]) -> dict[str, str] | None:
    entries = source_manifest.get("entries")
    if not isinstance(entries, list):
        return None
    result: dict[str, str] = {}
    for item in entries:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or not HEX64.fullmatch(item["sha256"])
        ):
            return None
        result[item["path"]] = item["sha256"]
    return result


def _build_provenance_from_labels(labels: Mapping[str, str]) -> dict[str, Any] | None:
    if set(labels) != set(isolation.MARB_IMAGE_PROVENANCE_LABELS):
        return None
    if not all(isinstance(value, str) for value in labels.values()):
        return None
    try:
        native_count = int(labels[_LABEL_PREFIX + "native-deb-package-count"])
        native_bytes = int(labels[_LABEL_PREFIX + "native-deb-total-bytes"])
    except (KeyError, ValueError):
        return None
    return {
        "schema": "marb_h2b_image_build_provenance.v3",
        "runtime_contract": labels[_LABEL_PREFIX + "runtime-contract"],
        "runtime_contract_sha256": labels[_LABEL_PREFIX + "runtime-contract-sha256"],
        "cadclaw_commit": labels[_LABEL_PREFIX + "cadclaw-commit"],
        "cadclaw_gate_spec_version": labels[
            _LABEL_PREFIX + "cadclaw-gate-spec-version"
        ],
        "cadclaw_gate_registry_version": labels[
            _LABEL_PREFIX + "cadclaw-gate-registry-version"
        ],
        "cadclaw_pin_basis": labels[_LABEL_PREFIX + "cadclaw-pin-basis"],
        "cadclaw_source_manifest_sha256": labels[
            _LABEL_PREFIX + "cadclaw-source-manifest-sha256"
        ],
        "cadclaw_calibration_sha256": labels[
            _LABEL_PREFIX + "cadclaw-calibration-sha256"
        ],
        "native_deb_package_count": native_count,
        "native_deb_total_bytes": native_bytes,
        **{key: labels[label] for label, key in _LABEL_TO_PROVENANCE_KEY.items()},
    }


def _image_provenance_ok(
    value: Any,
    *,
    image: str,
    source_manifest: Mapping[str, Any],
    positive_attestation: Mapping[str, Any] | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Cross-bind whitelisted Docker labels, tracked source, and in-image bytes."""
    if not isinstance(value, Mapping) or set(value) != {
        "image",
        "image_id",
        "labels",
        "repo_digests",
    }:
        return False, None
    labels = value.get("labels")
    repo_digests = value.get("repo_digests")
    if (
        value.get("image") != image
        or _image_id(value.get("image_id")) is None
        or not isinstance(repo_digests, list)
        or image not in repo_digests
        or not isinstance(labels, Mapping)
        or set(labels) != set(isolation.MARB_IMAGE_PROVENANCE_LABELS)
        or not all(
            isinstance(label, str) and isinstance(item, str)
            for label, item in labels.items()
        )
    ):
        return False, None
    source_sha256s = _source_sha256s(source_manifest)
    if source_sha256s is None:
        return False, None
    fixed = {
        _LABEL_PREFIX + "runtime-contract": probes.EXPECTED_RUNTIME[
            "runtime_contract"
        ],
        _LABEL_PREFIX + "runtime-contract-sha256": source_sha256s.get(
            "harness/container/runtime-contract.v0.13.json"
        ),
        _LABEL_PREFIX + "cadclaw-version": probes.EXPECTED_RUNTIME[
            "cadclaw_version"
        ],
        _LABEL_PREFIX + "cadclaw-commit": probes.EXPECTED_RUNTIME["cadclaw_commit"],
        _LABEL_PREFIX + "cadclaw-gate-spec-version": probes.EXPECTED_RUNTIME[
            "cadclaw_gate_spec_version"
        ],
        _LABEL_PREFIX + "cadclaw-gate-registry-version": probes.EXPECTED_RUNTIME[
            "cadclaw_gate_registry_version"
        ],
        _LABEL_PREFIX + "cadclaw-pin-basis": probes.EXPECTED_PIN_BASIS,
        _LABEL_PREFIX + "cadclaw-source-manifest-sha256": probes.EXPECTED_RUNTIME[
            "cadclaw_source_manifest_sha256"
        ],
        _LABEL_PREFIX + "cadclaw-calibration-sha256": source_sha256s.get(
            "harness/container/cadclaw-calibration.fad0dd55.json"
        ),
        _LABEL_PREFIX + "cadquery-version": probes.EXPECTED_RUNTIME[
            "cadquery_version"
        ],
        _LABEL_PREFIX + "cadquery-ocp-version": probes.EXPECTED_RUNTIME[
            "cadquery_ocp_version"
        ],
        _LABEL_PREFIX + "native-deb-package-count": "39",
        _LABEL_PREFIX + "native-deb-total-bytes": "48570480",
        _LABEL_PREFIX + "run-limiter-sha256": source_sha256s.get(
            "harness/container/run_limited.py"
        ),
        _LABEL_PREFIX + "dockerfile-sha256": source_sha256s.get(
            "harness/container/Dockerfile"
        ),
        _LABEL_PREFIX + "context-dockerignore-sha256": source_sha256s.get(
            "harness/container/.dockerignore"
        ),
        _LABEL_PREFIX + "requirements-lock-sha256": source_sha256s.get(
            "harness/container/requirements.lock"
        ),
        _LABEL_PREFIX + "native-deb-lock-sha256": source_sha256s.get(
            "harness/container/native-debs.lock.json"
        ),
        _LABEL_PREFIX + "native-bundle-verifier-sha256": source_sha256s.get(
            "harness/container/verify_native_bundle.py"
        ),
    }
    if any(
        expected is None or labels.get(label) != expected
        for label, expected in fixed.items()
    ):
        return False, None
    base_image = labels.get(_LABEL_PREFIX + "base-image")
    try:
        isolation.validate_image_reference(base_image)  # type: ignore[arg-type]
    except isolation.IsolationError:
        return False, None
    dynamic_hashes = {
        _LABEL_PREFIX + "wheelhouse-manifest-sha256",
        _LABEL_PREFIX + "cadclaw-wheel-sha256",
        _LABEL_PREFIX + "native-deb-manifest-sha256",
        _LABEL_PREFIX + "build-context-manifest-sha256",
    }
    if not all(HEX64.fullmatch(labels[label]) for label in dynamic_hashes):
        return False, None
    provenance = _build_provenance_from_labels(labels)
    expected_hash = (
        _sha256(_canonical_json(provenance, newline=True))
        if provenance is not None
        else None
    )
    if (
        positive_attestation is None
        or positive_attestation.get("build_provenance_sha256") != expected_hash
    ):
        return False, None
    return True, {
        "build_provenance_sha256": expected_hash,
        "image": image,
        "image_id": value["image_id"],
        "labels": dict(sorted(labels.items())),
    }


def _case_checks(
    case: probes.ProbeCase,
    result: Any | None,
    failure: dict[str, Any] | None,
    *,
    image: str,
    limiter_sha256: str,
    docker_commands: int,
    expected_image_id: str | None,
    workspace_unchanged: bool,
    inputs_unchanged: bool,
    input_contract_exact: bool,
    probe_bytes_exact: bool,
    docker_executable_stable: bool,
    staging_removed: bool,
) -> tuple[dict[str, bool], dict[str, Any] | None]:
    parsed = _parse_canonical_stdout(result) if result is not None else None
    expected_docker_commands = (
        0
        if case.case_id == "workspace_input_rejection_overflow"
        else 9 if case.case_id == "positive_provenance_and_import" else 8
    )
    common_result = bool(
        result is not None
        and failure is None
        and getattr(result, "image", None) == image
        and isinstance(getattr(result, "image_id", None), str)
        and isolation.IMAGE_ID_RE.fullmatch(getattr(result, "image_id"))
        and (
            expected_image_id is None
            or getattr(result, "image_id", None) == expected_image_id
        )
        and getattr(result, "cleanup_verified", False) is True
        and getattr(result, "container_absence_verified", False) is True
        and getattr(result, "export_staging_removed", False) is True
        and getattr(result, "stderr", None) == ""
        and getattr(result, "stderr_truncated", None) is False
        and getattr(result, "stdout_truncated", None) is False
        and _container_name(getattr(result, "container_name", None)) is not None
        and _container_id(getattr(result, "container_id", None)) is not None
        and inputs_unchanged
        and workspace_unchanged
        and staging_removed
    )
    common_runtime_failure = bool(
        failure
        and failure["cleanup_attempted"] is True
        and failure["cleanup_error_type"] is None
        and failure["cleanup_verified"] is True
        and failure["container_absence_verified"] is True
        and failure["container_name"] is not None
        and failure["container_id"] is not None
        and failure["export_staging_removed"] is True
        and failure["image"] == image
        and failure["image_id"] == expected_image_id
        and staging_removed
        and inputs_unchanged
        and workspace_unchanged
    )
    checks: dict[str, bool]
    if case.case_id == "positive_provenance_and_import":
        checks = {
            "attestation_exact": _positive_attestation_ok(parsed, limiter_sha256),
            "cleanup_and_absence": common_result,
            "returncode_exact": getattr(result, "returncode", None) == 0,
        }
    elif case.case_id == "expected_nonzero_exit":
        checks = {
            "cleanup_and_absence": common_result,
            "returncode_exact": getattr(result, "returncode", None) == probes.NONZERO_EXIT_CODE,
            "stdout_exact": getattr(result, "stdout", None) == "",
            "workspace_unchanged": workspace_unchanged,
        }
    elif case.case_id == "network_denial":
        expected = {
            "docker_socket_absent": True,
            "network_interfaces": ["lo"],
            "outbound_denied": True,
            "schema": probes.PROBE_SCHEMA,
        }
        checks = {
            "cleanup_and_absence": common_result,
            "denial_exact": parsed == expected,
            "returncode_exact": getattr(result, "returncode", None) == 0,
        }
    elif case.case_id == "protected_write_denial":
        expected = {
            "export_tmpfs_writable": True,
            "protected_denials": {
                "host_workspace": True,
                "kit": True,
                "root": True,
                "staged_input": True,
            },
            "schema": probes.PROBE_SCHEMA,
            "tmp_writable": True,
            "workspace_writable": True,
        }
        checks = {
            "cleanup_and_absence": common_result,
            "payload_exact": parsed == expected,
            "returncode_exact": getattr(result, "returncode", None) == 0,
        }
    elif case.case_id == "exact_clean_environment":
        checks = {
            "cleanup_and_absence": common_result,
            "environment_exact": parsed
            == {
                "environment_keys": sorted(probes.EXPECTED_ENVIRONMENT),
                "schema": probes.PROBE_SCHEMA,
            },
            "returncode_exact": getattr(result, "returncode", None) == 0,
        }
    elif case.case_id == "stdout_overflow_rejection":
        marker = isolation._OUTPUT_TRUNCATION_MARKER
        expected_stdout = (
            b"x" * (isolation.MAX_STDOUT_BYTES - len(marker)) + marker
        )
        checks = {
            "failure_runtime_invariants": common_runtime_failure,
            "cleanup_verified": bool(failure and failure["cleanup_verified"] is True),
            "container_absent": bool(
                failure and failure["container_absence_verified"] is True
            ),
            "output_rejected": bool(
                failure
                and failure["code"] == "output_limit_exceeded"
                and failure["stage"] == "container_start"
                and failure["returncode"] == 0
                and failure["stdout"]["bytes"] == isolation.MAX_STDOUT_BYTES
                and failure["stdout"]["sha256"] == _sha256(expected_stdout)
                and failure["stdout"]["truncated"] is True
                and failure["stdout"]["within_bound"] is True
                and failure["stderr"]["bytes"] == 0
                and failure["stderr"]["sha256"] == _sha256(b"")
                and failure["stderr"]["truncated"] is False
                and failure["primary_error_type"] == "IsolationError"
            ),
            "docker_commands_exact": docker_commands == 8,
            "owned_container_identity": bool(
                failure and failure["container_name"] and failure["container_id"]
            ),
            "runtime_identity_exact": bool(
                failure
                and failure["image"] == image
                and failure["image_id"] == expected_image_id
            ),
            "staging_removed": bool(failure and failure["export_staging_removed"] is True),
        }
    elif case.case_id == "workspace_input_rejection_overflow":
        checks = {
            "cleanup_not_attempted": bool(failure)
            and failure["cleanup_attempted"] is False
            and failure["cleanup_verified"] is None
            and failure["container_absence_verified"] is None
            and failure["container_name"] is None
            and failure["container_id"] is None
            and failure["export_staging_removed"] is None,
            "docker_commands_zero": docker_commands == 0,
            "input_limit_rejected": bool(failure)
            and failure["code"] == "workspace_input_limit_exceeded",
            "inputs_unchanged": inputs_unchanged,
            "pre_docker": bool(failure) and failure["stage"] == "validation",
            "runtime_not_inspected": bool(failure)
            and failure["image"] is None
            and failure["image_id"] is None
            and failure["returncode"] is None,
            "staging_absent": staging_removed,
            "workspace_preserved": workspace_unchanged,
        }
    elif case.case_id == "export_workspace_overflow":
        checks = {
            "failure_runtime_invariants": common_runtime_failure,
            "cleanup_verified": bool(failure and failure["cleanup_verified"] is True),
            "container_absent": bool(
                failure and failure["container_absence_verified"] is True
            ),
            "entry_overflow_exact": bool(
                failure
                and failure["code"] == "workspace_entry_limit_exceeded"
                and failure["stage"] == "workspace_limiter"
                and failure["returncode"] == isolation.LIMITER_EXIT
                and failure["stdout"]["bytes"] == 0
                and failure["stderr"]["bytes"]
                == len(isolation.WORKSPACE_ENTRY_LIMIT_REASON.encode("utf-8"))
                and failure["stderr"]["sha256"]
                == _sha256(isolation.WORKSPACE_ENTRY_LIMIT_REASON.encode("utf-8"))
                and failure["stderr"]["truncated"] is False
                and failure["primary_error_type"] == "IsolationError"
            ),
            "docker_commands_exact": docker_commands == 8,
            "owned_container_identity": bool(
                failure and failure["container_name"] and failure["container_id"]
            ),
            "runtime_identity_exact": bool(
                failure
                and failure["image"] == image
                and failure["image_id"] == expected_image_id
            ),
            "staging_removed": staging_removed
            and bool(failure and failure["export_staging_removed"] is True),
            "workspace_preserved": workspace_unchanged,
        }
    else:  # exact ninth case: timeout
        checks = {
            "failure_runtime_invariants": common_runtime_failure,
            "cleanup_verified": bool(failure and failure["cleanup_verified"] is True),
            "container_absent": bool(
                failure and failure["container_absence_verified"] is True
            ),
            "staging_removed": staging_removed
            and bool(failure and failure["export_staging_removed"] is True),
            "timeout_rejected": bool(failure and failure["code"] == "execution_timeout"),
            "timeout_cause_exact": bool(
                failure
                and failure["stage"] == "container_start"
                and failure["primary_error_type"] == "TimeoutExpired"
                and failure["returncode"] is None
                and failure["stdout"]["bytes"] == 0
                and failure["stderr"]["bytes"] == 0
                and failure["stdout"]["truncated"] is False
                and failure["stderr"]["truncated"] is False
            ),
            "docker_commands_exact": docker_commands == 8,
            "owned_container_identity": bool(
                failure and failure["container_name"] and failure["container_id"]
            ),
            "runtime_identity_exact": bool(
                failure
                and failure["image"] == image
                and failure["image_id"] == expected_image_id
            ),
        }
    checks.update(
        {
            "docker_executable_stable": docker_executable_stable,
            "docker_command_count_exact": docker_commands == expected_docker_commands,
            "host_workspace_unchanged": workspace_unchanged,
            "probe_bytes_exact": probe_bytes_exact,
            "staged_inputs_exact_and_unchanged": input_contract_exact and inputs_unchanged,
            "staging_removed": staging_removed,
        }
    )
    return checks, parsed


class _DefaultSmokeEngine:
    def __init__(
        self,
        *,
        image: str,
        docker_executable: str,
        input_root: Path,
        ordinal: int,
    ) -> None:
        self.docker_command_count = 0
        base_runner = isolation._default_command_runner

        def counted_runner(argv: Sequence[str], *, timeout=None, env=None):
            self.docker_command_count += 1
            return base_runner(argv, timeout=timeout, env=env)

        candidate = uuid.UUID(f"00000000-0000-4000-8000-{ordinal:012d}")
        allocated = False

        def uuid_factory() -> uuid.UUID:
            nonlocal allocated
            if allocated:
                raise AssertionError("unexpected second smoke container allocation")
            allocated = True
            return candidate

        self._engine = isolation.IsolatedDockerPython(
            image,
            input_root=input_root,
            docker_executable=docker_executable,
            command_runner=counted_runner,
            uuid_factory=uuid_factory,
        )

    def execute(self, workspace: Path, relative_script: str, **kwargs: Any) -> Any:
        return self._engine.execute(workspace, relative_script, **kwargs)

    def inspect_image_provenance(self) -> Mapping[str, Any]:
        return self._engine.inspect_local_image_provenance()


def _source_manifest() -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[1]
    identities = [
        _text_identity(repository.joinpath(*path.split("/")), path)
        for path in SOURCE_FILES
    ]
    return {
        "entries": identities,
        "manifest_sha256": _sha256(_canonical_json(identities)),
    }


def _verify_source_checkout(
    source_root: Path,
    source_revision: str,
    source_tree: str,
    git_executable: str,
    git_executable_sha256: str,
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove that the executing sources are exact blobs in one clean HEAD."""
    try:
        from . import cohort_executor as executor
    except ImportError:  # pragma: no cover - script-mode CLI
        import cohort_executor as executor  # type: ignore[no-redef]

    repository = Path(source_root).resolve(strict=True)
    executing_root = Path(__file__).resolve(strict=True).parents[1]
    if repository != executing_root:
        raise SmokeRunnerError("source root does not contain the executing smoke runner")
    authorization = {
        "git_executable": git_executable,
        "git_executable_sha256": git_executable_sha256,
    }

    def capture(arguments: Sequence[str], *, expect_empty: bool = False) -> str:
        try:
            with executor._locked_git_spawn_identity(authorization) as git_identity:
                completed = subprocess.run(
                    [
                        git_identity["path"],
                        "--no-replace-objects",
                        "-c",
                        f"safe.directory={repository.as_posix()}",
                        *arguments,
                    ],
                    cwd=repository,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=10.0,
                    check=False,
                    env=executor._minimal_git_env(),
                )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SmokeRunnerError("source checkout cannot be verified") from exc
        raw = completed.stdout
        if completed.returncode != 0 or len(raw) > 4096:
            raise SmokeRunnerError("source checkout cannot be verified")
        if expect_empty:
            if raw:
                raise SmokeRunnerError("source checkout has tracked changes")
            return ""
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SmokeRunnerError("source checkout returned a malformed identity") from exc
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]
        if "\n" in text or "\r" in text:
            raise SmokeRunnerError("source checkout returned a malformed identity")
        return text

    literal_head = capture(("rev-parse", "--verify", "HEAD"))
    head = capture(("rev-parse", "--verify", "HEAD^{commit}"))
    tree = capture(("rev-parse", "--verify", "HEAD^{tree}"))
    if (
        not HEX40.fullmatch(literal_head)
        or literal_head != source_revision
        or head != source_revision
        or tree != source_tree
    ):
        raise SmokeRunnerError("source checkout HEAD or tree does not match authorization")
    capture(
        ("status", "--porcelain=v1", "--untracked-files=no", "--ignore-submodules=none"),
        expect_empty=True,
    )

    committed_entries: list[dict[str, Any]] = []
    for item in source_manifest.get("entries", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise SmokeRunnerError("source implementation manifest is malformed")
        try:
            raw = executor._read_committed_blob_with_git(
                authorization, repository, source_revision, item["path"]
            )
        except Exception as exc:
            raise SmokeRunnerError("committed smoke source cannot be verified") from exc
        identity = {
            "bytes": len(raw),
            "hash_mode": "raw",
            "path": item["path"],
            "sha256": _sha256(raw),
        }
        if identity != dict(item):
            raise SmokeRunnerError("executing smoke source differs from committed HEAD")
        committed_entries.append(identity)
    git_identity = executor._verify_host_git_executable(authorization)
    return {
        "clean_tracked_checkout": True,
        "committed_blob_manifest_sha256": _sha256(_canonical_json(committed_entries)),
        "git_executable": {
            "label": PureWindowsPath(git_executable).name,
            "path_sha256": _sha256(git_executable.encode("utf-8")),
            "sha256": git_identity["sha256"],
        },
        "literal_head": literal_head,
        "revision": head,
        "tree": tree,
    }


def _source_proof_matches(
    proof: Any,
    *,
    revision: str,
    tree: str,
    git_sha256: str,
    source_manifest: Mapping[str, Any],
) -> bool:
    return bool(
        isinstance(proof, Mapping)
        and proof.get("clean_tracked_checkout") is True
        and proof.get("literal_head") == revision
        and proof.get("revision") == revision
        and proof.get("tree") == tree
        and proof.get("committed_blob_manifest_sha256")
        == source_manifest.get("manifest_sha256")
        and isinstance(proof.get("git_executable"), Mapping)
        and proof["git_executable"].get("sha256") == git_sha256
    )


def run_smoke(
    *,
    image: str,
    docker_executable: str,
    docker_executable_sha256: str,
    git_executable: str,
    git_executable_sha256: str,
    source_revision: str,
    source_root: Path,
    source_tree: str,
    work_root: Path,
    engine_factory: EngineFactory | None = None,
    source_verifier: SourceVerifier | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run the exact nine cases once and return a canonicalizable envelope."""
    if tuple(item.case_id for item in probes.CASES) != probes.REQUIRED_CASE_IDS:
        raise SmokeRunnerError("runtime smoke case contract is malformed")
    isolation.validate_image_reference(image)
    if not isinstance(docker_executable_sha256, str) or not HEX64.fullmatch(
        docker_executable_sha256
    ):
        raise SmokeRunnerError("Docker executable digest is malformed")
    if not isinstance(source_revision, str) or not HEX40.fullmatch(source_revision):
        raise SmokeRunnerError("source revision is malformed")
    if not isinstance(source_tree, str) or not HEX40.fullmatch(source_tree):
        raise SmokeRunnerError("source tree is malformed")
    if not isinstance(git_executable_sha256, str) or not HEX64.fullmatch(
        git_executable_sha256
    ):
        raise SmokeRunnerError("Git executable digest is malformed")
    work_root = Path(work_root).resolve(strict=True)
    source_manifest = _source_manifest()
    verifier = source_verifier or _verify_source_checkout
    source_preflight = verifier(
        Path(source_root),
        source_revision,
        source_tree,
        git_executable,
        git_executable_sha256,
        source_manifest,
    )
    if not _source_proof_matches(
        source_preflight,
        revision=source_revision,
        tree=source_tree,
        git_sha256=git_executable_sha256,
        source_manifest=source_manifest,
    ):
        raise SmokeRunnerError("source preflight proof is malformed or mismatched")
    docker_file_identity = _verify_docker_executable(
        docker_executable, docker_executable_sha256
    )
    expected_input_manifest = _expected_input_manifest()
    limiter_sha256 = next(
        item["sha256"]
        for item in source_manifest["entries"]
        if item["path"] == "harness/container/run_limited.py"
    )
    started = _utc_text(now())
    started_tick = monotonic()
    case_records: list[dict[str, Any]] = []
    positive_attestation: dict[str, Any] | None = None
    image_provenance: dict[str, Any] | None = None
    observed_image_id: str | None = None
    not_run_case_ids: list[str] = []
    factory = engine_factory

    with tempfile.TemporaryDirectory(prefix="marb-runtime-smoke-", dir=work_root) as temporary:
        temporary_root = Path(temporary).resolve(strict=True)
        for ordinal, case in enumerate(probes.CASES, 1):
            case_started = _utc_text(now())
            case_tick = monotonic()
            case_root = temporary_root / f"{ordinal:02d}-{case.case_id}"
            workspace = case_root / "workspace"
            input_root = case_root / "inputs"
            case_root.mkdir()
            workspace.mkdir()
            script_name = "probe.py"
            _write_exact(workspace / script_name, case.source)
            if case.oversized_host_input:
                for index in range(isolation.MAX_WORKSPACE_ENTRIES):
                    (workspace / f"overflow-{index:04d}").mkdir()
            _materialize_inputs(input_root)
            before_workspace = _manifest(workspace)
            before_inputs = _manifest(input_root)
            materialized_probe_before = (workspace / script_name).read_bytes()
            if materialized_probe_before != case.source:
                raise SmokeRunnerError("materialized smoke probe does not match its contract")
            if before_inputs != expected_input_manifest:
                raise SmokeRunnerError("materialized smoke inputs do not match their contract")
            before_staging = sorted(item.name for item in case_root.glob(".marb-export-*.tar"))
            if factory is None:
                engine: SmokeEngine = _DefaultSmokeEngine(
                    image=image,
                    docker_executable=docker_executable,
                    input_root=input_root,
                    ordinal=ordinal,
                )
            else:
                engine = factory(case, ordinal, input_root)
            command_before = _engine_count(engine)
            result: Any | None = None
            exception: BaseException | None = None
            raw_provenance: Mapping[str, Any] | None = None
            docker_before_case: dict[str, Any] | None = None
            docker_after_case: dict[str, Any] | None = None
            try:
                docker_before_case = _verify_docker_executable(
                    docker_executable, docker_executable_sha256
                )
                if ordinal == 1:
                    raw_provenance = engine.inspect_image_provenance()
                result = engine.execute(
                    workspace,
                    script_name,
                    inspect_timeout=(
                        30.0
                        if case.case_id == "timeout"
                        else min(30.0, case.timeout_seconds)
                    ),
                    execution_timeout=(60.0 if case.case_id == "timeout" else case.timeout_seconds),
                    attached_run_timeout=(
                        case.timeout_seconds if case.case_id == "timeout" else None
                    ),
                )
            except Exception as exc:  # fixed negative cases intentionally fail
                exception = exc
            try:
                docker_after_case = _verify_docker_executable(
                    docker_executable, docker_executable_sha256
                )
            except Exception as exc:
                exception = SmokeRunnerError(
                    "Docker executable changed during a smoke case",
                    code="docker_executable_drift",
                    stage=(
                        "post_case_readback"
                        if docker_before_case is not None
                        else "pre_case_validation"
                    ),
                    prior=exception,
                )
            command_after = _engine_count(engine)
            docker_commands = max(0, command_after - command_before)
            after_workspace = _manifest(workspace)
            after_inputs = _manifest(input_root)
            materialized_probe_after = (workspace / script_name).read_bytes()
            after_staging = sorted(item.name for item in case_root.glob(".marb-export-*.tar"))
            failure = _failure_summary(exception) if exception is not None else None
            checks, parsed = _case_checks(
                case,
                result,
                failure,
                image=image,
                limiter_sha256=limiter_sha256,
                docker_commands=docker_commands,
                expected_image_id=observed_image_id,
                workspace_unchanged=before_workspace == after_workspace,
                inputs_unchanged=before_inputs == after_inputs,
                input_contract_exact=before_inputs == expected_input_manifest,
                probe_bytes_exact=(
                    materialized_probe_before == materialized_probe_after == case.source
                ),
                docker_executable_stable=bool(
                    docker_before_case == docker_after_case == docker_file_identity
                ),
                staging_removed=before_staging == after_staging == [],
            )
            bound_image_id = getattr(result, "image_id", None) if result is not None else None
            image_id_source = "execution_result" if isinstance(bound_image_id, str) else None
            if not isinstance(bound_image_id, str) and failure is not None:
                failure_image_id = failure.get("image_id")
                if isinstance(failure_image_id, str):
                    bound_image_id = failure_image_id
                    image_id_source = "failure_evidence"
            if not isinstance(bound_image_id, str):
                bound_image_id = observed_image_id
                image_id_source = (
                    "prior_positive_case" if isinstance(observed_image_id, str) else None
                )
            if ordinal == 1:
                provenance_ok, safe_provenance = _image_provenance_ok(
                    raw_provenance if exception is None else None,
                    image=image,
                    source_manifest=source_manifest,
                    positive_attestation=parsed,
                )
                checks["image_provenance_exact"] = bool(
                    provenance_ok
                    and safe_provenance is not None
                    and safe_provenance["image_id"] == bound_image_id
                )
                if checks["image_provenance_exact"]:
                    image_provenance = safe_provenance
            status = "pass" if checks and all(checks.values()) else "fail"
            if case.case_id == "positive_provenance_and_import" and status == "pass":
                positive_attestation = parsed
                observed_image_id = _image_id(bound_image_id)
            record = {
                "bindings": {
                    "docker_executable_after": docker_after_case,
                    "docker_executable_before": docker_before_case,
                    "docker_executable_sha256": docker_executable_sha256,
                    "image_id": bound_image_id,
                    "image_id_source": image_id_source,
                    "image_repo_digest": image,
                    "input_manifest_sha256": before_inputs["manifest_sha256"],
                    "source_revision": source_revision,
                    "source_tree": source_tree,
                },
                "case_id": case.case_id,
                "checks": checks,
                "docker_commands": docker_commands,
                "expected": case.expected,
                "exception_category": failure["code"] if failure is not None else None,
                "failure": failure,
                "ordinal": ordinal,
                "pass": status == "pass",
                "probe": {
                    "after": {
                        "bytes": len(materialized_probe_after),
                        "sha256": _sha256(materialized_probe_after),
                    },
                    "before": {
                        "bytes": len(materialized_probe_before),
                        "sha256": _sha256(materialized_probe_before),
                    },
                    "contract": {"bytes": len(case.source), "sha256": _sha256(case.source)},
                },
                "result": _result_summary(result) if result is not None else None,
                "staged_input_after": after_inputs,
                "staged_input_before": before_inputs,
                "status": status,
                "timing": {
                    "duration_seconds": round(max(0.0, monotonic() - case_tick), 6),
                    "ended_utc": _utc_text(now()),
                    "started_utc": case_started,
                },
                "workspace_after": after_workspace,
                "workspace_before": before_workspace,
            }
            case_records.append(record)
            if status != "pass":
                not_run_case_ids = list(probes.CASE_IDS[ordinal:])
                break

    docker_postflight: dict[str, Any] | None = None
    source_postflight: dict[str, Any] | None = None
    try:
        docker_postflight = _verify_docker_executable(
            docker_executable, docker_executable_sha256
        )
    except Exception:
        pass
    source_manifest_postflight = _source_manifest()
    try:
        source_postflight = verifier(
            Path(source_root),
            source_revision,
            source_tree,
            git_executable,
            git_executable_sha256,
            source_manifest_postflight,
        )
    except Exception:
        pass
    postflight_checks = {
        "docker_executable_unchanged": docker_postflight == docker_file_identity,
        "source_checkout_unchanged": bool(
            source_manifest_postflight == source_manifest
            and source_postflight == source_preflight
            and _source_proof_matches(
                source_postflight,
                revision=source_revision,
                tree=source_tree,
                git_sha256=git_executable_sha256,
                source_manifest=source_manifest_postflight,
            )
        ),
    }
    overall_status = (
        "pass"
        if all(item["status"] == "pass" for item in case_records)
        and all(postflight_checks.values())
        else "fail"
    )
    probe_contract = [
        {
            "case_id": item.case_id,
            "expected": item.expected,
            "probe_bytes": len(item.source),
            "probe_sha256": _sha256(item.source),
            "timeout_seconds": item.timeout_seconds,
        }
        for item in probes.CASES
    ]
    input_contract = [
        {"bytes": len(raw), "path": path, "sha256": _sha256(raw)}
        for path, raw in probes.RUNTIME_SMOKE_INPUTS
    ]
    evidence = {
        "case_order": list(probes.CASE_IDS),
        "cases": case_records,
        "container": {
            "image": image,
            "observed_image_id": observed_image_id,
            "provenance": image_provenance,
        },
        "docker_executable": {
            "label": PureWindowsPath(docker_executable).name,
            "file_bytes": docker_file_identity["bytes"],
            "path_sha256": _sha256(docker_executable.encode("utf-8")),
            "sha256": docker_executable_sha256,
            "postflight": docker_postflight,
        },
        "execution_policy": {
            "credentials_loaded": False,
            "model_calls": False,
            "network_mode": "none",
            "provider_constructed": False,
        },
        "positive_attestation": positive_attestation,
        "postflight_checks": postflight_checks,
        "not_run_case_ids": not_run_case_ids,
        "probe_contract": {
            "cases": probe_contract,
            "manifest_sha256": _sha256(_canonical_json(probe_contract)),
        },
        "schema": EVIDENCE_SCHEMA,
        "source": {
            "implementation": source_manifest,
            "postflight": source_postflight,
            "preflight": source_preflight,
            "revision": source_revision,
            "tree": source_tree,
        },
        "staged_input_contract": {
            "entries": input_contract,
            "manifest_sha256": _sha256(_canonical_json(input_contract)),
            "materialized_manifest": expected_input_manifest,
        },
        "status": overall_status,
        "timing": {
            "duration_seconds": round(max(0.0, monotonic() - started_tick), 6),
            "ended_utc": _utc_text(now()),
            "started_utc": started,
        },
    }
    digest = _sha256(_canonical_json(evidence))
    return {"evidence": evidence, "evidence_sha256": digest, "schema": ENVELOPE_SCHEMA}


def write_evidence(path: Path, envelope: Mapping[str, Any]) -> None:
    raw = _canonical_json(dict(envelope), newline=True)
    path = Path(path)
    path.parent.resolve(strict=True)
    with path.open("xb") as handle:
        written = handle.write(raw)
        if written != len(raw):
            raise SmokeRunnerError("runtime smoke evidence write was incomplete")
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != raw:
        raise SmokeRunnerError("runtime smoke evidence readback did not match")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the nine-case provider-free MARB runtime smoke")
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker-executable", required=True)
    parser.add_argument("--docker-executable-sha256", required=True)
    parser.add_argument("--git-executable", required=True)
    parser.add_argument("--git-executable-sha256", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        envelope = run_smoke(
            image=args.image,
            docker_executable=args.docker_executable,
            docker_executable_sha256=args.docker_executable_sha256,
            git_executable=args.git_executable,
            git_executable_sha256=args.git_executable_sha256,
            source_revision=args.source_revision,
            source_root=args.source_root,
            source_tree=args.source_tree,
            work_root=args.work_root,
        )
        write_evidence(args.output, envelope)
    except Exception:
        print(_canonical_json({"status": "rejected"}).decode("ascii"), end="")
        return 2
    output = {
        "evidence_sha256": envelope["evidence_sha256"],
        "output": args.output.name,
        "status": envelope["evidence"]["status"],
    }
    print(_canonical_json(output, newline=True).decode("ascii"), end="")
    return 0 if envelope["evidence"]["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EVIDENCE_SCHEMA",
    "ENVELOPE_SCHEMA",
    "EngineFactory",
    "SmokeRunnerError",
    "main",
    "run_smoke",
    "write_evidence",
]
