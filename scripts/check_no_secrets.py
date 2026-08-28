#!/usr/bin/env python3
"""Fail closed on high-confidence secret material without printing values."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


RULES = (
    ("private-key-header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{40,255})\b")),
    ("openai-token", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


def _git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), stderr=subprocess.DEVNULL)


def _scan(path: str, data: bytes, source: str, findings: list[tuple[str, str, str]]) -> None:
    if b"\0" in data:
        return
    text = data.decode("utf-8", errors="ignore")
    for rule, pattern in RULES:
        if pattern.search(text):
            findings.append((path, rule, source))


def _tree_findings() -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    paths = [p for p in _git("ls-files", "-z").decode().split("\0") if p]
    for path in paths:
        candidate = Path(path)
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        _scan(path, data, "working-tree", findings)
    return findings


def _range_findings(revision_args: list[str]) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    commits = [c for c in _git("rev-list", "--reverse", *revision_args).decode().splitlines() if c]
    for commit in commits:
        names = _git(
            "diff-tree", "--root", "--first-parent", "--no-commit-id",
            "--name-only", "-r", "-z", commit
        ).decode().split("\0")
        for path in (name for name in names if name):
            try:
                data = _git("show", f"{commit}:{path}")
            except subprocess.CalledProcessError:
                continue
            _scan(path, data, commit[:12], findings)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--tree", action="store_true", help="scan tracked working-tree files")
    modes.add_argument("--range", nargs="+", metavar="REV", help="scan files in every selected commit")
    args = parser.parse_args(argv)
    findings = _tree_findings() if args.tree else _range_findings(args.range)
    if findings:
        print("BLOCKED: secret-like material detected (values suppressed):")
        for path, rule, source in sorted(set(findings)):
            print(f"  {path} [{rule}; {source}]")
        return 1
    print("Secret guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
