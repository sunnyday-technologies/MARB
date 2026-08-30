"""Fail-closed verification for MARB's offline Debian native-library bundle.

The archive gate runs before dpkg.  The runtime gate runs after the exact
archives and Python wheels are installed.  Neither gate performs network I/O.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Sequence


LOCK_SCHEMA = "marb_native_deb_lock.v1"
EXPECTED_PLATFORM = "linux/amd64"
EXPECTED_DISTRIBUTION = "debian"
EXPECTED_VERSION_ID = "13"
EXPECTED_CODENAME = "trixie"
EXPECTED_PACKAGE_COUNT = 39
EXPECTED_TOTAL_BYTES = 48_570_480
EXPECTED_NATIVE_FILE_COUNT = 420
EXPECTED_RUNTIME_ROOT_COUNT = 142
EXPECTED_REACHABLE_NATIVE_FILE_COUNT = 412
EXPECTED_VENDORED_NATIVE_FILE_COUNT = 70
EXPECTED_NATIVE_PATH_VECTOR_BYTES = 19_579
EXPECTED_NATIVE_PATH_VECTOR_SHA256 = (
    "258feec8ba330fcc5a168f1d1b0efae8158bf8b92ce1c091aff9473672971a0f"
)
EXPECTED_ROOT_PACKAGES = ("libexpat1", "libgl1", "libx11-6", "libxrender1")
EXPECTED_MISSING_SONAMES = {
    "libGL.so.1": 48,
    "libX11.so.6": 63,
    "libXrender.so.1": 46,
    "libexpat.so.1": 17,
}
BOUNDARIES = {
    "cadquery-ocp": ("OCP/", "cadquery_ocp.libs/"),
    "vtk": ("vtkmodules/", "vtk.libs/"),
}
IMPORTS = ("OCP", "cadquery", "vtk")
EXPECTED_EXTENSION_SUFFIX = ".cpython-311-x86_64-linux-gnu.so"
EXPECTED_VTK_EXTENSION_ROOT = (
    f"vtkmodules/vtkRenderingOpenGL2{EXPECTED_EXTENSION_SUFFIX}"
)
EXPECTED_VTK_RENDERING_LIBRARY = "vtkmodules/libvtkRenderingOpenGL2-9.3.so"
EXPECTED_VTK_XCURSOR = "vtk.libs/libXcursor-1a09904e.so.1.0.2"
EXPECTED_VTK_XFIXES = "vtk.libs/libXfixes-d274cb03.so.3.1.0"
EXPECTED_VTK_XFIXES_SONAME = "libXfixes-d274cb03.so.3.1.0"
EXPECTED_NON_RUNTIME_MEMBERS = (
    "vtkmodules/libvtkTestingDataModel-9.3.so",
    "vtkmodules/libvtkTestingGenericBridge-9.3.so",
    "vtkmodules/libvtkTestingIOSQL-9.3.so",
    "vtkmodules/libvtkUtilitiesBenchmarks-9.3.so",
    "vtkmodules/libvtkWrappingTools-9.3.so",
    "vtkmodules/libvtkm_io-9.3.so",
    "vtkmodules/libvtkm_source-9.3.so",
    "vtkmodules/libvtkzfp-9.3.so",
)
EXPECTED_BOUNDARY_COUNTS = {
    "OCP/": 1,
    "cadquery_ocp.libs/": 68,
    "vtk.libs/": 2,
    "vtkmodules/": 349,
}
EXPECTED_RUNTIME_ENV = (
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
HEX64 = re.compile(r"[0-9a-f]{64}")
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*")
ARCHIVE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+~_-]*\.deb")
MANIFEST_LINE = re.compile(
    r"([0-9a-f]{64})  (native-debs/[A-Za-z0-9][A-Za-z0-9.+~_-]*\.deb)"
)
MISSING_LIBRARY = re.compile(r"\s*(\S+)\s+=>\s+not found\s*")
RESOLVED_LIBRARY = re.compile(
    r"\s*(\S+)\s+=>\s+(\S+)\s+\(0x[0-9A-Fa-f]+\)\s*"
)
SONAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
NATIVE_LIBRARY = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+-]*\.so(?:\.[0-9]+)*"
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


class NativeBundleError(RuntimeError):
    """A deterministic native-bundle verification failure."""

    def __init__(
        self,
        reason: str,
        diagnostics: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics


def _fail(reason: str, diagnostics: dict[str, list[str]] | None = None) -> None:
    raise NativeBundleError(reason, diagnostics)


@dataclass(frozen=True)
class NativeObject:
    """One normalized native member from an exact installed wheel."""

    relative_path: str
    path: Path
    distribution: str
    runtime_root: bool
    vendored: bool


@dataclass(frozen=True)
class NativeInventory:
    """Exact native members plus the isolated analysis lookup boundary."""

    objects: tuple[NativeObject, ...]
    analysis_search_path: tuple[Path, ...]
    native_directories: tuple[Path, ...]


@dataclass(frozen=True)
class LddObservation:
    """Safe loader facts retained without command output or host paths."""

    missing_sonames: tuple[str, ...]
    resolved_libraries: tuple[tuple[str, Path], ...]

    @property
    def resolved_paths(self) -> tuple[Path, ...]:
        return tuple(path for _soname, path in self.resolved_libraries)


def _exact_keys(value: Any, expected: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(reason)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_lf_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("native_lock_unreadable")
    if not raw.endswith(b"\n") or b"\r" in raw:
        _fail("native_lock_not_lf_terminated")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("native_lock_invalid_json")
    if not isinstance(value, dict):
        _fail("native_lock_not_object")
    return value


def load_lock(path: Path, expected_base_image: str) -> dict[str, Any]:
    lock = _exact_keys(
        _read_lf_json(path),
        {
            "schema",
            "base",
            "diagnosis",
            "resolution",
            "metadata_policy",
            "metadata_sources",
            "packages",
        },
        "native_lock_fields_mismatch",
    )
    if lock["schema"] != LOCK_SCHEMA:
        _fail("native_lock_schema_mismatch")

    base = _exact_keys(
        lock["base"],
        {
            "image",
            "platform",
            "distribution",
            "version_id",
            "codename",
            "installed_package_count",
        },
        "native_lock_base_fields_mismatch",
    )
    if base != {
        "image": expected_base_image,
        "platform": EXPECTED_PLATFORM,
        "distribution": EXPECTED_DISTRIBUTION,
        "version_id": EXPECTED_VERSION_ID,
        "codename": EXPECTED_CODENAME,
        "installed_package_count": 87,
    }:
        _fail("native_lock_base_mismatch")

    diagnosis = _exact_keys(
        lock["diagnosis"],
        {
            "boundary",
            "native_file_count",
            "files_with_unresolved_dependencies",
            "unresolved_soname_file_counts",
        },
        "native_lock_diagnosis_fields_mismatch",
    )
    if (
        diagnosis["boundary"]
        != ["cadquery==2.7.0", "cadquery-ocp==7.8.1.1.post1", "vtk==9.3.1"]
        or diagnosis["native_file_count"] != EXPECTED_NATIVE_FILE_COUNT
        or diagnosis["files_with_unresolved_dependencies"] != 66
        or diagnosis["unresolved_soname_file_counts"] != EXPECTED_MISSING_SONAMES
    ):
        _fail("native_lock_diagnosis_mismatch")

    resolution = _exact_keys(
        lock["resolution"],
        {
            "root_packages",
            "selected_package_count",
            "selected_total_bytes",
            "verified_dependency_groups",
            "unresolved_dependency_groups",
            "unsatisfied_after_resolution",
        },
        "native_lock_resolution_fields_mismatch",
    )
    if (
        resolution["root_packages"] != list(EXPECTED_ROOT_PACKAGES)
        or resolution["selected_package_count"] != EXPECTED_PACKAGE_COUNT
        or resolution["selected_total_bytes"] != EXPECTED_TOTAL_BYTES
        or resolution["verified_dependency_groups"] != 118
        or resolution["unresolved_dependency_groups"] != []
        or resolution["unsatisfied_after_resolution"] != []
    ):
        _fail("native_lock_resolution_mismatch")

    policy = _exact_keys(
        lock["metadata_policy"],
        {
            "retrieval_transport",
            "inrelease_signatures_verified_offline",
            "signature_verifier",
            "archive_keyring",
            "packages_index_sha256_matched_inrelease",
            "package_payloads_downloaded_during_resolution",
        },
        "native_lock_metadata_policy_fields_mismatch",
    )
    if (
        policy["retrieval_transport"] != "https"
        or policy["inrelease_signatures_verified_offline"] is not True
        or policy["packages_index_sha256_matched_inrelease"] is not True
        or policy["package_payloads_downloaded_during_resolution"] is not False
    ):
        _fail("native_lock_metadata_policy_mismatch")
    for key in ("signature_verifier", "archive_keyring"):
        identity = policy.get(key)
        if (
            not isinstance(identity, dict)
            or not isinstance(identity.get("path"), str)
            or not isinstance(identity.get("sha256"), str)
            or not HEX64.fullmatch(identity["sha256"])
        ):
            _fail("native_lock_metadata_identity_malformed")

    sources = lock["metadata_sources"]
    if not isinstance(sources, list) or [item.get("suite") for item in sources] != [
        "trixie",
        "trixie-updates",
        "trixie-security",
    ]:
        _fail("native_lock_metadata_sources_mismatch")
    source_names: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            _fail("native_lock_metadata_source_malformed")
        required = {
            "suite",
            "base_url",
            "codename",
            "inrelease_date",
            "inrelease_sha256",
            "inrelease_size",
            "packages_index_path",
            "packages_index_sha256",
            "packages_index_size",
            "signature_verification",
        }
        if not required.issubset(source) or not set(source).issubset(required | {"valid_until"}):
            _fail("native_lock_metadata_source_fields_mismatch")
        if (
            source["suite"] in source_names
            or source["signature_verification"] != "sqv_success"
            or not source["base_url"].startswith("https://")
            or not HEX64.fullmatch(source["inrelease_sha256"])
            or not HEX64.fullmatch(source["packages_index_sha256"])
            or not isinstance(source["inrelease_size"], int)
            or not isinstance(source["packages_index_size"], int)
        ):
            _fail("native_lock_metadata_source_malformed")
        source_names.add(source["suite"])

    packages = lock["packages"]
    if not isinstance(packages, list) or len(packages) != EXPECTED_PACKAGE_COUNT:
        _fail("native_lock_package_count_mismatch")
    names: list[str] = []
    basenames: list[str] = []
    total_bytes = 0
    for item in packages:
        package = _exact_keys(
            item,
            {
                "package",
                "version",
                "architecture",
                "suite",
                "filename",
                "size",
                "sha256",
                "pre_depends",
                "depends",
                "provides",
            },
            "native_lock_package_fields_mismatch",
        )
        path = PurePosixPath(str(package["filename"]))
        if (
            not isinstance(package["package"], str)
            or not PACKAGE.fullmatch(package["package"])
            or not isinstance(package["version"], str)
            or not package["version"]
            or package["architecture"] not in {"all", "amd64"}
            or package["suite"] not in source_names
            or path.is_absolute()
            or not path.parts
            or path.parts[0] != "pool"
            or any(part in {"", ".", ".."} for part in path.parts)
            or not ARCHIVE_BASENAME.fullmatch(path.name)
            or not isinstance(package["size"], int)
            or package["size"] <= 0
            or not isinstance(package["sha256"], str)
            or not HEX64.fullmatch(package["sha256"])
            or not all(
                isinstance(package[field], str)
                for field in ("pre_depends", "depends", "provides")
            )
        ):
            _fail("native_lock_package_malformed")
        names.append(package["package"])
        basenames.append(path.name)
        total_bytes += package["size"]
    if names != sorted(set(names)) or len(basenames) != len(set(basenames)):
        _fail("native_lock_packages_not_unique_sorted")
    if total_bytes != EXPECTED_TOTAL_BYTES:
        _fail("native_lock_total_bytes_mismatch")
    return lock


def expected_manifest_entries(lock: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted(
        (
            f"native-debs/{PurePosixPath(item['filename']).name}",
            item["sha256"],
        )
        for item in lock["packages"]
    )


def _read_manifest(path: Path) -> list[tuple[str, str]]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("native_manifest_unreadable")
    if not raw.endswith(b"\n") or b"\r" in raw:
        _fail("native_manifest_not_lf_terminated")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        _fail("native_manifest_not_ascii")
    matches = [MANIFEST_LINE.fullmatch(line) for line in lines]
    if not lines or not all(matches):
        _fail("native_manifest_malformed")
    entries = [(match.group(2), match.group(1)) for match in matches if match]
    if [path for path, _digest in entries] != sorted({path for path, _digest in entries}):
        _fail("native_manifest_paths_not_unique_sorted")
    return entries


def _run_text(arguments: Sequence[str], runner: Runner) -> str:
    result = runner(
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        _fail("native_metadata_command_failed")
    return result.stdout.rstrip("\n")


def verify_archives(
    lock_path: Path,
    bundle_root: Path,
    manifest_path: Path,
    expected_base_image: str,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, int | str]:
    lock = load_lock(lock_path, expected_base_image)
    expected_entries = expected_manifest_entries(lock)
    if _read_manifest(manifest_path) != expected_entries:
        _fail("native_manifest_lock_mismatch")
    try:
        root_stat = bundle_root.lstat()
        children = sorted(bundle_root.iterdir(), key=lambda item: item.name)
    except OSError:
        _fail("native_bundle_unreadable")
    if not stat.S_ISDIR(root_stat.st_mode) or bundle_root.is_symlink():
        _fail("native_bundle_not_plain_directory")
    expected_names = [PurePosixPath(item[0]).name for item in expected_entries]
    if [item.name for item in children] != expected_names:
        _fail("native_bundle_inventory_mismatch")

    packages_by_name = {
        PurePosixPath(item["filename"]).name: item for item in lock["packages"]
    }
    verified_bytes = 0
    for archive in children:
        try:
            archive_stat = archive.lstat()
        except OSError:
            _fail("native_archive_unreadable")
        if not stat.S_ISREG(archive_stat.st_mode) or archive.is_symlink():
            _fail("native_archive_not_plain_file")
        package = packages_by_name[archive.name]
        if archive_stat.st_size != package["size"]:
            _fail("native_archive_size_mismatch")
        if _sha256(archive) != package["sha256"]:
            _fail("native_archive_sha256_mismatch")
        observed = {
            field: _run_text(["dpkg-deb", "--field", str(archive), field], runner)
            for field in ("Package", "Version", "Architecture")
        }
        if observed != {
            "Package": package["package"],
            "Version": package["version"],
            "Architecture": package["architecture"],
        }:
            _fail("native_archive_control_metadata_mismatch")
        verified_bytes += archive_stat.st_size
    if verified_bytes != EXPECTED_TOTAL_BYTES:
        _fail("native_bundle_total_bytes_mismatch")
    return {
        "schema": "marb_native_archive_verification.v1",
        "package_count": len(children),
        "total_bytes": verified_bytes,
    }


def _normalize_member_path(item: object) -> str:
    raw = str(item)
    path = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        or "\\" in raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != raw
    ):
        _fail("native_runtime_member_path_malformed")
    return raw


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _plain_directory(path: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("native_runtime_directory_unreadable")
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        _fail("native_runtime_directory_not_plain")
    return resolved


def _validate_native_path_vector(paths: Sequence[str]) -> None:
    ordered = tuple(sorted(paths))
    try:
        raw = "".join(f"{path}\n" for path in ordered).encode("ascii")
    except UnicodeEncodeError:
        _fail("native_runtime_path_inventory_mismatch")
    if (
        len(raw) != EXPECTED_NATIVE_PATH_VECTOR_BYTES
        or hashlib.sha256(raw).hexdigest() != EXPECTED_NATIVE_PATH_VECTOR_SHA256
    ):
        _fail("native_runtime_path_inventory_mismatch")


def _verify_physical_native_directory(
    directory: Path,
    prefix: str,
    distribution_root: Path,
    expected: dict[str, Path],
    identities: set[tuple[int, int]],
) -> None:
    observed: dict[str, Path] = {}
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            _fail("native_runtime_directory_unreadable")
        for child in children:
            try:
                metadata = child.lstat()
            except OSError:
                _fail("native_runtime_member_unreadable")
            native_name = NATIVE_LIBRARY.fullmatch(child.name) is not None
            if stat.S_ISLNK(metadata.st_mode):
                _fail("native_runtime_directory_entry_linked")
            if stat.S_ISDIR(metadata.st_mode):
                if native_name:
                    _fail("native_runtime_member_not_plain_file")
                try:
                    resolved_directory = child.resolve(strict=True)
                except OSError:
                    _fail("native_runtime_directory_unreadable")
                if not _is_within(resolved_directory, distribution_root):
                    _fail("native_runtime_directory_escape")
                pending.append(resolved_directory)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                _fail("native_runtime_directory_entry_not_plain")
            if not native_name:
                continue
            if metadata.st_nlink != 1:
                _fail("native_runtime_member_link_count_mismatch")
            try:
                resolved = child.resolve(strict=True)
                nested = resolved.relative_to(directory).as_posix()
            except (OSError, ValueError):
                _fail("native_runtime_member_escape")
            if not _is_within(resolved, distribution_root):
                _fail("native_runtime_member_escape")
            relative = _normalize_member_path(f"{prefix}{nested}")
            identity = (metadata.st_dev, metadata.st_ino)
            if relative in observed or identity in identities:
                _fail("native_runtime_member_duplicate")
            observed[relative] = resolved
            identities.add(identity)
    if set(observed) != set(expected) or any(
        _path_identity(observed[path]) != _path_identity(expected[path])
        for path in observed
    ):
        _fail("native_runtime_physical_inventory_mismatch")


def _native_distribution_files() -> NativeInventory:
    native: dict[str, NativeObject] = {}
    targets: set[str] = set()
    directories: dict[str, Path] = {}
    distribution_roots: dict[str, Path] = {}
    boundary_counts = {prefix: 0 for prefix in EXPECTED_BOUNDARY_COUNTS}

    for distribution_name, (root_prefix, vendored_prefix) in BOUNDARIES.items():
        distribution = importlib.metadata.distribution(distribution_name)
        try:
            distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
        except OSError:
            _fail("native_runtime_distribution_unreadable")
        for prefix in (root_prefix, vendored_prefix):
            directory = _plain_directory(
                Path(distribution.locate_file(prefix.rstrip("/")))
            )
            if not _is_within(directory, distribution_root):
                _fail("native_runtime_directory_escape")
            directories[prefix] = directory
            distribution_roots[prefix] = distribution_root

        for item in distribution.files or []:
            relative = _normalize_member_path(item)
            name = PurePosixPath(relative).name
            if not NATIVE_LIBRARY.fullmatch(name):
                continue
            prefixes = tuple(
                prefix
                for prefix in (root_prefix, vendored_prefix)
                if relative.startswith(prefix)
            )
            if len(prefixes) != 1:
                _fail("native_runtime_member_outside_boundary")
            prefix = prefixes[0]
            if PurePosixPath(relative).parent.as_posix() != prefix.rstrip("/"):
                _fail("native_runtime_member_nested")
            path = Path(distribution.locate_file(item))
            try:
                metadata = path.lstat()
                resolved = path.resolve(strict=True)
            except OSError:
                _fail("native_runtime_member_unreadable")
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                _fail("native_runtime_member_not_plain_file")
            if resolved.parent != directories[prefix] or not _is_within(
                resolved, distribution_root
            ):
                _fail("native_runtime_member_escape")
            target = os.path.normcase(str(resolved))
            if relative in native or target in targets:
                _fail("native_runtime_member_duplicate")
            runtime_root = (
                relative == f"OCP/OCP{EXPECTED_EXTENSION_SUFFIX}"
                if distribution_name == "cadquery-ocp"
                else bool(
                    re.fullmatch(
                        rf"vtkmodules/vtk[A-Za-z0-9_]+{re.escape(EXPECTED_EXTENSION_SUFFIX)}",
                        relative,
                    )
                )
            )
            native[relative] = NativeObject(
                relative_path=relative,
                path=resolved,
                distribution=distribution_name,
                runtime_root=runtime_root,
                vendored=prefix == vendored_prefix,
            )
            targets.add(target)
            boundary_counts[prefix] += 1

    if boundary_counts != EXPECTED_BOUNDARY_COUNTS:
        _fail("native_runtime_boundary_inventory_mismatch")
    objects = tuple(native[path] for path in sorted(native))
    if len(objects) != EXPECTED_NATIVE_FILE_COUNT:
        _fail("native_runtime_file_count_mismatch")
    if sum(item.runtime_root for item in objects) != EXPECTED_RUNTIME_ROOT_COUNT:
        _fail("native_runtime_root_count_mismatch")
    if sum(item.vendored for item in objects) != EXPECTED_VENDORED_NATIVE_FILE_COUNT:
        _fail("native_runtime_vendored_count_mismatch")
    _validate_native_path_vector(tuple(native))
    physical_identities: set[tuple[int, int]] = set()
    for prefix, directory in directories.items():
        _verify_physical_native_directory(
            directory,
            prefix,
            distribution_roots[prefix],
            {
                item.relative_path: item.path
                for item in objects
                if item.relative_path.startswith(prefix)
            },
            physical_identities,
        )
    return NativeInventory(
        objects=objects,
        analysis_search_path=(
            directories["cadquery_ocp.libs/"],
            directories["vtk.libs/"],
        ),
        native_directories=tuple(
            directories[prefix]
            for prefix in ("OCP/", "cadquery_ocp.libs/", "vtk.libs/", "vtkmodules/")
        ),
    )


def _run_ldd(
    native_object: NativeObject,
    environment: dict[str, str],
    runner: Runner,
) -> LddObservation:
    result = runner(
        ["/usr/bin/ldd", str(native_object.path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
        env=environment,
    )
    missing: set[str] = set()
    resolved: set[tuple[str, Path]] = set()
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        missing_match = MISSING_LIBRARY.fullmatch(line)
        if missing_match:
            soname = missing_match.group(1)
            if not SONAME.fullmatch(soname):
                _fail("native_runtime_ldd_output_malformed")
            missing.add(soname)
            continue
        resolved_match = RESOLVED_LIBRARY.fullmatch(line)
        if resolved_match:
            soname = resolved_match.group(1)
            path = Path(resolved_match.group(2))
            if not SONAME.fullmatch(soname) or not path.is_absolute():
                _fail("native_runtime_ldd_output_malformed")
            resolved.add((soname, path))
    if result.returncode != 0 and not missing:
        _fail("native_runtime_ldd_failed")
    return LddObservation(tuple(sorted(missing)), tuple(sorted(resolved)))


def _missing_diagnostics(
    observations: Sequence[tuple[NativeObject, LddObservation]],
) -> dict[str, list[str]]:
    return {
        item.relative_path: list(observation.missing_sonames)
        for item, observation in sorted(
            observations, key=lambda pair: pair[0].relative_path
        )
        if observation.missing_sonames
    }


def _verify_bound_vtk_topology(
    inventory: NativeInventory,
    root_observations: Sequence[tuple[NativeObject, LddObservation]],
    analysis_observations: Sequence[tuple[NativeObject, LddObservation]],
) -> None:
    by_relative = {item.relative_path: item for item in inventory.objects}
    required = {
        EXPECTED_VTK_EXTENSION_ROOT,
        EXPECTED_VTK_RENDERING_LIBRARY,
        EXPECTED_VTK_XCURSOR,
        EXPECTED_VTK_XFIXES,
    }
    if not required.issubset(by_relative):
        _fail("native_runtime_vtk_topology_mismatch")
    root_observation = next(
        (
            observation
            for item, observation in root_observations
            if item.relative_path == EXPECTED_VTK_EXTENSION_ROOT
        ),
        None,
    )
    if root_observation is None:
        _fail("native_runtime_vtk_topology_mismatch")
    root_targets = {_path_identity(path) for path in root_observation.resolved_paths}
    if {
        _path_identity(by_relative[path].path)
        for path in (
            EXPECTED_VTK_RENDERING_LIBRARY,
            EXPECTED_VTK_XCURSOR,
            EXPECTED_VTK_XFIXES,
        )
    } - root_targets:
        _fail("native_runtime_vtk_topology_mismatch")
    xcursor_observation = next(
        (
            observation
            for item, observation in analysis_observations
            if item.relative_path == EXPECTED_VTK_XCURSOR
        ),
        None,
    )
    if xcursor_observation is None:
        _fail("native_runtime_vtk_topology_mismatch")
    providers = {
        _path_identity(path)
        for soname, path in xcursor_observation.resolved_libraries
        if soname == EXPECTED_VTK_XFIXES_SONAME
    }
    if providers != {_path_identity(by_relative[EXPECTED_VTK_XFIXES].path)}:
        _fail("native_runtime_vtk_topology_mismatch")


def verify_runtime(
    lock_path: Path,
    expected_base_image: str,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, int | str]:
    lock = load_lock(lock_path, expected_base_image)
    if _run_text(["dpkg", "--audit"], runner):
        _fail("native_dpkg_audit_failed")
    for package in lock["packages"]:
        observed = _run_text(
            [
                "dpkg-query",
                "-W",
                "-f=${db:Status-Abbrev}\\t${Version}\\t${Architecture}",
                package["package"],
            ],
            runner,
        ).split("\t")
        if observed != ["ii ", package["version"], package["architecture"]]:
            _fail("native_installed_package_mismatch")

    inventory = _native_distribution_files()
    if len(inventory.objects) != EXPECTED_NATIVE_FILE_COUNT:
        _fail("native_runtime_file_count_mismatch")
    environment = dict(item.split("=", 1) for item in EXPECTED_RUNTIME_ENV)
    objects_by_target = {
        _path_identity(item.path): item for item in inventory.objects
    }
    runtime_roots = tuple(item for item in inventory.objects if item.runtime_root)
    if len(runtime_roots) != EXPECTED_RUNTIME_ROOT_COUNT:
        _fail("native_runtime_root_count_mismatch")
    root_observations = tuple(
        (item, _run_ldd(item, dict(environment), runner)) for item in runtime_roots
    )
    root_unresolved = _missing_diagnostics(root_observations)

    analysis_environment = dict(environment)
    analysis_environment["LD_LIBRARY_PATH"] = ":".join(
        str(path) for path in inventory.analysis_search_path
    )
    vendored = tuple(item for item in inventory.objects if item.vendored)
    if (
        len(inventory.analysis_search_path) != 2
        or len(set(inventory.analysis_search_path)) != 2
    ):
        _fail("native_runtime_analysis_path_mismatch")
    if len(vendored) != EXPECTED_VENDORED_NATIVE_FILE_COUNT:
        _fail("native_runtime_vendored_count_mismatch")
    analysis_observations = tuple(
        (item, _run_ldd(item, dict(analysis_environment), runner))
        for item in vendored
    )
    analysis_unresolved = _missing_diagnostics(analysis_observations)
    unresolved = {
        path: diagnostics
        for path, diagnostics in sorted(
            {**root_unresolved, **analysis_unresolved}.items()
        )
    }
    if unresolved:
        _fail("native_runtime_unresolved_libraries", unresolved)
    _verify_bound_vtk_topology(
        inventory,
        root_observations,
        analysis_observations,
    )

    reachable = {item.relative_path for item in runtime_roots}
    for _root, observation in root_observations:
        for resolved_path in observation.resolved_paths:
            target = _path_identity(resolved_path)
            member = objects_by_target.get(target)
            if member is not None:
                reachable.add(member.relative_path)
                continue
            canonical = resolved_path.resolve(strict=False)
            if NATIVE_LIBRARY.fullmatch(canonical.name) and any(
                _is_within(canonical, directory)
                for directory in inventory.native_directories
            ):
                _fail("native_runtime_unknown_resolved_member")

    unreachable = tuple(
        item.relative_path
        for item in inventory.objects
        if item.relative_path not in reachable
    )
    if (
        len(reachable) != EXPECTED_REACHABLE_NATIVE_FILE_COUNT
        or unreachable != EXPECTED_NON_RUNTIME_MEMBERS
    ):
        _fail("native_runtime_reachability_mismatch")
    import_result = runner(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "\n".join(f"import {module}" for module in IMPORTS),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
        env=environment,
    )
    if import_result.returncode != 0:
        _fail("native_runtime_import_failed")
    return {
        "schema": "marb_native_runtime_verification.v1",
        "package_count": len(lock["packages"]),
        "native_file_count": len(inventory.objects),
        "runtime_root_count": len(runtime_roots),
        "reachable_native_file_count": len(reachable),
        "classified_non_runtime_file_count": len(unreachable),
        "analyzed_vendored_file_count": len(vendored),
        "unresolved_library_count": 0,
        "import_count": len(IMPORTS),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    archives = subparsers.add_parser("archives")
    archives.add_argument("--lock", type=Path, required=True)
    archives.add_argument("--bundle", type=Path, required=True)
    archives.add_argument("--manifest", type=Path, required=True)
    archives.add_argument("--expected-base-image", required=True)
    runtime = subparsers.add_parser("runtime")
    runtime.add_argument("--lock", type=Path, required=True)
    runtime.add_argument("--expected-base-image", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        if options.command == "archives":
            result = verify_archives(
                options.lock,
                options.bundle,
                options.manifest,
                options.expected_base_image,
            )
        else:
            result = verify_runtime(options.lock, options.expected_base_image)
    except NativeBundleError as exc:
        print(f"native_bundle_verification_failed:{exc}", file=sys.stderr)
        if exc.diagnostics is not None:
            print(
                "native_bundle_verification_diagnostics:"
                + json.dumps(
                    exc.diagnostics,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                file=sys.stderr,
            )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
