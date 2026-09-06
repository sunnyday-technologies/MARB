"""Fake-only tests for the non-qualifying container policy probe."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
import hashlib
import importlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from harness import container_policy_probe as probe
from harness import runtime_smoke_probes as probes


IMAGE = "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "a" * 64
REVISION = "b" * 40
TREE = "c" * 40


class FakeEngine:
    def __init__(
        self, image: str, docker_executable: str, input_root: Path
    ) -> None:
        self.image = image
        self.docker_executable = docker_executable
        self.input_root = input_root
        self.calls = 0

    def probe_policy_readback(
        self,
        workspace: Path,
        relative_script: str,
        *,
        inspect_timeout: float | None = 30.0,
        execution_timeout: float | None = 60.0,
    ) -> object:
        self.calls += 1
        if (workspace / relative_script).read_bytes() != probes.CASES[0].source:
            raise AssertionError("positive probe bytes changed")
        for relative, raw in probes.RUNTIME_SMOKE_INPUTS:
            if self.input_root.joinpath(*relative.split("/")).read_bytes() != raw:
                raise AssertionError("positive input bytes changed")
        if inspect_timeout != 30.0 or execution_timeout != 60.0:
            raise AssertionError("policy probe timeout contract changed")
        return types.SimpleNamespace(
            image=self.image,
            image_id="sha256:" + "e" * 64,
            docker_executable=self.docker_executable,
            workspace=workspace,
            script=relative_script,
            container_name="marb-isolated-" + "1" * 32,
            container_id="1" * 64,
            create_command=("docker.exe", "create"),
            policy_readback_verified=True,
            cleanup_verified=True,
            container_absence_verified=True,
            export_staging_removed=True,
        )


class ContainerPolicyProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-policy-probe-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.docker = self.root / "docker.exe"
        self.docker.write_bytes(b"synthetic Docker executable\n")
        self.docker_sha256 = hashlib.sha256(self.docker.read_bytes()).hexdigest()
        self.git = self.root / "git.exe"
        self.git.write_bytes(b"synthetic Git executable\n")
        self.git_sha256 = hashlib.sha256(self.git.read_bytes()).hexdigest()
        self.engines: list[FakeEngine] = []
        self.source_calls = 0

    def factory(
        self, image: str, docker_executable: str, input_root: Path
    ) -> FakeEngine:
        engine = FakeEngine(image, docker_executable, input_root)
        self.engines.append(engine)
        return engine

    def source_verifier(
        self,
        source_root,
        revision,
        tree,
        git_executable,
        git_sha256,
        source_manifest,
    ):
        self.source_calls += 1
        self.assertEqual(Path(source_root), self.root)
        self.assertEqual(revision, REVISION)
        self.assertEqual(tree, TREE)
        self.assertEqual(git_executable, str(self.git))
        self.assertEqual(git_sha256, self.git_sha256)
        return {
            "clean_tracked_checkout": True,
            "git_executable_sha256": self.git_sha256,
            "revision": REVISION,
            "source_manifest_sha256": source_manifest["manifest_sha256"],
            "tree": TREE,
        }

    def run_probe(self, factory=None) -> dict[str, object]:
        return probe.run_policy_probe(
            image=IMAGE,
            docker_executable=str(self.docker),
            docker_executable_sha256=self.docker_sha256,
            git_executable=str(self.git),
            git_executable_sha256=self.git_sha256,
            source_revision=REVISION,
            source_root=self.root,
            source_tree=TREE,
            work_root=self.root,
            engine_factory=factory or self.factory,
            source_verifier=self.source_verifier,
        )

    def test_positive_layout_is_inspected_once_and_result_is_non_qualifying(self) -> None:
        payload = self.run_probe()
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(self.engines[0].calls, 1)
        self.assertEqual(self.source_calls, 2)
        self.assertEqual(
            payload,
            {
                "bindings": {
                    "docker_executable_sha256": self.docker_sha256,
                    "git_executable_sha256": self.git_sha256,
                    "image_repo_digest": IMAGE,
                    "source_manifest_sha256": probe._source_manifest()[
                        "manifest_sha256"
                    ],
                    "source_revision": REVISION,
                    "source_tree": TREE,
                },
                "checks": {
                    "container_absence_verified": True,
                    "docker_executable_stable": True,
                    "export_staging_removed": True,
                    "host_layout_unchanged": True,
                    "inert_container_cleanup_verified": True,
                    "policy_readback_verified": True,
                    "runtime_binding_exact": True,
                },
                "execution_policy": {
                    "benchmark_executed": False,
                    "container_started": False,
                    "model_invoked": False,
                    "provider_invoked": False,
                    "qualification_attempt_consumed": False,
                },
                "non_qualifying": True,
                "probe": probe.PROBE_ID,
                "qualification_eligible": False,
                "schema": probe.SCHEMA,
                "status": "diagnostic_pass",
            },
        )

    def test_docker_executable_is_verified_before_and_after(self) -> None:
        identity = (1, 2, 3, 4, 5, 6, self.docker_sha256)
        with mock.patch.object(
            probe, "_verify_docker_executable", side_effect=(identity, identity)
        ) as verifier:
            self.run_probe()
        self.assertEqual(verifier.call_count, 2)

    def test_source_is_verified_before_and_after(self) -> None:
        self.run_probe()
        self.assertEqual(self.source_calls, 2)

    def test_source_postflight_drift_rejects_diagnostic(self) -> None:
        def drifting_verifier(*args):
            proof = self.source_verifier(*args)
            if self.source_calls == 2:
                proof["tree"] = "d" * 40
            return proof

        with self.assertRaisesRegex(
            probe.PolicyProbeError, "source changed during diagnostic"
        ):
            probe.run_policy_probe(
                image=IMAGE,
                docker_executable=str(self.docker),
                docker_executable_sha256=self.docker_sha256,
                git_executable=str(self.git),
                git_executable_sha256=self.git_sha256,
                source_revision=REVISION,
                source_root=self.root,
                source_tree=TREE,
                work_root=self.root,
                engine_factory=self.factory,
                source_verifier=drifting_verifier,
            )

    def test_default_source_verifier_binds_head_tree_and_raw_blobs(self) -> None:
        repository = Path(probe.__file__).resolve().parents[1]
        manifest = probe._source_manifest()
        identities = iter((REVISION, REVISION, TREE, ""))

        def capture(*args, **kwargs):
            return next(identities)

        def committed(_authorization, root, revision, public_path):
            self.assertEqual(root, repository)
            self.assertEqual(revision, REVISION)
            return repository.joinpath(*public_path.split("/")).read_bytes()

        with mock.patch.object(probe, "_capture_git_identity", side_effect=capture):
            with mock.patch.object(
                probe.executor,
                "_read_committed_blob_with_git",
                side_effect=committed,
            ) as reader:
                with mock.patch.object(
                    probe.executor,
                    "_verify_host_git_executable",
                    return_value={"path": str(self.git), "sha256": self.git_sha256},
                ):
                    proof = probe._verify_source_checkout(
                        repository,
                        REVISION,
                        TREE,
                        str(self.git),
                        self.git_sha256,
                        manifest,
                    )
        self.assertTrue(proof["clean_tracked_checkout"])
        self.assertEqual(proof["revision"], REVISION)
        self.assertEqual(proof["tree"], TREE)
        self.assertEqual(
            proof["source_manifest_sha256"], manifest["manifest_sha256"]
        )
        self.assertEqual(reader.call_count, len(probe.SOURCE_FILES))
        self.assertTrue(
            all(item["hash_mode"] == "raw" for item in manifest["entries"])
        )

    def test_default_source_verifier_rejects_committed_blob_drift(self) -> None:
        repository = Path(probe.__file__).resolve().parents[1]
        manifest = probe._source_manifest()
        identities = iter((REVISION, REVISION, TREE, ""))

        def committed(_authorization, root, _revision, public_path):
            raw = root.joinpath(*public_path.split("/")).read_bytes()
            return raw + (b"drift" if public_path == probe.SOURCE_FILES[0] else b"")

        with mock.patch.object(
            probe, "_capture_git_identity", side_effect=lambda *a, **k: next(identities)
        ):
            with mock.patch.object(
                probe.executor,
                "_read_committed_blob_with_git",
                side_effect=committed,
            ):
                with self.assertRaisesRegex(
                    probe.PolicyProbeError, "differs from committed HEAD"
                ):
                    probe._verify_source_checkout(
                        repository,
                        REVISION,
                        TREE,
                        str(self.git),
                        self.git_sha256,
                        manifest,
                    )

    def test_primary_policy_failure_still_runs_docker_postflight(self) -> None:
        class RejectedEngine(FakeEngine):
            def probe_policy_readback(self, *args, **kwargs):
                error = probe.isolation.IsolationError(
                    "fixed public rejection",
                    policy_predicate="network_mode_mismatch",
                )
                raise error

        def rejected_factory(image, docker_executable, input_root):
            return RejectedEngine(image, docker_executable, input_root)

        identity = (1, 2, 3, 4, 5, 6, self.docker_sha256)
        with mock.patch.object(
            probe, "_verify_docker_executable", side_effect=(identity, identity)
        ) as verifier:
            with self.assertRaises(probe.isolation.IsolationError) as caught:
                self.run_probe(rejected_factory)
        self.assertEqual(verifier.call_count, 2)
        self.assertEqual(caught.exception.policy_predicate, "network_mode_mismatch")

    def test_docker_postflight_drift_overrides_diagnostic_success(self) -> None:
        before = (1, 2, 3, 4, 5, 6, self.docker_sha256)
        after = (1, 2, 3, 4, 5, 7, self.docker_sha256)
        with mock.patch.object(
            probe, "_verify_docker_executable", side_effect=(before, after)
        ):
            with self.assertRaisesRegex(
                probe.PolicyProbeError, "changed during policy probe"
            ):
                self.run_probe()

    def test_false_result_invariant_is_rejected(self) -> None:
        class IncompleteEngine(FakeEngine):
            def probe_policy_readback(self, workspace, relative_script, **kwargs):
                result = super().probe_policy_readback(
                    workspace, relative_script, **kwargs
                )
                result.container_absence_verified = False
                return result

        def incomplete_factory(image, docker_executable, input_root):
            return IncompleteEngine(image, docker_executable, input_root)

        with self.assertRaisesRegex(
            probe.PolicyProbeError, "diagnostic contract"
        ):
            self.run_probe(incomplete_factory)

    def test_cli_success_is_one_canonical_safe_json_line(self) -> None:
        safe_payload = {
            "checks": {"policy_readback_verified": True},
            "execution_policy": probe._execution_boundary(),
            "non_qualifying": True,
            "probe": probe.PROBE_ID,
            "qualification_eligible": False,
            "schema": probe.SCHEMA,
            "status": "diagnostic_pass",
        }
        argv = [
            "--image",
            IMAGE,
            "--docker-executable",
            str(self.docker),
            "--docker-executable-sha256",
            self.docker_sha256,
            "--git-executable",
            str(self.git),
            "--git-executable-sha256",
            self.git_sha256,
            "--source-revision",
            REVISION,
            "--source-root",
            str(self.root),
            "--source-tree",
            TREE,
            "--work-root",
            str(self.root),
        ]
        output = io.StringIO()
        with mock.patch.object(probe, "run_policy_probe", return_value=safe_payload):
            with redirect_stdout(output):
                code = probe.main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), probe._canonical_json(safe_payload))
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertLess(len(output.getvalue().encode("ascii")), 4096)
        self.assertNotIn(str(self.root), output.getvalue())

    def test_cli_failure_exposes_only_an_allowlisted_predicate(self) -> None:
        sentinel = "PRIVATE-LOCAL-DETAIL"
        error = probe.isolation.IsolationError(
            sentinel,
            policy_predicate="security_option_mismatch",
            container_name="marb-isolated-" + "9" * 32,
            container_id="9" * 64,
        )
        output = io.StringIO()
        with mock.patch.object(probe, "run_policy_probe", side_effect=error):
            with redirect_stdout(output):
                code = probe.main(
                    [
                        "--image",
                        IMAGE,
                        "--docker-executable",
                        str(self.docker),
                        "--docker-executable-sha256",
                        self.docker_sha256,
                        "--git-executable",
                        str(self.git),
                        "--git-executable-sha256",
                        self.git_sha256,
                        "--source-revision",
                        REVISION,
                        "--source-root",
                        str(self.root),
                        "--source-tree",
                        TREE,
                        "--work-root",
                        str(self.root),
                    ]
                )
        self.assertEqual(code, 2)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["policy_predicate"], "security_option_mismatch")
        self.assertEqual(parsed["status"], "diagnostic_rejected")
        self.assertTrue(parsed["non_qualifying"])
        self.assertFalse(parsed["qualification_eligible"])
        self.assertEqual(parsed["execution_policy"], probe._execution_boundary())
        self.assertNotIn(sentinel, output.getvalue())
        self.assertNotIn("marb-isolated-", output.getvalue())
        self.assertNotIn("9" * 64, output.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_unknown_predicate_and_forbidden_flags_are_not_echoed(self) -> None:
        sentinel = "dynamic-private-predicate"
        unknown = probe.PolicyProbeError("fixed rejection")
        unknown.policy_predicate = sentinel  # type: ignore[attr-defined]
        self.assertNotIn("policy_predicate", probe._rejected_payload(unknown))

        for forbidden in (
            "--output",
            "--ledger",
            "--packet",
            "--provider",
            "--model",
            "--qualification",
        ):
            with self.subTest(flag=forbidden):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = probe.main([forbidden, sentinel])
                self.assertEqual(code, 2)
                self.assertNotIn(sentinel, output.getvalue())
                self.assertNotIn("policy_predicate", output.getvalue())
                self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_probe_is_host_only_and_outside_the_build_context(self) -> None:
        repository = Path(probe.__file__).resolve().parents[1]
        manifest_source = (
            repository / "scripts" / "canonical_manifest.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"harness/container_policy_probe.py"', manifest_source)
        self.assertNotIn('"tests/test_container_policy_probe.py"', manifest_source)

    def test_import_is_inert_and_has_no_smoke_runner_dependency(self) -> None:
        source_path = Path(probe.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("runtime_smoke_runner", imports)
        self.assertNotIn(
            "run_smoke",
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)},
        )
        with mock.patch.object(
            probe.isolation,
            "IsolatedDockerPython",
            side_effect=AssertionError("engine construction during import"),
        ):
            importlib.reload(probe)


if __name__ == "__main__":
    unittest.main()
