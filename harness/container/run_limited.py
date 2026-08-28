#!/usr/local/bin/python3
"""Run one model-authored script in a capped tmpfs and export it safely."""
from __future__ import annotations

import io
import json
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath


HOST_INPUT = Path("/marb-host-workspace")
WORKSPACE = Path("/workspace")
EXPORT = Path("/marb-export/workspace.tar")
MAX_ENTRIES = 4_096
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEPTH = 32
MAX_PATH_BYTES = 1_024
POLL_SECONDS = 0.01
QUIESCENCE_SWEEPS = 32
QUIESCENCE_EMPTY_READBACKS = 3
LIMIT_EXIT = 125
SAFE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ /+-]{0,1023}\Z")
STATUS_MEMBER = "MARB_EXECUTION_STATUS.json"


class LimitViolation(RuntimeError):
    pass


def _relative(root: Path, candidate: Path) -> str:
    try:
        value = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise LimitViolation("path escaped workspace") from exc
    pure = PurePosixPath(value)
    if (
        not value
        or not SAFE_PATH.fullmatch(value)
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
        or len(pure.parts) > MAX_DEPTH
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise LimitViolation("workspace path is unsafe")
    return value


def inventory(root: Path, *, exclude_kit: bool) -> list[tuple[str, Path, os.stat_result]]:
    entries: list[tuple[str, Path, os.stat_result]] = []
    total = 0

    def walk_error(_error: OSError) -> None:
        raise LimitViolation("workspace cannot be inventoried")

    for current, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        if exclude_kit and current_path == root and "kit" in directories:
            directories.remove("kit")
        for name in [*directories, *files]:
            candidate = current_path / name
            relative = _relative(root, candidate)
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise LimitViolation("workspace changed during inventory") from exc
            if stat.S_ISLNK(info.st_mode):
                raise LimitViolation("workspace contains a link")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise LimitViolation("workspace contains a special object")
            if stat.S_ISREG(info.st_mode):
                if getattr(info, "st_nlink", 1) != 1:
                    raise LimitViolation("workspace contains a hard link")
                total += info.st_size
                if info.st_size > MAX_FILE_BYTES:
                    raise LimitViolation("workspace per-file limit exceeded")
                if total > MAX_TOTAL_BYTES:
                    raise LimitViolation("workspace aggregate limit exceeded")
            entries.append((relative, candidate, info))
            if len(entries) > MAX_ENTRIES:
                raise LimitViolation("workspace entry-count limit exceeded")
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    return entries


def _assert_ustar_member_name(name: str) -> None:
    """Reject names the deterministic USTAR writer cannot represent."""
    try:
        probe = tarfile.TarInfo(name)
        probe.tobuf(format=tarfile.USTAR_FORMAT, encoding="ascii", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise LimitViolation("workspace path is not representable in USTAR") from exc


def _copy_initial_workspace() -> None:
    for relative, source, info in inventory(HOST_INPUT, exclude_kit=False):
        target = WORKSPACE.joinpath(*PurePosixPath(relative).parts)
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o700, exist_ok=False)
        else:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with source.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            target.chmod(0o600)
    inventory(WORKSPACE, exclude_kit=True)


def _other_container_pids() -> list[int]:
    own_pid = os.getpid()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError as exc:
        raise LimitViolation("container process readback failed") from exc
    return sorted(
        int(entry.name)
        for entry in entries
        if entry.name.isdecimal() and int(entry.name) not in {1, own_pid}
    )


def _kill_processes(process: subprocess.Popen[bytes]) -> None:
    """Kill all untrusted processes and prove a stable, quiescent PID namespace."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        raise LimitViolation("model process could not be reaped") from exc

    empty_readbacks = 0
    for _attempt in range(QUIESCENCE_SWEEPS):
        pids = _other_container_pids()
        if not pids:
            empty_readbacks += 1
            if empty_readbacks >= QUIESCENCE_EMPTY_READBACKS:
                return
        else:
            empty_readbacks = 0
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
        time.sleep(POLL_SECONDS)
    raise LimitViolation("container process quiescence could not be proven")


def _export_workspace(returncode: int) -> None:
    entries = inventory(WORKSPACE, exclude_kit=True)
    if any(relative.casefold() == STATUS_MEMBER.casefold() for relative, _path, _info in entries):
        raise LimitViolation("workspace used a reserved export identity")
    _assert_ustar_member_name(STATUS_MEMBER)
    for relative, _source, info in entries:
        _assert_ustar_member_name(
            relative + ("/" if stat.S_ISDIR(info.st_mode) else "")
        )
    try:
        with tarfile.open(EXPORT, "w", format=tarfile.USTAR_FORMAT) as archive:
            status_raw = (
                json.dumps(
                    {"schema": "marb_container_execution_status.v1", "returncode": returncode},
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
                + b"\n"
            )
            status_item = tarfile.TarInfo(STATUS_MEMBER)
            status_item.uid = 65532
            status_item.gid = 65532
            status_item.uname = ""
            status_item.gname = ""
            status_item.mtime = 0
            status_item.mode = 0o600
            status_item.size = len(status_raw)
            archive.addfile(status_item, io.BytesIO(status_raw))
            for relative, source, info in entries:
                item = tarfile.TarInfo(relative + ("/" if stat.S_ISDIR(info.st_mode) else ""))
                item.uid = 65532
                item.gid = 65532
                item.uname = ""
                item.gname = ""
                item.mtime = 0
                if stat.S_ISDIR(info.st_mode):
                    item.type = tarfile.DIRTYPE
                    item.mode = 0o700
                    archive.addfile(item)
                else:
                    item.size = info.st_size
                    item.mode = 0o600
                    with source.open("rb") as handle:
                        archive.addfile(item, handle)
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise LimitViolation("workspace export failed") from exc


def run(relative_script: str) -> int:
    try:
        if os.getpid() != 1:
            raise LimitViolation("workspace limiter must be container PID 1")
        script_rel = _relative(WORKSPACE, WORKSPACE / relative_script)
        _copy_initial_workspace()
        script = WORKSPACE.joinpath(*PurePosixPath(script_rel).parts)
        if not script.is_file() or script.suffix != ".py":
            raise LimitViolation("unsafe invocation")
    except (OSError, LimitViolation):
        return LIMIT_EXIT

    def child_limits() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))

    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(script)],
        cwd=WORKSPACE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=child_limits,
    )
    try:
        while process.poll() is None:
            inventory(WORKSPACE, exclude_kit=True)
            time.sleep(POLL_SECONDS)
        returncode = int(process.returncode)
        _kill_processes(process)
        inventory(WORKSPACE, exclude_kit=True)
        _export_workspace(returncode)
        return 0
    except (OSError, LimitViolation):
        try:
            _kill_processes(process)
        except (OSError, LimitViolation):
            pass
        return LIMIT_EXIT
    except BaseException:
        _kill_processes(process)
        raise


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if len(values) != 1:
        return LIMIT_EXIT
    return run(values[0])


if __name__ == "__main__":
    raise SystemExit(main())
