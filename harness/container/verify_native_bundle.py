"""Fail-closed verification for MARB's offline Debian native-library bundle.

The archive gate runs before dpkg.  The runtime gate runs after the exact
archives and Python wheels are installed.  Neither gate performs network I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
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
EXPECTED_ROOT_PACKAGES = ("libexpat1", "libgl1", "libx11-6", "libxrender1")
EXPECTED_MISSING_SONAMES = {
    "libGL.so.1": 48,
    "libX11.so.6": 63,
    "libXrender.so.1": 46,
    "libexpat.so.1": 17,
}
BOUNDARIES = {
    "cadquery-ocp": ("OCP/", "cadquery_ocp.libs/"),
    "vtk": ("vtk.libs/", "vtkmodules/"),
}
IMPORTS = ("OCP", "cadquery", "vtk")
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
NATIVE_LIBRARY = re.compile(r".+\.so(?:\.[0-9]+)*")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class NativeBundleError(RuntimeError):
    """A deterministic native-bundle verification failure."""


def _fail(reason: str) -> None:
    raise NativeBundleError(reason)


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


def _native_distribution_files() -> list[Path]:
    native: dict[str, Path] = {}
    for distribution_name, prefixes in BOUNDARIES.items():
        distribution = importlib.metadata.distribution(distribution_name)
        for item in distribution.files or []:
            relative = str(item).replace("\\", "/")
            if not relative.startswith(prefixes) or not NATIVE_LIBRARY.fullmatch(
                PurePosixPath(relative).name
            ):
                continue
            path = Path(distribution.locate_file(item))
            native[str(path)] = path
    return [native[key] for key in sorted(native)]


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

    native_files = _native_distribution_files()
    if len(native_files) != EXPECTED_NATIVE_FILE_COUNT:
        _fail("native_runtime_file_count_mismatch")
    environment = dict(item.split("=", 1) for item in EXPECTED_RUNTIME_ENV)
    unresolved: set[str] = set()
    for path in native_files:
        result = runner(
            ["/usr/bin/ldd", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=30,
            env=environment,
        )
        if result.returncode != 0:
            _fail("native_runtime_ldd_failed")
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            match = MISSING_LIBRARY.fullmatch(line)
            if match:
                unresolved.add(match.group(1))
    if unresolved:
        _fail("native_runtime_unresolved_libraries")
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
        "native_file_count": len(native_files),
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
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
