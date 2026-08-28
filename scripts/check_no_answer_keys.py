#!/usr/bin/env python3
"""Block answer-key paths, raw blobs, LFS pointers, and identity removal."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REGISTRY_PATH = "results/marb_runs.json"
PROTECTED_TASKS = ("L2-RESOLVE", "L4-ECO")
PROTECTED_FIELDS = ("answer_key_step_sha256", "answer_key_spec_sha256")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_PATH_RE = re.compile(
    r"(^|/)[^/]*reference[^/]*\.step$"
    r"|(^|/)[^/]*reference[^/]*assembly[^/]*\.ya?ml$"
    r"|(^|/)[^/]*reference[^/]*(layout|scene)[^/]*\.(ya?ml|json)$"
    r"|(^|/)ph[0-9]+_reference"
    r"|(^|/)[^/]*leak_signature[^/]*\.ya?ml$",
    re.IGNORECASE,
)


class GuardConfigurationError(RuntimeError):
    """Raised when the guard cannot safely inspect requested identities."""


def _git(*args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GuardConfigurationError("a required Git object could not be inspected") from exc
    return completed.stdout


def _registry_at(revision: str) -> dict:
    payload = _git("show", f"{revision}:{REGISTRY_PATH}")
    try:
        registry = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardConfigurationError("the published digest registry is invalid") from exc
    if not isinstance(registry, dict):
        raise GuardConfigurationError("the published digest registry is invalid")
    return registry


def _protected_identities(registry: dict, *, allow_empty: bool = False) -> set[str]:
    tasks = registry.get("tasks")
    if not isinstance(tasks, dict):
        raise GuardConfigurationError("the published digest registry has no task map")
    identities: set[str] = set()
    for task_id in PROTECTED_TASKS:
        task = tasks.get(task_id)
        if task is None:
            continue
        if not isinstance(task, dict):
            raise GuardConfigurationError("published answer-key metadata is invalid")
        values = [task.get(field) for field in PROTECTED_FIELDS]
        if any(value is None for value in values):
            raise GuardConfigurationError("published answer-key metadata is incomplete")
        if not all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in values):
            raise GuardConfigurationError("published answer-key metadata is malformed")
        identities.update(values)
    if not identities and not allow_empty:
        raise GuardConfigurationError("no published private-key identities were found")
    return identities


def _preserve_identities(trusted: set[str], candidate: set[str]) -> None:
    if not trusted.issubset(candidate):
        raise PermissionError(
            "published answer-key identities may be added but not removed or replaced"
        )


def _parse_index_records(payload: bytes) -> Iterable[tuple[str, str]]:
    for record in payload.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            _, oid, _ = metadata.decode("ascii").split(" ", 2)
            path = os.fsdecode(raw_path)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GuardConfigurationError("Git index enumeration was malformed") from exc
        yield oid, path


def _parse_tree_records(payload: bytes) -> Iterable[tuple[str, str]]:
    for record in payload.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            _, object_type, oid = metadata.decode("ascii").split(" ", 2)
            path = os.fsdecode(raw_path)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GuardConfigurationError("Git tree enumeration was malformed") from exc
        if object_type == "blob":
            yield oid, path


def _safe_display(path: str) -> str:
    return json.dumps(path, ensure_ascii=True)


def _scan_payload(
    payload: bytes,
    protected: set[str],
    path: str,
    source: str,
    findings: set[tuple[str, str, str]],
) -> None:
    if hashlib.sha256(payload).hexdigest() in protected:
        findings.add((path, "raw-digest-match", source))
        return
    prefix = b"oid sha256:"
    for line in payload.splitlines():
        if line.startswith(prefix):
            candidate = line[len(prefix):].decode("ascii", errors="ignore")
            if candidate in protected:
                findings.add((path, "lfs-oid-match", source))
                return


def _scan_path(path: str, source: str, findings: set[tuple[str, str, str]]) -> None:
    if KEY_PATH_RE.search(path.replace("\\", "/")):
        findings.add((path, "blocked-path", source))


def _tree_mode(root: Path) -> set[tuple[str, str, str]]:
    trusted = _protected_identities(_registry_at("HEAD"))
    candidate = _protected_identities(_registry_at(""), allow_empty=True)
    _preserve_identities(trusted, candidate)
    protected = trusted | candidate
    findings: set[tuple[str, str, str]] = set()

    records = list(_parse_index_records(_git("ls-files", "-s", "-z")))
    for oid, path in records:
        _scan_path(path, "index", findings)
        _scan_payload(_git("cat-file", "blob", oid), protected, path, "index", findings)
    for _, path in records:
        working = root / path
        if working.is_symlink() or not working.is_file():
            continue
        try:
            payload = working.read_bytes()
        except OSError as exc:
            raise GuardConfigurationError("a tracked working-tree file could not be inspected") from exc
        _scan_path(path, "working-tree", findings)
        _scan_payload(payload, protected, path, "working-tree", findings)
    return findings


def _range_mode(rev_args: list[str]) -> set[tuple[str, str, str]]:
    commits = _git("rev-list", "--reverse", *rev_args).decode("ascii").split()
    if not commits:
        raise GuardConfigurationError("the commit range is empty")
    protected: set[str] = set()
    for commit in commits:
        candidate = _protected_identities(_registry_at(commit), allow_empty=True)
        parents = _git("show", "-s", "--format=%P", commit).decode("ascii").split()
        for parent in parents:
            trusted = _protected_identities(_registry_at(parent))
            _preserve_identities(trusted, candidate)
            protected.update(trusted)
        protected.update(candidate)

    findings: set[tuple[str, str, str]] = set()
    for commit in commits:
        source = commit[:12]
        for oid, path in _parse_tree_records(_git("ls-tree", "-r", "-z", commit)):
            _scan_path(path, source, findings)
            _scan_payload(_git("cat-file", "blob", oid), protected, path, source, findings)
    return findings


def _stdin_mode() -> set[tuple[str, str, str]]:
    findings: set[tuple[str, str, str]] = set()
    for path in sys.stdin.read().splitlines():
        _scan_path(path, "stdin", findings)
    return findings


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--tree"
    try:
        root = Path(_git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if mode == "--tree" and len(argv) <= 1:
            findings = _tree_mode(root)
        elif mode == "--range" and len(argv) >= 2:
            findings = _range_mode(argv[1:])
        elif mode == "--stdin" and len(argv) == 1:
            findings = _stdin_mode()
        else:
            print("usage: check_no_answer_keys.sh [--tree | --range <rev-args...> | --stdin]", file=sys.stderr)
            return 64
    except PermissionError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    except GuardConfigurationError as exc:
        print(f"answer-key guard: {exc}", file=sys.stderr)
        return 64

    if findings:
        print("BLOCKED: answer-key path or content identity detected:", file=sys.stderr)
        for path, match, source in sorted(findings):
            print(f"    {_safe_display(path)} [{match}; {source}]", file=sys.stderr)
        print("No answer-key values were printed. Remove the matching content.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
