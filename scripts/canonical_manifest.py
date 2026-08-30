#!/usr/bin/env python3
"""Create and verify canonical SHA-256 manifests for host-side OCI inputs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata
from typing import Iterable, Sequence


SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHA256_INPUT_RE = re.compile(r"[0-9a-fA-F]{64}")
PAYLOAD_BASENAME_RE = {
    ".whl": re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.whl"),
    ".deb": re.compile(r"[A-Za-z0-9][A-Za-z0-9.+~_-]*\.deb"),
}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

PROFILE_OUTPUTS = {
    "wheelhouse": "wheelhouse.sha256",
    "native-debs": "native-debs.sha256",
    "build-context": "build-context.sha256",
}
BUILD_CONTEXT_FILES = (
    "Dockerfile",
    ".dockerignore",
    "requirements.lock",
    "run_limited.py",
    "runtime-contract.v0.13.json",
    "cadclaw-calibration.fad0dd55.json",
    "wheelhouse.sha256",
    "native-debs.lock.json",
    "verify_native_bundle.py",
    "native-debs.sha256",
)


class ManifestError(RuntimeError):
    """Raised when an input cannot be represented or verified canonically."""


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    sha256: str
    size: int = 0


@dataclass(frozen=True)
class ManifestDocument:
    entries: tuple[ManifestEntry, ...]
    raw: bytes
    payload_bytes: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def normalize_relative_posix_path(value: str) -> str:
    """Require one portable, normalized, relative POSIX path."""

    if not isinstance(value, str) or not value:
        raise ManifestError("manifest path must be a non-empty string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ManifestError("manifest path must be valid UTF-8") from exc
    if not value.isascii():
        raise ManifestError("manifest path must use portable ASCII characters")
    if unicodedata.normalize("NFC", value) != value:
        raise ManifestError("manifest path must use NFC normalization")
    if value.startswith("/") or "\\" in value:
        raise ManifestError("manifest path must be relative POSIX syntax")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ManifestError("manifest path contains a control character")

    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ManifestError("manifest path contains an empty or traversal segment")
    for part in parts:
        if part != part.strip() or part.endswith((".", " ")):
            raise ManifestError("manifest path contains unsafe whitespace or suffixes")
        if ":" in part:
            raise ManifestError("manifest path contains a drive or stream separator")
        if any(character in '<>"|?*' for character in part):
            raise ManifestError("manifest path contains a non-portable Windows character")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ManifestError("manifest path contains a reserved portable name")
    return "/".join(parts)


def _normalize_digest(value: str) -> str:
    if not isinstance(value, str) or SHA256_INPUT_RE.fullmatch(value) is None:
        raise ManifestError("manifest digest must be exactly 64 hexadecimal characters")
    return value.lower()


def canonicalize_entries(entries: Iterable[ManifestEntry]) -> tuple[ManifestEntry, ...]:
    """Normalize, de-duplicate, and path-sort entries by UTF-8 byte order."""

    normalized: list[ManifestEntry] = []
    exact_paths: set[str] = set()
    portable_paths: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, ManifestEntry):
            raise ManifestError("manifest entries must be ManifestEntry values")
        path = normalize_relative_posix_path(entry.path)
        digest = _normalize_digest(entry.sha256)
        if not isinstance(entry.size, int) or isinstance(entry.size, bool) or entry.size < 0:
            raise ManifestError("manifest entry size must be a non-negative integer")
        if path in exact_paths:
            raise ManifestError("manifest contains a duplicate path")
        portable = path.casefold()
        if portable in portable_paths:
            raise ManifestError("manifest contains a case-colliding portable path")
        exact_paths.add(path)
        portable_paths[portable] = path
        normalized.append(ManifestEntry(path, digest, entry.size))
    if not normalized:
        raise ManifestError("manifest must contain at least one entry")
    return tuple(sorted(normalized, key=lambda item: item.path.encode("utf-8")))


def build_manifest(entries: Iterable[ManifestEntry]) -> ManifestDocument:
    """Render the one canonical byte representation used by preview and write."""

    canonical = canonicalize_entries(entries)
    raw = b"".join(
        item.sha256.encode("ascii")
        + b"  "
        + item.path.encode("utf-8")
        + b"\n"
        for item in canonical
    )
    return ManifestDocument(canonical, raw, sum(item.size for item in canonical))


def parse_manifest_bytes(raw: bytes) -> tuple[ManifestEntry, ...]:
    """Strictly parse an existing canonical manifest without touching payloads."""

    if not isinstance(raw, bytes) or not raw:
        raise ManifestError("manifest is empty")
    if not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw:
        raise ManifestError("manifest must be NUL-free and LF-terminated without CR")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest must be valid UTF-8") from exc

    parsed: list[ManifestEntry] = []
    for line in text[:-1].split("\n"):
        if len(line) < 67 or line[64:66] != "  ":
            raise ManifestError("manifest line must use lowercase SHA-256, two spaces, and path")
        digest, path = line[:64], line[66:]
        if SHA256_RE.fullmatch(digest) is None:
            raise ManifestError("manifest digest must be lowercase SHA-256")
        parsed.append(ManifestEntry(path, digest))

    canonical = canonicalize_entries(parsed)
    if tuple(parsed) != canonical:
        raise ManifestError("manifest paths are not in ordinal UTF-8 order")
    if build_manifest(parsed).raw != raw:
        raise ManifestError("manifest bytes are not canonical")
    return canonical


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & marker)


def _require_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"{label} is unavailable") from exc
    if path.is_symlink() or _is_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ManifestError(f"{label} must be a real directory")
    return metadata


def _require_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or _is_reparse_point(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise ManifestError(f"{label} must be a non-symlink regular file")
    return metadata


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _object_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _lexical_absolute_root(root: Path | str) -> Path:
    supplied = Path(root)
    if ".." in supplied.parts:
        raise ManifestError("manifest root cannot contain traversal components")
    try:
        absolute = Path(os.path.abspath(os.fspath(supplied)))
    except (OSError, ValueError) as exc:
        raise ManifestError("manifest root cannot be made absolute") from exc
    if not absolute.is_absolute() or not absolute.anchor:
        raise ManifestError("manifest root must resolve to an absolute path")
    return absolute


def _root_chain_snapshot(root: Path) -> tuple[tuple[int, int], ...]:
    """Pin every supplied root component without following links/reparse points."""

    if not root.is_absolute() or not root.anchor:
        raise ManifestError("manifest root must be absolute")
    current = Path(root.anchor)
    candidates = [current]
    for part in root.parts[1:]:
        current = current / part
        candidates.append(current)

    snapshot: list[tuple[int, int]] = []
    for candidate in candidates:
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise ManifestError("manifest root or an ancestor is unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise ManifestError(
                "manifest root and every ancestor must be real directories"
            )
        snapshot.append(_object_identity(metadata))
    return tuple(snapshot)


def _require_unchanged_root(
    root: Path, expected: tuple[tuple[int, int], ...]
) -> None:
    if _root_chain_snapshot(root) != expected:
        raise ManifestError("manifest root or an ancestor changed during operation")


def _root_chain_is_current(
    root: Path, expected: tuple[tuple[int, int], ...]
) -> bool:
    try:
        return _root_chain_snapshot(root) == expected
    except ManifestError:
        return False


def _unlink_owned_file(path: Path, expected: tuple[int, int]) -> bool:
    """Unlink only the exact regular-file object created by this process."""

    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or _object_identity(metadata) != expected
        ):
            return False
        path.unlink()
        return True
    except OSError:
        return False


def _inventory_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (*_stable_identity(metadata), metadata.st_ctime_ns)


def _resolve_beneath(root: Path, relative: str, *, directory: bool) -> Path:
    normalized = normalize_relative_posix_path(relative)
    candidate = root
    parts = normalized.split("/")
    for index, part in enumerate(parts):
        candidate = candidate / part
        final = index == len(parts) - 1
        if final and not directory:
            _require_regular_file(candidate, f"input {normalized}")
        else:
            _require_directory(candidate, f"path component {part}")
    return candidate


def _hash_regular_file(path: Path, label: str) -> tuple[str, int]:
    before = _require_regular_file(path, label)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _stable_identity(opened) != _stable_identity(before):
                raise ManifestError(f"{label} changed before hashing")
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
            after_handle = os.fstat(handle.fileno())
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(f"{label} could not be hashed") from exc
    after_path = _require_regular_file(path, label)
    identity = _stable_identity(before)
    if _stable_identity(after_handle) != identity or _stable_identity(after_path) != identity:
        raise ManifestError(f"{label} changed while hashing")
    return digest.hexdigest(), before.st_size


def _read_regular_file(path: Path, label: str) -> bytes:
    before = _require_regular_file(path, label)
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if _stable_identity(opened) != _stable_identity(before):
                raise ManifestError(f"{label} changed before readback")
            raw = handle.read()
            after_handle = os.fstat(handle.fileno())
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(f"{label} could not be read") from exc
    after_path = _require_regular_file(path, label)
    identity = _stable_identity(before)
    if _stable_identity(after_handle) != identity or _stable_identity(after_path) != identity:
        raise ManifestError(f"{label} changed during readback")
    return raw


def _validated_root(root: Path | str) -> Path:
    absolute = _lexical_absolute_root(root)
    before = _root_chain_snapshot(absolute)
    _require_unchanged_root(absolute, before)
    return absolute


def _file_entry(root: Path, relative: str) -> ManifestEntry:
    candidate = _resolve_beneath(root, relative, directory=False)
    digest, size = _hash_regular_file(candidate, f"input {relative}")
    return ManifestEntry(relative, digest, size)


def _portable_directory_children(
    directory: Path, label: str
) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(directory) as scan:
            children = list(scan)
    except OSError as exc:
        raise ManifestError(f"{label} cannot be enumerated") from exc
    for child in children:
        try:
            normalize_relative_posix_path(child.name)
        except ManifestError as exc:
            raise ManifestError(f"{label} contains an unsafe entry name") from exc
    return sorted(children, key=lambda item: item.name.encode("utf-8"))


def _directory_snapshot(
    root: Path, relative: str, suffix: str
) -> tuple[tuple[str, tuple[int, int, int, int, int]], ...]:
    directory = _resolve_beneath(root, relative, directory=True)
    children = _portable_directory_children(directory, f"input directory {relative}")
    if not children:
        raise ManifestError(f"input directory {relative} is empty")
    snapshot: list[tuple[str, tuple[int, int, int, int, int]]] = []
    for child in children:
        child_relative = normalize_relative_posix_path(f"{relative}/{child.name}")
        expected_name = PAYLOAD_BASENAME_RE.get(suffix)
        if expected_name is None or expected_name.fullmatch(child.name) is None:
            raise ManifestError(
                f"input directory {relative} contains a non-file or unexpected entry"
            )
        candidate = _resolve_beneath(root, child_relative, directory=False)
        metadata = _require_regular_file(candidate, f"input {child_relative}")
        snapshot.append((child_relative, _inventory_identity(metadata)))
    return tuple(snapshot)


def _directory_entries_with_snapshot(
    root: Path, relative: str, suffix: str
) -> tuple[
    list[ManifestEntry], tuple[tuple[str, tuple[int, int, int, int, int]], ...]
]:
    before = _directory_snapshot(root, relative, suffix)
    entries = [_file_entry(root, path) for path, _identity in before]
    if _directory_snapshot(root, relative, suffix) != before:
        raise ManifestError(f"input directory {relative} changed while hashing")
    return entries, before


def _directory_entries(root: Path, relative: str, suffix: str) -> list[ManifestEntry]:
    entries, _snapshot = _directory_entries_with_snapshot(root, relative, suffix)
    return entries


def _build_context_snapshot(
    root: Path,
) -> tuple[tuple[str, str, tuple[int, int, int, int, int]], ...]:
    required = set(BUILD_CONTEXT_FILES) | {"wheelhouse", "native-debs"}
    allowed = required | {PROFILE_OUTPUTS["build-context"]}
    children = _portable_directory_children(root, "build context")
    actual = {item.name for item in children}
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise ManifestError("build context has missing or unexpected top-level entries")
    snapshot: list[tuple[str, str, tuple[int, int, int, int, int]]] = []
    for child in children:
        normalized = normalize_relative_posix_path(child.name)
        if normalized in {"wheelhouse", "native-debs"}:
            candidate = _resolve_beneath(root, normalized, directory=True)
            metadata = _require_directory(candidate, f"input directory {normalized}")
            kind = "directory"
        else:
            candidate = _resolve_beneath(root, normalized, directory=False)
            metadata = _require_regular_file(candidate, f"input {normalized}")
            kind = "file"
        snapshot.append((normalized, kind, _inventory_identity(metadata)))
    return tuple(snapshot)


def _entry_vector(entries: Iterable[ManifestEntry]) -> tuple[tuple[str, str], ...]:
    return tuple((entry.path, entry.sha256) for entry in canonicalize_entries(entries))


def _verify_child_manifest(
    root: Path, relative: str, expected_entries: Iterable[ManifestEntry]
) -> None:
    manifest_path = _resolve_beneath(root, relative, directory=False)
    parsed = parse_manifest_bytes(_read_regular_file(manifest_path, relative))
    if _entry_vector(parsed) != _entry_vector(expected_entries):
        raise ManifestError(f"{relative} does not match its payload files")


def _verify_native_lock(root: Path, actual_entries: Iterable[ManifestEntry]) -> None:
    lock_path = _resolve_beneath(root, "native-debs.lock.json", directory=False)
    raw = _read_regular_file(lock_path, "native dependency lock")
    try:
        lock = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("native dependency lock is invalid JSON") from exc
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, list) or not packages:
        raise ManifestError("native dependency lock has no package inventory")
    expected: list[ManifestEntry] = []
    for package in packages:
        if not isinstance(package, dict):
            raise ManifestError("native dependency lock package is malformed")
        filename = package.get("filename")
        digest = package.get("sha256")
        size = package.get("size")
        if not isinstance(filename, str):
            raise ManifestError("native dependency lock filename is malformed")
        normalized = normalize_relative_posix_path(filename)
        basename = normalized.rsplit("/", 1)[-1]
        expected.append(ManifestEntry(f"native-debs/{basename}", digest, size))
    canonical_expected = canonicalize_entries(expected)
    canonical_actual = canonicalize_entries(actual_entries)
    if canonical_expected != canonical_actual:
        raise ManifestError("native dependency lock does not match payload files")


def collect_profile(root: Path | str, profile: str) -> ManifestDocument:
    """Hash exactly one supported host-side input profile."""

    canonical_root = _validated_root(root)
    root_snapshot = _root_chain_snapshot(canonical_root)
    if profile == "wheelhouse":
        entries = _directory_entries(canonical_root, "wheelhouse", ".whl")
    elif profile == "native-debs":
        entries, native_snapshot = _directory_entries_with_snapshot(
            canonical_root, "native-debs", ".deb"
        )
        _verify_native_lock(canonical_root, entries)
        if _directory_snapshot(canonical_root, "native-debs", ".deb") != native_snapshot:
            raise ManifestError("input directory native-debs changed during lock verification")
    elif profile == "build-context":
        before = _build_context_snapshot(canonical_root)
        wheel_entries, wheel_snapshot = _directory_entries_with_snapshot(
            canonical_root, "wheelhouse", ".whl"
        )
        native_entries, native_snapshot = _directory_entries_with_snapshot(
            canonical_root, "native-debs", ".deb"
        )
        _verify_child_manifest(canonical_root, "wheelhouse.sha256", wheel_entries)
        _verify_child_manifest(canonical_root, "native-debs.sha256", native_entries)
        _verify_native_lock(canonical_root, native_entries)
        entries = [_file_entry(canonical_root, path) for path in BUILD_CONTEXT_FILES]
        entries.extend(wheel_entries)
        entries.extend(native_entries)
        if _directory_snapshot(canonical_root, "wheelhouse", ".whl") != wheel_snapshot:
            raise ManifestError("input directory wheelhouse changed during context planning")
        if _directory_snapshot(canonical_root, "native-debs", ".deb") != native_snapshot:
            raise ManifestError("input directory native-debs changed during context planning")
        if _build_context_snapshot(canonical_root) != before:
            raise ManifestError("build context inventory changed while hashing")
    else:
        raise ManifestError("unknown manifest profile")
    document = build_manifest(entries)
    _require_unchanged_root(canonical_root, root_snapshot)
    return document


def _readback(
    root: Path,
    profile: str,
    *,
    approved_raw: bytes | None = None,
    approved_sha256: str | None = None,
    published_identity: tuple[int, int] | None = None,
) -> ManifestDocument:
    root_snapshot = _root_chain_snapshot(root)
    expected = collect_profile(root, profile)
    output_relative = PROFILE_OUTPUTS[profile]
    output = _resolve_beneath(root, output_relative, directory=False)
    output_metadata = _require_regular_file(output, f"{profile} manifest")
    if (
        published_identity is not None
        and _object_identity(output_metadata) != published_identity
    ):
        raise ManifestError("published manifest was replaced before readback")
    raw = _read_regular_file(output, f"{profile} manifest")
    output_metadata = _require_regular_file(output, f"{profile} manifest")
    if (
        published_identity is not None
        and _object_identity(output_metadata) != published_identity
    ):
        raise ManifestError("published manifest was replaced during readback")
    parse_manifest_bytes(raw)
    if raw != expected.raw:
        raise ManifestError("manifest readback does not match the current input files")
    if approved_raw is not None and raw != approved_raw:
        raise ManifestError("manifest readback differs from the reviewed preview bytes")
    if approved_sha256 is not None:
        approved_digest = _normalize_digest(approved_sha256)
        if hashlib.sha256(raw).hexdigest() != approved_digest:
            raise ManifestError("manifest readback differs from the reviewed preview digest")
    _require_unchanged_root(root, root_snapshot)
    return expected


def _write_no_clobber(
    root: Path, profile: str, raw: bytes
) -> tuple[Path, tuple[int, int], tuple[tuple[int, int], ...]]:
    root_snapshot = _root_chain_snapshot(root)
    output_relative = normalize_relative_posix_path(PROFILE_OUTPUTS[profile])
    output = root / output_relative
    if output.exists() or output.is_symlink():
        raise ManifestError(f"{profile} manifest output already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    created_identity: tuple[int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            created_identity = _object_identity(os.fstat(handle.fileno()))
        _require_unchanged_root(root, root_snapshot)
        os.link(temporary, output)
        _require_unchanged_root(root, root_snapshot)
        if not _unlink_owned_file(temporary, created_identity):
            raise ManifestError(f"{profile} manifest temporary file was replaced")
        metadata = _require_regular_file(output, f"{profile} manifest output")
        if _object_identity(metadata) != created_identity:
            raise ManifestError(f"{profile} manifest output was replaced during publication")
        _require_unchanged_root(root, root_snapshot)
        return output, created_identity, root_snapshot
    except BaseException:
        if created_identity is not None and _root_chain_is_current(root, root_snapshot):
            _unlink_owned_file(temporary, created_identity)
            _unlink_owned_file(output, created_identity)
        raise


def _summary(document: ManifestDocument, profile: str, mode: str) -> dict[str, object]:
    return {
        "entry_count": len(document.entries),
        "manifest": PROFILE_OUTPUTS[profile],
        "manifest_bytes": len(document.raw),
        "manifest_sha256": document.sha256,
        "mode": mode,
        "payload_bytes": document.payload_bytes,
        "profile": profile,
        "total_bytes_including_manifest": document.payload_bytes + len(document.raw),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "write", "readback"))
    parser.add_argument("profile", choices=tuple(PROFILE_OUTPUTS))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--expected-sha256",
        help="required write binding copied from the reviewed preview",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        root = _validated_root(options.root)
        if options.mode == "write":
            expected_digest = _normalize_digest(options.expected_sha256)
            preview = collect_profile(root, options.profile)
            if preview.sha256 != expected_digest:
                raise ManifestError("reviewed preview digest does not match current inputs")
            output, published_identity, published_root_snapshot = _write_no_clobber(
                root, options.profile, preview.raw
            )
            try:
                document = _readback(
                    root,
                    options.profile,
                    approved_raw=preview.raw,
                    approved_sha256=expected_digest,
                    published_identity=published_identity,
                )
            except BaseException:
                if _root_chain_is_current(root, published_root_snapshot):
                    _unlink_owned_file(output, published_identity)
                raise
        elif options.mode == "readback":
            if options.expected_sha256 is not None:
                raise ManifestError("readback does not accept --expected-sha256")
            document = _readback(root, options.profile)
        else:
            if options.expected_sha256 is not None:
                raise ManifestError("preview does not accept --expected-sha256")
            document = collect_profile(root, options.profile)
        print(json.dumps(_summary(document, options.profile, options.mode), sort_keys=True))
        return 0
    except (ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
