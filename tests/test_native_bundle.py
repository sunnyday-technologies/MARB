"""Regression tests for the hash-bound offline native dependency boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from harness import isolated_container
from harness.container import verify_native_bundle as native


REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "harness" / "container" / "native-debs.lock.json"
BASE_IMAGE = (
    "python@sha256:9a7765b36773a37061455b332f18e265"
    "e7f58f6fea9c419a550d2a8b0e9db834"
)


def _completed(arguments, stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(arguments, returncode, stdout=stdout, stderr="")


class NativeBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = native.load_lock(LOCK, BASE_IMAGE)
        self.by_archive = {
            Path(item["filename"]).name: item for item in self.lock["packages"]
        }
        self.runtime_calls = []

    def _manifest_text(self) -> str:
        return "".join(
            f"{digest}  {path}\n"
            for path, digest in native.expected_manifest_entries(self.lock)
        )

    def _archive_runner(self, arguments, **_kwargs):
        self.assertEqual(arguments[:2], ["dpkg-deb", "--field"])
        package = self.by_archive[Path(arguments[2]).name]
        value = {
            "Package": package["package"],
            "Version": package["version"],
            "Architecture": package["architecture"],
        }[arguments[3]]
        return _completed(arguments, value + "\n")

    def _runtime_runner(self, arguments, **kwargs):
        self.runtime_calls.append((list(arguments), kwargs))
        if arguments == ["dpkg", "--audit"]:
            return _completed(arguments)
        if arguments[0] == "dpkg-query":
            package = next(
                item for item in self.lock["packages"] if item["package"] == arguments[-1]
            )
            return _completed(
                arguments,
                f"ii \t{package['version']}\t{package['architecture']}",
            )
        if arguments[0] == "/usr/bin/ldd":
            return _completed(arguments, "linux-vdso.so.1 =>  (0x0000000000000000)\n")
        self.assertEqual(arguments[:4], [sys.executable, "-I", "-B", "-c"])
        self.assertEqual(arguments[4], "import OCP\nimport cadquery\nimport vtk")
        return _completed(arguments)

    def test_lock_records_complete_signed_resolution_without_payload_download(self) -> None:
        self.assertEqual(self.lock["schema"], native.LOCK_SCHEMA)
        self.assertEqual(len(self.lock["packages"]), native.EXPECTED_PACKAGE_COUNT)
        self.assertEqual(
            sum(item["size"] for item in self.lock["packages"]),
            native.EXPECTED_TOTAL_BYTES,
        )
        self.assertEqual(self.lock["resolution"]["verified_dependency_groups"], 118)
        self.assertEqual(self.lock["resolution"]["unresolved_dependency_groups"], [])
        self.assertEqual(self.lock["resolution"]["unsatisfied_after_resolution"], [])
        policy = self.lock["metadata_policy"]
        self.assertIs(policy["inrelease_signatures_verified_offline"], True)
        self.assertIs(policy["packages_index_sha256_matched_inrelease"], True)
        self.assertIs(policy["package_payloads_downloaded_during_resolution"], False)

    def test_lock_rejects_changed_resolution(self) -> None:
        changed = json.loads(LOCK.read_text(encoding="utf-8"))
        changed["resolution"]["unsatisfied_after_resolution"] = ["unexpected"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(changed) + "\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(
                native.NativeBundleError, "native_lock_resolution_mismatch"
            ):
                native.load_lock(path, BASE_IMAGE)

    def test_archive_gate_accepts_only_exact_inventory_size_hash_and_control_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "native-debs"
            bundle.mkdir()
            manifest = root / "native-debs.sha256"
            manifest.write_text(self._manifest_text(), encoding="ascii", newline="\n")
            for name, item in self.by_archive.items():
                with (bundle / name).open("wb") as handle:
                    handle.truncate(item["size"])
            with mock.patch.object(
                native,
                "_sha256",
                side_effect=lambda path: self.by_archive[path.name]["sha256"],
            ):
                result = native.verify_archives(
                    LOCK,
                    bundle,
                    manifest,
                    BASE_IMAGE,
                    runner=self._archive_runner,
                )
            self.assertEqual(result["package_count"], 39)
            self.assertEqual(result["total_bytes"], native.EXPECTED_TOTAL_BYTES)

    def test_archive_gate_rejects_extra_archive_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "native-debs"
            bundle.mkdir()
            (bundle / "unexpected_1_amd64.deb").write_bytes(b"not a package")
            manifest = root / "native-debs.sha256"
            manifest.write_text(self._manifest_text(), encoding="ascii", newline="\n")
            with self.assertRaisesRegex(
                native.NativeBundleError, "native_bundle_inventory_mismatch"
            ):
                native.verify_archives(
                    LOCK,
                    bundle,
                    manifest,
                    BASE_IMAGE,
                    runner=self._archive_runner,
                )

    def test_runtime_gate_checks_exact_dpkg_ldd_and_import_boundaries(self) -> None:
        native_files = [Path(f"/site/OCP/native_{index}.so") for index in range(420)]
        with mock.patch.object(
            native, "_native_distribution_files", return_value=native_files
        ):
            result = native.verify_runtime(
                LOCK,
                BASE_IMAGE,
                runner=self._runtime_runner,
            )
        self.assertEqual(result["native_file_count"], 420)
        self.assertEqual(result["unresolved_library_count"], 0)
        self.assertEqual(native.EXPECTED_RUNTIME_ENV, isolated_container.MINIMAL_ENV)
        expected_environment = dict(
            item.split("=", 1) for item in isolated_container.MINIMAL_ENV
        )
        ldd_calls = [
            (arguments, kwargs)
            for arguments, kwargs in self.runtime_calls
            if arguments[0] == "/usr/bin/ldd"
        ]
        self.assertEqual(len(ldd_calls), 420)
        self.assertTrue(
            all(kwargs["env"] == expected_environment for _arguments, kwargs in ldd_calls)
        )
        import_calls = [
            (arguments, kwargs)
            for arguments, kwargs in self.runtime_calls
            if arguments[:4] == [sys.executable, "-I", "-B", "-c"]
        ]
        self.assertEqual(len(import_calls), 1)
        self.assertEqual(import_calls[0][1]["env"], expected_environment)

    def test_runtime_gate_clears_ambient_loader_overrides(self) -> None:
        native_files = [Path(f"/site/OCP/native_{index}.so") for index in range(420)]
        with (
            mock.patch.object(native, "_native_distribution_files", return_value=native_files),
            mock.patch.dict(
                os.environ,
                {
                    "LD_LIBRARY_PATH": "/unapproved/cross-wheel-search",
                    "LD_PRELOAD": "/unapproved/preload.so",
                },
            ),
        ):
            native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)

        expected_environment = dict(
            item.split("=", 1) for item in isolated_container.MINIMAL_ENV
        )
        loader_calls = [
            kwargs["env"]
            for arguments, kwargs in self.runtime_calls
            if arguments[0] == "/usr/bin/ldd"
            or arguments[:4] == [sys.executable, "-I", "-B", "-c"]
        ]
        self.assertEqual(len(loader_calls), 421)
        self.assertTrue(all(environment == expected_environment for environment in loader_calls))
        self.assertTrue(
            all(
                "LD_LIBRARY_PATH" not in environment and "LD_PRELOAD" not in environment
                for environment in loader_calls
            )
        )

    def test_runtime_gate_rejects_unresolved_soname(self) -> None:
        native_files = [Path(f"/site/OCP/native_{index}.so") for index in range(420)]

        def runner(arguments, **kwargs):
            result = self._runtime_runner(arguments, **kwargs)
            if arguments[0] == "/usr/bin/ldd":
                return _completed(arguments, "libGL.so.1 => not found\n")
            return result

        with mock.patch.object(
            native, "_native_distribution_files", return_value=native_files
        ):
            with self.assertRaisesRegex(
                native.NativeBundleError, "native_runtime_unresolved_libraries"
            ):
                native.verify_runtime(LOCK, BASE_IMAGE, runner=runner)

    def test_runtime_gate_rejects_incomplete_dpkg_state(self) -> None:
        def runner(arguments, **kwargs):
            if arguments == ["dpkg", "--audit"]:
                return _completed(arguments, "package requires configuration\n")
            return self._runtime_runner(arguments, **kwargs)

        with self.assertRaisesRegex(
            native.NativeBundleError, "native_dpkg_audit_failed"
        ):
            native.verify_runtime(LOCK, BASE_IMAGE, runner=runner)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
