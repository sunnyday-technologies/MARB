"""Regression tests for the hash-bound offline native dependency boundary."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import io
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


def _completed(arguments, stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        arguments, returncode, stdout=stdout, stderr=stderr
    )


class NativeBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = native.load_lock(LOCK, BASE_IMAGE)
        self.by_archive = {
            Path(item["filename"]).name: item for item in self.lock["packages"]
        }
        self.runtime_calls = []
        self.runtime_inventory = self._make_runtime_inventory()
        self.runtime_by_target = {
            native._path_identity(item.path): item
            for item in self.runtime_inventory.objects
        }
        self.primary_root = next(
            item for item in self.runtime_inventory.objects if item.runtime_root
        )
        self.rendering_root = next(
            item
            for item in self.runtime_inventory.objects
            if item.relative_path == native.EXPECTED_VTK_EXTENSION_ROOT
        )

    def _make_runtime_inventory(self) -> native.NativeInventory:
        site = Path(os.path.abspath(os.sep)) / "marb-native-test-site"
        objects = [
            native.NativeObject(
                f"OCP/OCP{native.EXPECTED_EXTENSION_SUFFIX}",
                site / "OCP" / f"OCP{native.EXPECTED_EXTENSION_SUFFIX}",
                "cadquery-ocp",
                True,
                False,
            )
        ]
        vtk_root_names = [
            "vtkRenderingOpenGL2",
            *(f"vtkRoot{index:03d}" for index in range(1, 141)),
        ]
        objects.extend(
            native.NativeObject(
                f"vtkmodules/{name}{native.EXPECTED_EXTENSION_SUFFIX}",
                site
                / "vtkmodules"
                / f"{name}{native.EXPECTED_EXTENSION_SUFFIX}",
                "vtk",
                True,
                False,
            )
            for name in vtk_root_names
        )
        objects.extend(
            native.NativeObject(
                f"cadquery_ocp.libs/libocp-{index:03d}.so.1",
                site / "cadquery_ocp.libs" / f"libocp-{index:03d}.so.1",
                "cadquery-ocp",
                False,
                True,
            )
            for index in range(68)
        )
        objects.extend(
            (
                native.NativeObject(
                    native.EXPECTED_VTK_XCURSOR,
                    site / "vtk.libs" / Path(native.EXPECTED_VTK_XCURSOR).name,
                    "vtk",
                    False,
                    True,
                ),
                native.NativeObject(
                    native.EXPECTED_VTK_XFIXES,
                    site / "vtk.libs" / Path(native.EXPECTED_VTK_XFIXES).name,
                    "vtk",
                    False,
                    True,
                ),
            )
        )
        production_names = [
            Path(native.EXPECTED_VTK_RENDERING_LIBRARY).name,
            *(f"libvtkRuntime{index:03d}-9.3.so" for index in range(1, 200)),
        ]
        objects.extend(
            native.NativeObject(
                f"vtkmodules/{name}",
                site / "vtkmodules" / name,
                "vtk",
                False,
                False,
            )
            for name in production_names
        )
        objects.extend(
            native.NativeObject(
                relative,
                site.joinpath(*relative.split("/")),
                "vtk",
                False,
                False,
            )
            for relative in native.EXPECTED_NON_RUNTIME_MEMBERS
        )
        return native.NativeInventory(
            objects=tuple(sorted(objects, key=lambda item: item.relative_path)),
            analysis_search_path=(
                site / "cadquery_ocp.libs",
                site / "vtk.libs",
            ),
            native_directories=(
                site / "OCP",
                site / "cadquery_ocp.libs",
                site / "vtk.libs",
                site / "vtkmodules",
            ),
        )

    def _root_resolution_output(
        self, inventory: native.NativeInventory | None = None
    ) -> str:
        selected = inventory or self.runtime_inventory
        excluded = set(native.EXPECTED_NON_RUNTIME_MEMBERS)
        resolved = [
            item
            for item in selected.objects
            if not item.runtime_root and item.relative_path not in excluded
        ]
        return "".join(
            f"libresolved-{index:03d}.so => {item.path} (0x0000000000000000)\n"
            for index, item in enumerate(resolved)
        )

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
            item = self.runtime_by_target[native._path_identity(Path(arguments[1]))]
            if item.relative_path == self.rendering_root.relative_path:
                output = self._root_resolution_output()
            elif item.relative_path == native.EXPECTED_VTK_XCURSOR:
                xfixes = next(
                    (
                        member
                        for member in self.runtime_inventory.objects
                        if member.relative_path
                        == native.EXPECTED_VTK_XFIXES
                    ),
                    None,
                )
                expected_analysis_path = ":".join(
                    str(path) for path in self.runtime_inventory.analysis_search_path
                )
                if (
                    xfixes is not None
                    and kwargs["env"].get("LD_LIBRARY_PATH")
                    == expected_analysis_path
                ):
                    output = (
                        f"{native.EXPECTED_VTK_XFIXES_SONAME} => "
                        f"{xfixes.path} (0x0000000000000000)\n"
                    )
                else:
                    output = f"{native.EXPECTED_VTK_XFIXES_SONAME} => not found\n"
            else:
                output = ""
            return _completed(arguments, output)
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

    def test_exact_bound_native_path_vector_identity(self) -> None:
        self.assertEqual(native.EXPECTED_NATIVE_PATH_VECTOR_BYTES, 19_579)
        self.assertEqual(
            native.EXPECTED_NATIVE_PATH_VECTOR_SHA256,
            "258feec8ba330fcc5a168f1d1b0efae8158bf8b92ce1c091aff9473672971a0f",
        )

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
        with mock.patch.object(
            native,
            "_native_distribution_files",
            return_value=self.runtime_inventory,
        ):
            result = native.verify_runtime(
                LOCK,
                BASE_IMAGE,
                runner=self._runtime_runner,
            )
        self.assertEqual(result["native_file_count"], 420)
        self.assertEqual(result["runtime_root_count"], 142)
        self.assertEqual(result["reachable_native_file_count"], 412)
        self.assertEqual(result["classified_non_runtime_file_count"], 8)
        self.assertEqual(result["analyzed_vendored_file_count"], 70)
        self.assertEqual(result["unresolved_library_count"], 0)
        self.assertEqual(native.EXPECTED_RUNTIME_ENV, isolated_container.MINIMAL_ENV)
        expected_environment = dict(
            item.split("=", 1) for item in isolated_container.MINIMAL_ENV
        )
        root_calls = [
            (arguments, kwargs)
            for arguments, kwargs in self.runtime_calls
            if arguments[0] == "/usr/bin/ldd"
            and "LD_LIBRARY_PATH" not in kwargs["env"]
        ]
        self.assertEqual(len(root_calls), 142)
        self.assertTrue(
            all(kwargs["env"] == expected_environment for _arguments, kwargs in root_calls)
        )
        expected_analysis_environment = {
            **expected_environment,
            "LD_LIBRARY_PATH": ":".join(
                str(path) for path in self.runtime_inventory.analysis_search_path
            ),
        }
        analysis_calls = [
            (arguments, kwargs)
            for arguments, kwargs in self.runtime_calls
            if arguments[0] == "/usr/bin/ldd"
            and "LD_LIBRARY_PATH" in kwargs["env"]
        ]
        self.assertEqual(len(analysis_calls), 70)
        self.assertTrue(
            all(
                kwargs["env"] == expected_analysis_environment
                for _arguments, kwargs in analysis_calls
            )
        )
        import_calls = [
            (arguments, kwargs)
            for arguments, kwargs in self.runtime_calls
            if arguments[:4] == [sys.executable, "-I", "-B", "-c"]
        ]
        self.assertEqual(len(import_calls), 1)
        self.assertEqual(import_calls[0][1]["env"], expected_environment)

    def test_runtime_gate_clears_ambient_loader_overrides(self) -> None:
        with (
            mock.patch.object(
                native,
                "_native_distribution_files",
                return_value=self.runtime_inventory,
            ),
            mock.patch.dict(
                os.environ,
                {
                    "LD_LIBRARY_PATH": "/unapproved/cross-wheel-search",
                    "LD_PRELOAD": "/unapproved/preload.so",
                    "LD_AUDIT": "/unapproved/audit.so",
                },
            ),
        ):
            native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)

        expected_environment = dict(
            item.split("=", 1) for item in isolated_container.MINIMAL_ENV
        )
        clean_loader_calls = [
            kwargs["env"]
            for arguments, kwargs in self.runtime_calls
            if (
                arguments[:4] == [sys.executable, "-I", "-B", "-c"]
                or (
                    arguments[0] == "/usr/bin/ldd"
                    and "LD_LIBRARY_PATH" not in kwargs["env"]
                )
            )
        ]
        self.assertEqual(len(clean_loader_calls), 143)
        self.assertTrue(
            all(environment == expected_environment for environment in clean_loader_calls)
        )
        self.assertTrue(
            all(
                not any(key.startswith("LD_") for key in environment)
                for environment in clean_loader_calls
            )
        )
        analysis_environments = [
            kwargs["env"]
            for arguments, kwargs in self.runtime_calls
            if arguments[0] == "/usr/bin/ldd"
            and "LD_LIBRARY_PATH" in kwargs["env"]
        ]
        expected_analysis = {
            **expected_environment,
            "LD_LIBRARY_PATH": ":".join(
                str(path) for path in self.runtime_inventory.analysis_search_path
            ),
        }
        self.assertEqual(len(analysis_environments), 70)
        self.assertTrue(
            all(environment == expected_analysis for environment in analysis_environments)
        )
        self.assertTrue(
            all(
                "LD_PRELOAD" not in environment and "LD_AUDIT" not in environment
                for environment in analysis_environments
            )
        )

    def test_xcursor_uses_only_exact_ordered_analysis_path(self) -> None:
        with mock.patch.object(
            native,
            "_native_distribution_files",
            return_value=self.runtime_inventory,
        ):
            native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)

        xcursor = native.EXPECTED_VTK_XCURSOR
        xfixes = native.EXPECTED_VTK_XFIXES
        self.assertIn(xcursor, (item.relative_path for item in self.runtime_inventory.objects))
        self.assertIn(xfixes, (item.relative_path for item in self.runtime_inventory.objects))
        rendering_calls = [
            kwargs["env"]
            for arguments, kwargs in self.runtime_calls
            if arguments == ["/usr/bin/ldd", str(self.rendering_root.path)]
        ]
        self.assertEqual(len(rendering_calls), 1)
        self.assertNotIn("LD_LIBRARY_PATH", rendering_calls[0])
        xcursor_path = next(
            item.path
            for item in self.runtime_inventory.objects
            if item.relative_path == xcursor
        )
        calls = [
            kwargs["env"]
            for arguments, kwargs in self.runtime_calls
            if arguments == ["/usr/bin/ldd", str(xcursor_path)]
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["LD_LIBRARY_PATH"],
            ":".join(str(path) for path in self.runtime_inventory.analysis_search_path),
        )

    def test_runtime_gate_rejects_missing_or_renamed_xfixes_sibling(self) -> None:
        xfixes = native.EXPECTED_VTK_XFIXES
        missing_inventory = native.NativeInventory(
            objects=tuple(
                item
                for item in self.runtime_inventory.objects
                if item.relative_path != xfixes
            ),
            analysis_search_path=self.runtime_inventory.analysis_search_path,
            native_directories=self.runtime_inventory.native_directories,
        )
        with mock.patch.object(
            native,
            "_native_distribution_files",
            return_value=missing_inventory,
        ):
            with self.assertRaisesRegex(
                native.NativeBundleError, "native_runtime_file_count_mismatch"
            ):
                native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)

        original = next(
            item for item in self.runtime_inventory.objects if item.relative_path == xfixes
        )
        renamed = native.NativeObject(
            "vtk.libs/libXfixes-renamed.so.3.1.0",
            original.path.with_name("libXfixes-renamed.so.3.1.0"),
            original.distribution,
            original.runtime_root,
            original.vendored,
        )
        renamed_inventory = native.NativeInventory(
            objects=tuple(
                sorted(
                    (
                        renamed if item.relative_path == xfixes else item
                        for item in self.runtime_inventory.objects
                    ),
                    key=lambda item: item.relative_path,
                )
            ),
            analysis_search_path=self.runtime_inventory.analysis_search_path,
            native_directories=self.runtime_inventory.native_directories,
        )
        self.runtime_inventory = renamed_inventory
        self.runtime_by_target = {
            native._path_identity(item.path): item for item in renamed_inventory.objects
        }
        xcursor = native.EXPECTED_VTK_XCURSOR

        with mock.patch.object(
            native,
            "_native_distribution_files",
            return_value=renamed_inventory,
        ):
            with self.assertRaises(native.NativeBundleError) as raised:
                native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)
        self.assertEqual(str(raised.exception), "native_runtime_unresolved_libraries")
        self.assertEqual(
            raised.exception.diagnostics,
            {xcursor: [native.EXPECTED_VTK_XFIXES_SONAME]},
        )

    def test_runtime_diagnostics_are_sorted_safe_and_cli_stable(self) -> None:
        roots = [
            item for item in self.runtime_inventory.objects if item.runtime_root
        ][:2]
        xcursor = next(
            item
            for item in self.runtime_inventory.objects
            if item.relative_path == native.EXPECTED_VTK_XCURSOR
        )
        missing = {
            native._path_identity(roots[0].path): (
                "libz.so.2 => not found\nliba.so.1 => not found\n"
            ),
            native._path_identity(roots[1].path): "libm.so.3 => not found\n",
            native._path_identity(xcursor.path): (
                f"{native.EXPECTED_VTK_XFIXES_SONAME} => not found\n"
            ),
        }

        def runner(arguments, **kwargs):
            if arguments[0] == "/usr/bin/ldd":
                output = missing.get(native._path_identity(Path(arguments[1])))
                if output is not None:
                    self.runtime_calls.append((list(arguments), kwargs))
                    return _completed(arguments, output)
            return self._runtime_runner(arguments, **kwargs)

        with mock.patch.object(
            native,
            "_native_distribution_files",
            return_value=self.runtime_inventory,
        ):
            with self.assertRaises(native.NativeBundleError) as raised:
                native.verify_runtime(LOCK, BASE_IMAGE, runner=runner)
        expected = {
            roots[0].relative_path: ["liba.so.1", "libz.so.2"],
            roots[1].relative_path: ["libm.so.3"],
            xcursor.relative_path: [native.EXPECTED_VTK_XFIXES_SONAME],
        }
        self.assertEqual(raised.exception.diagnostics, expected)

        stderr = io.StringIO()
        with (
            mock.patch.object(
                native,
                "verify_runtime",
                side_effect=native.NativeBundleError(
                    "native_runtime_unresolved_libraries", expected
                ),
            ),
            redirect_stderr(stderr),
        ):
            exit_code = native.main(
                [
                    "runtime",
                    "--lock",
                    str(LOCK),
                    "--expected-base-image",
                    BASE_IMAGE,
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "native_bundle_verification_failed:native_runtime_unresolved_libraries\n"
            "native_bundle_verification_diagnostics:"
            + json.dumps(expected, sort_keys=True, separators=(",", ":"))
            + "\n",
        )
        self.assertNotIn(str(self.runtime_inventory.native_directories[0]), stderr.getvalue())

    def test_runtime_gate_rejects_unexpected_unreachable_member(self) -> None:
        replaced = native.EXPECTED_NON_RUNTIME_MEMBERS[0]
        original = next(
            item
            for item in self.runtime_inventory.objects
            if item.relative_path == replaced
        )
        unexpected = native.NativeObject(
            "vtkmodules/libvtkTestingUnexpected-9.3.so",
            original.path.with_name("libvtkTestingUnexpected-9.3.so"),
            original.distribution,
            False,
            False,
        )
        inventory = native.NativeInventory(
            objects=tuple(
                sorted(
                    (
                        unexpected if item.relative_path == replaced else item
                        for item in self.runtime_inventory.objects
                    ),
                    key=lambda item: item.relative_path,
                )
            ),
            analysis_search_path=self.runtime_inventory.analysis_search_path,
            native_directories=self.runtime_inventory.native_directories,
        )
        self.runtime_inventory = inventory
        self.runtime_by_target = {
            native._path_identity(item.path): item for item in inventory.objects
        }
        with mock.patch.object(
            native, "_native_distribution_files", return_value=inventory
        ):
            with self.assertRaisesRegex(
                native.NativeBundleError, "native_runtime_reachability_mismatch"
            ):
                native.verify_runtime(LOCK, BASE_IMAGE, runner=self._runtime_runner)

    def test_native_path_vector_rejects_reachable_production_rename(self) -> None:
        paths = [item.relative_path for item in self.runtime_inventory.objects]
        raw = "".join(f"{path}\n" for path in sorted(paths)).encode("ascii")
        with (
            mock.patch.object(native, "EXPECTED_NATIVE_PATH_VECTOR_BYTES", len(raw)),
            mock.patch.object(
                native,
                "EXPECTED_NATIVE_PATH_VECTOR_SHA256",
                hashlib.sha256(raw).hexdigest(),
            ),
        ):
            native._validate_native_path_vector(paths)
            renamed = [
                (
                    "vtkmodules/libvtkUnexpectedProduction-9.3.so"
                    if path == "vtkmodules/libvtkRuntime001-9.3.so"
                    else path
                )
                for path in paths
            ]
            with self.assertRaisesRegex(
                native.NativeBundleError,
                "native_runtime_path_inventory_mismatch",
            ):
                native._validate_native_path_vector(renamed)

    def test_physical_native_inventory_rejects_unexpected_and_linked_members(
        self,
    ) -> None:
        def make_boundary(root: Path) -> tuple[Path, Path, dict[str, Path]]:
            directory = root / "vtk.libs"
            directory.mkdir()
            provider = directory / "libXfixes-d274cb03.so.3.1.0"
            provider.write_bytes(b"provider")
            return (
                directory,
                provider,
                {"vtk.libs/libXfixes-d274cb03.so.3.1.0": provider.resolve()},
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory, _provider, expected = make_boundary(root)
            native._verify_physical_native_directory(
                directory, "vtk.libs/", root, expected, set()
            )

        for case in ("extra", "nested", "directory", "hardlink"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                directory, provider, expected = make_boundary(root)
                if case == "extra":
                    (directory / "libUnexpected.so.1").write_bytes(b"extra")
                elif case == "nested":
                    nested = directory / "nested"
                    nested.mkdir()
                    (nested / "libUnexpected.so.1").write_bytes(b"extra")
                elif case == "directory":
                    (directory / "libUnexpected.so.1").mkdir()
                else:
                    os.link(provider, directory / "libAlias.so.1")
                with self.assertRaises(native.NativeBundleError):
                    native._verify_physical_native_directory(
                        directory, "vtk.libs/", root, expected, set()
                    )

    def test_physical_native_inventory_rejects_symlinked_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "vtk.libs"
            directory.mkdir()
            provider = directory / "libXfixes-d274cb03.so.3.1.0"
            provider.write_bytes(b"provider")
            linked = directory / "libAlias.so.1"
            try:
                linked.symlink_to(provider.name)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            with self.assertRaisesRegex(
                native.NativeBundleError,
                "native_runtime_directory_entry_linked",
            ):
                native._verify_physical_native_directory(
                    directory,
                    "vtk.libs/",
                    root,
                    {"vtk.libs/libXfixes-d274cb03.so.3.1.0": provider.resolve()},
                    set(),
                )

    def test_runtime_member_paths_reject_malicious_or_noncanonical_forms(self) -> None:
        for value in (
            "../vtk.libs/libescape.so",
            "/absolute/libescape.so",
            "vtk.libs\\libescape.so",
            "vtk.libs//libescape.so",
            "./vtk.libs/libescape.so",
            "vtk.libs/libescape.so\x00ignored",
            "vtk.libs/libescape.so\nignored",
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(
                    native.NativeBundleError, "native_runtime_member_path_malformed"
                ):
                    native._normalize_member_path(value)

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
