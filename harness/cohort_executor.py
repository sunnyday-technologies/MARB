"""Explicitly authorized, provenance-first MARB cohort execution.

The H2b executor is deliberately separate from the legacy local harness.  The
trusted host process owns the provider conversation and the run journal.  Only
model-authored Python is executed, and only through the digest-pinned isolated
container backend in :mod:`isolated_container`.

Importing this module performs no environment reads, network access, provider
construction, subprocess execution, directory creation, or registry mutation.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import datetime as dt
import decimal
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterator, Mapping, NoReturn, Protocol, Sequence

try:  # package import in tests; script import for the CLI
    from . import cohort_runner
    from .runtime_smoke_probes import POSITIVE_PROVENANCE_AND_IMPORT_SOURCE
except ImportError:  # pragma: no cover - exercised by CLI smoke tests
    import cohort_runner  # type: ignore[no-redef]
    from runtime_smoke_probes import (  # type: ignore[no-redef]
        POSITIVE_PROVENANCE_AND_IMPORT_SOURCE,
    )


AUTH_SCHEMA = "marb_execution_authorization.v3"
RUN_LOG_SCHEMA = "marb_executor_run_log.v2"
EVENT_SCHEMA = "marb_executor_event.v1"
PROVIDER_PROTOCOL = "openai-compatible-chat-completions"
EXECUTE_LITERAL_PREFIX = "EXECUTE_MARB_MODEL_CALLS"
RUNTIME_CONTRACT_ID = "marb-v0.13-h2b"
RUNTIME_CONTRACT_SHA256 = (
    "2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80"
)
CADCLAW_SOURCE_MANIFEST_SHA256 = (
    "6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831"
)
HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256 = (
    "42170a1db8b7a11baeebf8c9a20a5bf4497019953e995895e15fc05ba070eff0"
)
CADCLAW_CALIBRATION_SHA256 = (
    "64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84"
)
HISTORICAL_RUNTIME_CONTRACT_SHA256 = (
    "fdca4e6e46f71b8fe00ed02ac0d867c882166f534039a77eb60f432fa2f32dee"
)
NATIVE_DEB_LOCK_SHA256 = (
    "4b12f84d010651166dd4067ede60689215c174d1d2926d4b1d07befff05232f1"
)
NATIVE_DEB_MANIFEST_SHA256 = (
    "0ad2f18d336e070c5cbaab7204e3cc76f1ec112e9d8fbd6d69a42902b27fa1e1"
)
NATIVE_BUNDLE_VERIFIER_SHA256 = (
    "f067b00c69c5c341d5dcd98a0d941cdf8c1bf0dbec4edc7aa1ceeb23df319179"
)
RUNTIME_SMOKE_PROBES_SHA256 = (
    "dc68c14c4bc79c843e4863f294576ee604a000f4ccc4d4f9d9ca186943745e63"
)
EXPECTED_RUNTIME = {
    "runtime_contract": RUNTIME_CONTRACT_ID,
    "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
    "cadclaw_version": "0.10.0",
    "cadclaw_commit": "fad0dd552a49a0b32336f1845c2b82873ad6360a",
    "cadclaw_gate_spec_version": "0.13.0",
    "cadclaw_gate_registry_version": "harness-gates.v1",
    "cadclaw_source_manifest_sha256": CADCLAW_SOURCE_MANIFEST_SHA256,
    "cadclaw_calibration_sha256": CADCLAW_CALIBRATION_SHA256,
    "cadquery_version": "2.7.0",
    "cadquery_ocp_version": "7.8.1.1.post1",
}
EXPECTED_CONTAINER_ENV_KEYS = sorted(
    {
        "HOME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "TZ",
        "LANG",
        "LC_ALL",
        "PATH",
    }
)
CADCLAW_PIN_BASIS = "marb_v0.13_calibrated_cadclaw_fad0dd55"
CADCLAW_CALIBRATION_ID = "cadclaw-fad0dd55-vs-60fc271f-20260829"
CADCLAW_CALIBRATION_SCOPE = (
    "low-level snapshot/grade-input, render-call, and exact NIST roundtrip "
    "surfaces; configured-harness report semantics intentionally advance "
    "to gate 0.13.0 and harness-gates.v1"
)
FROZEN_CADCLAW_COMMIT = "60fc271f68c8a794a4741f856b2dd4c9878416a6"
FROZEN_CADCLAW_TREE = "6b1cdeade8e6cfe46527d56c0011d05b360daec7"
CURRENT_CADCLAW_TREE = "97698a9ac17ab5423326c639e492888f219cafba"
SUPPORTED_L4_GRADE_CONTRACT = {
    "cadclaw_version": "0.10.0",
    "cadclaw_commit": FROZEN_CADCLAW_COMMIT,
    "cadquery_version": "2.7.0",
    "cadquery_ocp_version": "7.8.1.1.post1",
    "invariant_gate_version": "marb_l4_eco_invariant.v0.12.0",
    "requested_change_grade_method": "marb_task_local_reference.v0.12",
}
RUNTIME_CONTRACT_PATH = (
    Path(__file__).resolve().parent / "container" / "runtime-contract.v0.13.json"
)
HISTORICAL_RUNTIME_CONTRACT_PATH = (
    Path(__file__).resolve().parent / "container" / "runtime-contract.v0.12.json"
)
CADCLAW_CALIBRATION_PATH = (
    Path(__file__).resolve().parent
    / "container"
    / "cadclaw-calibration.fad0dd55.json"
)
GENERIC_PLAN_BLOCKERS = {
    "this plan does not authorize execution, provider access, or spend",
    "planned slots are not benchmark attempts or run evidence",
}
HEX64 = re.compile(r"[0-9a-f]{64}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
CREDENTIAL_ENV_NAME = re.compile(
    r"[A-Z][A-Z0-9_]{0,111}(?:_API_KEY|_ACCESS_TOKEN|_TOKEN)"
)
DENIED_CREDENTIAL_ENV_NAMES = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "USERPROFILE",
    "HOME",
    "TEMP",
    "TMP",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
}
WINDOWS_DOCKER_EXECUTABLE = re.compile(
    r"[A-Za-z]:/(?:[^/\\\x00-\x1f\x7f]+/)+docker\.exe",
    re.IGNORECASE,
)
SAFE_TOOL_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ /+-]{0,239}")
SAFE_PROVIDER_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}[A-Za-z0-9]|[A-Za-z0-9]")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SECRET_LITERAL = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
    rb"\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]|bearer\s+[A-Za-z0-9._~+/-]{12,}"
)
KNOWN_SECRET_PREFIX = re.compile(
    rb"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})"
)
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
MAX_TOOL_TEXT_BYTES = 1_000_000
MAX_READBACK_BYTES = 64_000
MAX_KIT_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_COMMITTED_BLOB_BYTES = MAX_KIT_ARCHIVE_BYTES
GIT_BLOB_TIMEOUT_SECONDS = 30.0
GIT_CLEANUP_TIMEOUT_SECONDS = 5.0
MAX_ARCHIVE_MEMBERS = 256
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_FILES = 4_096
MAX_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
MAX_WORKSPACE_FILE_BYTES = 512 * 1024 * 1024
MAX_EDITABLE_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_EDITABLE_SOURCE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EDITABLE_SOURCE_FILES = 512
MAX_RETAINED_FILES = MAX_WORKSPACE_FILES + MAX_ARCHIVE_MEMBERS + 512
MAX_RETAINED_BYTES = (
    MAX_WORKSPACE_BYTES
    + MAX_ARCHIVE_EXPANDED_BYTES
    + 2 * MAX_WORKSPACE_FILE_BYTES
    + 2 * MAX_EDITABLE_SOURCE_TOTAL_BYTES
    + 64 * 1024 * 1024
)


def _contains_exact_forbidden_secret(
    raw: bytes, forbidden_secrets: Sequence[str]
) -> bool:
    for value in forbidden_secrets:
        if value and value.encode("utf-8") in raw:
            return True
    return False


def _contains_secret_material(raw: bytes, forbidden_secrets: Sequence[str]) -> bool:
    return _contains_exact_forbidden_secret(raw, forbidden_secrets) or bool(
        SECRET_LITERAL.search(raw) or KNOWN_SECRET_PREFIX.search(raw)
    )


def _assert_safe_retained_bytes(
    raw: bytes, forbidden_secrets: Sequence[str], label: str
) -> None:
    if _contains_secret_material(raw, forbidden_secrets):
        raise ArtifactError(f"{label} contains prohibited secret material")


class ExecutorError(ValueError):
    """Fail-closed validation or preflight failure before a run begins."""


class ProviderCallError(RuntimeError):
    """Sanitized provider failure; response bodies and endpoints are discarded."""


class ArtifactError(RuntimeError):
    """A required run artifact was missing or unsafe."""


@dataclasses.dataclass(frozen=True)
class _DiscoveredStepOutput:
    """Content and filesystem identity frozen when a canonical STEP is found."""

    path: Path
    device: int
    inode: int
    mode: int
    bytes: int
    mtime_ns: int
    sha256: str


class BudgetError(RuntimeError):
    """Provider billing evidence is missing, contradictory, or above authorization."""


class RunExecutionError(RuntimeError):
    """A retained run ended unsuccessfully."""

    def __init__(self, category: str, run_dir: Path) -> None:
        super().__init__(f"MARB execution retained an unsuccessful run ({category})")
        self.category = category
        self.run_dir = run_dir


def _fail(message: str) -> NoReturn:
    raise ExecutorError(message)


def _deadline_tick(deadline_check: Callable[[], None] | None) -> None:
    if deadline_check is not None:
        deadline_check()


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(f"{label} has unknown or missing fields")
    return value


def _stable_read(path: Path, label: str) -> bytes:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or _is_link_like(path):
            _fail(f"{label} is not a regular unlinked file")
        with path.open("rb") as handle:
            raw = handle.read()
        after = path.lstat()
    except OSError:
        _fail(f"{label} cannot be read")
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(raw) != after.st_size:
        _fail(f"{label} changed while it was being read")
    return raw


def _validate_calibration_manifest(
    value: Any,
    *,
    expected_count: int,
    expected_sha256: str,
    label: str,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "file_count",
        "manifest_sha256",
        "files",
    }:
        _fail(f"{label} source manifest is malformed")
    files = value.get("files")
    if (
        value.get("file_count") != expected_count
        or value.get("manifest_sha256") != expected_sha256
        or not isinstance(files, list)
        or len(files) != expected_count
    ):
        _fail(f"{label} source manifest identity is unsupported")
    normalized: list[tuple[str, str]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            _fail(f"{label} source manifest member is malformed")
        path = item.get("path")
        digest = item.get("sha256")
        try:
            safe_path = _safe_relative(path, f"{label} source manifest path").as_posix()
        except (ExecutorError, TypeError):
            _fail(f"{label} source manifest member is malformed")
        if safe_path != path or not isinstance(digest, str) or not HEX64.fullmatch(digest):
            _fail(f"{label} source manifest member is malformed")
        normalized.append((path, digest))
    if [path for path, _digest in normalized] != sorted(
        {path for path, _digest in normalized}
    ):
        _fail(f"{label} source manifest paths are not unique and sorted")
    manifest_raw = b"".join(
        f"{digest}  {path}\n".encode("utf-8") for path, digest in normalized
    )
    if hashlib.sha256(manifest_raw).hexdigest() != expected_sha256:
        _fail(f"{label} source manifest bytes contradict its identity")


def _validate_runtime_contract_content(
    contract: Any, historical: Any, calibration: Any
) -> None:
    """Validate the semantics behind the three independently hash-bound files."""
    if (
        not isinstance(contract, dict)
        or contract.get("schema") != "marb_h2b_runtime_contract.v1"
        or contract.get("contract_id") != RUNTIME_CONTRACT_ID
        or contract.get("pin_basis") != CADCLAW_PIN_BASIS
        or contract.get("status")
        != "source_calibrated_runtime_image_unqualified"
    ):
        _fail("H2b runtime contract content is unsupported")
    cadclaw = contract.get("cadclaw")
    runtime = contract.get("runtime")
    grade = contract.get("historical_grade_compatibility")
    if (
        not isinstance(cadclaw, dict)
        or cadclaw.get("commit") != EXPECTED_RUNTIME["cadclaw_commit"]
        or cadclaw.get("version") != EXPECTED_RUNTIME["cadclaw_version"]
        or cadclaw.get("gate_spec_version")
        != EXPECTED_RUNTIME["cadclaw_gate_spec_version"]
        or cadclaw.get("gate_registry_version")
        != EXPECTED_RUNTIME["cadclaw_gate_registry_version"]
        or cadclaw.get("git_tree") != CURRENT_CADCLAW_TREE
        or cadclaw.get("package_source_file_count") != 53
        or cadclaw.get("package_source_manifest_sha256")
        != CADCLAW_SOURCE_MANIFEST_SHA256
        or cadclaw.get("calibration_evidence_sha256")
        != CADCLAW_CALIBRATION_SHA256
        or not isinstance(runtime, dict)
        or runtime.get("cadquery_version")
        != EXPECTED_RUNTIME["cadquery_version"]
        or runtime.get("cadquery_ocp_version")
        != EXPECTED_RUNTIME["cadquery_ocp_version"]
        or not isinstance(grade, dict)
        or grade.get("runtime_contract_sha256")
        != HISTORICAL_RUNTIME_CONTRACT_SHA256
        or grade.get("cadclaw_package_source_manifest_sha256")
        != HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256
        or grade.get("grade_runtime_contract") != SUPPORTED_L4_GRADE_CONTRACT
        or grade.get("task_id") != "L4-ECO"
        or grade.get("grade_contract_id")
        != SUPPORTED_L4_GRADE_CONTRACT["invariant_gate_version"]
    ):
        _fail("H2b runtime contract content contradicts its bound sources")
    historical_cadclaw = (
        historical.get("cadclaw") if isinstance(historical, dict) else None
    )
    if (
        not isinstance(historical, dict)
        or historical.get("contract_id") != "marb-v0.12-h2b"
        or historical.get("pin_basis") != "marb_v0.12_frozen_functional_core"
        or historical.get("status") != "historical_preserved"
        or not isinstance(historical_cadclaw, dict)
        or historical_cadclaw.get("commit") != FROZEN_CADCLAW_COMMIT
        or historical_cadclaw.get("gate_spec_version") != "0.12.0"
        or historical_cadclaw.get("gate_registry_version") is not None
        or historical_cadclaw.get("package_source_file_count") != 51
        or historical_cadclaw.get("package_source_manifest_sha256")
        != HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256
        or not isinstance(calibration, dict)
        or calibration.get("schema") != "marb_h2b_cadclaw_calibration.v1"
    ):
        _fail("H2b historical or calibration evidence is unsupported")

    source_control = calibration.get("source_control")
    manifests = calibration.get("source_manifests")
    wheel_source = (
        manifests.get("source_to_wheel_inputs") if isinstance(manifests, dict) else None
    )
    expected_scope = [
        "cadclaw",
        "cadclaw_cli",
        "cadclaw_mcp",
        "cadharness",
        "pyproject.toml",
        "README.md",
        "LICENSE",
    ]
    if (
        calibration.get("calibration_id") != CADCLAW_CALIBRATION_ID
        or calibration.get("calibration_kind") != "local_software_compatibility"
        or calibration.get("classification") != "compatible"
        or calibration.get("compatibility_scope") != CADCLAW_CALIBRATION_SCOPE
        or calibration.get("not_a_benchmark_or_grade") is not True
        or calibration.get("failed_checks") != []
        or not isinstance(source_control, dict)
        or source_control.get("fetch_performed") is not False
        or source_control.get("origin_main_ref") != "refs/remotes/origin/main"
        or source_control.get("candidate_matches_fetched_origin_main") is not True
        or source_control.get("frozen_is_ancestor_of_candidate") is not True
        or source_control.get("frozen_commit") != FROZEN_CADCLAW_COMMIT
        or source_control.get("candidate_commit")
        != EXPECTED_RUNTIME["cadclaw_commit"]
        or source_control.get("frozen_tree") != FROZEN_CADCLAW_TREE
        or source_control.get("candidate_tree") != CURRENT_CADCLAW_TREE
        or not isinstance(manifests, dict)
        or manifests.get("algorithm")
        != "path-sorted lowercase-sha256, two spaces, POSIX path, LF"
        or not isinstance(wheel_source, dict)
        or wheel_source.get("scope") != expected_scope
    ):
        _fail("CADCLAW calibration classification or source identity is unsupported")
    _validate_calibration_manifest(
        wheel_source.get("frozen"),
        expected_count=51,
        expected_sha256=HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256,
        label="frozen CADCLAW",
    )
    _validate_calibration_manifest(
        wheel_source.get("candidate"),
        expected_count=53,
        expected_sha256=CADCLAW_SOURCE_MANIFEST_SHA256,
        label="candidate CADCLAW",
    )

    expected_calibration_runtime = {
        "python": "3.11.15",
        "cadclaw": "0.10.0",
        "cadquery": "2.7.0",
        "cadquery-ocp": "7.8.1.1.post1",
        "Pillow": "12.2.0",
        "PyYAML": "6.0.3",
        "pydantic": "2.13.4",
        "vtk": "9.3.1",
    }
    calibration_runtime = calibration.get("runtime")
    configured = calibration.get("configured_harness_calibration")
    gate_identity = configured.get("gate_identity") if isinstance(configured, dict) else None
    cases = configured.get("cases") if isinstance(configured, dict) else None
    candidate_gate = (
        gate_identity.get("candidate") if isinstance(gate_identity, dict) else None
    )
    candidate_registry = (
        candidate_gate.get("gate_registry")
        if isinstance(candidate_gate, dict)
        else None
    )
    if (
        not isinstance(calibration_runtime, dict)
        or calibration_runtime.get("expected") != expected_calibration_runtime
        or calibration_runtime.get("frozen") != expected_calibration_runtime
        or calibration_runtime.get("candidate") != expected_calibration_runtime
        or not isinstance(configured, dict)
        or configured.get("method")
        != "cadclaw harness --only interference --report-format json"
        or not isinstance(gate_identity, dict)
        or gate_identity.get("frozen")
        != {
            "gate_spec_version": "0.12.0",
            "gate_registry": {"status": "absent", "version": None, "ids": []},
        }
        or not isinstance(candidate_gate, dict)
        or candidate_gate.get("gate_spec_version") != "0.13.0"
        or not isinstance(candidate_registry, dict)
        or candidate_registry.get("status") != "present"
        or candidate_registry.get("version") != "harness-gates.v1"
        or not isinstance(candidate_registry.get("ids"), list)
        or "interference" not in candidate_registry["ids"]
        or not isinstance(cases, list)
    ):
        _fail("CADCLAW calibration runtime or gate identity is unsupported")
    cases_by_id = {
        item.get("id"): item for item in cases if isinstance(item, dict)
    }
    separated = cases_by_id.get("separated-three-solid", {})
    overlap = cases_by_id.get("overlap-three-solid", {})
    separated_frozen = separated.get("frozen") if isinstance(separated, dict) else None
    separated_candidate = (
        separated.get("candidate") if isinstance(separated, dict) else None
    )
    overlap_frozen = overlap.get("frozen") if isinstance(overlap, dict) else None
    overlap_candidate = overlap.get("candidate") if isinstance(overlap, dict) else None
    if (
        set(cases_by_id) != {"separated-three-solid", "overlap-three-solid"}
        or not isinstance(separated_frozen, dict)
        or separated_frozen.get("exit_code") != 0
        or separated_frozen.get("checked") != []
        or not isinstance(separated_candidate, dict)
        or separated_candidate.get("exit_code") != 0
        or separated_candidate.get("overall") != "pass"
        or separated_candidate.get("checked") != ["interference"]
        or not isinstance(overlap_frozen, dict)
        or overlap_frozen.get("exit_code") != 0
        or overlap_frozen.get("checked") != []
        or not isinstance(overlap_candidate, dict)
        or overlap_candidate.get("exit_code") != 1
        or overlap_candidate.get("overall") != "fail"
        or overlap_candidate.get("finding_ids") != ["interference.clip"]
        or overlap_candidate.get("checked") != ["interference"]
    ):
        _fail("CADCLAW calibrated configured-harness behavior is unsupported")
    snapshot = calibration.get("snapshot_geometry")
    aggregate = snapshot.get("tracked_fixture_aggregate") if isinstance(snapshot, dict) else None
    synthetic = snapshot.get("synthetic_three_solid") if isinstance(snapshot, dict) else None
    nist = calibration.get("nist_roundtrip_integration")
    expected_nist = {
        "status": "pass",
        "tests_run": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    checks = calibration.get("checks")
    if (
        not isinstance(aggregate, dict)
        or aggregate.get("parity") is not True
        or not isinstance(synthetic, dict)
        or synthetic.get("parity") is not True
        or synthetic.get("part_count") != {"frozen": 3, "candidate": 3}
        or not isinstance(nist, dict)
        or nist.get("frozen") != expected_nist
        or nist.get("candidate") != expected_nist
        or not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(item, dict)
            or set(item) != {"id", "status"}
            or item.get("status") != "pass"
            for item in checks
        )
        or len({item["id"] for item in checks}) != len(checks)
    ):
        _fail("CADCLAW calibration executable evidence is incomplete")


def _validate_runtime_contract_sources() -> None:
    """Bind the active runtime to reviewed, tracked contract/calibration bytes."""
    contract_raw = _stable_read(RUNTIME_CONTRACT_PATH, "H2b runtime contract")
    if hashlib.sha256(contract_raw).hexdigest() != RUNTIME_CONTRACT_SHA256:
        _fail("H2b runtime contract digest does not match the executor")
    historical_raw = _stable_read(
        HISTORICAL_RUNTIME_CONTRACT_PATH, "historical H2b runtime contract"
    )
    if (
        hashlib.sha256(historical_raw).hexdigest()
        != HISTORICAL_RUNTIME_CONTRACT_SHA256
    ):
        _fail("historical H2b runtime contract digest does not match the executor")
    calibration_raw = _stable_read(
        CADCLAW_CALIBRATION_PATH, "CADCLAW calibration evidence"
    )
    if hashlib.sha256(calibration_raw).hexdigest() != CADCLAW_CALIBRATION_SHA256:
        _fail("CADCLAW calibration digest does not match the executor")
    try:
        contract = json.loads(contract_raw.decode("utf-8"))
        historical = json.loads(historical_raw.decode("utf-8"))
        calibration = json.loads(calibration_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("H2b runtime contract evidence is malformed")
    _validate_runtime_contract_content(contract, historical, calibration)


def _stream_read_bounded(
    path: Path,
    label: str,
    max_bytes: int,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> bytes:
    """Read a stable regular file without an unbounded allocation or deadline gap."""
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(path)
            or before.st_size > max_bytes
        ):
            raise OSError
        chunks: list[bytes] = []
        total = 0
        with path.open("rb") as handle:
            while True:
                _deadline_tick(deadline_check)
                chunk = handle.read(min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise OSError
        after = path.lstat()
    except OSError:
        raise ArtifactError(f"{label} cannot be read safely") from None
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or total != after.st_size
    ):
        raise ArtifactError(f"{label} changed while it was being read")
    return b"".join(chunks)


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return path.is_symlink()
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _windows_identity(value: str) -> str:
    return "/".join(
        part.rstrip(". ").casefold() for part in value.replace("\\", "/").split("/")
    )


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or CONTROL.search(value)
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or value.endswith("/")
        or not SAFE_TOOL_PATH.fullmatch(value)
    ):
        _fail(f"{label} is not a safe relative path")
    parts = value.split("/")
    if any(
        part in {"", ".", ".."}
        or part.endswith((".", " "))
        or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        for part in parts
    ):
        _fail(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if path.as_posix() != value:
        _fail(f"{label} is not normalized")
    return path


def _workspace_path(workspace: Path, relative: Any, label: str) -> Path:
    rel = _safe_relative(relative, label)
    root = workspace.resolve(strict=True)
    candidate = root.joinpath(*rel.parts)
    cursor = root
    for part in rel.parts[:-1]:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if _is_link_like(cursor) or not cursor.is_dir():
                _fail(f"{label} traverses an unsafe path")
    if candidate.exists() or candidate.is_symlink():
        if _is_link_like(candidate):
            _fail(f"{label} targets a linked path")
    try:
        resolved_parent = candidate.parent.resolve(strict=False)
    except OSError:
        _fail(f"{label} parent cannot be resolved")
    if root != resolved_parent and root not in resolved_parent.parents:
        _fail(f"{label} escapes the run workspace")
    return candidate


def _validated_docker_executable_text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or "\\" in value
        or "//" in value
        or not WINDOWS_DOCKER_EXECUTABLE.fullmatch(value)
        or not value[0].isupper()
    ):
        _fail("authorized Docker executable must be an absolute normalized docker.exe path")
    pure = PureWindowsPath(value)
    if pure.as_posix() != value or any(
        part in {".", ".."}
        or part.endswith((".", " "))
        or (":" in part and index != 0)
        or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        for index, part in enumerate(pure.parts)
    ):
        _fail("authorized Docker executable must be an absolute normalized docker.exe path")
    return value


def _validated_git_executable_text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or CONTROL.search(value)
        or "\\" in value
        or "//" in value
    ):
        _fail("authorized Git executable must be an absolute normalized git path")
    if re.match(r"^[A-Za-z]:/", value):
        pure: PurePosixPath | PureWindowsPath = PureWindowsPath(value)
        if (
            not value[0].isupper()
            or not pure.is_absolute()
            or pure.as_posix() != value
            or pure.name.casefold() != "git.exe"
            or any(
                part in {".", ".."}
                or part.endswith((".", " "))
                or (":" in part and index != 0)
                or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
                for index, part in enumerate(pure.parts)
            )
        ):
            _fail("authorized Git executable must be an absolute normalized git path")
    else:
        pure = PurePosixPath(value)
        if (
            not pure.is_absolute()
            or pure.as_posix() != value
            or pure.name != "git"
            or any(part in {"", ".", ".."} for part in pure.parts[1:])
        ):
            _fail("authorized Git executable must be an absolute normalized git path")
    return value


def _parse_utc(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(f"{label} must be an RFC3339 UTC timestamp")
    if parsed.tzinfo != dt.timezone.utc:
        _fail(f"{label} must be UTC")
    return parsed


def _utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_uuid4(value: Any, label: str) -> uuid.UUID:
    if not isinstance(value, str):
        _fail(f"{label} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _fail(f"{label} must be a UUIDv4")
    if parsed.version != 4 or str(parsed) != value.lower():
        _fail(f"{label} must be a canonical UUIDv4")
    return parsed


def sanitize_endpoint(value: Any) -> tuple[str, str]:
    """Return the in-memory endpoint and a credential-free provenance form."""
    if not isinstance(value, str) or not value or CONTROL.search(value) or len(value) > 512:
        _fail("provider endpoint is malformed")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("provider endpoint is malformed")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail("provider endpoint must be an http(s) URL without credentials, query, or fragment")
    if "%" in parsed.netloc or "%" in parsed.path:
        _fail("provider endpoint must not contain percent-encoded components")
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"127.0.0.1", "::1", "localhost"}:
        _fail("plaintext provider endpoints must be loopback-only")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host + (f":{port}" if port not in {None, default_port} else "")
    path = parsed.path.rstrip("/")
    if path and (not path.startswith("/") or "//" in path or "/../" in f"{path}/"):
        _fail("provider endpoint path is not normalized")
    sanitized = f"{parsed.scheme}://{authority}"
    canonical = sanitized + path
    return canonical, sanitized


def _decimal_text(value: Any, label: str, *, allow_zero: bool) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value):
        _fail(f"{label} must be a nonnegative decimal string")
    try:
        parsed = decimal.Decimal(value)
    except decimal.InvalidOperation:
        _fail(f"{label} must be a nonnegative decimal string")
    if parsed < 0 or (not allow_zero and parsed == 0):
        _fail(f"{label} is outside the allowed range")
    return format(parsed, "f")


def verify_authorization(
    raw: bytes,
    *,
    expected_sha256: str,
    expected_plan_sha256: str,
    expected_run_id: str,
    expected_model_id: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """Verify the externally digest-bound execution authorization envelope."""
    _validate_runtime_contract_sources()
    try:
        value, _ = cohort_runner._decode_json(raw, "execution authorization")
    except cohort_runner.PlanError as exc:
        _fail(str(exc))
    value = _exact_keys(
        value,
        {"schema", "authorization_sha256", "authorization"},
        "execution authorization envelope",
    )
    if value.get("schema") != AUTH_SCHEMA:
        _fail("execution authorization schema is unsupported")
    if raw != _canonical_json(value, newline=True):
        _fail("execution authorization must be canonical JSON with one LF terminator")
    payload = value.get("authorization")
    if not isinstance(payload, dict):
        _fail("execution authorization payload is malformed")
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if (
        not isinstance(expected_sha256, str)
        or not HEX64.fullmatch(expected_sha256)
        or value.get("authorization_sha256") != digest
        or digest != expected_sha256
    ):
        _fail("execution authorization digest does not match independent approval")
    payload = _exact_keys(
        payload,
        {
            "authorization_id",
            "approved_by",
            "issued_utc",
            "expires_utc",
            "plan_sha256",
            "planned_run_id",
            "execution",
            "provider",
            "container",
            "spend",
            "implementation",
        },
        "execution authorization payload",
    )
    _parse_uuid4(payload.get("authorization_id"), "authorization_id")
    approved_by = payload.get("approved_by")
    if (
        not isinstance(approved_by, str)
        or not approved_by.strip()
        or len(approved_by) > 100
        or not approved_by.isascii()
        or CONTROL.search(approved_by)
    ):
        _fail("approved_by must be a short public ASCII label")
    issued = _parse_utc(payload.get("issued_utc"), "issued_utc")
    expires = _parse_utc(payload.get("expires_utc"), "expires_utc")
    if expires <= issued or now < issued - dt.timedelta(minutes=5) or now >= expires:
        _fail("execution authorization is not currently valid")
    if payload.get("plan_sha256") != expected_plan_sha256:
        _fail("execution authorization is bound to a different plan")
    if payload.get("planned_run_id") != expected_run_id:
        _fail("execution authorization is bound to a different run slot")

    execution = _exact_keys(
        payload.get("execution"),
        {"execute", "model_calls", "provider_access", "spend_authorized"},
        "execution authorization grants",
    )
    if execution != {
        "execute": True,
        "model_calls": True,
        "provider_access": True,
        "spend_authorized": True,
    }:
        _fail("execution authorization grants are not explicit")

    provider = _exact_keys(
        payload.get("provider"),
        {
            "protocol",
            "model_id",
            "endpoint",
            "credential_env",
            "billing_mode",
            "settings",
        },
        "execution authorization provider",
    )
    if provider.get("protocol") != PROVIDER_PROTOCOL:
        _fail("provider protocol is unsupported")
    if provider.get("model_id") != expected_model_id:
        _fail("authorized provider model does not match the plan")
    canonical_endpoint, sanitized = sanitize_endpoint(provider.get("endpoint"))
    provider["endpoint"] = canonical_endpoint
    provider["sanitized_endpoint"] = sanitized
    credential_env = provider.get("credential_env")
    if credential_env is not None and (
        not isinstance(credential_env, str)
        or not ENV_NAME.fullmatch(credential_env)
        or not CREDENTIAL_ENV_NAME.fullmatch(credential_env)
        or credential_env in DENIED_CREDENTIAL_ENV_NAMES
    ):
        _fail("credential_env must be null or a dedicated API-key/token variable name")
    if credential_env is not None and not str(provider.get("endpoint")).startswith("https://"):
        _fail("credentialed provider endpoints must use HTTPS")
    if provider.get("billing_mode") not in {"metered", "local-no-charge"}:
        _fail("provider billing_mode is unsupported")
    settings = _exact_keys(
        provider.get("settings"),
        {
            "temperature",
            "max_output_tokens",
            "max_total_turns",
            "max_total_tool_calls",
            "max_run_python_calls",
            "max_request_bytes",
            "max_transcript_bytes",
            "max_wall_clock_seconds",
            "timeout_seconds",
            "seed",
        },
        "execution authorization provider settings",
    )
    temperature = settings.get("temperature")
    if (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not math.isfinite(float(temperature))
        or not 0 <= float(temperature) <= 2
    ):
        _fail("provider temperature is outside the allowed range")
    for key, lower, upper in (
        ("max_output_tokens", 1, 200_000),
        ("max_total_turns", 1, 64),
        ("max_total_tool_calls", 1, 512),
        ("max_run_python_calls", 1, 128),
        ("max_request_bytes", 1_024, 8_000_000),
        ("max_transcript_bytes", 1_024, 32_000_000),
        ("max_wall_clock_seconds", 1, 7_200),
        ("timeout_seconds", 1, 3_600),
    ):
        item = settings.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or not lower <= item <= upper:
            _fail(f"provider {key} is outside the allowed range")
    seed = settings.get("seed")
    if seed is not None and (
        not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1
    ):
        _fail("provider seed is outside the allowed range")

    container = _exact_keys(
        payload.get("container"),
        {
            "image",
            "docker_executable",
            "docker_executable_sha256",
            "cadclaw_pin_basis",
            "run_limiter_sha256",
            *EXPECTED_RUNTIME.keys(),
        },
        "execution authorization container",
    )
    image = container.get("image")
    if (
        not isinstance(image, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{1,254}@sha256:[0-9a-f]{64}", image)
        or "//" in image
    ):
        _fail("container image must be an immutable lowercase OCI repository digest")
    for key, expected in EXPECTED_RUNTIME.items():
        if container.get(key) != expected:
            _fail(f"container {key} does not match the frozen executor runtime")
    if container.get("cadclaw_pin_basis") != CADCLAW_PIN_BASIS:
        _fail("container CADCLAW pin basis is unsupported")
    docker_executable = _validated_docker_executable_text(
        container.get("docker_executable")
    )
    docker_digest = container.get("docker_executable_sha256")
    if not isinstance(docker_digest, str) or not HEX64.fullmatch(docker_digest):
        _fail("authorized Docker executable digest is malformed")
    if not isinstance(container.get("run_limiter_sha256"), str) or not HEX64.fullmatch(
        container["run_limiter_sha256"]
    ):
        _fail("authorized run limiter digest is malformed")

    implementation = _exact_keys(
        payload.get("implementation"),
        {
            "source_revision",
            "planner_sha256",
            "executor_sha256",
            "isolated_container_sha256",
            "provider_transport_sha256",
            "run_limiter_sha256",
            "runtime_contract_sha256",
            "historical_runtime_contract_sha256",
            "cadclaw_calibration_sha256",
            "git_executable",
            "git_executable_sha256",
        },
        "execution authorization implementation",
    )
    if not isinstance(implementation.get("source_revision"), str) or not FULL_COMMIT.fullmatch(
        implementation["source_revision"]
    ):
        _fail("authorized implementation source revision is malformed")
    for key in (
        "planner_sha256",
        "executor_sha256",
        "isolated_container_sha256",
        "provider_transport_sha256",
        "run_limiter_sha256",
        "runtime_contract_sha256",
        "historical_runtime_contract_sha256",
        "cadclaw_calibration_sha256",
    ):
        if not isinstance(implementation.get(key), str) or not HEX64.fullmatch(implementation[key]):
            _fail(f"authorized implementation {key} is malformed")
    if implementation["run_limiter_sha256"] != container["run_limiter_sha256"]:
        _fail("authorized container and implementation limiter identities disagree")
    if (
        implementation["runtime_contract_sha256"]
        != container["runtime_contract_sha256"]
        or implementation["runtime_contract_sha256"] != RUNTIME_CONTRACT_SHA256
        or implementation["historical_runtime_contract_sha256"]
        != HISTORICAL_RUNTIME_CONTRACT_SHA256
        or implementation["cadclaw_calibration_sha256"]
        != container["cadclaw_calibration_sha256"]
        or implementation["cadclaw_calibration_sha256"]
        != CADCLAW_CALIBRATION_SHA256
    ):
        _fail("authorized runtime evidence identities disagree")
    implementation["git_executable"] = _validated_git_executable_text(
        implementation.get("git_executable")
    )
    if not isinstance(
        implementation.get("git_executable_sha256"), str
    ) or not HEX64.fullmatch(implementation["git_executable_sha256"]):
        _fail("authorized Git executable digest is malformed")

    spend = _exact_keys(
        payload.get("spend"),
        {"currency", "max_cost_usd", "zero_cost_attested"},
        "execution authorization spend",
    )
    if spend.get("currency") != "USD" or not isinstance(spend.get("zero_cost_attested"), bool):
        _fail("execution authorization spend metadata is malformed")
    billing = provider["billing_mode"]
    if billing == "local-no-charge":
        if spend.get("zero_cost_attested") is not True or spend.get("max_cost_usd") is not None:
            _fail("local-no-charge execution requires an explicit zero-cost attestation")
    else:
        _fail(
            "metered execution is disabled until a pre-call price and token budget is implemented"
        )
    return payload


def make_authorization_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonical envelope helper for reviewed operator tooling and tests.

    This does not authorize anything by itself.  Execution still requires the
    independently supplied payload digest and exact CLI confirmation literal.
    """
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return {"schema": AUTH_SCHEMA, "authorization_sha256": digest, "authorization": payload}


def _rebuild_plan(repo_root: Path, envelope: dict[str, Any]) -> None:
    plan = envelope["plan"]
    cohort = plan["cohort"]
    rebuilt = cohort_runner.build_plan(
        repo_root,
        task_id=plan["task"]["id"],
        source_revision=plan["source"]["revision"],
        cell_id=cohort["cell_id"],
        cell_label=cohort["cell_label"],
        cohort_id=cohort["cohort_id"],
        model_id=cohort["model"]["id"],
        model_name=cohort["model"]["name"],
        driver_id=cohort["driver"]["id"],
        driver_version=cohort["driver"]["version"],
        prompt_variant=cohort["prompt_variant"],
        seed_basis=cohort["seed_basis"],
        seeds=[item["seed"] for item in plan["runs"]],
    )
    if _canonical_json(rebuilt, newline=True) != _canonical_json(envelope, newline=True):
        _fail("cohort plan no longer matches current frozen inputs and registry state")


def _select_run(envelope: dict[str, Any], planned_run_id: str) -> dict[str, Any]:
    matches = [item for item in envelope["plan"]["runs"] if item.get("run_id") == planned_run_id]
    if len(matches) != 1:
        _fail("planned run slot is absent or ambiguous")
    return matches[0]


def _assert_execution_ready(envelope: dict[str, Any]) -> None:
    blockers = envelope["plan"]["readiness"]["blockers"]
    extras = [item for item in blockers if item not in GENERIC_PLAN_BLOCKERS]
    if extras:
        _fail("task evidence is not ready for benchmark execution")
    driver_id = envelope["plan"]["cohort"]["driver"]["id"]
    if driver_id != "cadquery":
        _fail("only the isolated CadQuery execution backend is currently supported")
    task = envelope["plan"]["task"]
    runtime = task.get("runtime_contract", {})
    if task.get("id") == "L4-ECO":
        if runtime != SUPPORTED_L4_GRADE_CONTRACT:
            _fail("L4 plan grade contract is not the calibrated historical contract")
    elif runtime != {}:
        _fail("non-L4 plan must not invent a grade runtime contract")


def _assert_runs_root(repo_root: Path, runs_root: Path) -> Path:
    repo = repo_root.resolve(strict=True)
    expected = repo / "runs"
    supplied = runs_root.absolute()
    if os.path.normcase(str(supplied)) != os.path.normcase(str(expected)):
        _fail("runs root must be the repository's dedicated runs directory")
    if expected.exists() or expected.is_symlink():
        if _is_link_like(expected) or not expected.is_dir():
            _fail("runs root must be a real directory, not a link or file")
    return expected


def _existing_run_names(runs_root: Path) -> list[str]:
    if not runs_root.exists():
        return []
    try:
        return [entry.name for entry in runs_root.iterdir()]
    except OSError:
        _fail("local runs directory cannot be enumerated")


def _assert_no_local_slot_collision(runs_root: Path, planned_run_id: str) -> None:
    target = _windows_identity(planned_run_id)
    prefix = target + "--"
    for name in _existing_run_names(runs_root):
        identity = _windows_identity(name)
        if identity == target or identity.startswith(prefix):
            _fail("planned run slot collides with existing unregistered local evidence")


def _allocate_run_dir(
    runs_root: Path,
    planned_run_id: str,
    attempt: uuid.UUID,
) -> tuple[uuid.UUID, Path, dict[str, Any]]:
    runs_root.mkdir(mode=0o700, parents=False, exist_ok=True)
    if _is_link_like(runs_root):
        _fail("runs root became linked during allocation")
    _assert_no_local_slot_collision(runs_root, planned_run_id)
    existing = {_windows_identity(name) for name in _existing_run_names(runs_root)}
    if not isinstance(attempt, uuid.UUID) or attempt.version != 4:
        _fail("attempt ID generator did not return a UUIDv4")
    name = f"{planned_run_id}--{attempt}"
    if _windows_identity(name) in existing:
        _fail("generated UUID run directory collides with local evidence")
    claims_root = runs_root / ".slot-claims"
    try:
        claims_root.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        _fail("permanent slot-claim directory cannot be created")
    if _is_link_like(claims_root) or not claims_root.is_dir():
        _fail("permanent slot-claim directory is unsafe")
    claim_path = claims_root / f"{planned_run_id}.json"
    claim = {
        "schema": "marb_logical_slot_claim.v1",
        "planned_run_id": planned_run_id,
        "attempt_id": str(attempt),
    }
    try:
        with claim_path.open("xb") as handle:
            claim_raw = _canonical_json(claim, newline=True)
            handle.write(claim_raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        _fail("planned run slot already has a permanent claim")
    except OSError:
        _fail("permanent slot claim cannot be written")
    target = runs_root / name
    try:
        target.mkdir(mode=0o700, exist_ok=False)
    except OSError:
        _fail("generated UUID run directory cannot be created")
    if _is_link_like(target):
        _fail("new run directory is unexpectedly linked")
    claim_identity = {
        "path": claim_path.relative_to(runs_root.parent).as_posix(),
        "sha256": hashlib.sha256(claim_raw).hexdigest(),
        "bytes": len(claim_raw),
        "permanent": True,
    }
    return attempt, target, claim_identity


def _bind_planned_outputs(
    repo_root: Path,
    run_dir: Path,
    slot: dict[str, Any],
    attempt_id: uuid.UUID,
) -> tuple[dict[str, dict[str, str]], dict[str, Path]]:
    planned = slot.get("planned_outputs")
    if not isinstance(planned, dict) or set(planned) != {"executor", "downstream"}:
        _fail("selected plan slot has no output contract")
    executor_planned = planned.get("executor")
    downstream_planned = planned.get("downstream")
    if not isinstance(executor_planned, dict) or not executor_planned:
        _fail("selected plan slot has no executor output contract")
    if not isinstance(downstream_planned, dict):
        _fail("selected plan slot has a malformed downstream output contract")
    token = cohort_runner.ATTEMPT_UUID_TOKEN
    expected_prefix = f"runs/{slot['run_id']}--{attempt_id}"
    actual: dict[str, dict[str, str]] = {"executor": {}, "downstream": {}}
    paths: dict[str, Path] = {}
    for owner, owned in (("executor", executor_planned), ("downstream", downstream_planned)):
        for key, template in owned.items():
            if (
                not isinstance(key, str)
                or not isinstance(template, str)
                or template.count(token) != 1
            ):
                _fail("planned output does not contain exactly one attempt UUID token")
            expanded = template.replace(token, str(attempt_id))
            rel = _safe_relative(expanded, "bound planned output")
            if rel.parts[:2] != ("runs", run_dir.name) or not expanded.startswith(
                expected_prefix + "/"
            ):
                _fail("bound planned output escapes the allocated attempt directory")
            candidate = repo_root.joinpath(*rel.parts)
            try:
                candidate.relative_to(run_dir)
            except ValueError:
                _fail("bound planned output escapes the allocated attempt directory")
            actual[owner][key] = expanded
            if owner == "executor":
                paths[key] = candidate
    run_log_path = paths.get("run_log")
    if run_log_path != run_dir / "run_log.json":
        _fail("planned run log path does not match the executor contract")
    for key, candidate in paths.items():
        if key != "run_log" and candidate.parent != run_dir / "evidence":
            _fail("planned evidence path does not match the executor contract")
    return actual, paths


def _record_planned_artifact(
    artifacts: dict[str, Any],
    key: str,
    record: dict[str, Any],
    actual_outputs: dict[str, str],
) -> None:
    if key not in actual_outputs or record.get("path") != actual_outputs[key]:
        raise ArtifactError("run artifact does not match its bound planned output")
    artifacts[key] = record


def _assert_executor_outputs_complete(
    task_id: str,
    artifacts: dict[str, Any],
    actual_outputs: dict[str, str],
) -> None:
    if task_id == "L1-ASSEMBLE":
        required = {
            "baseline_step",
            "baseline_editable_source_zip",
            "final_step",
            "final_editable_source_zip",
        }
    else:
        required = {
            "baseline_step",
            "baseline_editable_source",
            "changed_step",
            "changed_editable_source",
        }
    if set(artifacts) != required or not required.issubset(actual_outputs):
        raise ArtifactError("executor output set does not match the bound plan")
    for key in required:
        if artifacts[key].get("path") != actual_outputs[key]:
            raise ArtifactError("executor artifact path does not match the bound plan")


def _validate_zip_member(info: zipfile.ZipInfo, seen: set[str]) -> PurePosixPath | None:
    name = info.filename.rstrip("/")
    if not name:
        return None
    try:
        rel = _safe_relative(name, "kit archive member")
    except ExecutorError:
        _fail("selected kit contains an unsafe archive member")
    identity = _windows_identity(rel.as_posix())
    if identity in seen:
        _fail("selected kit contains duplicate or Windows-aliased archive members")
    seen.add(identity)
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(unix_mode) or info.flag_bits & 0x1:
        _fail("selected kit contains a linked or encrypted archive member")
    return rel


def _stage_kit(
    repo_root: Path,
    plan: dict[str, Any],
    workspace: Path,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    identity = plan["frozen_public_inputs"]["kit"]
    kit_path = repo_root.joinpath(*identity["path"].split("/"))
    raw = _stream_read_bounded(
        kit_path,
        "selected kit",
        MAX_KIT_ARCHIVE_BYTES,
        deadline_check=deadline_check,
    )
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        _fail("selected kit changed after plan revalidation")
    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                _fail("selected kit exceeds the archive member limit")
            for info in infos:
                _deadline_tick(deadline_check)
                rel = _validate_zip_member(info, seen)
                if rel is None:
                    continue
                total += info.file_size
                if total > MAX_ARCHIVE_EXPANDED_BYTES:
                    _fail("selected kit exceeds the expanded-size limit")
                target = workspace.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("xb") as output:
                    copied = 0
                    while True:
                        _deadline_tick(deadline_check)
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > info.file_size:
                            _fail("selected kit member expanded beyond its declared size")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if copied != info.file_size:
                    _fail("selected kit member was truncated")
                member_digest, member_size = _stream_file_digest(
                    target,
                    "staged kit member",
                    MAX_ARCHIVE_EXPANDED_BYTES,
                    deadline_check=deadline_check,
                )
                staged.append(
                    {
                        "path": rel.as_posix(),
                        "sha256": member_digest,
                        "bytes": member_size,
                    }
                )
                try:
                    target.chmod(0o444)
                except OSError:
                    _fail("staged kit member permissions cannot be restricted")
    except BudgetError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError):
        _fail("selected kit cannot be staged safely")
    staged.sort(key=lambda item: item["path"].encode("utf-8"))
    manifest = {
        "schema": "marb_staged_kit_manifest.v1",
        "archive": identity,
        "container_root": "/marb-input",
        "kit_compatibility_root": "/workspace/kit",
        "files": staged,
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    _verify_staged_kit(workspace, manifest, deadline_check=deadline_check)
    try:
        for root, directories, _files in os.walk(workspace, topdown=False):
            for name in directories:
                (Path(root) / name).chmod(0o555)
        workspace.chmod(0o555)
    except OSError:
        _fail("immutable input directory permissions cannot be restricted")
    return manifest


def _inventory_workspace(
    workspace: Path,
    deadline_check: Callable[[], None] | None = None,
    *,
    content_bound: bool = False,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    total = 0
    entries = 0
    seen: set[str] = set()
    def walk_error(_error: OSError) -> None:
        raise ArtifactError("run workspace cannot be enumerated safely")

    for root, directories, files in os.walk(
        workspace, followlinks=False, onerror=walk_error
    ):
        _deadline_tick(deadline_check)
        root_path = Path(root)
        file_info: dict[str, os.stat_result] = {}
        for name in [*directories, *files]:
            _deadline_tick(deadline_check)
            candidate = root_path / name
            entries += 1
            if entries > MAX_WORKSPACE_FILES:
                raise ArtifactError("run workspace exceeds the entry-count limit")
            if _is_link_like(candidate):
                raise ArtifactError("run workspace contains a symlink or reparse point")
            try:
                info = candidate.lstat()
            except OSError:
                raise ArtifactError("run workspace changed during inventory") from None
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ArtifactError("run workspace contains a special filesystem object")
            if stat.S_ISREG(info.st_mode):
                file_info[name] = info
        for name in files:
            candidate = root_path / name
            relative = candidate.relative_to(workspace).as_posix()
            try:
                _safe_relative(relative, "run workspace file")
            except ExecutorError:
                raise ArtifactError("run workspace contains an unsafe filename") from None
            identity = _windows_identity(relative)
            if identity in seen:
                raise ArtifactError("run workspace contains a Windows-normalized alias")
            seen.add(identity)
            info = file_info[name]
            size = info.st_size
            if size > MAX_WORKSPACE_FILE_BYTES:
                raise ArtifactError("run workspace file exceeds the size limit")
            total += size
            if total > MAX_WORKSPACE_BYTES:
                raise ArtifactError("run workspace exceeds the total size limit")
            item: dict[str, Any] = {"path": relative, "bytes": size}
            if content_bound:
                digest, measured_size = _stream_file_digest(
                    candidate,
                    "run workspace file",
                    MAX_WORKSPACE_FILE_BYTES,
                    deadline_check=deadline_check,
                )
                try:
                    after = candidate.lstat()
                except OSError:
                    raise ArtifactError("run workspace changed during inventory") from None
                if (
                    (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                    )
                    != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    )
                    or measured_size != size
                ):
                    raise ArtifactError("run workspace changed during inventory")
                item.update(
                    {
                        "device": info.st_dev,
                        "inode": info.st_ino,
                        "mtime_ns": info.st_mtime_ns,
                        "sha256": digest,
                    }
                )
            inventory.append(item)
    inventory.sort(key=lambda item: item["path"].encode("utf-8"))
    return inventory


def _verify_staged_kit(
    workspace: Path,
    manifest: dict[str, Any],
    deadline_check: Callable[[], None] | None = None,
) -> None:
    expected = {item["path"]: item for item in manifest["files"]}
    full_inventory = _inventory_workspace(
        workspace, deadline_check=deadline_check
    )
    current_paths = {item["path"] for item in full_inventory}
    if current_paths != set(expected):
        raise ArtifactError("staged public-input inventory changed during execution")
    for relative, identity in expected.items():
        _deadline_tick(deadline_check)
        target = _workspace_path(workspace, relative, "staged kit member")
        if not target.exists():
            raise ArtifactError("staged kit member was removed during execution")
        digest, size = _stream_file_digest(
            target,
            "staged kit member",
            MAX_ARCHIVE_EXPANDED_BYTES,
            deadline_check=deadline_check,
        )
        if size != identity["bytes"] or digest != identity["sha256"]:
            raise ArtifactError("staged kit member changed during execution")


def _stream_contains_secret_material(
    path: Path,
    forbidden_secrets: Sequence[str],
    *,
    deadline_check: Callable[[], None] | None = None,
) -> bool:
    exact = [value.encode("utf-8") for value in forbidden_secrets if value]
    overlap = max([16_384, *(len(item) + 256 for item in exact)])
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or _is_link_like(path):
            raise OSError
        tail = b""
        with path.open("rb") as handle:
            while True:
                _deadline_tick(deadline_check)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                window = tail + chunk
                if _contains_secret_material(window, forbidden_secrets):
                    return True
                tail = window[-overlap:]
        after = path.lstat()
    except OSError:
        raise ArtifactError("authored workspace file cannot be scanned safely") from None
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ArtifactError("authored workspace file changed during scanning")
    return False


def _scrub_workspace_secrets(
    workspace: Path,
    staged_paths: set[str],
    forbidden_secrets: Sequence[str],
    deadline_check: Callable[[], None] | None = None,
) -> None:
    del staged_paths  # immutable public inputs live outside the writable workspace
    unsafe_files: list[Path] = []
    unsafe_dirs: list[Path] = []
    entries = 0
    total = 0

    def walk_error(_error: OSError) -> None:
        raise ArtifactError("authored workspace cannot be enumerated safely")

    try:
        for root, directories, files in os.walk(
            workspace, topdown=False, followlinks=False, onerror=walk_error
        ):
            _deadline_tick(deadline_check)
            root_path = Path(root)
            for name in [*directories, *files]:
                _deadline_tick(deadline_check)
                entries += 1
                candidate = root_path / name
                try:
                    info = candidate.lstat()
                    relative = candidate.relative_to(workspace).as_posix()
                except (OSError, ValueError):
                    raise ArtifactError("authored workspace changed during cleanup") from None
                if entries > MAX_WORKSPACE_FILES:
                    (unsafe_dirs if stat.S_ISDIR(info.st_mode) else unsafe_files).append(
                        candidate
                    )
                    continue
                try:
                    _safe_relative(relative, "authored workspace path")
                except ExecutorError:
                    (unsafe_dirs if stat.S_ISDIR(info.st_mode) else unsafe_files).append(
                        candidate
                    )
                    continue
                if _is_link_like(candidate) or not (
                    stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                ):
                    (unsafe_dirs if stat.S_ISDIR(info.st_mode) else unsafe_files).append(
                        candidate
                    )
                    continue
                if stat.S_ISDIR(info.st_mode):
                    continue
                total += info.st_size
                if (
                    info.st_size > MAX_WORKSPACE_FILE_BYTES
                    or total > MAX_WORKSPACE_BYTES
                ):
                    unsafe_files.append(candidate)
                    continue
                try:
                    contains_secret = _stream_contains_secret_material(
                        candidate,
                        forbidden_secrets,
                        deadline_check=deadline_check,
                    )
                except ArtifactError:
                    unsafe_files.append(candidate)
                    continue
                if contains_secret:
                    unsafe_files.append(candidate)
    except OSError:
        raise ArtifactError("authored workspace cleanup failed") from None
    removal_failed = False
    for target in unsafe_files:
        try:
            target.unlink()
        except OSError:
            removal_failed = True
    for target in unsafe_dirs:
        try:
            if _is_link_like(target):
                target.unlink()
            else:
                target.rmdir()
        except OSError:
            removal_failed = True
    if removal_failed:
        raise ArtifactError("authored workspace secret could not be removed safely")
    if unsafe_files or unsafe_dirs:
        raise ArtifactError("authored workspace contained unsafe or prohibited material")


def _public_text(
    repo_root: Path,
    identity: dict[str, Any],
    label: str,
    deadline_check: Callable[[], None] | None = None,
) -> str:
    path = repo_root.joinpath(*identity["path"].split("/"))
    raw = _stream_read_bounded(
        path, label, 8 * 1024 * 1024, deadline_check=deadline_check
    )
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        _fail(f"{label} is not valid UTF-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != identity["sha256"]:
        _fail(f"{label} changed after plan revalidation")
    return text


def _extract_driver_brief_payload(brief: str) -> str:
    """Return only the canonical prompt payload between exact marker lines."""
    begin = "`=== BEGIN ===`"
    end = "`=== END ===`"
    lines = brief.splitlines(keepends=True)
    begins = [index for index, line in enumerate(lines) if line.rstrip("\n") == begin]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\n") == end]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        _fail("frozen driver brief has missing, duplicate, or malformed payload markers")
    payload = "".join(lines[begins[0] + 1 : ends[0]]).strip("\n")
    if not payload or begin in payload or end in payload:
        _fail("frozen driver brief payload is empty or malformed")
    return payload + "\n"


def _write_canonical(path: Path, value: Any) -> None:
    payload = _canonical_json(value, newline=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_canonical_exclusive(path: Path, value: Any) -> None:
    payload = _canonical_json(value, newline=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        _fail("exclusive journal file already exists")


def _append_event(path: Path, sequence: int, event: str, phase: str, at: str) -> None:
    payload = {
        "schema": EVENT_SCHEMA,
        "sequence": sequence,
        "event": event,
        "phase": phase,
        "at_utc": at,
    }
    with path.open("ab") as handle:
        handle.write(_canonical_json(payload, newline=True))
        handle.flush()
        os.fsync(handle.fileno())


def _build_retained_inventory(
    run_dir: Path,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    excluded = {"run_log.json", "run_log.sha256", "artifact_inventory.json"}
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    entries = 0

    def walk_error(_error: OSError) -> None:
        raise ArtifactError("retained run cannot be enumerated safely")

    for root, directories, names in os.walk(
        run_dir, followlinks=False, onerror=walk_error
    ):
        _deadline_tick(deadline_check)
        root_path = Path(root)
        for name in [*directories, *names]:
            _deadline_tick(deadline_check)
            candidate = root_path / name
            entries += 1
            if entries > MAX_RETAINED_FILES:
                raise ArtifactError("retained run exceeds the entry-count limit")
            if _is_link_like(candidate):
                raise ArtifactError("retained run contains a link or reparse point")
            try:
                info = candidate.lstat()
            except OSError:
                raise ArtifactError("retained run changed during inventory") from None
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ArtifactError("retained run contains a special filesystem object")
            relative = candidate.relative_to(run_dir).as_posix()
            try:
                _safe_relative(relative, "retained run path")
            except ExecutorError:
                raise ArtifactError("retained run contains an unsafe path") from None
        for name in names:
            candidate = root_path / name
            relative = candidate.relative_to(run_dir).as_posix()
            if relative in excluded:
                continue
            identity = _windows_identity(relative)
            if identity in seen:
                raise ArtifactError("retained run contains a Windows-normalized alias")
            seen.add(identity)
            digest, size = _stream_file_digest(
                candidate,
                "retained run file",
                MAX_RETAINED_BYTES,
                deadline_check=deadline_check,
            )
            total += size
            if total > MAX_RETAINED_BYTES:
                raise ArtifactError("retained run exceeds the total byte limit")
            files.append({"path": relative, "sha256": digest, "bytes": size})
    files.sort(key=lambda item: item["path"].casefold())
    return {
        "schema": "marb_retained_inventory.v1",
        "files": files,
        "file_count": len(files),
        "total_bytes": total,
    }


def _stream_file_digest(
    path: Path,
    label: str,
    max_bytes: int,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> tuple[str, int]:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(path)
            or before.st_size > max_bytes
        ):
            raise OSError
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as handle:
            while True:
                _deadline_tick(deadline_check)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OSError
                digest.update(chunk)
        after = path.lstat()
    except OSError:
        raise ArtifactError(f"{label} cannot be hashed safely") from None
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or total != after.st_size
    ):
        raise ArtifactError(f"{label} changed while it was being hashed")
    return digest.hexdigest(), total


def _file_identity(
    path: Path,
    run_dir: Path,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    digest, size = _stream_file_digest(
        path,
        "run artifact",
        MAX_RETAINED_BYTES,
        deadline_check=deadline_check,
    )
    return {
        "path": path.relative_to(run_dir.parent.parent).as_posix(),
        "sha256": digest,
        "bytes": size,
    }


def _local_file_identity(path: Path, run_dir: Path, label: str) -> dict[str, Any]:
    digest, size = _stream_file_digest(path, label, MAX_RETAINED_BYTES)
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "sha256": digest,
        "bytes": size,
    }


def _seal_run_journal(
    run_dir: Path,
    run_log: dict[str, Any],
    events_path: Path,
    sequence: int,
    *,
    phase: str,
    ended_utc: str,
    forbidden_secrets: Sequence[str],
    deadline_check: Callable[[], None] | None = None,
) -> tuple[str | None, int]:
    """Seal final events and retained inventory before the run-log digest."""
    run_log_path = run_dir / "run_log.json"
    sidecar = run_dir / "run_log.sha256"
    inventory_path = run_dir / "artifact_inventory.json"
    try:
        sequence += 1
        _append_event(
            events_path,
            sequence,
            "run_finalized",
            phase,
            ended_utc,
        )
        _deadline_tick(deadline_check)
        events_identity = _local_file_identity(
            events_path, run_dir, "final events journal"
        )
        inventory = _build_retained_inventory(
            run_dir, deadline_check=deadline_check
        )
        _deadline_tick(deadline_check)
        _write_canonical_exclusive(inventory_path, inventory)
        inventory_identity = _local_file_identity(
            inventory_path, run_dir, "retained artifact inventory"
        )
        run_log["journal"] = {
            "schema": "marb_run_journal_seal.v1",
            "status": "sealed",
            "final_event_sequence": sequence,
            "final_event": "run_finalized",
            "events": events_identity,
            "retained_inventory": inventory_identity,
        }
        log_raw = _canonical_json(run_log, newline=True)
        _assert_safe_retained_bytes(log_raw, forbidden_secrets, "run log")
        _write_canonical(run_log_path, run_log)
        log_digest = hashlib.sha256(
            _stable_read(run_log_path, "run log")
        ).hexdigest()
        with sidecar.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(log_digest + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return log_digest, sequence
    except BaseException as exc:
        run_log["status"] = "partial" if run_log.get("artifacts") else "failed"
        run_log["failure"] = {
            "category": (
                "budget_failure"
                if isinstance(exc, BudgetError)
                else "journal_integrity_failure"
            ),
            "phase": phase,
        }
        run_log["journal"] = {
            "schema": "marb_run_journal_seal.v1",
            "status": (
                "failed_closed_deadline"
                if isinstance(exc, BudgetError)
                else "failed_closed"
            ),
            "final_event_sequence": sequence,
            "final_event": "run_finalized",
            "events": None,
            "retained_inventory": None,
        }
        try:
            log_raw = _canonical_json(run_log, newline=True)
            _assert_safe_retained_bytes(log_raw, forbidden_secrets, "failed run log")
            _write_canonical(run_log_path, run_log)
            log_digest = hashlib.sha256(
                _stable_read(run_log_path, "failed run log")
            ).hexdigest()
            if not sidecar.exists():
                with sidecar.open("x", encoding="ascii", newline="\n") as handle:
                    handle.write(log_digest + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            return log_digest, sequence
        except BaseException:
            return None, sequence


def _executor_source_identity() -> dict[str, Any]:
    raw = _stable_read(Path(__file__).resolve(), "cohort executor source")
    try:
        normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        _fail("cohort executor source is not valid UTF-8")
    return {
        "path": "harness/cohort_executor.py",
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "bytes": len(normalized),
        "hash_mode": "utf8-lf",
    }


def _text_source_identity(path: Path, public_path: str, label: str) -> dict[str, Any]:
    raw = _stable_read(path, label)
    try:
        normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        _fail(f"{label} is not valid UTF-8")
    return {
        "path": public_path,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "bytes": len(normalized),
        "hash_mode": "utf8-lf",
    }


def _raw_source_identity(path: Path, public_path: str, label: str) -> dict[str, Any]:
    raw = _stable_read(path, label)
    return {
        "path": public_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "hash_mode": "raw",
    }


def _minimal_git_env() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "NUL" if os.name == "nt" else "/dev/null",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return result


def _verify_host_docker_executable(container: dict[str, Any]) -> dict[str, str]:
    text = _validated_docker_executable_text(container["docker_executable"])
    path = Path(text)
    try:
        cursor = Path(path.anchor)
        for part in path.parts[1:]:
            cursor = cursor / part
            if _is_link_like(cursor):
                raise OSError
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_link_like(path):
            raise OSError
        digest, _size = _stream_file_digest(
            path, "authorized Docker executable", 256 * 1024 * 1024
        )
    except (OSError, ArtifactError):
        _fail("authorized Docker executable is unavailable or unsafe")
    if digest != container["docker_executable_sha256"]:
        _fail("authorized Docker executable digest does not match the host binary")
    return {"path": text, "sha256": digest}


def _verify_host_git_executable(implementation: Mapping[str, Any]) -> dict[str, str]:
    text = _validated_git_executable_text(implementation.get("git_executable"))
    path = Path(text)
    try:
        if not path.is_absolute():
            raise OSError
        cursor = Path(path.anchor)
        if _is_link_like(cursor):
            raise OSError
        for part in path.parts[1:]:
            cursor = cursor / part
            if _is_link_like(cursor):
                raise OSError
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_link_like(path):
            raise OSError
        digest, _size = _stream_file_digest(
            path, "authorized Git executable", 256 * 1024 * 1024
        )
    except (OSError, ArtifactError):
        _fail("authorized Git executable is unavailable or unsafe")
    if digest != implementation.get("git_executable_sha256"):
        _fail("authorized Git executable digest does not match the host binary")
    return {"path": text, "sha256": digest}


@contextlib.contextmanager
def _locked_git_spawn_identity(
    implementation: Mapping[str, Any],
) -> Iterator[dict[str, str]]:
    """Bind Windows path components and executable bytes across process creation.

    H2b production execution is Windows Docker Desktop-only. On Windows this
    guard holds every parent directory without delete sharing and the executable
    without write/delete sharing while hashing and spawning. The POSIX branch
    exists only so fake-only tests can exercise the reader on CI; execute_plan
    rejects the production sandbox before reaching it on non-Windows hosts.
    """
    if os.name != "nt":
        yield _verify_host_git_executable(implementation)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    text = _validated_git_executable_text(implementation.get("git_executable"))
    path = Path(text)
    if not path.is_absolute():
        _fail("authorized Git executable is unavailable or unsafe")

    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    generic_read = 0x80000000
    open_existing = 3
    file_attribute_tag_info_class = 9
    invalid_handle_value = ctypes.c_void_p(-1).value

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_file_information = kernel32.GetFileInformationByHandleEx
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_file_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    directory_handles: list[int] = []
    executable_file: Any | None = None
    executable_handle: int | None = None

    def open_checked(candidate: Path, *, directory: bool) -> int:
        desired_access = file_read_attributes if directory else generic_read
        share_mode = file_share_read | (file_share_write if directory else 0)
        flags = file_flag_open_reparse_point | (
            file_flag_backup_semantics if directory else 0
        )
        handle = create_file(
            str(candidate),
            desired_access,
            share_mode,
            None,
            open_existing,
            flags,
            None,
        )
        handle_value = int(handle) if handle is not None else 0
        if handle_value in {0, invalid_handle_value}:
            raise OSError
        info = FileAttributeTagInfo()
        if not get_file_information(
            handle,
            file_attribute_tag_info_class,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            close_handle(handle)
            raise OSError
        is_directory = bool(info.FileAttributes & file_attribute_directory)
        if (
            info.FileAttributes & file_attribute_reparse_point
            or is_directory != directory
        ):
            close_handle(handle)
            raise OSError
        return handle_value

    try:
        cursor = Path(path.anchor)
        for part in path.parts[1:-1]:
            cursor = cursor / part
            directory_handles.append(open_checked(cursor, directory=True))
        executable_handle = open_checked(path, directory=False)
        try:
            descriptor = msvcrt.open_osfhandle(
                executable_handle, os.O_RDONLY | os.O_BINARY
            )
        except OSError:
            close_handle(executable_handle)
            executable_handle = None
            raise
        executable_handle = None  # descriptor owns the native handle now
        executable_file = os.fdopen(descriptor, "rb", closefd=True)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = executable_file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 256 * 1024 * 1024:
                raise OSError
            digest.update(chunk)
        measured = digest.hexdigest()
        if measured != implementation.get("git_executable_sha256"):
            _fail("authorized Git executable digest does not match the host binary")
        yield {"path": text, "sha256": measured}
    except ExecutorError:
        raise
    except (OSError, ValueError):
        _fail("authorized Git executable is unavailable or unsafe")
    finally:
        if executable_file is not None:
            try:
                executable_file.close()
            except OSError:
                pass
        if executable_handle is not None:
            try:
                close_handle(executable_handle)
            except BaseException:
                pass
        for handle in reversed(directory_handles):
            try:
                close_handle(handle)
            except BaseException:
                pass


class _GitOutputCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = bytearray()
        self.truncated = False

    def consume(self, stream: Any, process: subprocess.Popen[bytes]) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                remaining = self.limit - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
                    try:
                        process.kill()
                    except BaseException:
                        pass
                    return
        finally:
            try:
                stream.close()
            except OSError:
                pass


def _terminate_git_process(
    process: subprocess.Popen[bytes] | None,
    stdout_thread: threading.Thread | None,
) -> None:
    if process is not None:
        try:
            if process.poll() is None:
                process.kill()
        except BaseException:
            pass
        try:
            process.wait(timeout=GIT_CLEANUP_TIMEOUT_SECONDS)
        except BaseException:
            pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except BaseException:
            pass
    if stdout_thread is not None:
        stdout_thread.join(timeout=GIT_CLEANUP_TIMEOUT_SECONDS)


def _read_committed_blob_with_git(
    git_authorization: Mapping[str, Any],
    repo_root: Path,
    revision: str,
    public_path: str,
    *,
    process_factory: Callable[..., subprocess.Popen[bytes]] | None = None,
) -> bytes:
    if not isinstance(revision, str) or not FULL_COMMIT.fullmatch(revision):
        _fail("committed blob revision is malformed")
    try:
        normalized_path = _safe_relative(public_path, "committed blob path").as_posix()
    except ExecutorError:
        _fail("committed blob path is malformed")
    if normalized_path != public_path:
        _fail("committed blob path is malformed")
    try:
        repository = repo_root.resolve(strict=True)
    except OSError:
        _fail("committed blob repository is unavailable")
    if not repository.is_dir():
        _fail("committed blob repository is unavailable")

    factory = process_factory or subprocess.Popen
    process: subprocess.Popen[bytes] | None = None
    stdout_thread: threading.Thread | None = None
    capture = _GitOutputCapture(MAX_COMMITTED_BLOB_BYTES)
    try:
        # Reverify and lock the exact executable path chain and bytes across
        # process creation. The executable is never resolved through cwd/PATH.
        with _locked_git_spawn_identity(git_authorization) as git_identity:
            argv = [
                git_identity["path"],
                "--no-replace-objects",
                "-c",
                f"safe.directory={repository.as_posix()}",
                "cat-file",
                "blob",
                f"{revision}:{public_path}",
            ]
            process = factory(
                argv,
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=_minimal_git_env(),
            )
        if process.stdout is None:
            raise OSError("Git stdout pipe is unavailable")
        stdout_thread = threading.Thread(
            target=capture.consume,
            args=(process.stdout, process),
            daemon=True,
        )
        stdout_thread.start()
        process.wait(timeout=GIT_BLOB_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_git_process(process, stdout_thread)
        _fail("committed implementation blob cannot be read")
    except (OSError, subprocess.SubprocessError):
        _terminate_git_process(process, stdout_thread)
        _fail("committed implementation blob cannot be read")
    except BaseException:
        _terminate_git_process(process, stdout_thread)
        raise
    stdout_thread.join(timeout=GIT_CLEANUP_TIMEOUT_SECONDS)
    if stdout_thread.is_alive():
        _terminate_git_process(process, stdout_thread)
        _fail("committed implementation blob cannot be read")
    if process.returncode != 0 or capture.truncated:
        _fail("committed implementation blob cannot be read")
    return bytes(capture.buffer)


class _AuthorizedGitBlobReader:
    """Production reader bound to one independently authorized Git binary."""

    def __init__(self, implementation: Mapping[str, Any]) -> None:
        self._authorization = {
            "git_executable": implementation["git_executable"],
            "git_executable_sha256": implementation["git_executable_sha256"],
        }

    def preflight(self) -> dict[str, str]:
        return _verify_host_git_executable(self._authorization)

    def __call__(self, repo_root: Path, revision: str, public_path: str) -> bytes:
        return _read_committed_blob_with_git(
            self._authorization, repo_root, revision, public_path
        )


def _normalized_text_digest(raw: bytes, label: str) -> str:
    try:
        normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        _fail(f"{label} is not valid UTF-8")
    return hashlib.sha256(normalized).hexdigest()


def _executing_module_paths() -> dict[str, Path]:
    try:
        from . import isolated_container as executing_isolation
        from . import provider_transport as executing_transport
        from . import runtime_smoke_probes as executing_smoke_probes
    except ImportError:  # pragma: no cover - script-mode CLI
        import isolated_container as executing_isolation  # type: ignore[no-redef]
        import provider_transport as executing_transport  # type: ignore[no-redef]
        import runtime_smoke_probes as executing_smoke_probes  # type: ignore[no-redef]
    return {
        "planner": Path(cohort_runner.__file__).resolve(strict=True),
        "executor": Path(__file__).resolve(strict=True),
        "isolated_container": Path(executing_isolation.__file__).resolve(strict=True),
        "provider_transport": Path(executing_transport.__file__).resolve(strict=True),
        "runtime_smoke_probes": Path(executing_smoke_probes.__file__).resolve(strict=True),
        "run_limiter": Path(__file__).resolve(strict=True).parent / "container" / "run_limited.py",
        "runtime_contract": RUNTIME_CONTRACT_PATH,
        "historical_runtime_contract": HISTORICAL_RUNTIME_CONTRACT_PATH,
        "cadclaw_calibration": CADCLAW_CALIBRATION_PATH,
    }


def _verify_committed_implementation(
    repo_root: Path,
    plan: dict[str, Any],
    authorization: dict[str, Any],
    blob_reader: Callable[[Path, str, str], bytes],
) -> dict[str, dict[str, Any]]:
    revision = plan["source"]["revision"]
    approved = authorization["implementation"]
    if approved["source_revision"] != revision:
        _fail("authorized implementation revision does not match the plan")
    paths = {
        "planner": ("harness/cohort_runner.py", approved["planner_sha256"], "utf8-lf"),
        "executor": ("harness/cohort_executor.py", approved["executor_sha256"], "utf8-lf"),
        "isolated_container": (
            "harness/isolated_container.py",
            approved["isolated_container_sha256"],
            "utf8-lf",
        ),
        "provider_transport": (
            "harness/provider_transport.py",
            approved["provider_transport_sha256"],
            "utf8-lf",
        ),
        "runtime_smoke_probes": (
            "harness/runtime_smoke_probes.py",
            RUNTIME_SMOKE_PROBES_SHA256,
            "utf8-lf",
        ),
        "run_limiter": (
            "harness/container/run_limited.py",
            approved["run_limiter_sha256"],
            "utf8-lf",
        ),
        "runtime_contract": (
            "harness/container/runtime-contract.v0.13.json",
            approved["runtime_contract_sha256"],
            "raw",
        ),
        "historical_runtime_contract": (
            "harness/container/runtime-contract.v0.12.json",
            approved["historical_runtime_contract_sha256"],
            "raw",
        ),
        "cadclaw_calibration": (
            "harness/container/cadclaw-calibration.fad0dd55.json",
            approved["cadclaw_calibration_sha256"],
            "raw",
        ),
    }
    executing_paths = _executing_module_paths()
    expected_repository = executing_paths["executor"].parents[1]
    if repo_root.resolve(strict=True) != expected_repository:
        _fail("repo_root is not the repository containing the executing H2b modules")
    identities: dict[str, dict[str, Any]] = {}
    for key, (public_path, expected, hash_mode) in paths.items():
        actual_path = executing_paths.get(key)
        required_path = repo_root.joinpath(*public_path.split("/")).resolve(strict=True)
        if actual_path is None or actual_path.resolve(strict=True) != required_path:
            _fail(f"executing {key} module is outside the authorized repository")
        identity = _raw_source_identity if hash_mode == "raw" else _text_source_identity
        working = identity(actual_path, public_path, f"{key} implementation")
        committed_raw = blob_reader(repo_root, revision, public_path)
        committed_digest = (
            hashlib.sha256(committed_raw).hexdigest()
            if hash_mode == "raw"
            else _normalized_text_digest(
                committed_raw, f"committed {key} implementation"
            )
        )
        if working["sha256"] != expected or committed_digest != expected:
            _fail(f"authorized {key} implementation is not the exact committed blob")
        identities[key] = working
    if identities["planner"]["sha256"] != plan["source"]["planner"]["sha256"]:
        _fail("authorized planner identity contradicts the plan")
    return identities


def _verify_committed_public_identity(
    repo_root: Path,
    revision: str,
    identity: dict[str, Any],
    label: str,
    blob_reader: Callable[[Path, str, str], bytes],
) -> dict[str, Any]:
    if "path" in identity:
        public_path = identity["path"]
        raw = blob_reader(repo_root, revision, public_path)
        if identity["hash_mode"] == "utf8-lf":
            try:
                measured = (
                    raw.decode("utf-8")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .encode("utf-8")
                )
            except UnicodeDecodeError:
                _fail(f"committed {label} is not valid UTF-8")
        else:
            measured = raw
        if (
            len(measured) != identity["bytes"]
            or hashlib.sha256(measured).hexdigest() != identity["sha256"]
        ):
            _fail(f"committed {label} does not match the frozen plan identity")
        return dict(identity)
    archive_path = identity["archive_path"]
    archive_raw = blob_reader(repo_root, revision, archive_path)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
            matches = [
                info
                for info in archive.infolist()
                if info.filename.rstrip("/") == identity["member_path"]
                and not info.is_dir()
            ]
            if len(matches) != 1:
                _fail(f"committed {label} archive member is missing or ambiguous")
            member_raw = archive.read(matches[0])
    except (OSError, RuntimeError, zipfile.BadZipFile):
        _fail(f"committed {label} archive cannot be verified")
    if identity["hash_mode"] == "utf8-lf":
        try:
            measured = (
                member_raw.decode("utf-8")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .encode("utf-8")
            )
        except UnicodeDecodeError:
            _fail(f"committed {label} archive member is not valid UTF-8")
    else:
        measured = member_raw
    if (
        len(measured) != identity["bytes"]
        or hashlib.sha256(measured).hexdigest() != identity["sha256"]
    ):
        _fail(f"committed {label} archive member does not match the frozen plan identity")
    return dict(identity)


def _verify_committed_plan_inputs(
    repo_root: Path,
    plan: dict[str, Any],
    blob_reader: Callable[[Path, str, str], bytes],
) -> dict[str, dict[str, Any]]:
    revision = plan["source"]["revision"]
    identities = {"registry": plan["source"]["registry"], **plan["frozen_public_inputs"]}
    verified: dict[str, dict[str, Any]] = {}
    for label, identity in identities.items():
        if not isinstance(identity, dict):
            _fail("frozen public input identity is malformed")
        verified[label] = _verify_committed_public_identity(
            repo_root,
            revision,
            identity,
            label.replace("_", " "),
            blob_reader,
        )
    return verified


def _copy_evidence(
    source: Path,
    target: Path,
    run_dir: Path,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    try:
        before = source.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(source)
            or before.st_size < 1
            or before.st_size > MAX_WORKSPACE_FILE_BYTES
        ):
            raise OSError
        digest = hashlib.sha256()
        total = 0
        with source.open("rb") as input_handle, target.open("xb") as output_handle:
            while True:
                _deadline_tick(deadline_check)
                chunk = input_handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_WORKSPACE_FILE_BYTES:
                    raise OSError
                digest.update(chunk)
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        after = source.lstat()
    except OSError:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise ArtifactError("required STEP output cannot be copied safely") from None
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or total != after.st_size
    ):
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise ArtifactError("required STEP output changed during evidence capture")
    return {
        "path": target.relative_to(run_dir.parent.parent).as_posix(),
        "sha256": digest.hexdigest(),
        "bytes": total,
    }


def _step_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
    )


def _discovered_step_identity(
    discovered: _DiscoveredStepOutput,
) -> tuple[int, int, int, int, int]:
    return (
        discovered.device,
        discovered.inode,
        discovered.mode,
        discovered.bytes,
        discovered.mtime_ns,
    )


def _copy_discovered_step(
    discovered: _DiscoveredStepOutput,
    target: Path,
    run_dir: Path,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Copy exactly the STEP bytes and file identity accepted at discovery."""
    source = discovered.path
    expected_identity = _discovered_step_identity(discovered)
    target_created = False
    try:
        before = source.lstat()
        if (
            _step_identity(before) != expected_identity
            or not stat.S_ISREG(before.st_mode)
            or _is_link_like(source)
            or before.st_size < 1
            or before.st_size > MAX_WORKSPACE_FILE_BYTES
        ):
            raise ArtifactError("required STEP output changed after validation")
        digest = hashlib.sha256()
        total = 0
        with source.open("rb") as input_handle:
            opened = os.fstat(input_handle.fileno())
            if _step_identity(opened) != expected_identity:
                raise ArtifactError("required STEP output changed after validation")
            with target.open("xb") as output_handle:
                target_created = True
                while True:
                    _deadline_tick(deadline_check)
                    chunk = input_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_WORKSPACE_FILE_BYTES:
                        raise ArtifactError("required STEP output changed after validation")
                    digest.update(chunk)
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            after_handle = os.fstat(input_handle.fileno())
        after_path = source.lstat()
        if (
            _step_identity(after_handle) != expected_identity
            or _step_identity(after_path) != expected_identity
            or total != discovered.bytes
            or digest.hexdigest() != discovered.sha256
        ):
            raise ArtifactError("required STEP output changed after validation")
    except BaseException as exc:
        cleanup_failed = False
        if target_created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise ArtifactError("required STEP output cleanup could not be verified") from None
        if isinstance(exc, OSError):
            raise ArtifactError("required STEP output cannot be copied safely") from None
        raise
    return {
        "path": target.relative_to(run_dir.parent.parent).as_posix(),
        "sha256": discovered.sha256,
        "bytes": discovered.bytes,
    }


def _deterministic_source_zip(
    workspace: Path,
    staged_paths: set[str],
    target: Path,
    run_dir: Path,
    forbidden_secrets: Sequence[str] = (),
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    del staged_paths  # immutable inputs are outside the writable model workspace
    members: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    has_python = False
    source_total = 0
    workspace_inventory = _inventory_workspace(
        workspace, deadline_check=deadline_check, content_bound=True
    )
    for item in workspace_inventory:
        _deadline_tick(deadline_check)
        relative = item["path"]
        try:
            rel = _safe_relative(relative, "editable source path")
        except ExecutorError:
            raise ArtifactError("editable source contains an unsafe filename") from None
        identity = _windows_identity(relative)
        if identity == _windows_identity("MARB_SOURCE_MANIFEST.json"):
            raise ArtifactError("editable source uses a reserved manifest identity")
        if identity == _windows_identity("MARB_EXECUTION_STATUS.json"):
            raise ArtifactError("editable source uses a reserved execution-status identity")
        if rel.name.casefold().startswith(".marb_"):
            raise ArtifactError("editable source contains an unexpected executor-reserved file")
        if identity in {
            _windows_identity("export.step"),
            _windows_identity("cadquery_native_export.step"),
        }:
            continue
        size = item["bytes"]
        if size > MAX_EDITABLE_SOURCE_FILE_BYTES:
            raise ArtifactError("editable source file exceeds the size limit")
        source_total += size
        if source_total > MAX_EDITABLE_SOURCE_TOTAL_BYTES:
            raise ArtifactError("editable source exceeds the total size limit")
        if len(members) + 1 > MAX_EDITABLE_SOURCE_FILES:
            raise ArtifactError("editable source exceeds the file-count limit")
        target_source = _workspace_path(workspace, relative, "editable source path")
        try:
            before = target_source.lstat()
        except OSError:
            raise ArtifactError("editable source changed after workspace inventory")
        expected_stat = (
            item["device"],
            item["inode"],
            item["bytes"],
            item["mtime_ns"],
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(target_source)
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != expected_stat
        ):
            raise ArtifactError("editable source changed after workspace inventory")
        raw = _stream_read_bounded(
            target_source,
            "editable source file",
            MAX_EDITABLE_SOURCE_FILE_BYTES,
            deadline_check=deadline_check,
        )
        try:
            after = target_source.lstat()
        except OSError:
            raise ArtifactError("editable source changed after workspace inventory") from None
        if (
            (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            != expected_stat
            or len(raw) != size
            or hashlib.sha256(raw).hexdigest() != item["sha256"]
        ):
            raise ArtifactError("editable source changed after workspace inventory")
        _assert_safe_retained_bytes(raw, forbidden_secrets, "editable source file")
        if identity in seen:
            raise ArtifactError("editable source contains a Windows-normalized alias")
        seen.add(identity)
        members.append((relative, raw))
        has_python = has_python or rel.suffix.casefold() == ".py"
    final_inventory = _inventory_workspace(
        workspace, deadline_check=deadline_check, content_bound=True
    )
    if final_inventory != workspace_inventory:
        raise ArtifactError("editable source workspace changed during capture")
    if not members or not has_python:
        raise ArtifactError("CadQuery run did not retain editable Python source")
    manifest = {
        "schema": "marb_editable_source_manifest.v1",
        "files": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, raw in members
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        entries = members + [("MARB_SOURCE_MANIFEST.json", _canonical_json(manifest, newline=True))]
        for name, raw in sorted(entries, key=lambda item: item[0].encode("utf-8")):
            _deadline_tick(deadline_check)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, raw)
    payload = buffer.getvalue()
    temporary = target.with_name(f".{target.name}.marb-tmp")
    temporary_created = False
    published = False
    try:
        handle = temporary.open("xb")
        temporary_created = True
        with handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("short write")
            handle.flush()
            os.fsync(handle.fileno())
        verified_digest, verified_size = _stream_file_digest(
            temporary,
            "editable source archive",
            MAX_RETAINED_BYTES,
            deadline_check=deadline_check,
        )
        os.link(temporary, target)
        published = True
        temporary.unlink()
        temporary_created = False
        result = _file_identity(target, run_dir, deadline_check=deadline_check)
        if (
            result["sha256"] != verified_digest
            or result["bytes"] != verified_size
        ):
            raise ArtifactError("editable source ZIP changed during publication")
    except BaseException as exc:
        cleanup_failed = False
        if published:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        if temporary_created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise ArtifactError("editable source ZIP cleanup could not be verified") from None
        if isinstance(exc, OSError):
            raise ArtifactError("editable source ZIP cannot be published safely") from None
        raise
    result["manifest"] = manifest
    return result


@dataclasses.dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class ProviderResponse:
    content: str | None
    tool_calls: tuple[ProviderToolCall, ...]
    usage: dict[str, int] | None = None
    cost_usd: str | None = None
    response_model: str | None = None
    response_id: str | None = None
    request_id: str | None = None
    system_fingerprint: str | None = None
    transport_request_sha256: str | None = None
    transport_response_sha256: str | None = None


class ProviderSession(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> ProviderResponse: ...


def _provider_identifier(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and value
        and len(value) <= 256
        and value.isascii()
        and not CONTROL.search(value)
    ):
        return value
    return None


def _provider_identity_for_record(
    value: Any,
    forbidden_secrets: Sequence[str],
    *,
    expected_visible_label: str | None = None,
) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    identifier = _provider_identifier(value)
    if identifier is None:
        raise ProviderCallError("provider response identity metadata was malformed")
    if any(secret and secret in identifier for secret in forbidden_secrets):
        raise ProviderCallError("provider response identity contained prohibited material")
    digest = hashlib.sha256(identifier.encode("ascii")).hexdigest()
    visible = (
        identifier
        if expected_visible_label is not None
        and identifier == expected_visible_label
        and SAFE_PROVIDER_MODEL.fullmatch(identifier)
        else None
    )
    return visible, digest


class Sandbox(Protocol):
    def start(self, timeout_seconds: float) -> dict[str, Any]: ...

    def run_python(self, relative_path: str, timeout_seconds: float) -> dict[str, Any]: ...

    def close(self) -> None: ...


ProviderTransportRunner = Callable[
    [str, str, dict[str, str], bytes, float], tuple[str, str | None, bytes]
]
MAX_PROVIDER_HELPER_STDOUT_BYTES = 24_000_000


class _ProviderOutputCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = bytearray()
        self.truncated = False

    def consume(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                remaining = self.limit - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        finally:
            stream.close()


def _write_provider_input(
    stream: Any, payload: bytes, errors: list[BaseException]
) -> None:
    try:
        stream.write(payload)
        stream.flush()
    except BaseException as exc:
        errors.append(exc)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _terminate_provider_process(
    process: subprocess.Popen[bytes] | None,
    writer_thread: threading.Thread | None,
    stdout_thread: threading.Thread | None,
) -> None:
    if process is not None:
        try:
            if process.poll() is None:
                process.kill()
        except BaseException:
            pass
        try:
            process.wait()
        except BaseException:
            pass
    if writer_thread is not None:
        writer_thread.join()
    if stdout_thread is not None:
        stdout_thread.join()


def _minimal_provider_transport_env() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return result


def _subprocess_provider_transport(
    endpoint: str,
    approved_origin: str,
    headers: dict[str, str],
    request_raw: bytes,
    timeout_seconds: float,
) -> tuple[str, str | None, bytes]:
    helper = Path(__file__).resolve().with_name("provider_transport.py")
    payload = _canonical_json(
        {
            "endpoint": endpoint,
            "approved_origin": approved_origin,
            "headers": headers,
            "request_body_b64": base64.b64encode(request_raw).decode("ascii"),
        }
    )
    process: subprocess.Popen[bytes] | None = None
    stdout_thread: threading.Thread | None = None
    writer_thread: threading.Thread | None = None
    capture = _ProviderOutputCapture(MAX_PROVIDER_HELPER_STDOUT_BYTES)
    writer_errors: list[BaseException] = []
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(helper)],
            cwd=helper.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=_minimal_provider_transport_env(),
        )
        if process.stdin is None or process.stdout is None:
            raise OSError("provider transport pipes unavailable")
        stdout_thread = threading.Thread(
            target=capture.consume, args=(process.stdout,), daemon=True
        )
        writer_thread = threading.Thread(
            target=_write_provider_input,
            args=(process.stdin, payload, writer_errors),
            daemon=True,
        )
        stdout_thread.start()
        writer_thread.start()
        process.wait(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired:
        _terminate_provider_process(process, writer_thread, stdout_thread)
        raise ProviderCallError("provider request exceeded the hard wall-clock timeout") from None
    except (OSError, subprocess.SubprocessError):
        _terminate_provider_process(process, writer_thread, stdout_thread)
        raise ProviderCallError("provider transport process failed") from None
    except BaseException:
        _terminate_provider_process(process, writer_thread, stdout_thread)
        raise
    writer_thread.join()
    stdout_thread.join()
    stdout = bytes(capture.buffer)
    if process.returncode != 0 or capture.truncated or writer_errors:
        raise ProviderCallError("provider transport rejected the request")
    try:
        value = json.loads(stdout.decode("ascii"))
        if not isinstance(value, dict) or set(value) != {
            "status",
            "response_url",
            "request_id",
            "response_body_b64",
        }:
            raise ValueError
        if value["status"] != "ok" or not isinstance(value["response_url"], str):
            raise ValueError
        request_id = value["request_id"]
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError
        raw = base64.b64decode(value["response_body_b64"], validate=True)
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        raise ProviderCallError("provider transport returned malformed output") from None
    if len(raw) > 16_000_000:
        raise ProviderCallError("provider response exceeded the size limit")
    return value["response_url"], request_id, raw


class OpenAICompatibleSession:
    """Minimal non-streaming adapter, constructed only after all safety preflights."""

    def __init__(
        self,
        endpoint: str,
        model_id: str,
        credential: str | None,
        *,
        transport_runner: ProviderTransportRunner | None = None,
    ) -> None:
        self._url = endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"
        _raw, self._approved_origin = sanitize_endpoint(endpoint)
        self._model_id = model_id
        self._credential = credential
        self._transport_runner = transport_runner or _subprocess_provider_transport

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "tools": tools,
            "temperature": settings["temperature"],
            "max_tokens": settings["max_output_tokens"],
        }
        if settings["seed"] is not None:
            body["seed"] = settings["seed"]
        headers = {"Content-Type": "application/json"}
        if self._credential is not None:
            headers["Authorization"] = "Bearer " + self._credential
        request_raw = _canonical_json(body)
        try:
            response_url, request_id, raw = self._transport_runner(
                self._url,
                self._approved_origin,
                headers,
                request_raw,
                settings["timeout_seconds"],
            )
            _response_url, response_origin = sanitize_endpoint(response_url)
            if response_origin != self._approved_origin:
                raise ProviderCallError("provider response origin changed")
            if request_id is not None and _provider_identifier(request_id) is None:
                raise ProviderCallError("provider request identity metadata was malformed")
        except ProviderCallError:
            raise
        except (OSError, TimeoutError):
            raise ProviderCallError("provider request failed") from None
        if len(raw) > 16_000_000:
            raise ProviderCallError("provider response exceeded the size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError
            choices = value.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, dict) or set(choice) - {"index", "message", "finish_reason", "logprobs"}:
                raise TypeError
            message = choice.get("message")
            if not isinstance(message, dict):
                raise TypeError
            if not set(message).issubset({"role", "content", "tool_calls", "refusal"}):
                raise TypeError
            content_value = message.get("content")
            if content_value is not None and not isinstance(content_value, str):
                raise TypeError
            tool_calls_value = message.get("tool_calls")
            if tool_calls_value is not None and not isinstance(tool_calls_value, list):
                raise TypeError
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ProviderCallError("provider response was malformed") from None
        calls: list[ProviderToolCall] = []
        for item in tool_calls_value or []:
            try:
                if not isinstance(item, dict) or set(item) != {"id", "type", "function"}:
                    raise TypeError
                if (
                    item.get("type") != "function"
                    or _provider_identifier(item.get("id")) is None
                ):
                    raise TypeError
                function = item.get("function")
                if not isinstance(function, dict) or set(function) != {"name", "arguments"}:
                    raise TypeError
                if function.get("name") not in {"write_file", "run_python", "read_output"}:
                    raise TypeError
                if not isinstance(function.get("arguments"), str):
                    raise TypeError
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise TypeError
                calls.append(
                    ProviderToolCall(
                        call_id=item["id"],
                        name=function["name"],
                        arguments=arguments,
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                raise ProviderCallError("provider tool call was malformed") from None
        usage_value = value.get("usage")
        usage = None
        if usage_value is not None:
            if not isinstance(usage_value, dict) or not {
                "prompt_tokens", "completion_tokens", "total_tokens"
            }.issubset(usage_value):
                raise ProviderCallError("provider usage metadata was malformed")
            candidate = {
                "input_tokens": usage_value.get("prompt_tokens"),
                "output_tokens": usage_value.get("completion_tokens"),
                "total_tokens": usage_value.get("total_tokens"),
            }
            if not all(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in candidate.values()
            ) or candidate["input_tokens"] + candidate["output_tokens"] != candidate["total_tokens"]:
                raise ProviderCallError("provider usage metadata was malformed")
            usage = candidate  # type: ignore[assignment]
        cost = value.get("cost_usd")
        if cost is not None:
            try:
                cost = _decimal_text(cost, "provider cost", allow_zero=True)
            except ExecutorError:
                raise ProviderCallError("provider cost metadata was malformed") from None
        for key in ("model", "id", "system_fingerprint"):
            if (
                key in value
                and value[key] is not None
                and _provider_identifier(value[key]) is None
            ):
                raise ProviderCallError("provider identity metadata was malformed")
        return ProviderResponse(
            content=content_value,
            tool_calls=tuple(calls),
            usage=usage,
            cost_usd=cost,
            response_model=value.get("model") if isinstance(value.get("model"), str) else None,
            response_id=value.get("id") if isinstance(value.get("id"), str) else None,
            request_id=request_id,
            system_fingerprint=(
                value.get("system_fingerprint")
                if isinstance(value.get("system_fingerprint"), str)
                else None
            ),
            transport_request_sha256=hashlib.sha256(request_raw).hexdigest(),
            transport_response_sha256=hashlib.sha256(raw).hexdigest(),
        )


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write or replace one editable text file inside the isolated run workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_python",
                "description": "Execute one workspace-relative Python source file in the isolated CadQuery container.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_output",
                "description": "Read a bounded UTF-8 text output from the run workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _write_model_file(
    workspace: Path,
    arguments: dict[str, Any],
    written: set[str],
    forbidden_secrets: Sequence[str] = (),
) -> str:
    if set(arguments) != {"path", "content"} or not isinstance(arguments.get("content"), str):
        return "error: malformed write_file arguments"
    raw = arguments["content"].encode("utf-8")
    if len(raw) > MAX_TOOL_TEXT_BYTES:
        return "error: write_file content exceeds the size limit"
    _assert_safe_retained_bytes(raw, forbidden_secrets, "model-authored file")
    try:
        rel = _safe_relative(arguments.get("path"), "model write path")
        if rel.name.casefold().startswith(".marb_") or _windows_identity(
            rel.as_posix()
        ) in {
            _windows_identity("MARB_SOURCE_MANIFEST.json"),
            _windows_identity("MARB_EXECUTION_STATUS.json"),
        } or rel.parts[0].casefold() == "kit":
            raise ExecutorError("reserved path")
        target = _workspace_path(workspace, rel.as_posix(), "model write path")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".marb-tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ExecutorError("temporary path already exists")
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        written.add(rel.as_posix())
        return f"ok: wrote {len(raw)} bytes"
    except (ExecutorError, OSError):
        return "error: write_file path or operation was rejected"


def _read_model_output(workspace: Path, arguments: dict[str, Any]) -> str:
    if set(arguments) != {"path"}:
        return "error: malformed read_output arguments"
    try:
        target = _workspace_path(workspace, arguments.get("path"), "model read path")
        try:
            if target.lstat().st_size > MAX_READBACK_BYTES:
                return "error: output exceeds the readback size limit"
        except OSError:
            raise ExecutorError("model read output is unavailable") from None
        raw = _stream_read_bounded(
            target, "model read output", MAX_READBACK_BYTES
        )
        if len(raw) > MAX_READBACK_BYTES:  # defensive; helper already enforces this
            return "error: output exceeds the readback size limit"
        return raw.decode("utf-8")
    except (ExecutorError, ArtifactError, UnicodeDecodeError):
        return "error: read_output path or content was rejected"


def _run_model_python(
    workspace: Path,
    sandbox: Sandbox,
    arguments: dict[str, Any],
    written: set[str],
    timeout_seconds: float,
) -> str:
    if set(arguments) != {"path"}:
        return "error: malformed run_python arguments"
    try:
        rel = _safe_relative(arguments.get("path"), "Python tool path")
        if rel.suffix.casefold() != ".py" or rel.as_posix() not in written:
            return "error: run_python accepts only model-written Python files"
        target = _workspace_path(workspace, rel.as_posix(), "Python tool path")
        _stream_file_digest(target, "Python tool source", MAX_TOOL_TEXT_BYTES)
        result = sandbox.run_python(rel.as_posix(), timeout_seconds)
    except (ExecutorError, ArtifactError, OSError):
        return "error: isolated Python execution failed"
    status = result.get("status")
    output = result.get("output")
    if status not in {"ok", "error", "timed_out"} or not isinstance(output, str):
        return "error: isolated Python executor returned malformed output"
    encoded = output.encode("utf-8", errors="replace")[:MAX_READBACK_BYTES]
    return f"{status}: " + encoded.decode("utf-8", errors="replace")


def _assistant_message(response: ProviderResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True, separators=(",", ":")),
                },
            }
            for call in response.tool_calls
        ]
    return message


@dataclasses.dataclass
class ExecutionBudget:
    max_total_turns: int
    max_total_tool_calls: int
    max_run_python_calls: int
    max_request_bytes: int
    max_transcript_bytes: int
    deadline_monotonic: float
    billing_mode: str
    max_cost_usd: decimal.Decimal | None
    zero_cost_attested: bool
    turns_used: int = 0
    tool_calls_used: int = 0
    run_python_calls_used: int = 0
    reported_cost_usd: decimal.Decimal = decimal.Decimal("0")


def _remaining_wall_seconds(
    budget: ExecutionBudget, monotonic: Callable[[], float]
) -> float:
    remaining = budget.deadline_monotonic - monotonic()
    if remaining <= 0:
        raise BudgetError("authorized execution wall-clock deadline was exhausted")
    return remaining


def _assert_transcript_bound(
    messages: list[dict[str, Any]], budget: ExecutionBudget
) -> None:
    if len(_canonical_json(messages)) > budget.max_transcript_bytes:
        raise BudgetError("provider transcript exceeds the authorized byte limit")


def _phase_loop(
    session: ProviderSession,
    sandbox: Sandbox,
    workspace: Path,
    messages: list[dict[str, Any]],
    settings: dict[str, Any],
    written: set[str],
    calls: list[dict[str, Any]],
    phase: str,
    budget: ExecutionBudget,
    provider_model_id: str,
    now: Callable[[], dt.datetime],
    monotonic: Callable[[], float],
    staged_manifest: dict[str, Any],
    immutable_input_root: Path,
    forbidden_secrets: Sequence[str],
    checkpoint: Callable[[str, str], None],
    reserve_turns_after_phase: int = 0,
) -> None:
    tools = _tool_definitions()
    turn = 0
    phase_turn_limit = budget.max_total_turns - budget.turns_used - reserve_turns_after_phase
    if phase_turn_limit < 1:
        raise BudgetError("authorized turn budget cannot reach the requested phase")
    while turn < phase_turn_limit and budget.turns_used < budget.max_total_turns:
        remaining_wall = _remaining_wall_seconds(budget, monotonic)
        if (
            budget.billing_mode == "metered"
            and budget.max_cost_usd is not None
            and budget.reported_cost_usd >= budget.max_cost_usd
        ):
            raise BudgetError("authorized provider cost budget is exhausted")
        turn += 1
        budget.turns_used += 1
        _assert_transcript_bound(messages, budget)
        effective_settings = dict(settings)
        effective_settings["timeout_seconds"] = min(
            float(settings["timeout_seconds"]), remaining_wall
        )
        intent = {
            "protocol": PROVIDER_PROTOCOL,
            "model_id": provider_model_id,
            "messages": messages,
            "tools": tools,
            "settings": effective_settings,
        }
        intent_raw = _canonical_json(intent)
        if len(intent_raw) > budget.max_request_bytes:
            raise BudgetError("provider request exceeds the authorized byte limit")
        record: dict[str, Any] = {
            "ordinal": len(calls) + 1,
            "phase": phase,
            "phase_turn": turn,
            "total_turn": budget.turns_used,
            "status": "attempted",
            "started_utc": _utc_text(now()),
            "ended_utc": None,
            "elapsed_seconds": None,
            "request_intent_sha256": hashlib.sha256(intent_raw).hexdigest(),
            "request_bytes": len(intent_raw),
            "effective_timeout_seconds": effective_settings["timeout_seconds"],
            "transport_request_sha256": None,
            "transport_response_sha256": None,
            "response_model": None,
            "response_model_sha256": None,
            "response_id_sha256": None,
            "request_id_sha256": None,
            "system_fingerprint_sha256": None,
            "usage": None,
            "cost_usd": None,
            "tool_call_count": None,
            "policy_violations": [],
        }
        calls.append(record)
        checkpoint("provider_call_attempted", phase)
        started = monotonic()
        try:
            response = session.complete(messages, tools, effective_settings)
        except BaseException:
            record["status"] = "attempted_not_reported"
            record["ended_utc"] = _utc_text(now())
            record["elapsed_seconds"] = round(max(0.0, monotonic() - started), 6)
            checkpoint("provider_call_failed", phase)
            raise
        after_call = monotonic()
        elapsed = round(max(0.0, after_call - started), 6)
        response_after_deadline = after_call >= budget.deadline_monotonic
        try:
            if not isinstance(response, ProviderResponse):
                raise ProviderCallError("provider adapter returned an invalid response")
            if response.content is not None and not isinstance(response.content, str):
                raise ProviderCallError("provider response content was malformed")
            if (
                not isinstance(response.tool_calls, tuple)
                or any(
                    not isinstance(call, ProviderToolCall)
                    or not isinstance(call.call_id, str)
                    or not _provider_identifier(call.call_id)
                    or call.name not in {"write_file", "run_python", "read_output"}
                    or not isinstance(call.arguments, dict)
                    for call in response.tool_calls
                )
                or len({call.call_id for call in response.tool_calls})
                != len(response.tool_calls)
            ):
                raise ProviderCallError("provider tool-call metadata was malformed")
            usage = response.usage
            if usage is not None and (
                set(usage) != {"input_tokens", "output_tokens", "total_tokens"}
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    for value in usage.values()
                )
                or usage["input_tokens"] + usage["output_tokens"]
                != usage["total_tokens"]
            ):
                raise ProviderCallError("provider usage metadata was malformed")
            cost: str | None = None
            if response.cost_usd is not None:
                try:
                    cost = _decimal_text(
                        response.cost_usd, "provider cost", allow_zero=True
                    )
                except ExecutorError:
                    raise ProviderCallError(
                        "provider cost metadata was malformed"
                    ) from None
            response_model, response_model_sha256 = _provider_identity_for_record(
                response.response_model,
                forbidden_secrets,
                expected_visible_label=provider_model_id,
            )
            if response.response_model is None or response_model != provider_model_id:
                raise ProviderCallError(
                    "provider response model did not match the authorized model"
                )
            _discarded, response_id_sha256 = _provider_identity_for_record(
                response.response_id, forbidden_secrets
            )
            _discarded, request_id_sha256 = _provider_identity_for_record(
                response.request_id, forbidden_secrets
            )
            _discarded, system_fingerprint_sha256 = _provider_identity_for_record(
                response.system_fingerprint, forbidden_secrets
            )
            for value in (
                response.transport_request_sha256,
                response.transport_response_sha256,
            ):
                if value is not None and (
                    not isinstance(value, str) or not HEX64.fullmatch(value)
                ):
                    raise ProviderCallError(
                        "provider transport hash metadata was malformed"
                    )
        except (ProviderCallError, TypeError, AttributeError):
            record["status"] = "response_rejected"
            record["ended_utc"] = _utc_text(now())
            record["elapsed_seconds"] = elapsed
            checkpoint("provider_call_rejected", phase)
            raise
        record.update(
            {
                "status": "response_received",
                "ended_utc": _utc_text(now()),
                "elapsed_seconds": elapsed,
                "transport_request_sha256": response.transport_request_sha256,
                "transport_response_sha256": response.transport_response_sha256,
                "response_model": response_model,
                "response_model_sha256": response_model_sha256,
                "response_id_sha256": response_id_sha256,
                "request_id_sha256": request_id_sha256,
                "system_fingerprint_sha256": system_fingerprint_sha256,
                "usage": usage,
                "cost_usd": cost,
                "tool_call_count": len(response.tool_calls),
            }
        )
        run_python_count = sum(
            1 for call in response.tool_calls if call.name == "run_python"
        )
        if response_after_deadline:
            record["policy_violations"].append("wall_deadline")
        if budget.tool_calls_used + len(response.tool_calls) > budget.max_total_tool_calls:
            record["policy_violations"].append("total_tool_calls")
        if budget.run_python_calls_used + run_python_count > budget.max_run_python_calls:
            record["policy_violations"].append("run_python_calls")
        if budget.billing_mode == "metered":
            if cost is None:
                record["policy_violations"].append("cost_not_reported")
                record["status"] = "response_received_cost_not_reported"
                checkpoint("provider_call_rejected", phase)
                raise BudgetError("metered provider did not report call cost")
            budget.reported_cost_usd += decimal.Decimal(cost)
            if (
                budget.max_cost_usd is None
                or budget.reported_cost_usd > budget.max_cost_usd
            ):
                record["policy_violations"].append("cost_budget")
                record["status"] = "budget_exceeded"
                checkpoint("provider_call_rejected", phase)
                raise BudgetError("provider reported cost above the authorized maximum")
        elif cost is not None and decimal.Decimal(cost) != 0:
            record["policy_violations"].append("zero_cost_attestation")
            record["status"] = "zero_cost_attestation_contradicted"
            checkpoint("provider_call_rejected", phase)
            raise BudgetError("provider cost contradicts the zero-cost authorization")
        if response_after_deadline:
            record["status"] = "response_received_after_deadline"
            checkpoint("provider_call_rejected", phase)
            raise BudgetError("provider response arrived after the authorized deadline")
        if "total_tool_calls" in record["policy_violations"]:
            record["status"] = "tool_call_budget_exceeded"
            checkpoint("provider_call_rejected", phase)
            raise BudgetError("provider tool-call count exceeds the authorized limit")
        if "run_python_calls" in record["policy_violations"]:
            record["status"] = "run_python_budget_exceeded"
            checkpoint("provider_call_rejected", phase)
            raise BudgetError("run_python count exceeds the authorized limit")
        budget.tool_calls_used += len(response.tool_calls)
        budget.run_python_calls_used += run_python_count
        checkpoint("provider_call_completed", phase)
        messages.append(_assistant_message(response))
        _assert_transcript_bound(messages, budget)
        if not response.tool_calls:
            return
        for call in response.tool_calls:
            if call.name == "write_file":
                result = _write_model_file(
                    workspace, call.arguments, written, forbidden_secrets
                )
            elif call.name == "run_python":
                _verify_staged_kit(
                    immutable_input_root,
                    staged_manifest,
                    deadline_check=lambda: _remaining_wall_seconds(
                        budget, monotonic
                    ),
                )
                tool_timeout = min(
                    float(settings["timeout_seconds"]),
                    _remaining_wall_seconds(budget, monotonic),
                )
                result = _run_model_python(
                    workspace,
                    sandbox,
                    call.arguments,
                    written,
                    tool_timeout,
                )
            elif call.name == "read_output":
                result = _read_model_output(workspace, call.arguments)
            else:
                result = "error: unsupported tool"
            messages.append({"role": "tool", "tool_call_id": call.call_id, "content": result})
            _assert_transcript_bound(messages, budget)
            _remaining_wall_seconds(budget, monotonic)
            _scrub_workspace_secrets(
                workspace,
                {item["path"] for item in staged_manifest["files"]},
                forbidden_secrets,
                deadline_check=lambda: _remaining_wall_seconds(budget, monotonic),
            )
            _verify_staged_kit(
                immutable_input_root,
                staged_manifest,
                deadline_check=lambda: _remaining_wall_seconds(budget, monotonic),
            )
        if budget.turns_used >= budget.max_total_turns:
            return


def _find_step_output(
    workspace: Path,
    deadline_check: Callable[[], None] | None = None,
) -> _DiscoveredStepOutput:
    allowed = {"export.step", "cadquery_native_export.step"}
    allowed_identities = {_windows_identity(name) for name in allowed}
    matches = [
        item["path"]
        for item in _inventory_workspace(workspace, deadline_check=deadline_check)
        if _windows_identity(item["path"]) in allowed_identities
    ]
    if len(matches) != 1:
        raise ArtifactError("run must produce exactly one canonical STEP output")
    if matches[0] not in allowed:
        raise ArtifactError("canonical STEP output must use its exact published spelling")
    path = _workspace_path(workspace, matches[0], "canonical STEP output")
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(path)
            or before.st_size < 1
            or before.st_size > MAX_WORKSPACE_FILE_BYTES
        ):
            raise OSError
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if _step_identity(opened) != _step_identity(before):
                raise ArtifactError("STEP output changed while it was being validated")
            while True:
                _deadline_tick(deadline_check)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_WORKSPACE_FILE_BYTES:
                    raise OSError
                digest.update(chunk)
            after_handle = os.fstat(handle.fileno())
        after_path = path.lstat()
    except OSError:
        raise ArtifactError("STEP output cannot be validated safely") from None
    if (
        _step_identity(after_handle) != _step_identity(before)
        or _step_identity(after_path) != _step_identity(before)
        or total != before.st_size
    ):
        raise ArtifactError("STEP output changed while it was being validated")
    return _DiscoveredStepOutput(
        path=path,
        device=before.st_dev,
        inode=before.st_ino,
        mode=before.st_mode,
        bytes=total,
        mtime_ns=before.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def _usage_summary(calls: list[dict[str, Any]], billing: str, zero_cost: bool) -> dict[str, Any]:
    if not calls:
        return {
            "tokens": {
                "status": "not_incurred",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "cost": {"status": "not_incurred", "currency": "USD", "amount": None},
        }
    if any(
        item.get("status") in {"attempted_not_reported", "response_rejected"}
        for item in calls
    ):
        return {
            "tokens": {
                "status": "attempted_not_reported",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "cost": {
                "status": "attempted_not_reported",
                "currency": "USD",
                "amount": None,
            },
        }
    usages = [item.get("usage") for item in calls]
    if all(usage is not None for usage in usages):
        tokens = {
            "status": "reported",
            "input_tokens": sum(usage["input_tokens"] for usage in usages),
            "output_tokens": sum(usage["output_tokens"] for usage in usages),
            "total_tokens": sum(usage["total_tokens"] for usage in usages),
        }
    else:
        tokens = {
            "status": "provider_not_reported",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    reported_costs = [
        decimal.Decimal(item["cost_usd"])
        for item in calls
        if item.get("cost_usd") is not None
    ]
    if billing == "local-no-charge" and zero_cost and any(
        amount != 0 for amount in reported_costs
    ):
        cost = {
            "status": "zero_cost_attestation_contradicted",
            "currency": "USD",
            "amount": format(sum(reported_costs, decimal.Decimal(0)), "f"),
        }
    elif billing == "local-no-charge" and zero_cost:
        cost = {"status": "not_billed_attested", "currency": "USD", "amount": "0"}
    elif any(
        item.get("status") == "response_received_cost_not_reported"
        for item in calls
    ):
        cost = {
            "status": "attempted_not_reported",
            "currency": "USD",
            "amount": None,
        }
    elif all(item["cost_usd"] is not None for item in calls):
        amount = sum(decimal.Decimal(item["cost_usd"]) for item in calls)
        cost = {"status": "reported", "currency": "USD", "amount": format(amount, "f")}
    else:
        cost = {"status": "provider_not_reported", "currency": "USD", "amount": None}
    return {"tokens": tokens, "cost": cost}


def _safe_failure_category(exc: BaseException) -> str:
    if isinstance(exc, ProviderCallError):
        return "provider_failure"
    if isinstance(exc, ArtifactError):
        return "artifact_failure"
    if isinstance(exc, BudgetError):
        return "budget_failure"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, KeyboardInterrupt):
        return "cancelled"
    return "sandbox_or_executor_failure"


def _validate_sandbox_attestation(value: Any) -> dict[str, Any]:
    required = {
        *EXPECTED_RUNTIME,
        "cadclaw_pin_basis",
        "build_provenance_sha256",
        "run_limiter_sha256",
        "uid",
        "gid",
        "capabilities_zero",
        "no_new_privileges",
        "seccomp_filtered",
        "root_read_only",
        "network_interfaces",
        "docker_socket_absent",
        "environment_keys",
        "cwd",
        "kit_read_only",
        "staged_inputs_read_only",
        "staged_input_root",
        "image",
        "docker_executable",
        "docker_executable_sha256",
        "container_config_readback",
        "cleanup_verified",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("sandbox attestation malformed")
    expected = {
        **EXPECTED_RUNTIME,
        "cadclaw_pin_basis": CADCLAW_PIN_BASIS,
        "uid": 65532,
        "gid": 65532,
        "capabilities_zero": True,
        "no_new_privileges": True,
        "seccomp_filtered": True,
        "root_read_only": True,
        "network_interfaces": ["lo"],
        "docker_socket_absent": True,
        "environment_keys": EXPECTED_CONTAINER_ENV_KEYS,
        "cwd": "/workspace",
        "kit_read_only": True,
        "staged_inputs_read_only": True,
        "staged_input_root": "/marb-input",
        "docker_executable": value.get("docker_executable"),
        "docker_executable_sha256": value.get("docker_executable_sha256"),
        "container_config_readback": True,
        "cleanup_verified": True,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RuntimeError("sandbox attestation does not satisfy the isolation contract")
    image = value.get("image")
    if not isinstance(image, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._/-]*(?::[a-z0-9][a-z0-9._-]*)?@sha256:[0-9a-f]{64}",
        image,
    ):
        raise RuntimeError("sandbox attestation image identity is malformed")
    docker_executable = value.get("docker_executable")
    docker_digest = value.get("docker_executable_sha256")
    limiter_digest = value.get("run_limiter_sha256")
    build_provenance_digest = value.get("build_provenance_sha256")
    if (
        not isinstance(docker_executable, str)
        or not WINDOWS_DOCKER_EXECUTABLE.fullmatch(docker_executable)
        or not isinstance(docker_digest, str)
        or not HEX64.fullmatch(docker_digest)
        or not isinstance(limiter_digest, str)
        or not HEX64.fullmatch(limiter_digest)
        or not isinstance(build_provenance_digest, str)
        or not HEX64.fullmatch(build_provenance_digest)
    ):
        raise RuntimeError("sandbox attestation Docker executable identity is malformed")
    return value


def _default_provider_factory(
    provider: dict[str, Any], credential: str | None
) -> ProviderSession:
    return OpenAICompatibleSession(provider["endpoint"].rstrip("/"), provider["model_id"], credential)


def _default_sandbox_factory(
    workspace: Path,
    input_root: Path,
    container: dict[str, Any],
    attempt_id: uuid.UUID,
) -> Sandbox:
    try:
        from .isolated_container import IsolatedDockerPython
    except ImportError:  # pragma: no cover - exercised by CLI smoke tests
        from isolated_container import IsolatedDockerPython  # type: ignore[no-redef]

    required_probe_identities = (
        "4b12f84d010651166dd4067ede60689215c174d1d2926d4b1d07befff05232f1",
        "0ad2f18d336e070c5cbaab7204e3cc76f1ec112e9d8fbd6d69a42902b27fa1e1",
        "f067b00c69c5c341d5dcd98a0d941cdf8c1bf0dbec4edc7aa1ceeb23df319179",
    )
    if not all(item in POSITIVE_PROVENANCE_AND_IMPORT_SOURCE for item in required_probe_identities):
        raise RuntimeError("shared runtime preflight provenance identities are incomplete")

    class DockerSandboxAdapter:
        _PREFLIGHT = POSITIVE_PROVENANCE_AND_IMPORT_SOURCE

        def __init__(self) -> None:
            self._docker_identity = _verify_host_docker_executable(container)
            self._engine = IsolatedDockerPython(
                container["image"],
                input_root=input_root,
                docker_executable=container["docker_executable"],
            )
            self._workspace = workspace
            self._attempt_id = attempt_id
            self._closed = False

        def start(self, timeout_seconds: float) -> dict[str, Any]:
            if self._closed:
                raise RuntimeError("isolated sandbox is closed")
            probe = self._workspace / "marb_runtime_preflight.py"
            with probe.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(self._PREFLIGHT)
            try:
                result = self._engine.execute(
                    self._workspace,
                    probe.name,
                    execution_timeout=float(min(60, timeout_seconds)),
                )
            finally:
                try:
                    probe.unlink()
                except FileNotFoundError:
                    pass
            if result.returncode != 0:
                raise RuntimeError("isolated container preflight failed")
            try:
                attestation = json.loads(result.stdout.strip())
            except (json.JSONDecodeError, AttributeError):
                raise RuntimeError("isolated container preflight returned malformed output") from None
            required = {
                *EXPECTED_RUNTIME,
                "cadclaw_pin_basis",
                "build_provenance_sha256",
                "run_limiter_sha256",
                "uid",
                "gid",
                "capabilities_zero",
                "no_new_privileges",
                "seccomp_filtered",
                "root_read_only",
                "network_interfaces",
                "docker_socket_absent",
                "environment_keys",
                "cwd",
                "kit_read_only",
                "staged_inputs_read_only",
                "staged_input_root",
            }
            if not isinstance(attestation, dict) or set(attestation) != required:
                raise RuntimeError("isolated container preflight attestation is incomplete")
            if (
                result.cleanup_verified is not True
                or result.container_absence_verified is not True
                or result.export_staging_removed is not True
            ):
                raise RuntimeError("isolated container preflight cleanup was not verified")
            attestation.update(
                {
                    "image": result.image,
                    "docker_executable": result.docker_executable,
                    "docker_executable_sha256": self._docker_identity["sha256"],
                    "container_config_readback": True,
                    "cleanup_verified": result.cleanup_verified,
                }
            )
            return attestation

        def run_python(self, relative_path: str, timeout_seconds: float) -> dict[str, Any]:
            if self._closed:
                raise RuntimeError("isolated sandbox is closed")
            result = self._engine.execute(
                self._workspace,
                relative_path,
                execution_timeout=float(timeout_seconds),
            )
            status = "ok" if result.returncode == 0 else "error"
            if (
                result.image != container["image"]
                or result.docker_executable != self._docker_identity["path"]
                or result.cleanup_verified is not True
                or result.container_absence_verified is not True
                or result.export_staging_removed is not True
            ):
                raise RuntimeError("isolated container execution cleanup was not verified")
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            return {"status": status, "output": output}

        def close(self) -> None:
            self._closed = True

    return DockerSandboxAdapter()


def execute_plan(
    repo_root: Path,
    *,
    plan_raw: bytes,
    expected_plan_sha256: str,
    authorization_raw: bytes,
    expected_authorization_sha256: str,
    authorization_literal: str,
    planned_run_id: str,
    runs_root: Path | None = None,
    provider_factory: Callable[[dict[str, Any], str | None], ProviderSession] | None = None,
    sandbox_factory: Callable[[Path, Path, dict[str, Any], uuid.UUID], Sandbox] | None = None,
    now: Callable[[], dt.datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one authorized planned slot and retain its complete or partial record."""
    try:
        repo_root = repo_root.resolve(strict=True)
    except OSError:
        _fail("repository root is unavailable")
    if not repo_root.is_dir():
        _fail("repository root is not a directory")
    now = now or (lambda: dt.datetime.now(dt.timezone.utc))
    monotonic = monotonic or time.monotonic
    provider_factory = provider_factory or _default_provider_factory
    using_default_sandbox = sandbox_factory is None
    sandbox_factory = sandbox_factory or _default_sandbox_factory
    environ = os.environ if environ is None else environ
    if using_default_sandbox and os.name != "nt":
        _fail(
            "the current H2b Docker bind contract is supported only on Windows Docker Desktop"
        )

    try:
        envelope = cohort_runner.verify_plan_envelope(
            plan_raw, expected_sha256=expected_plan_sha256
        )
    except cohort_runner.PlanError as exc:
        _fail(str(exc))
    slot = _select_run(envelope, planned_run_id)
    literal = f"{EXECUTE_LITERAL_PREFIX}:{expected_plan_sha256}:{planned_run_id}"
    if authorization_literal != literal:
        _fail("exact execution confirmation literal is missing or mismatched")
    auth = verify_authorization(
        authorization_raw,
        expected_sha256=expected_authorization_sha256,
        expected_plan_sha256=expected_plan_sha256,
        expected_run_id=planned_run_id,
        expected_model_id=envelope["plan"]["cohort"]["model"]["id"],
        now=now(),
    )
    if using_default_sandbox:
        _verify_host_docker_executable(auth["container"])
    authorized_git_reader = _AuthorizedGitBlobReader(auth["implementation"])
    git_identity = authorized_git_reader.preflight()
    commit_blob_reader: Callable[[Path, str, str], bytes] = authorized_git_reader
    plan = envelope["plan"]
    implementation_identities = _verify_committed_implementation(
        repo_root, plan, auth, commit_blob_reader
    )
    committed_input_identities = _verify_committed_plan_inputs(
        repo_root, plan, commit_blob_reader
    )
    if (
        plan["task"]["id"] in {"L2-RESOLVE", "L4-ECO"}
        and auth["provider"]["settings"]["max_total_turns"] < 4
    ):
        _fail("change-loop execution requires at least four total provider turns")
    seed_basis = plan["cohort"]["seed_basis"]
    authorized_seed = auth["provider"]["settings"]["seed"]
    if seed_basis == "provider-seed":
        if not str(slot["seed"]).isdecimal() or authorized_seed != int(slot["seed"]):
            _fail("authorized provider seed does not match the planned provider seed")
    elif authorized_seed is not None:
        _fail("ordinal seed plans require a null provider seed")
    _rebuild_plan(repo_root, envelope)
    _assert_execution_ready(envelope)
    root = _assert_runs_root(repo_root, runs_root or (repo_root / "runs"))
    _assert_no_local_slot_collision(root, planned_run_id)

    credential_name = auth["provider"]["credential_env"]
    credential = environ.get(credential_name) if credential_name is not None else None
    if credential_name is not None and (
        not isinstance(credential, str)
        or not 16 <= len(credential) <= 8_192
        or CONTROL.search(credential)
        or any(character.isspace() for character in credential)
    ):
        _fail("authorized provider credential is missing or malformed")
    forbidden_secrets = (credential,) if isinstance(credential, str) else ()
    credential_status = "not_required" if credential_name is None else "present"

    attempt_candidate = uuid_factory()
    continuity_candidate = uuid_factory()
    if (
        not isinstance(attempt_candidate, uuid.UUID)
        or attempt_candidate.version != 4
        or not isinstance(continuity_candidate, uuid.UUID)
        or continuity_candidate.version != 4
        or attempt_candidate == continuity_candidate
    ):
        _fail("attempt and continuity ID generators must return distinct UUIDv4 values")
    attempt_id, run_dir, slot_claim = _allocate_run_dir(
        root, planned_run_id, attempt_candidate
    )
    started_dt = now()
    started_monotonic = monotonic()
    phase = "preparing"
    sequence = 1
    events_path = run_dir / "events.jsonl"
    run_log_path = run_dir / "run_log.json"
    minimal_log: dict[str, Any] = {
        "schema": RUN_LOG_SCHEMA,
        "status": "running",
        "phase": phase,
        "attempt_id": str(attempt_id),
        "logical_run_id": planned_run_id,
        "logical_slot_claim": slot_claim,
        "source": {
            "marb_revision": plan["source"]["revision"],
            "plan_sha256": expected_plan_sha256,
        },
        "authorization": {
            "authorization_id": auth["authorization_id"],
            "authorization_sha256": expected_authorization_sha256,
        },
        "timing": {
            "started_utc": _utc_text(started_dt),
            "ended_utc": None,
            "elapsed_seconds": None,
        },
        "artifacts": {},
        "failure": None,
        "retention_guard": {"status": "pending"},
        "publication": {
            "graded": False,
            "registry_mutated": False,
            "board_mutated": False,
            "site_rebuilt": False,
            "deployed": False,
        },
    }

    def fail_setup(exc: BaseException) -> NoReturn:
        category = _safe_failure_category(exc)
        try:
            ended = now()
        except BaseException:
            ended = started_dt
        try:
            elapsed = round(max(0.0, monotonic() - started_monotonic), 6)
        except BaseException:
            elapsed = None
        minimal_log["status"] = "failed"
        minimal_log["phase"] = phase
        minimal_log["failure"] = {"category": category, "phase": phase}
        minimal_log["retention_guard"] = {"status": "not_started"}
        minimal_log["timing"]["ended_utc"] = _utc_text(ended)
        minimal_log["timing"]["elapsed_seconds"] = elapsed
        _seal_run_journal(
            run_dir,
            minimal_log,
            events_path,
            sequence,
            phase=phase,
            ended_utc=minimal_log["timing"]["ended_utc"],
            forbidden_secrets=forbidden_secrets,
        )
        raise RunExecutionError(category, run_dir) from None

    try:
        _write_canonical_exclusive(run_log_path, minimal_log)
        _append_event(
            events_path,
            sequence,
            "run_directory_created",
            phase,
            _utc_text(started_dt),
        )
    except BaseException as exc:
        fail_setup(exc)

    def guarded_setup(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except BaseException as exc:
            fail_setup(exc)

    bound_outputs, output_paths = guarded_setup(
        lambda: _bind_planned_outputs(repo_root, run_dir, slot, attempt_id)
    )
    actual_outputs = bound_outputs["executor"]
    downstream_outputs = bound_outputs["downstream"]
    workspace = run_dir / "workspace"
    input_root = run_dir / "inputs"
    evidence = run_dir / "evidence"
    guarded_setup(lambda: workspace.mkdir(mode=0o700, exist_ok=False))
    guarded_setup(lambda: input_root.mkdir(mode=0o700, exist_ok=False))
    guarded_setup(lambda: evidence.mkdir(mode=0o700, exist_ok=False))
    if output_paths["run_log"] != run_log_path:
        fail_setup(ArtifactError("bound run log path changed after allocation"))

    executor_identity = guarded_setup(_executor_source_identity)
    run_log: dict[str, Any] = {
        "schema": RUN_LOG_SCHEMA,
        "status": "running",
        "phase": phase,
        "attempt_id": str(attempt_id),
        "logical_run_id": planned_run_id,
        "seed": slot["seed"],
        "driver_continuity_id": str(continuity_candidate),
        "logical_slot_claim": slot_claim,
        "authorization": {
            "authorization_id": auth["authorization_id"],
            "authorization_sha256": expected_authorization_sha256,
            "approved_by": auth["approved_by"],
            "issued_utc": auth["issued_utc"],
            "expires_utc": auth["expires_utc"],
            "grants": auth["execution"],
            "spend": auth["spend"],
        },
        "source": {
            "marb_revision": plan["source"]["revision"],
            "plan_sha256": expected_plan_sha256,
            "registry": plan["source"]["registry"],
            "planner": plan["source"]["planner"],
            "executor": executor_identity,
            "implementation": implementation_identities,
            "committed_frozen_inputs": committed_input_identities,
            "git_executable": {
                "label": PureWindowsPath(git_identity["path"]).name,
                "sha256": git_identity["sha256"],
                "reader": "authorized_git",
            },
        },
        "task": {
            **plan["task"],
            "frozen_public_inputs": plan["frozen_public_inputs"],
        },
        "cohort": plan["cohort"],
        "identity_semantics": {
            "model_id": "provider_response_bound_authorized_identity",
            "model_name": "operator_supplied_display_label",
            "cell_label": "operator_supplied_display_label",
            "model_alias_policy": None,
        },
        "execution_modality": {
            "provider_input": "text",
            "native_image_view_tool_available": False,
            "staged_file_bytes_available_to_python": True,
            "staged_inputs_may_include_images": True,
            "vision_attested": False,
        },
        "output_contract": {
            "schema": "marb_plan_output_binding.v2",
            "attempt_uuid_token": cohort_runner.ATTEMPT_UUID_TOKEN,
            "planned": slot["planned_outputs"],
            "executor_owned_outputs": actual_outputs,
            "downstream_grader_owned": downstream_outputs,
            "downstream_status": "deferred_ungraded",
        },
        "provider": {
            "protocol": auth["provider"]["protocol"],
            "model_id": auth["provider"]["model_id"],
            "endpoint": auth["provider"]["sanitized_endpoint"],
            "credential": {"env_name": credential_name, "status": credential_status},
            "billing_mode": auth["provider"]["billing_mode"],
            "settings": auth["provider"]["settings"],
            "calls": [],
        },
        "conversation": {
            "provider_session_count": 0,
            "system_message_sha256": None,
            "initial_user_message_sha256": None,
            "change_message_sha256": None,
            "transcript_sha256": None,
        },
        "container": {
            "image": auth["container"]["image"],
            **{key: auth["container"][key] for key in EXPECTED_RUNTIME},
            "cadclaw_pin_basis": auth["container"]["cadclaw_pin_basis"],
            "run_limiter_sha256": auth["container"]["run_limiter_sha256"],
            "docker_executable": {
                "label": PureWindowsPath(auth["container"]["docker_executable"]).name,
                "sha256": auth["container"]["docker_executable_sha256"],
            },
            "isolation_policy": "marb_digest_pinned_networkless_container.v1",
            "attestation": None,
        },
        "timing": {
            "started_utc": _utc_text(started_dt),
            "ended_utc": None,
            "elapsed_seconds": None,
        },
        "usage": _usage_summary([], auth["provider"]["billing_mode"], auth["spend"]["zero_cost_attested"]),
        "artifacts": {},
        "phase_history": [],
        "failure": None,
        "retention_guard": {"status": "pending"},
        "publication": {
            "graded": False,
            "registry_mutated": False,
            "board_mutated": False,
            "site_rebuilt": False,
            "deployed": False,
        },
    }
    guarded_setup(lambda: _write_canonical(run_log_path, run_log))

    def checkpoint(event: str, event_phase: str) -> None:
        nonlocal sequence
        _write_canonical(run_log_path, run_log)
        sequence += 1
        _append_event(events_path, sequence, event, event_phase, _utc_text(now()))

    sandbox: Sandbox | None = None
    written: set[str] = set()
    staged_paths: set[str] = set()
    messages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = run_log["provider"]["calls"]
    maximum_cost = (
        decimal.Decimal(auth["spend"]["max_cost_usd"])
        if auth["spend"]["max_cost_usd"] is not None
        else None
    )
    budget = ExecutionBudget(
        max_total_turns=auth["provider"]["settings"]["max_total_turns"],
        max_total_tool_calls=auth["provider"]["settings"]["max_total_tool_calls"],
        max_run_python_calls=auth["provider"]["settings"]["max_run_python_calls"],
        max_request_bytes=auth["provider"]["settings"]["max_request_bytes"],
        max_transcript_bytes=auth["provider"]["settings"]["max_transcript_bytes"],
        deadline_monotonic=(
            started_monotonic
            + auth["provider"]["settings"]["max_wall_clock_seconds"]
        ),
        billing_mode=auth["provider"]["billing_mode"],
        max_cost_usd=maximum_cost,
        zero_cost_attested=auth["spend"]["zero_cost_attested"],
    )

    def enforce_deadline() -> None:
        _remaining_wall_seconds(budget, monotonic)

    try:
        enforce_deadline()
        phase = "staging_public_inputs"
        sequence += 1
        _append_event(events_path, sequence, "phase_started", phase, _utc_text(now()))
        staged_manifest = _stage_kit(
            repo_root,
            plan,
            input_root,
            deadline_check=enforce_deadline,
        )
        staged_paths = {item["path"] for item in staged_manifest["files"]}
        staged_files = sorted(staged_paths, key=lambda item: item.encode("utf-8"))
        run_log["workspace_inputs"] = staged_manifest
        prompt = _public_text(
            repo_root,
            plan["frozen_public_inputs"]["prompt"],
            "frozen prompt",
            deadline_check=enforce_deadline,
        )
        brief_source = _public_text(
            repo_root,
            plan["frozen_public_inputs"]["driver_brief"],
            "frozen driver brief",
            deadline_check=enforce_deadline,
        )
        brief = _extract_driver_brief_payload(brief_source)
        system_message = (
            "You are the isolated CadQuery driver for a blind MARB run. "
            "Use only the supplied frozen generated driver brief and staged inputs. The full "
            "read-only staged tree is at /marb-input; geometry is also available through the "
            "brief-compatible /workspace/kit paths. Write editable "
            "Python with write_file, execute it only with run_python, and export exactly one STEP "
            "as export.step or cadquery_native_export.step. Do not grade the result."
        )
        messages.extend(
            [
                {"role": "system", "content": system_message},
                {"role": "user", "content": brief},
            ]
        )
        run_log["conversation"]["system_message_sha256"] = hashlib.sha256(
            system_message.encode("utf-8")
        ).hexdigest()
        run_log["conversation"]["initial_user_message_sha256"] = hashlib.sha256(
            brief.encode("utf-8")
        ).hexdigest()
        run_log["conversation"]["delivered_driver_payload_sha256"] = hashlib.sha256(
            brief.encode("utf-8")
        ).hexdigest()
        run_log["conversation"]["delivered_driver_payload_bytes"] = len(
            brief.encode("utf-8")
        )
        run_log["conversation"]["frozen_shared_prompt_sha256"] = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        run_log["conversation"]["staged_file_list_sha256"] = hashlib.sha256(
            _canonical_json(staged_files)
        ).hexdigest()
        sandbox = sandbox_factory(workspace, input_root, auth["container"], attempt_id)
        sandbox_timeout = min(
            float(auth["provider"]["settings"]["timeout_seconds"]),
            _remaining_wall_seconds(budget, monotonic),
        )
        attestation = _validate_sandbox_attestation(sandbox.start(sandbox_timeout))
        _verify_staged_kit(
            input_root, staged_manifest, deadline_check=enforce_deadline
        )
        for key in (
            "image",
            "docker_executable",
            "docker_executable_sha256",
            "run_limiter_sha256",
        ):
            if attestation[key] != auth["container"][key]:
                raise RuntimeError(
                    "sandbox attestation identity does not match authorization"
                )
        safe_attestation = dict(attestation)
        safe_attestation.pop("docker_executable_sha256")
        safe_attestation["docker_executable"] = {
            "label": PureWindowsPath(attestation["docker_executable"]).name,
            "sha256": auth["container"]["docker_executable_sha256"],
        }
        run_log["container"]["attestation"] = safe_attestation

        session = provider_factory(auth["provider"], credential)
        run_log["conversation"]["provider_session_count"] = 1
        del credential
        phase = "baseline"
        sequence += 1
        _append_event(events_path, sequence, "phase_started", phase, _utc_text(now()))
        _phase_loop(
            session,
            sandbox,
            workspace,
            messages,
            auth["provider"]["settings"],
            written,
            calls,
            phase,
            budget,
            auth["provider"]["model_id"],
            now,
            monotonic,
            staged_manifest,
            input_root,
            forbidden_secrets,
            checkpoint,
            2 if plan["task"]["id"] in {"L2-RESOLVE", "L4-ECO"} else 0,
        )
        _verify_staged_kit(
            input_root, staged_manifest, deadline_check=enforce_deadline
        )
        _scrub_workspace_secrets(
            workspace,
            staged_paths,
            forbidden_secrets,
            deadline_check=enforce_deadline,
        )
        baseline_step = _find_step_output(
            workspace, deadline_check=enforce_deadline
        )
        baseline_step_record = _copy_discovered_step(
            baseline_step,
            output_paths["baseline_step"],
            run_dir,
            deadline_check=enforce_deadline,
        )
        _record_planned_artifact(
            run_log["artifacts"], "baseline_step", baseline_step_record, actual_outputs
        )
        checkpoint("artifact_captured", "baseline")
        baseline_source_record = _deterministic_source_zip(
            workspace,
            staged_paths,
            output_paths[
                "baseline_editable_source_zip"
                if plan["task"]["id"] == "L1-ASSEMBLE"
                else "baseline_editable_source"
            ],
            run_dir,
            forbidden_secrets,
            deadline_check=enforce_deadline,
        )
        baseline_source_key = (
            "baseline_editable_source_zip"
            if plan["task"]["id"] == "L1-ASSEMBLE"
            else "baseline_editable_source"
        )
        _record_planned_artifact(
            run_log["artifacts"],
            baseline_source_key,
            baseline_source_record,
            actual_outputs,
        )
        checkpoint("artifact_captured", "baseline")
        run_log["phase_history"].append(
            {"phase": "baseline", "status": "captured", "provider_session_ordinal": 1}
        )
        run_log["phase"] = "baseline_frozen"
        run_log["usage"] = _usage_summary(
            calls, auth["provider"]["billing_mode"], auth["spend"]["zero_cost_attested"]
        )
        _write_canonical(run_log_path, run_log)
        sequence += 1
        _append_event(events_path, sequence, "baseline_frozen", "baseline", _utc_text(now()))

        task_id = plan["task"]["id"]
        if task_id in {"L2-RESOLVE", "L4-ECO"}:
            baseline_step.path.unlink()
            phase = "change"
            request = _public_text(
                repo_root,
                plan["frozen_public_inputs"]["change_request"],
                "frozen change request",
                deadline_check=enforce_deadline,
            )
            change_message = (
                "Continue in this same session from your captured editable baseline. "
                "Apply this frozen change request, then replace the canonical STEP output.\n\n"
                + request
            )
            messages.append({"role": "user", "content": change_message})
            run_log["conversation"]["change_message_sha256"] = hashlib.sha256(
                change_message.encode("utf-8")
            ).hexdigest()
            sequence += 1
            _append_event(events_path, sequence, "change_request_revealed", phase, _utc_text(now()))
            _phase_loop(
                session,
                sandbox,
                workspace,
                messages,
                auth["provider"]["settings"],
                written,
                calls,
                phase,
                budget,
                auth["provider"]["model_id"],
                now,
                monotonic,
                staged_manifest,
                input_root,
                forbidden_secrets,
                checkpoint,
            )
            _verify_staged_kit(
                input_root, staged_manifest, deadline_check=enforce_deadline
            )
            _scrub_workspace_secrets(
                workspace,
                staged_paths,
                forbidden_secrets,
                deadline_check=enforce_deadline,
            )
            changed_step = _find_step_output(
                workspace, deadline_check=enforce_deadline
            )
            changed_step_record = _copy_discovered_step(
                changed_step,
                output_paths["changed_step"],
                run_dir,
                deadline_check=enforce_deadline,
            )
            _record_planned_artifact(
                run_log["artifacts"], "changed_step", changed_step_record, actual_outputs
            )
            checkpoint("artifact_captured", "change")
            changed_source_record = _deterministic_source_zip(
                workspace,
                staged_paths,
                output_paths["changed_editable_source"],
                run_dir,
                forbidden_secrets,
                deadline_check=enforce_deadline,
            )
            _record_planned_artifact(
                run_log["artifacts"],
                "changed_editable_source",
                changed_source_record,
                actual_outputs,
            )
            checkpoint("artifact_captured", "change")
            run_log["phase_history"].append(
                {"phase": "change", "status": "captured", "provider_session_ordinal": 1}
            )
        else:
            final_step_record = _copy_evidence(
                output_paths["baseline_step"],
                output_paths["final_step"],
                run_dir,
                deadline_check=enforce_deadline,
            )
            _record_planned_artifact(
                run_log["artifacts"], "final_step", final_step_record, actual_outputs
            )
            checkpoint("artifact_captured", "baseline")
            final_source_record = _deterministic_source_zip(
                workspace,
                staged_paths,
                output_paths["final_editable_source_zip"],
                run_dir,
                forbidden_secrets,
                deadline_check=enforce_deadline,
            )
            _record_planned_artifact(
                run_log["artifacts"],
                "final_editable_source_zip",
                final_source_record,
                actual_outputs,
            )
            checkpoint("artifact_captured", "baseline")

        _assert_executor_outputs_complete(
            task_id, run_log["artifacts"], actual_outputs
        )
        _remaining_wall_seconds(budget, monotonic)
        run_log["status"] = "completed_ungraded"
        run_log["phase"] = "complete"
    except BaseException as exc:
        category = _safe_failure_category(exc)
        run_log["status"] = "partial" if run_log["artifacts"] else "failed"
        run_log["phase"] = phase
        run_log["failure"] = {"category": category, "phase": phase}
    finally:
        def mark_finalization_failure(category: str) -> None:
            if run_log["failure"] is None:
                run_log["status"] = "partial" if run_log["artifacts"] else "failed"
                run_log["failure"] = {"category": category, "phase": phase}

        try:
            if workspace.exists():
                _scrub_workspace_secrets(
                    workspace, staged_paths, forbidden_secrets
                )
            run_log["retention_guard"] = {"status": "passed"}
        except BaseException:
            run_log["retention_guard"] = {
                "status": "prohibited_material_removed_or_cleanup_failed"
            }
            mark_finalization_failure("artifact_failure")
        if sandbox is not None:
            try:
                sandbox.close()
            except BaseException:
                mark_finalization_failure("sandbox_close_failure")
        try:
            ended = now()
        except BaseException:
            ended = started_dt
            mark_finalization_failure("journal_integrity_failure")
        try:
            run_log["provider"]["calls"] = calls
            run_log["conversation"]["transcript_sha256"] = hashlib.sha256(
                _canonical_json(messages)
            ).hexdigest()
            run_log["usage"] = _usage_summary(
                calls,
                auth["provider"]["billing_mode"],
                auth["spend"]["zero_cost_attested"],
            )
        except BaseException:
            run_log["conversation"]["transcript_sha256"] = None
            run_log["usage"] = None
            mark_finalization_failure("journal_integrity_failure")
        run_log["timing"]["ended_utc"] = _utc_text(ended)
        try:
            run_log["timing"]["elapsed_seconds"] = round(
                max(0.0, monotonic() - started_monotonic), 6
            )
        except BaseException:
            run_log["timing"]["elapsed_seconds"] = None
            mark_finalization_failure("journal_integrity_failure")
        log_digest, sequence = _seal_run_journal(
            run_dir,
            run_log,
            events_path,
            sequence,
            phase=run_log["phase"],
            ended_utc=run_log["timing"]["ended_utc"],
            forbidden_secrets=forbidden_secrets,
            deadline_check=enforce_deadline,
        )
    result = {
        "run_dir": run_dir,
        "run_log": run_log,
        "run_log_sha256": log_digest,
    }
    if run_log["status"] != "completed_ungraded":
        raise RunExecutionError(run_log["failure"]["category"], run_dir)
    if log_digest is None:
        raise RunExecutionError("journal_integrity_failure", run_dir)
    return result


def _decode_canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail(f"{label} is not canonical JSON")
    if not isinstance(value, dict) or raw != _canonical_json(value, newline=True):
        _fail(f"{label} is not a canonical JSON object")
    return value


def _relative_run_dir(repo_root: Path, run_dir: Path) -> str:
    try:
        repo = repo_root.resolve(strict=True)
        relative = run_dir.resolve(strict=True).relative_to(repo).as_posix()
    except (OSError, ValueError):
        _fail("retained run path cannot be reported safely")
    if not relative.startswith("runs/") or relative.count("/") != 1:
        _fail("retained run path is outside the dedicated runs directory")
    _safe_relative(relative, "retained run path")
    return relative


def _authorization_template_payload(args: argparse.Namespace) -> dict[str, Any]:
    try:
        repo_root = args.repo_root.resolve(strict=True)
    except OSError:
        _fail("repository root is unavailable")
    executing_root = Path(__file__).resolve(strict=True).parents[1]
    if os.path.normcase(str(repo_root)) != os.path.normcase(str(executing_root)):
        _fail("repository root does not contain the executing H2b implementation")
    plan_raw = _stable_read(args.plan, "cohort plan")
    try:
        envelope = cohort_runner.verify_plan_envelope(
            plan_raw, expected_sha256=args.expected_plan_sha256
        )
    except cohort_runner.PlanError as exc:
        _fail(str(exc))
    slot = _select_run(envelope, args.slot)
    plan = envelope["plan"]
    issued = _parse_utc(args.issued_utc, "issued_utc")
    _parse_utc(args.expires_utc, "expires_utc")
    source_root = Path(__file__).resolve().parent
    limiter_identity = _text_source_identity(
        source_root / "container" / "run_limited.py",
        "harness/container/run_limited.py",
        "run limiter source",
    )
    seed = int(slot["seed"]) if plan["cohort"]["seed_basis"] == "provider-seed" else None
    payload = {
        "authorization_id": str(uuid.uuid4()),
        "approved_by": args.approved_by,
        "issued_utc": args.issued_utc,
        "expires_utc": args.expires_utc,
        "plan_sha256": args.expected_plan_sha256,
        "planned_run_id": args.slot,
        "execution": {
            "execute": True,
            "model_calls": True,
            "provider_access": True,
            "spend_authorized": True,
        },
        "provider": {
            "protocol": PROVIDER_PROTOCOL,
            "model_id": plan["cohort"]["model"]["id"],
            "endpoint": args.provider_endpoint,
            "credential_env": args.credential_env,
            "billing_mode": "local-no-charge",
            "settings": {
                "temperature": 0,
                "max_output_tokens": args.max_output_tokens,
                "max_total_turns": args.max_total_turns,
                "max_total_tool_calls": args.max_total_tool_calls,
                "max_run_python_calls": args.max_run_python_calls,
                "max_request_bytes": args.max_request_bytes,
                "max_transcript_bytes": args.max_transcript_bytes,
                "max_wall_clock_seconds": args.max_wall_clock_seconds,
                "timeout_seconds": args.timeout_seconds,
                "seed": seed,
            },
        },
        "container": {
            "image": args.container_image,
            "docker_executable": args.docker_executable,
            "docker_executable_sha256": args.docker_executable_sha256,
            "cadclaw_pin_basis": CADCLAW_PIN_BASIS,
            "run_limiter_sha256": limiter_identity["sha256"],
            **EXPECTED_RUNTIME,
        },
        "spend": {
            "currency": "USD",
            "max_cost_usd": None,
            "zero_cost_attested": True,
        },
        "implementation": {
            "source_revision": plan["source"]["revision"],
            "planner_sha256": plan["source"]["planner"]["sha256"],
            "executor_sha256": _executor_source_identity()["sha256"],
            "isolated_container_sha256": _text_source_identity(
                source_root / "isolated_container.py",
                "harness/isolated_container.py",
                "isolated container source",
            )["sha256"],
            "provider_transport_sha256": _text_source_identity(
                source_root / "provider_transport.py",
                "harness/provider_transport.py",
                "provider transport source",
            )["sha256"],
            "run_limiter_sha256": limiter_identity["sha256"],
            "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
            "historical_runtime_contract_sha256": HISTORICAL_RUNTIME_CONTRACT_SHA256,
            "cadclaw_calibration_sha256": CADCLAW_CALIBRATION_SHA256,
            "git_executable": args.git_executable,
            "git_executable_sha256": args.git_executable_sha256,
        },
    }
    trial = make_authorization_envelope(payload)
    verify_authorization(
        _canonical_json(trial, newline=True),
        expected_sha256=trial["authorization_sha256"],
        expected_plan_sha256=args.expected_plan_sha256,
        expected_run_id=args.slot,
        expected_model_id=plan["cohort"]["model"]["id"],
        now=issued,
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute one explicitly authorized MARB plan slot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    execute = subparsers.add_parser("execute", help="execute one authorized plan slot")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--expected-plan-sha256", required=True)
    execute.add_argument("--authorization", type=Path, required=True)
    execute.add_argument("--expected-authorization-sha256", required=True)
    execute.add_argument("--authorize-execution", required=True)
    execute.add_argument("--slot", required=True)
    execute.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    template = subparsers.add_parser(
        "authorization-template",
        help="write one complete local-no-charge authorization payload for review",
    )
    template.add_argument("--plan", type=Path, required=True)
    template.add_argument("--expected-plan-sha256", required=True)
    template.add_argument("--slot", required=True)
    template.add_argument("--approved-by", required=True)
    template.add_argument("--issued-utc", required=True)
    template.add_argument("--expires-utc", required=True)
    template.add_argument("--provider-endpoint", required=True)
    template.add_argument("--credential-env")
    template.add_argument("--container-image", required=True)
    template.add_argument("--docker-executable", required=True)
    template.add_argument("--docker-executable-sha256", required=True)
    template.add_argument("--git-executable", required=True)
    template.add_argument("--git-executable-sha256", required=True)
    template.add_argument("--max-output-tokens", type=int, default=4096)
    template.add_argument("--max-total-turns", type=int, default=8)
    template.add_argument("--max-total-tool-calls", type=int, default=32)
    template.add_argument("--max-run-python-calls", type=int, default=8)
    template.add_argument("--max-request-bytes", type=int, default=1_000_000)
    template.add_argument("--max-transcript-bytes", type=int, default=4_000_000)
    template.add_argument("--max-wall-clock-seconds", type=int, default=300)
    template.add_argument("--timeout-seconds", type=int, default=30)
    template.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    template.add_argument("--output", type=Path, required=True)
    seal = subparsers.add_parser(
        "seal-authorization", help="digest-wrap a reviewed canonical authorization payload"
    )
    seal.add_argument("--payload", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser(
        "validate-authorization", help="validate a sealed authorization without execution"
    )
    validate.add_argument("--authorization", type=Path, required=True)
    validate.add_argument("--expected-authorization-sha256", required=True)
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--expected-plan-sha256", required=True)
    validate.add_argument("--slot", required=True)
    validate.add_argument("--at-utc", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "authorization-template":
            payload = _authorization_template_payload(args)
            _write_canonical_exclusive(args.output, payload)
            output = {
                "status": "authorization_template_written",
                "output": args.output.name,
                "model_calls": False,
            }
        elif args.command == "seal-authorization":
            payload = _decode_canonical_object(
                _stable_read(args.payload, "authorization payload"),
                "authorization payload",
            )
            envelope = make_authorization_envelope(payload)
            _write_canonical_exclusive(args.output, envelope)
            output = {
                "status": "authorization_sealed",
                "output": args.output.name,
                "authorization_sha256": envelope["authorization_sha256"],
                "model_calls": False,
            }
        elif args.command == "validate-authorization":
            plan_raw = _stable_read(args.plan, "cohort plan")
            try:
                plan_envelope = cohort_runner.verify_plan_envelope(
                    plan_raw, expected_sha256=args.expected_plan_sha256
                )
            except cohort_runner.PlanError as exc:
                _fail(str(exc))
            auth = verify_authorization(
                _stable_read(args.authorization, "execution authorization"),
                expected_sha256=args.expected_authorization_sha256,
                expected_plan_sha256=args.expected_plan_sha256,
                expected_run_id=args.slot,
                expected_model_id=plan_envelope["plan"]["cohort"]["model"]["id"],
                now=_parse_utc(args.at_utc, "at_utc"),
            )
            output = {
                "status": "authorization_valid",
                "authorization_id": auth["authorization_id"],
                "planned_run_id": auth["planned_run_id"],
                "model_calls": False,
            }
        else:
            result = execute_plan(
                args.repo_root,
                plan_raw=_stable_read(args.plan, "cohort plan"),
                expected_plan_sha256=args.expected_plan_sha256,
                authorization_raw=_stable_read(
                    args.authorization, "execution authorization"
                ),
                expected_authorization_sha256=args.expected_authorization_sha256,
                authorization_literal=args.authorize_execution,
                planned_run_id=args.slot,
            )
            output = {
                "status": result["run_log"]["status"],
                "run_dir": _relative_run_dir(args.repo_root, result["run_dir"]),
                "run_log_sha256": result["run_log_sha256"],
            }
    except RunExecutionError as exc:
        try:
            retained = _relative_run_dir(args.repo_root, exc.run_dir)
        except ExecutorError:
            retained = None
        print(
            json.dumps(
                {
                    "status": "retained_failure",
                    "category": exc.category,
                    "run_dir": retained,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (ExecutorError, ArtifactError):
        print(
            json.dumps(
                {
                    "status": "error",
                    "category": "invalid_input_or_filesystem",
                    "message": "input or policy validation failed",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except OSError:
        print(
            json.dumps(
                {
                    "status": "error",
                    "category": "filesystem_error",
                    "message": "filesystem input or output was unavailable",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "error",
                    "category": "executor_failure",
                    "message": "executor failed without exposing local paths or credentials",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
