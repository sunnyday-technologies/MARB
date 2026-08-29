#!/usr/bin/env python3
"""Calibrate one exact CADCLAW source revision for the MARB H2b contract.

This is a local software-compatibility calibration.  It is not a benchmark,
grade, model-selection run, H2b attempt, container qualification, or release
claim.  It reads only an allowlisted set of tracked Git objects, never reads an
``.env`` file, and does not intentionally invoke Docker, a network client, or a
provider.  Helper processes fail closed on common Python socket egress APIs;
categorical network isolation still requires an externally disabled network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "marb_h2b_cadclaw_calibration.v1"
CALIBRATION_ID = "cadclaw-fad0dd55-vs-60fc271f-20260829"
FROZEN_COMMIT = "60fc271f68c8a794a4741f856b2dd4c9878416a6"
CANDIDATE_COMMIT = "fad0dd552a49a0b32336f1845c2b82873ad6360a"
FROZEN_TREE = "6b1cdeade8e6cfe46527d56c0011d05b360daec7"
CANDIDATE_TREE = "97698a9ac17ab5423326c639e492888f219cafba"

SOURCE_TO_WHEEL_INPUT_SCOPES = (
    "cadclaw",
    "cadclaw_cli",
    "cadclaw_mcp",
    "cadharness",
    "pyproject.toml",
    "README.md",
    "LICENSE",
)
CORE_SOURCE_SCOPES = (
    "cadclaw",
    "cadclaw_cli",
    "cadharness",
    "pyproject.toml",
)
EXPECTED_MANIFESTS = {
    "source_to_wheel_inputs": {
        "frozen": (51, "42170a1db8b7a11baeebf8c9a20a5bf4497019953e995895e15fc05ba070eff0"),
        "candidate": (53, "6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831"),
    },
    "requested_core_source": {
        "frozen": (45, "d6fbf9fd3bcd3b9a94eeee6e8e08f8d62d5439479e61c89aba70997bb7414e37"),
        "candidate": (47, "b9f01d60aece254108ac20b63aecb05a3194f244c1db1939c2554b4df38599e7"),
    },
}

TRACKED_FIXTURES = (
    "examples/m3_crete/generated/ZPMM_6p1_motor_mount_spacer_6mm_holes.step",
    "examples/relative_placement/parts/plate.step",
    "examples/relative_placement/parts/rail_x.step",
    "examples/relative_placement/parts/rail_y.step",
)
NIST_FIXTURE = "tests/fixtures/pmi_semantic/nist_ftc_11_asme1_ap242-e2.stp"
NIST_SECONDARY_FIXTURE = "tests/fixtures/pmi_semantic/nist_stc_06_asme1_ap242-e3.stp"
NIST_TEST_FILE = "tests/test_roundtrip.py"
NIST_TEST_ID = (
    "test_roundtrip.TestNistRoundtripIntegration."
    "test_real_ap242_export_reimport_preserves_geometry_and_pmi"
)

HIGH_VALUE_FILES = (
    "pyproject.toml",
    "cadclaw/__init__.py",
    "cadclaw/roundtrip.py",
    "cadclaw/render.py",
    "cadclaw/pmi.py",
    "cadclaw/harness.py",
    "cadclaw/gate_registry.py",
    "cadclaw_cli/main.py",
    "cadharness/__init__.py",
    NIST_TEST_FILE,
    NIST_FIXTURE,
)

EXPECTED_DECLARED_DEPENDENCIES = (
    "cadquery>=2.7",
    "cadquery-ocp>=7.8",
    "Pillow>=10.0",
    "pyyaml>=6.0",
    "pydantic>=2.5",
    "vtk>=9.3",
)
EXPECTED_RUNTIME = {
    "python": "3.11.15",
    "cadclaw": "0.10.0",
    "cadquery": "2.7.0",
    "cadquery-ocp": "7.8.1.1.post1",
    "Pillow": "12.2.0",
    "PyYAML": "6.0.3",
    "pydantic": "2.13.4",
    "vtk": "9.3.1",
}
EXPECTED_PACKAGE_CONTRACT = {
    "name": "cadclaw",
    "version": "0.10.0",
    "requires_python": ">=3.10",
    "dependencies": list(EXPECTED_DECLARED_DEPENDENCIES),
    "scripts": {
        "cadclaw": "cadclaw_cli.main:main",
        "cadclaw-mcp": "cadclaw_mcp.server:main",
    },
    "package_include": [
        "cadclaw*",
        "cadclaw_mcp*",
        "cadclaw_cli*",
        "cadharness*",
    ],
}

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_GIT_COMMANDS = frozenset(
    {"archive", "cat-file", "ls-tree", "merge-base", "rev-parse"}
)


class CalibrationError(RuntimeError):
    """A fail-closed calibration error identified only by a safe reason code."""

    def __init__(self, reason_code: str):
        if not re.fullmatch(r"[a-z0-9_]{3,80}", reason_code):
            reason_code = "unclassified_calibration_error"
        self.reason_code = reason_code
        super().__init__(reason_code)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _safe_environment(temp_root: Path) -> dict[str, str]:
    """Return a minimal subprocess environment without credential variables."""
    environment: dict[str, str] = {}
    for name in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    temporary = str(temp_root)
    environment.update(
        {
            "APPDATA": temporary,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": temporary,
            "LOCALAPPDATA": temporary,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TEMP": temporary,
            "TMP": temporary,
            "USERPROFILE": temporary,
        }
    )
    return environment


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path | None = None,
    timeout: int = 300,
    reason_code: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CalibrationError(reason_code) from exc
    if completed.returncode != 0:
        raise CalibrationError(reason_code)
    return completed


class _LocalGit:
    """Small allowlisted Git reader.  It has no network-capable operation."""

    def __init__(self, executable: Path, repo: Path, environment: Mapping[str, str]):
        self.executable = executable
        self.repo = repo
        self.environment = environment

    def run(
        self,
        *arguments: str,
        reason_code: str,
        allow_nonzero: bool = False,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[bytes]:
        if not arguments or arguments[0] not in _ALLOWED_GIT_COMMANDS:
            raise CalibrationError("git_operation_not_allowlisted")
        command = [
            str(self.executable),
            "--no-replace-objects",
            "-c",
            f"safe.directory={self.repo.as_posix()}",
            "-C",
            str(self.repo),
            *arguments,
        ]
        try:
            completed = subprocess.run(
                command,
                env=dict(self.environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CalibrationError(reason_code) from exc
        if completed.returncode and not allow_nonzero:
            raise CalibrationError(reason_code)
        return completed

    def text(self, *arguments: str, reason_code: str) -> str:
        raw = self.run(*arguments, reason_code=reason_code).stdout
        try:
            return raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise CalibrationError(reason_code) from exc

    def blob(self, commit: str, path: str) -> bytes:
        return self.run(
            "cat-file",
            "blob",
            f"{commit}:{path}",
            reason_code="tracked_blob_unavailable",
        ).stdout


def _resolve_executable(value: str, reason_code: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        found = shutil.which(value)
        if not found:
            raise CalibrationError(reason_code)
        candidate = Path(found)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError(reason_code) from exc
    if not resolved.is_file():
        raise CalibrationError(reason_code)
    return resolved


def _resolve_repo(path: Path) -> Path:
    if path.is_symlink():
        raise CalibrationError("cadclaw_repo_must_not_be_symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError("cadclaw_repo_unavailable") from exc
    if not resolved.is_dir():
        raise CalibrationError("cadclaw_repo_unavailable")
    return resolved


def _require_expected_commits(frozen: str, candidate: str) -> None:
    if not _COMMIT.fullmatch(frozen) or frozen != FROZEN_COMMIT:
        raise CalibrationError("unexpected_frozen_commit")
    if not _COMMIT.fullmatch(candidate) or candidate != CANDIDATE_COMMIT:
        raise CalibrationError("unexpected_candidate_commit")


def _resolve_commit(git: _LocalGit, revision: str, reason_code: str) -> str:
    resolved = git.text(
        "rev-parse", "--verify", f"{revision}^{{commit}}", reason_code=reason_code
    )
    if not _COMMIT.fullmatch(resolved):
        raise CalibrationError(reason_code)
    return resolved


def _is_env_path(path: str) -> bool:
    return any(
        part.casefold() == ".env" or part.casefold().startswith(".env.")
        for part in PurePosixPath(path).parts
    )


def _tracked_paths(git: _LocalGit, commit: str, scopes: Iterable[str]) -> list[str]:
    raw = git.run(
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        commit,
        "--",
        *scopes,
        reason_code="tracked_path_inventory_failed",
    ).stdout
    try:
        paths = [item.decode("utf-8", errors="strict") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise CalibrationError("tracked_path_inventory_failed") from exc
    if not paths or len(paths) != len(set(paths)):
        raise CalibrationError("tracked_path_inventory_invalid")
    if any(_is_env_path(path) for path in paths):
        # Stop before git-archive or cat-file can read the prohibited blob.
        raise CalibrationError("environment_file_in_calibration_scope")
    return sorted(paths)


def _source_manifest(
    git: _LocalGit, commit: str, scopes: Iterable[str]
) -> dict[str, Any]:
    paths = _tracked_paths(git, commit, scopes)
    lines = bytearray()
    files = []
    for path in paths:
        digest = _sha256_bytes(git.blob(commit, path))
        lines.extend(f"{digest}  {path}\n".encode("utf-8"))
        files.append({"path": path, "sha256": digest})
    return {
        "file_count": len(paths),
        "manifest_sha256": _sha256_bytes(bytes(lines)),
        "files": files,
    }


def _file_identity(git: _LocalGit, commit: str, path: str) -> dict[str, Any]:
    result = git.run(
        "cat-file",
        "blob",
        f"{commit}:{path}",
        reason_code="high_value_file_lookup_failed",
        allow_nonzero=True,
    )
    if result.returncode == 128:
        return {"status": "absent", "sha256": None}
    if result.returncode:
        raise CalibrationError("high_value_file_lookup_failed")
    return {"status": "present", "sha256": _sha256_bytes(result.stdout)}


def _package_contract(blob: bytes) -> dict[str, Any]:
    try:
        document = tomllib.loads(blob.decode("utf-8", errors="strict"))
        project = document["project"]
        package_find = document["tool"]["setuptools"]["packages"]["find"]
        value = {
            "name": project["name"],
            "version": project["version"],
            "requires_python": project["requires-python"],
            "dependencies": list(project["dependencies"]),
            "scripts": dict(project["scripts"]),
            "package_include": list(package_find["include"]),
        }
    except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CalibrationError("package_contract_invalid") from exc
    if not all(isinstance(item, str) for item in value["dependencies"]):
        raise CalibrationError("package_contract_invalid")
    return value


def _archive_commit(
    git: _LocalGit,
    commit: str,
    destination: Path,
    tar_path: Path,
) -> None:
    archive_scopes = tuple(
        sorted(
            {
                *SOURCE_TO_WHEEL_INPUT_SCOPES,
                *TRACKED_FIXTURES,
                NIST_FIXTURE,
                NIST_SECONDARY_FIXTURE,
                NIST_TEST_FILE,
            }
        )
    )
    required_paths = _tracked_paths(git, commit, archive_scopes)
    git.run(
        "archive",
        "--format=tar",
        f"--output={tar_path}",
        commit,
        "--",
        *archive_scopes,
        reason_code="git_archive_failed",
        timeout=120,
    )
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    try:
        with tarfile.open(tar_path, mode="r:") as archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                if (
                    pure.is_absolute()
                    or not pure.parts
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or _is_env_path(member.name)
                    or (not member.isdir() and not member.isreg())
                ):
                    raise CalibrationError("git_archive_member_rejected")
                rendered = pure.as_posix()
                folded = rendered.casefold()
                if rendered in seen or folded in seen_casefold:
                    raise CalibrationError("git_archive_member_collision")
                seen.add(rendered)
                seen_casefold.add(folded)
                output = destination.joinpath(*pure.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    output.mkdir(exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise CalibrationError("git_archive_member_rejected")
                with output.open("xb") as stream:
                    shutil.copyfileobj(source, stream)
    except (OSError, tarfile.TarError) as exc:
        raise CalibrationError("git_archive_extract_failed") from exc
    extracted_paths = sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    )
    if extracted_paths != required_paths:
        raise CalibrationError("git_archive_path_set_mismatch")
    for path in required_paths:
        extracted = destination.joinpath(*PurePosixPath(path).parts)
        if extracted.is_symlink() or not extracted.is_file():
            raise CalibrationError("git_archive_incomplete")
        if _sha256_file(extracted) != _sha256_bytes(git.blob(commit, path)):
            raise CalibrationError("git_archive_blob_identity_mismatch")


SYNTHETIC_GENERATOR = r'''
import hashlib
import json
from pathlib import Path
import re
import socket
import sys

def blocked(*args, **kwargs):
    raise RuntimeError("external access is disabled during calibration")

socket.create_connection = blocked
socket.getaddrinfo = blocked
for method in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
    if hasattr(socket.socket, method):
        setattr(socket.socket, method, blocked)

import cadquery as cq

root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=False)

def export_case(name, placements):
    solids = []
    dimensions = ((10.0, 10.0, 10.0), (8.0, 12.0, 6.0), (6.0, 7.0, 9.0))
    for dims, center in zip(dimensions, placements):
        solid = cq.Workplane("XY").box(*dims).val().translate(cq.Vector(*center))
        solids.append(solid)
    path = root / (name + ".step")
    cq.exporters.export(cq.Compound.makeCompound(solids), str(path), exportType="STEP")
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    raw, replacements = re.subn(
        rb"(FILE_NAME\([^,]+,')\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(')",
        rb"\g<1>2000-01-01T00:00:00\2",
        raw,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("STEP header normalization failed")
    path.write_bytes(raw)
    rules = root / (name + ".yaml")
    rules.write_text(
        "schema_version: \"0.9\"\n"
        "meta:\n"
        "  project: marb-h2b-cadclaw-calibration\n"
        "  step: " + json.dumps(str(path)) + "\n"
        "belt_heuristic: false\n"
        "interference:\n"
        "  skip_labels: []\n"
        "  min_volume_mm3: 1.0\n"
        "  min_clearance_mm: 1.0\n",
        encoding="utf-8",
        newline="\n",
    )

export_case("separated-three-solid", ((0, 0, 0), (30, 0, 0), (0, 30, 0)))
export_case("overlap-three-solid", ((0, 0, 0), (6, 0, 0), (0, 30, 0)))
'''


REVISION_PROBE = r'''
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import socket
import sys
from contextlib import redirect_stderr, redirect_stdout

def blocked(*args, **kwargs):
    raise RuntimeError("external access is disabled during calibration")

socket.create_connection = blocked
socket.getaddrinfo = blocked
for method in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
    if hasattr(socket.socket, method):
        setattr(socket.socket, method, blocked)

archive_root = Path(sys.argv[1]).resolve()
synthetic_root = Path(sys.argv[2]).resolve()
render_output = Path(sys.argv[3]).resolve()
result_output = Path(sys.argv[4]).resolve()
fixture_paths = sys.argv[5:]
os.chdir(archive_root)
sys.path.insert(0, str(archive_root))

import cadclaw
from cadclaw.gate_spec import GATE_SPEC_VERSION
from cadclaw.render import render_step_to_png
from cadclaw.roundtrip import snapshot_geometry
from cadclaw_cli.main import main as cli_main
from PIL import Image

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def snapshot(path):
    value = snapshot_geometry(path).to_dict()
    value.pop("step_path", None)
    return {
        "part_count": value["part_count"],
        "sha256": hashlib.sha256(canonical(value)).hexdigest(),
    }

def run_harness(case_id):
    rules = synthetic_root / (case_id + ".yaml")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = cli_main([
            "harness", "--rules", str(rules), "--repo", str(archive_root),
            "--only", "interference", "--report-format", "json",
        ])
    report = json.loads(stdout.getvalue())
    return {
        "exit_code": exit_code,
        "report": report,
        "stderr_present": bool(stderr.getvalue().strip()),
    }

try:
    from cadclaw.gate_registry import HARNESS_GATE_REGISTRY
except ModuleNotFoundError as exc:
    if exc.name != "cadclaw.gate_registry":
        raise
    gate_registry = {"status": "absent", "version": None, "ids": []}
else:
    gate_registry = {
        "status": "present",
        "version": HARNESS_GATE_REGISTRY.version,
        "ids": list(HARNESS_GATE_REGISTRY.ids),
    }

snapshots = {}
for relative in fixture_paths:
    snapshots[relative] = snapshot(archive_root / Path(relative))
snapshots["synthetic/separated-three-solid.step"] = snapshot(
    synthetic_root / "separated-three-solid.step"
)

render_step_to_png(
    str(synthetic_root / "separated-three-solid.step"),
    str(render_output),
    width=200,
    height=150,
    tessellation_tol=1.0,
)
with Image.open(render_output) as image:
    image.load()
    dimensions = list(image.size)

runtime = {
    "python": platform.python_version(),
    "cadclaw": cadclaw.__version__,
}
for distribution in ("cadquery", "cadquery-ocp", "Pillow", "PyYAML", "pydantic", "vtk"):
    runtime[distribution] = importlib.metadata.version(distribution)

payload = {
    "runtime": runtime,
    "cadclaw_loaded_from_archive": Path(cadclaw.__file__).resolve().is_relative_to(archive_root),
    "gate_identity": {
        "gate_spec_version": GATE_SPEC_VERSION,
        "gate_registry": gate_registry,
    },
    "snapshots": snapshots,
    "render": {
        "status": "pass",
        "sha256": hashlib.sha256(render_output.read_bytes()).hexdigest(),
        "bytes": render_output.stat().st_size,
        "dimensions": dimensions,
    },
    "configured_harness": {
        "separated-three-solid": run_harness("separated-three-solid"),
        "overlap-three-solid": run_harness("overlap-three-solid"),
    },
}
with result_output.open("xb") as stream:
    stream.write((json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
'''


NIST_TEST_RUNNER = r'''
import io
import json
import os
from pathlib import Path
import socket
import sys
import unittest

def blocked(*args, **kwargs):
    raise RuntimeError("external access is disabled during calibration")

socket.create_connection = blocked
socket.getaddrinfo = blocked
for method in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
    if hasattr(socket.socket, method):
        setattr(socket.socket, method, blocked)

archive_root = Path(sys.argv[1]).resolve()
result_output = Path(sys.argv[2]).resolve()
test_id = sys.argv[3]
os.chdir(archive_root)
sys.path.insert(0, str(archive_root))
sys.path.insert(0, str(archive_root / "tests"))

suite = unittest.defaultTestLoader.loadTestsFromName(test_id)
result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
payload = {
    "status": "pass" if result.wasSuccessful() and result.testsRun == 1 and not result.skipped else "fail",
    "tests_run": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "skipped": len(result.skipped),
}
with result_output.open("xb") as stream:
    stream.write((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
'''


def _run_python_source(
    python: Path,
    source: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path,
    reason_code: str,
) -> None:
    _run(
        [str(python), "-I", "-c", source, *arguments],
        environment=environment,
        cwd=cwd,
        timeout=300,
        reason_code=reason_code,
    )


def _read_probe(path: Path, reason_code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationError(reason_code) from exc
    if not isinstance(value, dict):
        raise CalibrationError(reason_code)
    return value


def _normalize_report(value: Any, replacements: Mapping[str, str]) -> Any:
    """Remove timing and replace known local paths; retain all gate semantics."""
    if isinstance(value, dict):
        return {
            key: _normalize_report(item, replacements)
            for key, item in value.items()
            if key != "duration_ms"
        }
    if isinstance(value, list):
        return [_normalize_report(item, replacements) for item in value]
    if isinstance(value, str):
        normalized = value
        for source, replacement in replacements.items():
            normalized = normalized.replace(source, replacement)
            normalized = normalized.replace(source.replace("\\", "/"), replacement)
        return normalized
    return value


def _harness_case_summary(
    raw: Mapping[str, Any], replacements: Mapping[str, str]
) -> dict[str, Any]:
    try:
        report = _normalize_report(raw["report"], replacements)
        meta = report["meta"]
        registry = meta.get("gate_registry", {})
        summary = {
            "exit_code": int(raw["exit_code"]),
            "overall": report["overall"],
            "finding_ids": sorted(item["id"] for item in report["findings"]),
            "checked": list(report["confidence_budget"]["checked"]),
            "not_checked": list(report["confidence_budget"]["not_checked"]),
            "gate_registry_version": registry.get("version"),
            "gate_registry_aggregate_status": registry.get("aggregate_status"),
            "normalized_report_sha256": _sha256_bytes(_canonical_json(report)),
            "stderr_present": bool(raw["stderr_present"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError("configured_harness_probe_invalid") from exc
    rendered = json.dumps(summary, sort_keys=True)
    if any(source.casefold() in rendered.casefold() for source in replacements):
        raise CalibrationError("configured_harness_path_redaction_failed")
    return summary


def _snapshot_aggregate(entries: Sequence[tuple[str, str]]) -> str:
    payload = "".join(
        f"{digest}  {path}\n" for path, digest in sorted(entries)
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _new_output_path(path: Path) -> Path:
    if path.is_symlink() or path.exists():
        raise CalibrationError("output_must_be_new")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError("output_parent_unavailable") from exc
    if not parent.is_dir() or parent.is_symlink():
        raise CalibrationError("output_parent_unavailable")
    return parent / path.name


def _add_check(
    checks: list[dict[str, str]], failures: list[str], check_id: str, passed: bool
) -> None:
    checks.append({"id": check_id, "status": "pass" if passed else "fail"})
    if not passed:
        failures.append(check_id)


def _build_evidence(
    *,
    git: _LocalGit,
    frozen_commit: str,
    candidate_commit: str,
    frozen_tree: str,
    candidate_tree: str,
    frozen_probe: Mapping[str, Any],
    candidate_probe: Mapping[str, Any],
    frozen_nist: Mapping[str, Any],
    candidate_nist: Mapping[str, Any],
    synthetic_root: Path,
    replacements: Mapping[str, str],
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    failures: list[str] = []

    source_manifests: dict[str, Any] = {
        "algorithm": "path-sorted lowercase-sha256, two spaces, POSIX path, LF",
    }
    for manifest_id, scopes in (
        ("source_to_wheel_inputs", SOURCE_TO_WHEEL_INPUT_SCOPES),
        ("requested_core_source", CORE_SOURCE_SCOPES),
    ):
        frozen_manifest = _source_manifest(git, frozen_commit, scopes)
        candidate_manifest = _source_manifest(git, candidate_commit, scopes)
        expected = EXPECTED_MANIFESTS[manifest_id]
        source_manifests[manifest_id] = {
            "scope": list(scopes),
            "frozen": frozen_manifest,
            "candidate": candidate_manifest,
        }
        _add_check(
            checks,
            failures,
            f"{manifest_id}_frozen_identity",
            (frozen_manifest["file_count"], frozen_manifest["manifest_sha256"])
            == expected["frozen"],
        )
        _add_check(
            checks,
            failures,
            f"{manifest_id}_candidate_identity",
            (candidate_manifest["file_count"], candidate_manifest["manifest_sha256"])
            == expected["candidate"],
        )

    high_value_files = []
    for path in HIGH_VALUE_FILES:
        high_value_files.append(
            {
                "path": path,
                "frozen": _file_identity(git, frozen_commit, path),
                "candidate": _file_identity(git, candidate_commit, path),
            }
        )

    frozen_package = _package_contract(git.blob(frozen_commit, "pyproject.toml"))
    candidate_package = _package_contract(git.blob(candidate_commit, "pyproject.toml"))
    _add_check(
        checks,
        failures,
        "frozen_package_contract_exact",
        frozen_package == EXPECTED_PACKAGE_CONTRACT,
    )
    _add_check(
        checks,
        failures,
        "candidate_package_contract_exact",
        candidate_package == EXPECTED_PACKAGE_CONTRACT,
    )
    _add_check(
        checks,
        failures,
        "package_contract_parity",
        frozen_package == candidate_package,
    )

    frozen_runtime = dict(frozen_probe.get("runtime", {}))
    candidate_runtime = dict(candidate_probe.get("runtime", {}))
    _add_check(checks, failures, "frozen_runtime_exact", frozen_runtime == EXPECTED_RUNTIME)
    _add_check(
        checks, failures, "candidate_runtime_exact", candidate_runtime == EXPECTED_RUNTIME
    )
    _add_check(
        checks,
        failures,
        "cadclaw_loaded_from_frozen_archive",
        frozen_probe.get("cadclaw_loaded_from_archive") is True,
    )
    _add_check(
        checks,
        failures,
        "cadclaw_loaded_from_candidate_archive",
        candidate_probe.get("cadclaw_loaded_from_archive") is True,
    )

    tracked_fixture_rows = []
    frozen_aggregate_entries = []
    candidate_aggregate_entries = []
    for path in TRACKED_FIXTURES:
        frozen_source_sha = _sha256_bytes(git.blob(frozen_commit, path))
        candidate_source_sha = _sha256_bytes(git.blob(candidate_commit, path))
        try:
            frozen_snapshot = frozen_probe["snapshots"][path]
            candidate_snapshot = candidate_probe["snapshots"][path]
            parity = frozen_snapshot["sha256"] == candidate_snapshot["sha256"]
        except (KeyError, TypeError) as exc:
            raise CalibrationError("snapshot_probe_invalid") from exc
        tracked_fixture_rows.append(
            {
                "path": path,
                "source_sha256": {
                    "frozen": frozen_source_sha,
                    "candidate": candidate_source_sha,
                },
                "source_bytes_equal": frozen_source_sha == candidate_source_sha,
                "snapshot_sha256": {
                    "frozen": frozen_snapshot["sha256"],
                    "candidate": candidate_snapshot["sha256"],
                },
                "part_count": {
                    "frozen": frozen_snapshot["part_count"],
                    "candidate": candidate_snapshot["part_count"],
                },
                "parity": parity,
            }
        )
        frozen_aggregate_entries.append((path, frozen_snapshot["sha256"]))
        candidate_aggregate_entries.append((path, candidate_snapshot["sha256"]))
        _add_check(
            checks,
            failures,
            f"tracked_fixture_source_equal_{len(tracked_fixture_rows)}",
            frozen_source_sha == candidate_source_sha,
        )
        _add_check(
            checks,
            failures,
            f"tracked_fixture_snapshot_parity_{len(tracked_fixture_rows)}",
            parity,
        )

    frozen_aggregate = _snapshot_aggregate(frozen_aggregate_entries)
    candidate_aggregate = _snapshot_aggregate(candidate_aggregate_entries)
    _add_check(
        checks,
        failures,
        "tracked_fixture_snapshot_aggregate_parity",
        frozen_aggregate == candidate_aggregate,
    )

    synthetic_path = synthetic_root / "separated-three-solid.step"
    overlap_path = synthetic_root / "overlap-three-solid.step"
    synthetic_key = "synthetic/separated-three-solid.step"
    try:
        frozen_synthetic = frozen_probe["snapshots"][synthetic_key]
        candidate_synthetic = candidate_probe["snapshots"][synthetic_key]
    except (KeyError, TypeError) as exc:
        raise CalibrationError("synthetic_snapshot_probe_invalid") from exc
    synthetic_parity = frozen_synthetic["sha256"] == candidate_synthetic["sha256"]
    _add_check(
        checks,
        failures,
        "synthetic_three_solid_part_count",
        frozen_synthetic["part_count"] == candidate_synthetic["part_count"] == 3,
    )
    _add_check(
        checks, failures, "synthetic_three_solid_snapshot_parity", synthetic_parity
    )

    try:
        frozen_render = dict(frozen_probe["render"])
        candidate_render = dict(candidate_probe["render"])
    except (KeyError, TypeError) as exc:
        raise CalibrationError("render_probe_invalid") from exc
    for revision, render in (("frozen", frozen_render), ("candidate", candidate_render)):
        _add_check(
            checks,
            failures,
            f"{revision}_render_smoke",
            render.get("status") == "pass"
            and render.get("dimensions") == [200, 150]
            and isinstance(render.get("bytes"), int)
            and render["bytes"] > 500
            and bool(re.fullmatch(r"[0-9a-f]{64}", str(render.get("sha256", "")))),
        )

    try:
        frozen_cases_raw = frozen_probe["configured_harness"]
        candidate_cases_raw = candidate_probe["configured_harness"]
    except (KeyError, TypeError) as exc:
        raise CalibrationError("configured_harness_probe_invalid") from exc
    harness_cases = []
    for case_id in ("separated-three-solid", "overlap-three-solid"):
        frozen_case = _harness_case_summary(frozen_cases_raw[case_id], replacements)
        candidate_case = _harness_case_summary(candidate_cases_raw[case_id], replacements)
        harness_cases.append(
            {
                "id": case_id,
                "synthetic_step_sha256": _sha256_file(
                    synthetic_path if case_id == "separated-three-solid" else overlap_path
                ),
                "frozen": frozen_case,
                "candidate": candidate_case,
            }
        )
        expected_frozen = (
            frozen_case["exit_code"] == 0
            and frozen_case["overall"] == "pass"
            and frozen_case["finding_ids"] == []
            and frozen_case["checked"] == []
            and not frozen_case["stderr_present"]
        )
        _add_check(
            checks,
            failures,
            f"configured_harness_frozen_{case_id}_vacuous_baseline",
            expected_frozen,
        )
        if case_id == "separated-three-solid":
            expected_candidate = (
                candidate_case["exit_code"] == 0
                and candidate_case["overall"] == "pass"
                and candidate_case["finding_ids"] == []
                and candidate_case["checked"] == ["interference"]
                and candidate_case["gate_registry_version"] == "harness-gates.v1"
                and candidate_case["gate_registry_aggregate_status"] == "pass"
                and not candidate_case["stderr_present"]
            )
        else:
            expected_candidate = (
                candidate_case["exit_code"] == 1
                and candidate_case["overall"] == "fail"
                and candidate_case["finding_ids"] == ["interference.clip"]
                and candidate_case["checked"] == ["interference"]
                and candidate_case["gate_registry_version"] == "harness-gates.v1"
                and candidate_case["gate_registry_aggregate_status"] == "fail"
                and not candidate_case["stderr_present"]
            )
        _add_check(
            checks,
            failures,
            f"configured_harness_candidate_{case_id}_fail_closed",
            expected_candidate,
        )

    frozen_identity = frozen_probe.get("gate_identity")
    candidate_identity = candidate_probe.get("gate_identity")
    _add_check(
        checks,
        failures,
        "frozen_gate_identity",
        frozen_identity
        == {
            "gate_spec_version": "0.12.0",
            "gate_registry": {"status": "absent", "version": None, "ids": []},
        },
    )
    candidate_registry = (
        candidate_identity.get("gate_registry", {})
        if isinstance(candidate_identity, dict)
        else {}
    )
    _add_check(
        checks,
        failures,
        "candidate_gate_identity",
        isinstance(candidate_identity, dict)
        and candidate_identity.get("gate_spec_version") == "0.13.0"
        and candidate_registry.get("status") == "present"
        and candidate_registry.get("version") == "harness-gates.v1"
        and "interference" in candidate_registry.get("ids", []),
    )

    for revision, result in (("frozen", frozen_nist), ("candidate", candidate_nist)):
        _add_check(
            checks,
            failures,
            f"{revision}_nist_roundtrip_integration",
            result
            == {
                "status": "pass",
                "tests_run": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
            },
        )

    status_by_id = {item["id"]: item["status"] for item in checks}
    low_level_ids = [
        item["id"]
        for item in checks
        if item["id"].startswith("tracked_fixture_")
        or item["id"].startswith("synthetic_three_solid_")
        or item["id"].endswith("_render_smoke")
        or item["id"].endswith("_nist_roundtrip_integration")
    ]
    configured_harness_ids = [
        item["id"]
        for item in checks
        if item["id"].startswith("configured_harness_")
        or item["id"].endswith("_gate_identity")
    ]
    source_contract_ids = [
        item["id"]
        for item in checks
        if item["id"].startswith("source_to_wheel_inputs_")
        or item["id"].startswith("requested_core_source_")
        or "package_contract" in item["id"]
        or "runtime_exact" in item["id"]
        or item["id"].startswith("cadclaw_loaded_from_")
    ]

    def grouped_status(check_ids: Sequence[str]) -> str:
        return (
            "pass"
            if check_ids and all(status_by_id[check_id] == "pass" for check_id in check_ids)
            else "fail"
        )

    return {
        "schema": SCHEMA,
        "calibration_id": CALIBRATION_ID,
        "calibration_kind": "local_software_compatibility",
        "classification": "compatible" if not failures else "incompatible",
        "compatibility_scope": (
            "low-level snapshot/grade-input, render-call, and exact NIST roundtrip "
            "surfaces; configured-harness report semantics intentionally advance "
            "to gate 0.13.0 and harness-gates.v1"
        ),
        "not_a_benchmark_or_grade": True,
        "probe_summary": {
            "required_probe_ids": [
                "source_to_wheel_input_manifest",
                "package_and_runtime_contract",
                "tracked_fixture_snapshot_geometry",
                "synthetic_three_solid_snapshot_geometry",
                "synthetic_three_solid_render_step_to_png",
                "nist_ftc_11_exact_roundtrip_unittest",
                "configured_harness_separated_interference",
                "configured_harness_overlap_interference",
            ],
            "source_contract_status": grouped_status(source_contract_ids),
            "low_level_common_surfaces_status": grouped_status(low_level_ids),
            "configured_harness_expected_divergence_status": grouped_status(
                configured_harness_ids
            ),
            "configured_harness_semantics": "intentional_versioned_delta",
            "semantically_identical": False,
        },
        "source_control": {
            "fetch_performed": False,
            "origin_main_ref": "refs/remotes/origin/main",
            "candidate_matches_fetched_origin_main": True,
            "frozen_is_ancestor_of_candidate": True,
            "frozen_commit": frozen_commit,
            "candidate_commit": candidate_commit,
            "frozen_tree": frozen_tree,
            "candidate_tree": candidate_tree,
        },
        "source_manifests": source_manifests,
        "high_value_files": high_value_files,
        "package_contract": {
            "expected": EXPECTED_PACKAGE_CONTRACT,
            "frozen": frozen_package,
            "candidate": candidate_package,
        },
        "runtime": {
            "expected": EXPECTED_RUNTIME,
            "frozen": frozen_runtime,
            "candidate": candidate_runtime,
        },
        "snapshot_geometry": {
            "normalization": (
                "canonical compact sorted JSON of GeometrySnapshot.to_dict "
                "with only step_path omitted"
            ),
            "tracked_fixtures": tracked_fixture_rows,
            "tracked_fixture_aggregate": {
                "algorithm": "path-sorted snapshot sha256, two spaces, POSIX path, LF",
                "frozen_sha256": frozen_aggregate,
                "candidate_sha256": candidate_aggregate,
                "parity": frozen_aggregate == candidate_aggregate,
            },
            "synthetic_three_solid": {
                "generator_sha256": _sha256_bytes(SYNTHETIC_GENERATOR.encode("utf-8")),
                "source_step_sha256": _sha256_file(synthetic_path),
                "part_count": {
                    "frozen": frozen_synthetic["part_count"],
                    "candidate": candidate_synthetic["part_count"],
                },
                "snapshot_sha256": {
                    "frozen": frozen_synthetic["sha256"],
                    "candidate": candidate_synthetic["sha256"],
                },
                "parity": synthetic_parity,
            },
        },
        "configured_harness_calibration": {
            "method": "cadclaw harness --only interference --report-format json",
            "normalization": (
                "duration_ms removed and known temporary paths replaced; all gate "
                "semantics, findings, checked sets, registry data, and exit codes retained"
            ),
            "intentional_versioned_delta": (
                "frozen 0.12 CLI records a vacuous pass with no checked gate; candidate "
                "0.13 executes interference through harness-gates.v1 and fails overlap"
            ),
            "gate_identity": {
                "frozen": frozen_identity,
                "candidate": candidate_identity,
            },
            "cases": harness_cases,
        },
        "render_step_to_png_smoke": {
            "input": "synthetic/separated-three-solid.step",
            "expected_dimensions": [200, 150],
            "frozen": frozen_render,
            "candidate": candidate_render,
        },
        "nist_roundtrip_integration": {
            "test_id": NIST_TEST_ID,
            "fixture_path": NIST_FIXTURE,
            "fixture_sha256": {
                "frozen": _sha256_bytes(git.blob(frozen_commit, NIST_FIXTURE)),
                "candidate": _sha256_bytes(git.blob(candidate_commit, NIST_FIXTURE)),
            },
            "frozen": dict(frozen_nist),
            "candidate": dict(candidate_nist),
        },
        "checks": checks,
        "failed_checks": failures,
        "limitations": [
            "This is software calibration, not a benchmark, grade, or model-selection result.",
            "No Docker image was built, pulled, pushed, or run; no OCI RepoDigest or runtime image is qualified.",
            "No model or provider was contacted, no H2b attempt was allocated, and no board or grade was mutated.",
            "Common Python socket egress APIs are blocked fail-closed, but this is not OS or network-namespace isolation; categorical isolation requires an externally disabled network.",
            "Render hashes are recorded as smoke evidence and are not treated as cross-version pixel-equivalence gates.",
            "The NIST fixture is an authored software-qualification input; its use does not imply NIST endorsement or an error-free conformance reference.",
            "This calibration makes no compliance, manufacturability, physical-validation, durability, or readiness claim.",
        ],
    }


def run_calibration(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    _require_expected_commits(args.frozen_commit, args.candidate_commit)
    output = _new_output_path(args.output)
    repo = _resolve_repo(args.cadclaw_repo)
    python = _resolve_executable(args.python, "python_executable_unavailable")
    git_executable = _resolve_executable("git", "git_executable_unavailable")

    with tempfile.TemporaryDirectory(prefix="marb-h2b-cadclaw-calibration-") as temp:
        temp_root = Path(temp).resolve()
        environment = _safe_environment(temp_root)
        git = _LocalGit(git_executable, repo, environment)
        inside = git.text(
            "rev-parse", "--is-inside-work-tree", reason_code="cadclaw_repo_invalid"
        )
        if inside != "true":
            raise CalibrationError("cadclaw_repo_invalid")

        frozen_commit = _resolve_commit(
            git, args.frozen_commit, "frozen_commit_unavailable"
        )
        candidate_commit = _resolve_commit(
            git, args.candidate_commit, "candidate_commit_unavailable"
        )
        origin_main = _resolve_commit(
            git, "refs/remotes/origin/main", "origin_main_ref_unavailable"
        )
        if candidate_commit != origin_main:
            raise CalibrationError("candidate_not_fetched_origin_main")
        ancestor = git.run(
            "merge-base",
            "--is-ancestor",
            frozen_commit,
            candidate_commit,
            reason_code="commit_ancestry_check_failed",
            allow_nonzero=True,
        )
        if ancestor.returncode == 1:
            raise CalibrationError("frozen_not_candidate_ancestor")
        if ancestor.returncode:
            raise CalibrationError("commit_ancestry_check_failed")

        frozen_tree = git.text(
            "rev-parse", f"{frozen_commit}^{{tree}}", reason_code="frozen_tree_unavailable"
        )
        candidate_tree = git.text(
            "rev-parse",
            f"{candidate_commit}^{{tree}}",
            reason_code="candidate_tree_unavailable",
        )
        if frozen_tree != FROZEN_TREE or candidate_tree != CANDIDATE_TREE:
            raise CalibrationError("unexpected_commit_tree_identity")

        frozen_root = temp_root / "frozen"
        candidate_root = temp_root / "candidate"
        frozen_root.mkdir()
        candidate_root.mkdir()
        _archive_commit(git, frozen_commit, frozen_root, temp_root / "frozen.tar")
        _archive_commit(git, candidate_commit, candidate_root, temp_root / "candidate.tar")

        synthetic_root = temp_root / "synthetic"
        _run_python_source(
            python,
            SYNTHETIC_GENERATOR,
            [str(synthetic_root)],
            environment=environment,
            cwd=temp_root,
            reason_code="synthetic_fixture_generation_failed",
        )

        probes: dict[str, dict[str, Any]] = {}
        nist_results: dict[str, dict[str, Any]] = {}
        for revision, archive_root in (
            ("frozen", frozen_root),
            ("candidate", candidate_root),
        ):
            probe_path = temp_root / f"{revision}-probe.json"
            render_path = temp_root / f"{revision}-render.png"
            _run_python_source(
                python,
                REVISION_PROBE,
                [
                    str(archive_root),
                    str(synthetic_root),
                    str(render_path),
                    str(probe_path),
                    *TRACKED_FIXTURES,
                ],
                environment=environment,
                cwd=temp_root,
                reason_code=f"{revision}_revision_probe_failed",
            )
            probes[revision] = _read_probe(
                probe_path, f"{revision}_revision_probe_invalid"
            )

            nist_path = temp_root / f"{revision}-nist.json"
            _run_python_source(
                python,
                NIST_TEST_RUNNER,
                [str(archive_root), str(nist_path), NIST_TEST_ID],
                environment=environment,
                cwd=temp_root,
                reason_code=f"{revision}_nist_test_runner_failed",
            )
            nist_results[revision] = _read_probe(
                nist_path, f"{revision}_nist_test_result_invalid"
            )

        replacements = {
            str(temp_root): "<calibration-temp>",
            str(repo): "<cadclaw-repo>",
        }
        evidence = _build_evidence(
            git=git,
            frozen_commit=frozen_commit,
            candidate_commit=candidate_commit,
            frozen_tree=frozen_tree,
            candidate_tree=candidate_tree,
            frozen_probe=probes["frozen"],
            candidate_probe=probes["candidate"],
            frozen_nist=nist_results["frozen"],
            candidate_nist=nist_results["candidate"],
            synthetic_root=synthetic_root,
            replacements=replacements,
        )

        payload = _canonical_json(evidence)
        try:
            with output.open("xb") as stream:
                stream.write(payload)
        except OSError as exc:
            raise CalibrationError("output_creation_failed") from exc
    return evidence, output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadclaw-repo", type=Path, required=True)
    parser.add_argument("--frozen-commit", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence, _output = run_calibration(args)
    except CalibrationError as exc:
        print(f"calibration error: {exc.reason_code}", file=sys.stderr)
        return 2
    print(
        "local CADCLAW software calibration created; "
        f"classification={evidence['classification']}"
    )
    return 0 if evidence["classification"] == "compatible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
