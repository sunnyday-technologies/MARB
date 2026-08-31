"""Fail-closed Docker sandbox for model-authored Python tools.

The provider/conversation process must remain outside this module. The sandbox
receives one already-created workspace and exposes only that directory to a
digest-pinned, networkless container. It never pulls an image, never mounts the
repository or Docker control socket, and never inherits the caller's
credential-bearing environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, NoReturn, Protocol, Sequence


CONTAINER_WORKSPACE = "/workspace"
CONTAINER_PYTHON = "/usr/local/bin/python3"
CONTAINER_INPUT = "/workspace/kit"
CONTAINER_INPUT_ROOT = "/marb-input"
CONTAINER_HOST_WORKSPACE = "/marb-host-workspace"
CONTAINER_EXPORT = "/marb-export"
LIMITER_EXIT = 125
WORKSPACE_ENTRY_LIMIT_REASON = "MARB_LIMIT_REASON=workspace_entry_count_exceeded\n"
CONTAINER_LIMITER = "/opt/marb/run_limited.py"
STATUS_MEMBER = "MARB_EXECUTION_STATUS.json"
DEFAULT_DOCKER_EXECUTABLE = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
CONTAINER_UID_GID = "65532:65532"
CONTAINER_NAME_PREFIX = "marb-isolated-"
OWNERSHIP_LABEL = "org.sunnyday.marb.isolated-container"
PID_LIMIT = "256"
MEMORY_LIMIT = "16g"
MEMORY_LIMIT_BYTES = 16 * 1024 * 1024 * 1024
CPU_LIMIT = "4"
NANO_CPU_LIMIT = 4_000_000_000
NOFILE_LIMIT = "1024:1024"
MAX_WORKSPACE_FILES = 4_096
MAX_WORKSPACE_FILE_BYTES = 512 * 1024 * 1024
MAX_WORKSPACE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_WORKSPACE_ENTRIES = MAX_WORKSPACE_FILES
MAX_WORKSPACE_DEPTH = 32
MAX_WORKSPACE_PATH_BYTES = 1_024
MAX_EXPORT_TAR_BYTES = MAX_WORKSPACE_TOTAL_BYTES + 16 * 1024 * 1024
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
CLEANUP_TIMEOUT_SECONDS = 15.0
TMPFS_SPEC = (
    "/tmp:rw,nosuid,nodev,noexec,size=1073741824,uid=65532,gid=65532,mode=0700"
)
WORKSPACE_TMPFS_SPEC = (
    "/workspace:rw,nosuid,nodev,noexec,size=2147483648,uid=65532,gid=65532,mode=0700"
)
EXPORT_TMPFS_SPEC = (
    "/marb-export:rw,nosuid,nodev,noexec,size=2164260864,uid=65532,gid=65532,mode=0700"
)
MINIMAL_ENV = (
    "HOME=/tmp",
    "TMPDIR=/tmp",
    "XDG_CACHE_HOME=/tmp/cache",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
    "TZ=UTC",
    "LANG=C.UTF-8",
    "LC_ALL=C.UTF-8",
    "PATH=/usr/local/bin:/usr/bin:/bin",
)

_OUTPUT_TRUNCATION_MARKER = b"\n[MARB output truncated]\n"
_HOST_ENV_ALLOWLIST = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTAINER_NAME_RE = re.compile(r"marb-isolated-[0-9a-f]{32}\Z")
REPOSITORY_COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]*[a-z0-9]|[a-z0-9]")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
PROTECTED_COMPONENT_RE = re.compile(
    r"(?:^|[._-])(?:answer[_-]?keys?|private|credentials?|secrets?|tokens?)(?:$|[._-])",
    re.IGNORECASE,
)
PROTECTED_EXACT_NAMES = {
    ".aws",
    ".azure",
    ".env",
    ".git",
    ".gnupg",
    ".ssh",
    "docker.sock",
}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class IsolationError(RuntimeError):
    """A bounded, machine-readable failure of the isolation contract.

    ``str(error)`` intentionally remains a fixed, human-readable message.
    Bounded child output is retained privately for immediate handling, while
    serialized evidence contains only its byte counts, hashes, truncation
    flags, stable status values, and non-secret container identities. Raw
    command lines, environments, output bodies, and arbitrary exception
    messages are never copied into the evidence.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "isolation_contract_violation",
        stage: str = "validation",
        stdout: str = "",
        stderr: str = "",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
        returncode: int | None = None,
        cleanup_attempted: bool = False,
        cleanup_verified: bool | None = None,
        container_absence_verified: bool | None = None,
        export_staging_removed: bool | None = None,
        image: str | None = None,
        image_id: str | None = None,
        container_name: str | None = None,
        container_id: str | None = None,
        primary_error_type: str | None = None,
        cleanup_error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self._stdout = stdout
        self._stderr = stderr
        self.stdout_truncated = bool(stdout_truncated)
        self.stderr_truncated = bool(stderr_truncated)
        self.returncode = returncode
        self.cleanup_attempted = bool(cleanup_attempted)
        self.cleanup_verified = cleanup_verified
        self.container_absence_verified = container_absence_verified
        self.export_staging_removed = export_staging_removed
        self.image = image
        self.image_id = image_id
        self.container_name = container_name
        self.container_id = container_id
        self.primary_error_type = primary_error_type
        self.cleanup_error_type = cleanup_error_type

    @property
    def stdout(self) -> str:
        return self._stdout

    @property
    def stderr(self) -> str:
        return self._stderr

    @property
    def stdout_bytes(self) -> int:
        return len(self.stdout.encode("utf-8", errors="replace"))

    @property
    def stderr_bytes(self) -> int:
        return len(self.stderr.encode("utf-8", errors="replace"))

    @property
    def stdout_sha256(self) -> str:
        return hashlib.sha256(
            self.stdout.encode("utf-8", errors="replace")
        ).hexdigest()

    @property
    def stderr_sha256(self) -> str:
        return hashlib.sha256(
            self.stderr.encode("utf-8", errors="replace")
        ).hexdigest()

    @property
    def evidence(self) -> dict[str, Any]:
        return self.to_dict()

    def _record_execution_evidence(
        self,
        *,
        stage: str,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        returncode: int | None,
        image: str | None,
        image_id: str | None,
        container_name: str | None,
        container_id: str | None,
        primary_error_type: str | None = None,
    ) -> None:
        self.stage = stage
        self._stdout = stdout
        self._stderr = stderr
        self.stdout_truncated = bool(stdout_truncated)
        self.stderr_truncated = bool(stderr_truncated)
        self.returncode = returncode
        self.image = image
        self.image_id = image_id
        self.container_name = container_name
        self.container_id = container_id
        if primary_error_type is not None:
            self.primary_error_type = primary_error_type

    def _record_cleanup_evidence(
        self,
        *,
        attempted: bool,
        verified: bool | None,
        container_absence_verified: bool | None,
        export_staging_removed: bool | None,
        cleanup_error_type: str | None = None,
    ) -> None:
        self.cleanup_attempted = bool(attempted)
        self.cleanup_verified = verified
        self.container_absence_verified = container_absence_verified
        self.export_staging_removed = export_staging_removed
        self.cleanup_error_type = cleanup_error_type

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON-safe evidence schema for logs and smoke gates."""
        return {
            "schema": "marb_isolation_error.v1",
            "code": self.code,
            "stage": self.stage,
            "returncode": self.returncode,
            "stdout": {
                "bytes": self.stdout_bytes,
                "sha256": self.stdout_sha256,
                "truncated": self.stdout_truncated,
            },
            "stderr": {
                "bytes": self.stderr_bytes,
                "sha256": self.stderr_sha256,
                "truncated": self.stderr_truncated,
            },
            "cleanup_attempted": self.cleanup_attempted,
            "cleanup_verified": self.cleanup_verified,
            "container_absence_verified": self.container_absence_verified,
            "export_staging_removed": self.export_staging_removed,
            "image": self.image,
            "image_id": self.image_id,
            "container_name": self.container_name,
            "container_id": self.container_id,
            "primary_error_type": self.primary_error_type,
            "cleanup_error_type": self.cleanup_error_type,
        }


class CommandResultLike(Protocol):
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResultLike: ...


@dataclass(frozen=True)
class _BoundedCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True)
class ExecutionResult:
    """Readback from one isolated Python execution."""

    image: str
    image_id: str
    docker_executable: str
    workspace: Path
    script: str
    container_name: str
    container_id: str
    create_command: tuple[str, ...]
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    cleanup_verified: bool


@dataclass(frozen=True)
class WorkspaceUsage:
    file_count: int
    total_bytes: int


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._buffer = bytearray()
        self.truncated = False

    def consume(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                remaining = self._limit - len(self._buffer)
                if remaining > 0:
                    self._buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        finally:
            stream.close()

    def value(self) -> bytes:
        raw = bytes(self._buffer)
        if not self.truncated:
            return raw
        retained = max(0, self._limit - len(_OUTPUT_TRUNCATION_MARKER))
        return raw[:retained] + _OUTPUT_TRUNCATION_MARKER[: self._limit - retained]


def _fail(
    message: str,
    *,
    code: str = "isolation_contract_violation",
    stage: str = "validation",
) -> NoReturn:
    raise IsolationError(message, code=code, stage=stage)


def _minimal_host_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Select only process-launch settings; never forward provider credentials."""
    selected: dict[str, str] = {}
    for key in _HOST_ENV_ALLOWLIST:
        value = source.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            selected[key] = value
    selected.setdefault("PATH", os.defpath)
    selected["LANG"] = "C.UTF-8"
    selected["LC_ALL"] = "C.UTF-8"
    return selected


def _default_command_runner(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> _BoundedCommandResult:
    """Run a Docker CLI command while keeping captured output memory bounded."""
    if env is None:
        _fail("Docker CLI subprocess requires an explicit minimal host environment")
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        shell=False,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        process.kill()
        _fail("Docker CLI subprocess output pipes were unavailable")
    stdout_capture = _BoundedCapture(MAX_STDOUT_BYTES)
    stderr_capture = _BoundedCapture(MAX_STDERR_BYTES)
    stdout_thread = threading.Thread(
        target=stdout_capture.consume, args=(process.stdout,), daemon=True
    )
    stderr_thread = threading.Thread(
        target=stderr_capture.consume, args=(process.stderr,), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        timeout_error = subprocess.TimeoutExpired(
            list(argv),
            exc.timeout,
            output=stdout_capture.value(),
            stderr=stderr_capture.value(),
        )
        timeout_error.stdout_truncated = stdout_capture.truncated  # type: ignore[attr-defined]
        timeout_error.stderr_truncated = stderr_capture.truncated  # type: ignore[attr-defined]
        raise timeout_error from None
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        raise
    stdout_thread.join()
    stderr_thread.join()
    return _BoundedCommandResult(
        returncode=returncode,
        stdout=stdout_capture.value(),
        stderr=stderr_capture.value(),
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
    )


def _bounded_text(value: str | bytes | None, limit: int) -> tuple[str, bool]:
    """Return valid UTF-8 text whose encoded form never exceeds ``limit`` bytes."""
    if value is None:
        return "", False
    if limit < 0:
        _fail("output byte limit must not be negative")
    raw = value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
    raw_exceeded = len(raw) > limit
    candidate = raw[:limit] if raw_exceeded else raw
    decoded = candidate.decode("utf-8", errors="replace")
    decoded_bytes = decoded.encode("utf-8")
    normalization_exceeded = len(decoded_bytes) > limit
    if not raw_exceeded and not normalization_exceeded:
        return decoded, False

    marker = _OUTPUT_TRUNCATION_MARKER[:limit]
    retained = max(0, limit - len(marker))
    prefix_raw = raw[:retained]
    # Invalid bytes and a split multibyte code point can expand under replacement.
    # Re-encode once, then cut only at a valid UTF-8 boundary.
    prefix_encoded = prefix_raw.decode("utf-8", errors="replace").encode("utf-8")
    prefix = prefix_encoded[:retained].decode("utf-8", errors="ignore")
    return prefix + marker.decode("ascii"), True


def _structured_execution_error(
    exc: BaseException,
    *,
    stage: str,
    stdout: str,
    stderr: str,
    stdout_truncated: bool,
    stderr_truncated: bool,
    returncode: int | None,
    image: str | None,
    image_id: str | None,
    container_name: str | None,
    container_id: str | None,
) -> IsolationError:
    """Normalize runtime failures without copying arbitrary exception messages."""
    primary_type = type(exc).__name__
    if isinstance(exc, IsolationError):
        error = exc
    elif isinstance(exc, subprocess.TimeoutExpired):
        timeout_stdout, timeout_stdout_truncated = _bounded_text(
            exc.output, MAX_STDOUT_BYTES
        )
        timeout_stderr, timeout_stderr_truncated = _bounded_text(
            exc.stderr, MAX_STDERR_BYTES
        )
        stdout = timeout_stdout
        stderr = timeout_stderr
        stdout_truncated = timeout_stdout_truncated or bool(
            getattr(exc, "stdout_truncated", False)
        )
        stderr_truncated = timeout_stderr_truncated or bool(
            getattr(exc, "stderr_truncated", False)
        )
        error = IsolationError(
            "isolated Docker command exceeded its timeout",
            code="execution_timeout",
            stage=stage,
        )
    else:
        error = IsolationError(
            "isolated Docker execution failed",
            code="execution_failed",
            stage=stage,
        )
    error._record_execution_evidence(
        stage=stage,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        returncode=returncode,
        image=image,
        image_id=image_id,
        container_name=container_name,
        container_id=container_id,
        primary_error_type=primary_type,
    )
    return error


def _validate_repository_name(name: str) -> None:
    if (
        not name
        or len(name) > 255
        or name != name.lower()
        or CONTROL_RE.search(name)
        or "\\" in name
        or "://" in name
        or "//" in name
        or name.startswith(("/", "."))
        or name.endswith(("/", "."))
    ):
        _fail("container image must use a safe lowercase repository name")
    components = name.split("/")
    registry = components[0]
    if ":" in registry:
        host, separator, port = registry.rpartition(":")
        if (
            not separator
            or not REPOSITORY_COMPONENT_RE.fullmatch(host)
            or not port.isdecimal()
            or not (1 <= int(port) <= 65535)
        ):
            _fail("container image repository has an invalid registry port")
        components[0] = host
    if any(not REPOSITORY_COMPONENT_RE.fullmatch(part) for part in components):
        _fail("container image must use a safe lowercase repository name")


def validate_image_reference(image: str) -> str:
    """Require an immutable ``repository@sha256:<digest>`` image reference."""
    if not isinstance(image, str) or image.count("@sha256:") != 1:
        _fail("container image must be pinned as name@sha256:<64 lowercase hex>")
    name, digest = image.rsplit("@sha256:", 1)
    _validate_repository_name(name)
    if not SHA256_RE.fullmatch(digest):
        _fail("container image must be pinned as name@sha256:<64 lowercase hex>")
    return image


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _assert_path_chain_is_plain(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _is_link_or_reparse(current):
                _fail(f"{label} contains a symlink or reparse point")
        except FileNotFoundError:
            _fail(f"{label} does not exist")


def _is_protected_component(component: str) -> bool:
    lowered = component.rstrip(". ").casefold()
    stem = lowered.split(".", 1)[0]
    return (
        lowered in PROTECTED_EXACT_NAMES
        or stem in WINDOWS_RESERVED_NAMES
        or bool(PROTECTED_COMPONENT_RE.search(lowered))
    )


def _validate_relative_python_path(relative_script: str) -> tuple[str, ...]:
    if (
        not isinstance(relative_script, str)
        or not relative_script
        or CONTROL_RE.search(relative_script)
        or "\\" in relative_script
        or ":" in relative_script
    ):
        _fail("Python tool path must be a safe workspace-relative POSIX path")
    raw_parts = relative_script.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        _fail("Python tool path must stay inside the workspace")
    pure = PurePosixPath(relative_script)
    if pure.is_absolute():
        _fail("Python tool path must stay inside the workspace")
    if pure.suffix != ".py":
        _fail("isolated execution accepts a .py file only")
    if any(_is_protected_component(part) for part in pure.parts):
        _fail("Python tool path uses a protected path component")
    return pure.parts


def _validate_workspace_tree(workspace: Path) -> WorkspaceUsage:
    entry_count = 0
    total_bytes = 0

    def walk_error(_error: OSError) -> None:
        _fail("workspace cannot be enumerated safely")

    for root, directories, files in os.walk(
        workspace, followlinks=False, onerror=walk_error
    ):
        root_path = Path(root)
        for name in [*directories, *files]:
            if _is_protected_component(name):
                _fail("workspace contains a protected path")
            candidate = root_path / name
            try:
                relative = candidate.relative_to(workspace).as_posix()
            except ValueError:
                _fail("workspace path escaped its root")
            if (
                len(relative.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES
                or len(PurePosixPath(relative).parts) > MAX_WORKSPACE_DEPTH
            ):
                _fail("workspace path exceeds the structural limits")
            try:
                info = candidate.lstat()
            except OSError:
                _fail("workspace changed during validation")
            if _is_link_or_reparse(candidate):
                _fail("workspace contains a symlink or reparse point")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                _fail("workspace contains a non-file filesystem object")
            entry_count += 1
            if entry_count > MAX_WORKSPACE_ENTRIES:
                _fail(
                    "workspace exceeds the entry-count limit",
                    code="workspace_input_limit_exceeded",
                )
            if stat.S_ISREG(info.st_mode):
                if getattr(info, "st_nlink", 1) != 1:
                    _fail("workspace contains a hard-linked file")
                total_bytes += info.st_size
                if info.st_size > MAX_WORKSPACE_FILE_BYTES:
                    _fail(
                        "workspace file exceeds the individual-size limit",
                        code="workspace_input_limit_exceeded",
                    )
                if total_bytes > MAX_WORKSPACE_TOTAL_BYTES:
                    _fail(
                        "workspace exceeds the total-size limit",
                        code="workspace_input_limit_exceeded",
                    )
    return WorkspaceUsage(file_count=entry_count, total_bytes=total_bytes)


def validate_workspace(workspace: Path, relative_script: str) -> tuple[Path, str]:
    """Validate the sole bind source and the Python entrypoint within it."""
    workspace = Path(workspace)
    if not workspace.is_absolute():
        _fail("workspace must be an absolute path")
    raw_workspace = str(workspace)
    if CONTROL_RE.search(raw_workspace) or "," in raw_workspace:
        _fail("workspace path cannot be represented safely as a Docker mount")
    _assert_path_chain_is_plain(workspace, "workspace")
    if not workspace.is_dir() or workspace.parent == workspace:
        _fail("workspace must be an existing non-root directory")
    if _is_protected_component(workspace.name):
        _fail("workspace uses a protected path component")
    canonical = workspace.resolve(strict=True)
    _validate_workspace_tree(canonical)

    parts = _validate_relative_python_path(relative_script)
    script = canonical.joinpath(*parts)
    _assert_path_chain_is_plain(script, "Python tool path")
    if not script.is_file():
        _fail("Python tool path must name an existing regular file")
    try:
        script.relative_to(canonical)
    except ValueError:
        _fail("Python tool path escapes the workspace")
    return canonical, PurePosixPath(*parts).as_posix()


def validate_input_root(input_root: Path, workspace: Path) -> Path:
    """Validate the complete immutable public-input tree and kit alias source."""
    value = Path(input_root)
    if not value.is_absolute() or CONTROL_RE.search(str(value)) or "," in str(value):
        _fail("immutable input root cannot be represented safely")
    _assert_path_chain_is_plain(value, "immutable input root")
    if not value.is_dir() or value.parent == value:
        _fail("immutable input root must be an existing non-root directory")
    canonical = value.resolve(strict=True)
    if canonical.parent != workspace.parent:
        _fail("workspace and immutable input root must share one fresh run directory")
    if (workspace / "kit").exists() or (workspace / "kit").is_symlink():
        _fail("writable workspace must not contain a path shadowing immutable kit inputs")
    _validate_workspace_tree(canonical)
    kit = canonical / "kit"
    _assert_path_chain_is_plain(kit, "immutable kit input")
    if not kit.is_dir():
        _fail("immutable kit input is missing")
    _validate_workspace_tree(kit)
    return canonical


def _safe_export_member(name: str) -> PurePosixPath:
    if (
        not isinstance(name, str)
        or not name
        or not name.isascii()
        or CONTROL_RE.search(name)
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or len(name.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES
    ):
        _fail("container workspace export contains an unsafe path")
    normalized = name.rstrip("/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or len(pure.parts) > MAX_WORKSPACE_DEPTH
        or any(
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or _is_protected_component(part)
            for part in pure.parts
        )
        or pure.as_posix() != normalized
    ):
        _fail("container workspace export contains an unsafe path")
    return pure


def _extract_workspace_export(
    archive_path: Path,
    destination: Path,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> int:
    try:
        info = archive_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or _is_link_or_reparse(archive_path)
            or info.st_size < 1
            or info.st_size > MAX_EXPORT_TAR_BYTES
        ):
            raise OSError
        destination.mkdir(mode=0o700, exist_ok=False)
        seen: set[str] = set()
        total = 0
        entries = 0
        child_returncode: int | None = None
        with tarfile.open(archive_path, "r:") as archive:
            for member in archive:
                if deadline_check is not None:
                    deadline_check()
                pure = _safe_export_member(member.name)
                identity = "/".join(part.rstrip(". ").casefold() for part in pure.parts)
                if identity in seen:
                    _fail("container workspace export contains an aliased path")
                seen.add(identity)
                if identity == STATUS_MEMBER.casefold():
                    if member.name != STATUS_MEMBER or not member.isfile() or member.size > 512:
                        _fail("container workspace export status is malformed")
                    source = archive.extractfile(member)
                    if source is None:
                        _fail("container workspace export status is missing")
                    with source:
                        status_raw = source.read(513)
                    try:
                        status = json.loads(status_raw.decode("ascii"))
                    except (UnicodeError, json.JSONDecodeError):
                        _fail("container workspace export status is malformed")
                    if (
                        status_raw != (
                            json.dumps(
                                status,
                                ensure_ascii=True,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("ascii")
                            + b"\n"
                        )
                        or not isinstance(status, dict)
                        or set(status) != {"schema", "returncode"}
                        or status.get("schema") != "marb_container_execution_status.v1"
                        or not isinstance(status.get("returncode"), int)
                        or isinstance(status.get("returncode"), bool)
                        or not -255 <= status["returncode"] <= 255
                    ):
                        _fail("container workspace export status is malformed")
                    child_returncode = status["returncode"]
                    continue
                entries += 1
                if entries > MAX_WORKSPACE_ENTRIES:
                    _fail(
                        "container workspace export exceeds the entry-count limit",
                        code="workspace_export_limit_exceeded",
                        stage="workspace_restore",
                    )
                target = destination.joinpath(*pure.parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(mode=0o700, exist_ok=True)
                    continue
                if not member.isfile() or member.size < 0 or member.size > MAX_WORKSPACE_FILE_BYTES:
                    _fail("container workspace export contains an unsafe object")
                total += member.size
                if total > MAX_WORKSPACE_TOTAL_BYTES:
                    _fail(
                        "container workspace export exceeds the total-size limit",
                        code="workspace_export_limit_exceeded",
                        stage="workspace_restore",
                    )
                source = archive.extractfile(member)
                if source is None:
                    _fail("container workspace export member cannot be read")
                copied = 0
                with source, target.open("xb") as output:
                    while True:
                        if deadline_check is not None:
                            deadline_check()
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > member.size:
                            _fail("container workspace export member expanded unexpectedly")
                        output.write(chunk)
                if copied != member.size:
                    _fail("container workspace export member was truncated")
        if deadline_check is not None:
            deadline_check()
        _validate_workspace_tree(destination)
        if child_returncode is None:
            _fail("container workspace export status is missing")
        return child_returncode
    except IsolationError:
        raise
    except (OSError, tarfile.TarError, EOFError):
        _fail("container workspace export cannot be restored safely")


def _remove_internal_tree(path: Path, parent: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
        expected_parent = parent.resolve(strict=True)
    except OSError:
        _fail("internal workspace cleanup target cannot be resolved")
    if resolved.parent != expected_parent or resolved == expected_parent:
        _fail("internal workspace cleanup target escaped the run directory")
    try:
        shutil.rmtree(resolved)
    except OSError:
        _fail("internal workspace cleanup failed")


def _replace_workspace_from_export(
    workspace: Path,
    archive_path: Path,
    token: str,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> int:
    parent = workspace.parent.resolve(strict=True)
    staging = parent / f".workspace-import-{token}"
    previous = parent / f".workspace-previous-{token}"
    if staging.exists() or previous.exists() or staging.is_symlink() or previous.is_symlink():
        _fail("workspace import staging path already exists")
    try:
        if deadline_check is not None:
            deadline_check()
        child_returncode = _extract_workspace_export(
            archive_path,
            staging,
            deadline_check=deadline_check,
        )
        if deadline_check is not None:
            deadline_check()
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            _remove_internal_tree(staging, parent)
        raise
    try:
        os.replace(workspace, previous)
        try:
            os.replace(staging, workspace)
        except OSError:
            os.replace(previous, workspace)
            raise
        _remove_internal_tree(previous, parent)
    except OSError:
        if staging.exists():
            _remove_internal_tree(staging, parent)
        _fail("container workspace export cannot replace the prior workspace")
    try:
        archive_path.unlink()
    except OSError:
        _fail("container workspace export staging file cannot be removed")
    return child_returncode


def _unlink_export_staging(path: Path, expected_parent: Path) -> None:
    """Remove only the exact per-call export entry; unlinking a link is safe."""
    if path.parent != expected_parent:
        _fail("workspace export staging path escaped the run directory")
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        _fail("workspace export staging file cannot be removed")


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"Docker returned malformed {label} metadata")
    return value


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == {}


def _inspect_reports_absent(result: CommandResultLike) -> bool:
    if result.returncode == 0:
        return False
    stderr, truncated = _bounded_text(result.stderr, MAX_STDERR_BYTES)
    if truncated:
        return False
    lowered = stderr.casefold()
    return "no such object" in lowered or "no such container" in lowered


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _is_owned_container(
    metadata: dict[str, Any], container_name: str, container_id: str | None = None
) -> bool:
    config = metadata.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    return (
        metadata.get("Name") == f"/{container_name}"
        and (container_id is None or metadata.get("Id") == container_id)
        and isinstance(labels, dict)
        and labels.get(OWNERSHIP_LABEL) == container_name
    )


class IsolatedDockerPython:
    """Execute one workspace Python file under the fixed Docker policy."""

    def __init__(
        self,
        image: str,
        *,
        input_root: Path,
        docker_executable: str = DEFAULT_DOCKER_EXECUTABLE,
        command_runner: CommandRunner | None = None,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        host_environ: Mapping[str, str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.image = validate_image_reference(image)
        self.input_root = Path(input_root)
        pure_docker = PureWindowsPath(docker_executable) if isinstance(docker_executable, str) else None
        if (
            not isinstance(docker_executable, str)
            or not docker_executable
            or "\\" in docker_executable
            or "//" in docker_executable
            or not docker_executable[0].isupper()
            or not re.fullmatch(
                r"[A-Za-z]:/(?:[^/\\\x00-\x1f\x7f]+/)+docker\.exe",
                docker_executable,
                re.IGNORECASE,
            )
            or pure_docker is None
            or pure_docker.as_posix() != docker_executable
            or any(
                part in {".", ".."}
                or part.endswith((".", " "))
                or (":" in part and index != 0)
                or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
                for index, part in enumerate(pure_docker.parts)
            )
        ):
            _fail("Docker executable must be an absolute normalized docker.exe path")
        self.docker_executable = docker_executable
        self._command_runner = command_runner or _default_command_runner
        self._uuid_factory = uuid_factory
        self._monotonic = monotonic
        self._host_environment = _minimal_host_environment(
            os.environ if host_environ is None else host_environ
        )

    def _invoke(
        self, argv: Sequence[str], *, timeout: float | None
    ) -> CommandResultLike:
        return self._command_runner(
            list(argv), timeout=timeout, env=dict(self._host_environment)
        )

    def _remaining_timeout(
        self,
        deadline: float | None,
        cap: float | None,
        *,
        label: str,
    ) -> float | None:
        if deadline is None:
            return cap
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            _fail(f"{label} exceeded the authorized wall-clock deadline")
        return remaining if cap is None else min(remaining, cap)

    def _deadline_check(self, deadline: float | None) -> None:
        self._remaining_timeout(deadline, None, label="isolated execution")

    def _inspect_local_image_details(
        self, *, timeout: float | None
    ) -> tuple[tuple[str, ...], str]:
        command = [
            self.docker_executable,
            "image",
            "inspect",
            "--format",
            "{{json .}}",
            self.image,
        ]
        result = self._invoke(command, timeout=timeout)
        if result.returncode != 0:
            _fail("digest-pinned container image is not available locally")
        raw, truncated = _bounded_text(result.stdout, MAX_STDOUT_BYTES)
        if truncated:
            _fail("Docker image inspection output exceeded the limit")
        try:
            parsed = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            _fail("Docker returned malformed image metadata")
        metadata = _require_mapping(parsed, "image")
        repo_digests = metadata.get("RepoDigests")
        if not isinstance(repo_digests, list) or not all(
            isinstance(item, str) for item in repo_digests
        ):
            _fail("Docker returned malformed RepoDigest metadata")
        if self.image not in repo_digests:
            _fail("local image RepoDigest does not match the authorized digest")
        image_id = metadata.get("Id")
        if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
            _fail("Docker returned malformed image identity metadata")
        config = _require_mapping(metadata.get("Config"), "image configuration")
        if not _empty(config.get("Volumes")):
            _fail("container image declares volumes and is not eligible for isolation")
        return tuple(repo_digests), image_id

    def inspect_local_image(self, *, timeout: float | None = 30.0) -> tuple[str, ...]:
        """Verify the exact immutable local image and reject declared volumes."""
        repo_digests, _ = self._inspect_local_image_details(timeout=timeout)
        return repo_digests

    def _new_container_name(self) -> str:
        candidate = self._uuid_factory()
        if not isinstance(candidate, uuid.UUID):
            _fail("container name factory did not return a UUID")
        name = CONTAINER_NAME_PREFIX + candidate.hex
        if not CONTAINER_NAME_RE.fullmatch(name):
            _fail("container name is not safe")
        return name

    @staticmethod
    def _container_command(script: str) -> list[str]:
        return [
            "-i",
            *MINIMAL_ENV,
            CONTAINER_PYTHON,
            "-I",
            "-B",
            CONTAINER_LIMITER,
            script,
        ]

    def build_create_command(
        self,
        workspace: Path,
        relative_script: str,
        container_name: str,
        export_archive: Path,
    ) -> list[str]:
        """Return the complete named ``docker create`` invocation."""
        if not isinstance(container_name, str) or not CONTAINER_NAME_RE.fullmatch(
            container_name
        ):
            _fail("container name is not safe")
        canonical, script = validate_workspace(workspace, relative_script)
        input_root = validate_input_root(self.input_root, canonical)
        export_archive = Path(export_archive)
        try:
            export_info = export_archive.lstat()
        except OSError:
            _fail("workspace export staging file is missing")
        if (
            not export_archive.is_absolute()
            or export_archive.parent.resolve(strict=True) != canonical.parent
            or not stat.S_ISREG(export_info.st_mode)
            or _is_link_or_reparse(export_archive)
            or export_info.st_size != 0
        ):
            _fail("workspace export staging file is unsafe")
        mount = (
            f"type=bind,source={canonical},target={CONTAINER_HOST_WORKSPACE},"
            "readonly,bind-propagation=rprivate"
        )
        input_mount = (
            f"type=bind,source={input_root},target={CONTAINER_INPUT_ROOT},"
            "readonly,bind-propagation=rprivate"
        )
        kit_compatibility_mount = (
            f"type=bind,source={input_root / 'kit'},target={CONTAINER_INPUT},"
            "readonly,bind-propagation=rprivate"
        )
        export_mount = (
            f"type=bind,source={export_archive},target={CONTAINER_EXPORT}/workspace.tar,"
            "bind-propagation=rprivate"
        )
        return [
            self.docker_executable,
            "create",
            "--name",
            container_name,
            "--label",
            f"{OWNERSHIP_LABEL}={container_name}",
            "--pull",
            "never",
            "--network",
            "none",
            "--ipc",
            "none",
            "--cgroupns",
            "private",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            CONTAINER_UID_GID,
            "--pids-limit",
            PID_LIMIT,
            "--memory",
            MEMORY_LIMIT,
            "--memory-swap",
            MEMORY_LIMIT,
            "--cpus",
            CPU_LIMIT,
            "--ulimit",
            f"nofile={NOFILE_LIMIT}",
            "--ulimit",
            f"fsize={MAX_EXPORT_TAR_BYTES}:{MAX_EXPORT_TAR_BYTES}",
            "--tmpfs",
            TMPFS_SPEC,
            "--tmpfs",
            WORKSPACE_TMPFS_SPEC,
            "--tmpfs",
            EXPORT_TMPFS_SPEC,
            "--mount",
            mount,
            "--mount",
            input_mount,
            "--mount",
            kit_compatibility_mount,
            "--mount",
            export_mount,
            "--workdir",
            CONTAINER_WORKSPACE,
            "--entrypoint",
            "/usr/bin/env",
            "--no-healthcheck",
            "--log-driver",
            "none",
            self.image,
            *self._container_command(script),
        ]

    def _read_container_metadata(
        self, reference: str, *, timeout: float | None
    ) -> tuple[CommandResultLike, dict[str, Any] | None]:
        command = [
            self.docker_executable,
            "container",
            "inspect",
            "--format",
            "{{json .}}",
            reference,
        ]
        result = self._invoke(command, timeout=timeout)
        if result.returncode != 0:
            return result, None
        raw, truncated = _bounded_text(result.stdout, MAX_STDOUT_BYTES)
        if truncated:
            _fail("Docker container inspection output exceeded the limit")
        try:
            parsed = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            _fail("Docker returned malformed container metadata")
        return result, _require_mapping(parsed, "container")

    def _validate_container_metadata(
        self,
        metadata: dict[str, Any],
        *,
        container_id: str,
        container_name: str,
        image_id: str,
        workspace: Path,
        input_root: Path,
        export_archive: Path,
        script: str,
    ) -> None:
        if metadata.get("Id") != container_id or metadata.get("Name") != f"/{container_name}":
            _fail("Docker container identity readback does not match the created container")
        if metadata.get("Image") != image_id:
            _fail("Docker container image readback does not match the inspected image")
        state = _require_mapping(metadata.get("State"), "container state")
        if state.get("Status") != "created" or state.get("Running") is not False:
            _fail("Docker container was not inert at policy readback")

        config = _require_mapping(metadata.get("Config"), "container configuration")
        expected_command = self._container_command(script)
        if config.get("Image") != self.image:
            _fail("Docker container configuration does not retain the authorized image")
        if config.get("User") != CONTAINER_UID_GID:
            _fail("Docker container user policy readback failed")
        if config.get("WorkingDir") != CONTAINER_WORKSPACE:
            _fail("Docker container workdir policy readback failed")
        if config.get("Entrypoint") != ["/usr/bin/env"] or config.get("Cmd") != expected_command:
            _fail("Docker container clean-environment command readback failed")
        if not _empty(config.get("Volumes")):
            _fail("Docker container has an unauthorized declared volume")
        if not _empty(config.get("ExposedPorts")):
            _fail("Docker container has an unauthorized exposed port")
        healthcheck = config.get("Healthcheck")
        if not isinstance(healthcheck, dict) or healthcheck.get("Test") != ["NONE"]:
            _fail("Docker container healthcheck was not disabled")
        labels = config.get("Labels")
        if not isinstance(labels, dict) or labels.get(OWNERSHIP_LABEL) != container_name:
            _fail("Docker container ownership label readback failed")

        host = _require_mapping(metadata.get("HostConfig"), "container host configuration")
        exact_values = {
            "NetworkMode": "none",
            "IpcMode": "none",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "AutoRemove": False,
            "PidsLimit": int(PID_LIMIT),
            "Memory": MEMORY_LIMIT_BYTES,
            "MemorySwap": MEMORY_LIMIT_BYTES,
            "NanoCpus": NANO_CPU_LIMIT,
            "PublishAllPorts": False,
        }
        if any(host.get(key) != value for key, value in exact_values.items()):
            _fail("Docker container isolation policy readback failed")
        if host.get("CapDrop") != ["ALL"] or not _empty(host.get("CapAdd")):
            _fail("Docker container capability policy readback failed")
        if host.get("SecurityOpt") != ["no-new-privileges"]:
            _fail("Docker container security option readback failed")
        restart_policy = host.get("RestartPolicy")
        if restart_policy != {"Name": "no", "MaximumRetryCount": 0}:
            _fail("Docker container restart policy readback failed")
        log_config = host.get("LogConfig")
        if not isinstance(log_config, dict) or log_config.get("Type") != "none":
            _fail("Docker container logging was not disabled")
        tmpfs = host.get("Tmpfs")
        expected_tmpfs = {
            item.split(":", 1)[0]: item.split(":", 1)[1]
            for item in (TMPFS_SPEC, WORKSPACE_TMPFS_SPEC, EXPORT_TMPFS_SPEC)
        }
        if not isinstance(tmpfs, dict) or set(tmpfs) != set(expected_tmpfs):
            _fail("Docker container tmpfs policy readback failed")
        for destination, expected_options in expected_tmpfs.items():
            actual_options = tmpfs[destination]
            if not isinstance(actual_options, str) or set(actual_options.split(",")) != set(
                expected_options.split(",")
            ):
                _fail("Docker container tmpfs policy readback failed")

        ulimits = host.get("Ulimits")
        if not isinstance(ulimits, list):
            _fail("Docker container resource-limit readback failed")
        parsed_ulimits: dict[str, tuple[Any, Any]] = {}
        for item in ulimits:
            if not isinstance(item, dict) or not isinstance(item.get("Name"), str):
                _fail("Docker container resource-limit readback failed")
            parsed_ulimits[item["Name"]] = (item.get("Soft"), item.get("Hard"))
        if parsed_ulimits != {
            "nofile": (int(NOFILE_LIMIT.split(":", 1)[0]), int(NOFILE_LIMIT.split(":", 1)[1])),
            "fsize": (MAX_EXPORT_TAR_BYTES, MAX_EXPORT_TAR_BYTES),
        }:
            _fail("Docker container resource-limit readback failed")

        for key in (
            "Binds",
            "VolumesFrom",
            "Devices",
            "DeviceRequests",
            "DeviceCgroupRules",
            "PortBindings",
            "Links",
            "ExtraHosts",
        ):
            if not _empty(host.get(key)):
                _fail("Docker container has an unauthorized host resource")
        network_settings = _require_mapping(
            metadata.get("NetworkSettings"), "container network configuration"
        )
        if not _empty(network_settings.get("Ports")):
            _fail("Docker container has an unauthorized published port")

        mounts = metadata.get("Mounts")
        if not isinstance(mounts, list):
            _fail("Docker container mount readback is malformed")
        bind_mounts = [
            item for item in mounts
            if isinstance(item, dict) and item.get("Type") == "bind"
        ]
        other_mounts = [item for item in mounts if item not in bind_mounts]
        if len(bind_mounts) != 4:
            _fail("Docker container must have exactly three read-only inputs and one export bind")
        by_destination = {
            item.get("Destination"): item for item in bind_mounts
        }
        if set(by_destination) != {
            CONTAINER_HOST_WORKSPACE,
            CONTAINER_INPUT_ROOT,
            CONTAINER_INPUT,
            f"{CONTAINER_EXPORT}/workspace.tar",
        }:
            _fail("Docker container bind destinations are unauthorized")
        bind = by_destination[CONTAINER_HOST_WORKSPACE]
        source = bind.get("Source")
        if (
            not isinstance(source, str)
            or not _same_path(source, workspace)
            or bind.get("Destination") != CONTAINER_HOST_WORKSPACE
            or bind.get("RW") is not False
            or bind.get("Propagation") != "rprivate"
        ):
            _fail("Docker workspace bind readback failed")
        input_bind = by_destination[CONTAINER_INPUT_ROOT]
        input_source = input_bind.get("Source")
        if (
            not isinstance(input_source, str)
            or not _same_path(input_source, input_root)
            or input_bind.get("RW") is not False
            or input_bind.get("Propagation") != "rprivate"
        ):
            _fail("Docker immutable-input bind readback failed")
        kit_bind = by_destination[CONTAINER_INPUT]
        kit_source = kit_bind.get("Source")
        if (
            not isinstance(kit_source, str)
            or not _same_path(kit_source, input_root / "kit")
            or kit_bind.get("RW") is not False
            or kit_bind.get("Propagation") != "rprivate"
        ):
            _fail("Docker kit compatibility bind readback failed")
        export_bind = by_destination[f"{CONTAINER_EXPORT}/workspace.tar"]
        export_source = export_bind.get("Source")
        if (
            not isinstance(export_source, str)
            or not _same_path(export_source, export_archive)
            or export_bind.get("RW") is not True
            or export_bind.get("Propagation") != "rprivate"
        ):
            _fail("Docker exact-file export bind readback failed")
        tmpfs_mount_destinations = {
            mount.get("Destination")
            for mount in other_mounts
            if isinstance(mount, dict) and mount.get("Type") == "tmpfs"
        }
        if len(other_mounts) != 3 or tmpfs_mount_destinations != set(expected_tmpfs):
            _fail("Docker container has an unauthorized extra mount")
        mount_text = json.dumps(mounts, sort_keys=True).casefold()
        if "docker.sock" in mount_text or "docker_engine" in mount_text:
            _fail("Docker control socket mount is forbidden")
        if metadata.get("Path") != "/usr/bin/env" or metadata.get("Args") != expected_command:
            _fail("Docker effective process readback failed")

    def _cleanup_container(self, container_id: str, *, deadline: float) -> bool:
        def cleanup_timeout() -> float:
            # Issue every cleanup command even at the boundary; the one-millisecond
            # floor is only for the CLI to observe/kill its child, not model work.
            return max(0.001, deadline - self._monotonic())

        for command in (
            [self.docker_executable, "kill", container_id],
            [self.docker_executable, "stop", "--time", "0", container_id],
        ):
            try:
                self._invoke(command, timeout=cleanup_timeout())
            except BaseException:
                # Removal is still attempted even when kill/stop cannot report cleanly.
                pass
        try:
            removal = self._invoke(
                [self.docker_executable, "rm", "--force", container_id],
                timeout=cleanup_timeout(),
            )
        except BaseException as exc:
            raise IsolationError("Docker container cleanup failed") from exc
        if removal.returncode != 0:
            _fail("Docker container cleanup failed")
        try:
            inspect_result, metadata = self._read_container_metadata(
                container_id, timeout=cleanup_timeout()
            )
        except BaseException as exc:
            raise IsolationError("Docker container removal readback failed") from exc
        if metadata is not None:
            _fail("Docker container remained after cleanup")
        if not _inspect_reports_absent(inspect_result):
            _fail("Docker container removal absence could not be verified")
        return True

    def _cleanup_uncertain_creation(
        self, container_name: str, *, deadline: float
    ) -> bool:
        inspect_result, metadata = self._read_container_metadata(
            container_name, timeout=max(0.001, deadline - self._monotonic())
        )
        if metadata is None:
            if _inspect_reports_absent(inspect_result):
                return True
            _fail("uncertain Docker creation could not be resolved")
        if not _is_owned_container(metadata, container_name):
            # Never destroy a pre-existing container that merely collided by name.
            return False
        container_id = metadata.get("Id")
        if not isinstance(container_id, str) or not CONTAINER_ID_RE.fullmatch(container_id):
            _fail("owned Docker container has a malformed identity")
        return self._cleanup_container(container_id, deadline=deadline)

    def execute(
        self,
        workspace: Path,
        relative_script: str,
        *,
        inspect_timeout: float | None = 30.0,
        execution_timeout: float | None = 600.0,
        attached_run_timeout: float | None = None,
    ) -> ExecutionResult:
        """Create, attest, run, and remove one named isolated container."""
        if execution_timeout is not None and execution_timeout <= 0:
            _fail("execution timeout must be positive")
        if inspect_timeout is not None and inspect_timeout <= 0:
            _fail("inspection timeout must be positive")
        if attached_run_timeout is not None and attached_run_timeout <= 0:
            _fail("attached run timeout must be positive")
        started = self._monotonic()
        deadline = None if execution_timeout is None else started + execution_timeout
        canonical, script = validate_workspace(workspace, relative_script)
        self._deadline_check(deadline)
        input_root = validate_input_root(self.input_root, canonical)
        self._deadline_check(deadline)
        _, image_id = self._inspect_local_image_details(
            timeout=self._remaining_timeout(
                deadline, inspect_timeout, label="Docker image preflight"
            )
        )
        try:
            self._deadline_check(deadline)
            container_name = self._new_container_name()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            preparation_error = _structured_execution_error(
                exc,
                stage="post_image_preparation",
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                returncode=None,
                image=self.image,
                image_id=image_id,
                container_name=None,
                container_id=None,
            )
            if preparation_error is exc:
                raise
            raise preparation_error from exc
        export_archive = canonical.parent / f".marb-export-{container_name}.tar"
        create_command: list[str] | None = None
        container_id: str | None = None
        creation_uncertain = False
        primary_error: BaseException | None = None
        export_ready = False
        start_returncode: int | None = None
        start_command: list[str] | None = None
        stdout = ""
        stderr = ""
        stdout_truncated = False
        stderr_truncated = False
        failure_stage = "export_staging"
        try:
            try:
                with export_archive.open("xb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                _fail("workspace export staging file cannot be created")
            self._deadline_check(deadline)
            create_command = self.build_create_command(
                canonical, script, container_name, export_archive
            )
            self._deadline_check(deadline)
            failure_stage = "container_creation"
            creation_uncertain = True
            create_result = self._invoke(
                create_command,
                timeout=self._remaining_timeout(
                    deadline, inspect_timeout, label="Docker container creation"
                ),
            )
            if create_result.returncode != 0:
                _fail("Docker container creation failed")
            raw_id, truncated = _bounded_text(create_result.stdout, MAX_STDOUT_BYTES)
            candidate_id = raw_id.strip()
            if truncated or not CONTAINER_ID_RE.fullmatch(candidate_id):
                _fail("Docker returned a malformed container identity")
            failure_stage = "container_policy_readback"
            _, metadata = self._read_container_metadata(
                candidate_id,
                timeout=self._remaining_timeout(
                    deadline, inspect_timeout, label="Docker policy readback"
                ),
            )
            if metadata is None:
                _fail("Docker container disappeared before policy readback")
            if not _is_owned_container(metadata, container_name, candidate_id):
                _fail("Docker container ownership readback failed")
            container_id = candidate_id
            creation_uncertain = False
            self._validate_container_metadata(
                metadata,
                container_id=container_id,
                container_name=container_name,
                image_id=image_id,
                workspace=canonical,
                input_root=input_root,
                export_archive=export_archive,
                script=script,
            )
            self._deadline_check(deadline)
            # Close the create/readback race before admitting model-authored code.
            failure_stage = "pre_start_revalidation"
            validate_workspace(canonical, script)
            validate_input_root(self.input_root, canonical)
            self._deadline_check(deadline)
            failure_stage = "container_start"
            start_command = [self.docker_executable, "start", "--attach", container_id]
            start_result = self._invoke(
                start_command,
                timeout=self._remaining_timeout(
                    deadline, attached_run_timeout, label="isolated model execution"
                ),
            )
            stdout, stdout_truncated = _bounded_text(
                start_result.stdout, MAX_STDOUT_BYTES
            )
            stderr, stderr_truncated = _bounded_text(
                start_result.stderr, MAX_STDERR_BYTES
            )
            stdout_truncated = stdout_truncated or bool(
                getattr(start_result, "stdout_truncated", False)
            )
            stderr_truncated = stderr_truncated or bool(
                getattr(start_result, "stderr_truncated", False)
            )
            start_returncode = int(start_result.returncode)
            self._deadline_check(deadline)
            if stdout_truncated or stderr_truncated:
                raise IsolationError(
                    "isolated container output exceeded the capture limit",
                    code="output_limit_exceeded",
                    stage=failure_stage,
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    returncode=start_returncode,
                    image=self.image,
                    image_id=image_id,
                    container_name=container_name,
                    container_id=container_id,
                )
            if start_returncode != 0:
                exact_entry_overflow = bool(
                    start_returncode == LIMITER_EXIT
                    and not stdout
                    and not stdout_truncated
                    and not stderr_truncated
                    and stderr == WORKSPACE_ENTRY_LIMIT_REASON
                )
                if exact_entry_overflow:
                    failure_stage = "workspace_limiter"
                raise IsolationError(
                    "container workspace limiter rejected execution",
                    code=(
                        "workspace_entry_limit_exceeded"
                        if exact_entry_overflow
                        else "container_limiter_rejected"
                    ),
                    stage=failure_stage,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=start_returncode,
                    image=self.image,
                    image_id=image_id,
                    container_name=container_name,
                    container_id=container_id,
                )
            failure_stage = "export_validation"
            try:
                export_info = export_archive.lstat()
            except OSError:
                _fail("container workspace export is missing")
            if (
                not stat.S_ISREG(export_info.st_mode)
                or _is_link_or_reparse(export_archive)
                or export_info.st_size < 1
                or export_info.st_size > MAX_EXPORT_TAR_BYTES
            ):
                _fail(
                    "container workspace export is outside the allowed bound",
                    code="workspace_export_limit_exceeded",
                    stage=failure_stage,
                )
            export_ready = True
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                primary_error = exc
                raise
            primary_error = _structured_execution_error(
                exc,
                stage=failure_stage,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                returncode=start_returncode,
                image=self.image,
                image_id=image_id,
                container_name=container_name,
                container_id=container_id,
            )
            if primary_error is exc:
                raise
            raise primary_error from exc
        finally:
            cleanup_attempted = container_id is not None or creation_uncertain
            cleanup_verified: bool | None = None
            container_absence_verified: bool | None = None
            cleanup_error: BaseException | None = None
            export_staging_removed: bool | None = None
            try:
                cleanup_deadline = self._monotonic() + CLEANUP_TIMEOUT_SECONDS
                if container_id is not None:
                    container_absence_verified = self._cleanup_container(
                        container_id, deadline=cleanup_deadline
                    )
                elif creation_uncertain:
                    container_absence_verified = self._cleanup_uncertain_creation(
                        container_name, deadline=cleanup_deadline
                    )
                if cleanup_attempted:
                    cleanup_verified = True
            except BaseException as observed_cleanup_error:
                cleanup_error = observed_cleanup_error
                cleanup_verified = False
                container_absence_verified = False

            staging_error: IsolationError | None = None
            if primary_error is not None or cleanup_error is not None:
                try:
                    _unlink_export_staging(export_archive, canonical.parent)
                except IsolationError as observed_staging_error:
                    staging_error = observed_staging_error
                    export_staging_removed = False
                else:
                    export_staging_removed = True

            if isinstance(primary_error, IsolationError):
                primary_error._record_cleanup_evidence(
                    attempted=cleanup_attempted,
                    verified=cleanup_verified,
                    container_absence_verified=container_absence_verified,
                    export_staging_removed=export_staging_removed,
                    cleanup_error_type=(
                        type(cleanup_error or staging_error).__name__
                        if cleanup_error is not None or staging_error is not None
                        else None
                    ),
                )
            if cleanup_error is not None and primary_error is None:
                cleanup_failure = IsolationError(
                    "Docker container cleanup could not be verified",
                    code="cleanup_verification_failed",
                    stage="container_cleanup",
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    returncode=start_returncode,
                    image=self.image,
                    image_id=image_id,
                    container_name=container_name,
                    container_id=container_id,
                    cleanup_attempted=cleanup_attempted,
                    cleanup_verified=False,
                    container_absence_verified=False,
                    export_staging_removed=export_staging_removed,
                    cleanup_error_type=type(cleanup_error).__name__,
                )
                raise cleanup_failure from cleanup_error
        if (
            not export_ready
            or container_id is None
            or create_command is None
            or start_command is None
            or start_returncode is None
        ):
            _fail("isolated execution produced no workspace export")
        try:
            child_returncode = _replace_workspace_from_export(
                canonical,
                export_archive,
                container_id[:16],
                deadline_check=lambda: self._deadline_check(deadline),
            )
        except BaseException as exc:
            export_staging_removed = True
            try:
                _unlink_export_staging(export_archive, canonical.parent)
            except IsolationError:
                export_staging_removed = False
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            restore_error = _structured_execution_error(
                exc,
                stage="workspace_restore",
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                returncode=start_returncode,
                image=self.image,
                image_id=image_id,
                container_name=container_name,
                container_id=container_id,
            )
            restore_error._record_cleanup_evidence(
                attempted=True,
                verified=True,
                container_absence_verified=True,
                export_staging_removed=export_staging_removed,
            )
            if restore_error is exc:
                raise
            raise restore_error from exc
        return ExecutionResult(
            image=self.image,
            image_id=image_id,
            docker_executable=self.docker_executable,
            workspace=canonical,
            script=script,
            container_name=container_name,
            container_id=container_id,
            create_command=tuple(create_command),
            command=tuple(start_command),
            returncode=child_returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            cleanup_verified=True,
        )


__all__ = [
    "ExecutionResult",
    "IsolatedDockerPython",
    "IsolationError",
    "WorkspaceUsage",
    "validate_image_reference",
    "validate_workspace",
]
