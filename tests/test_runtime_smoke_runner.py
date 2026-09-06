"""Fake-only tests for the deterministic no-provider runtime smoke."""
from __future__ import annotations

import datetime as dt
import ast
from contextlib import contextmanager
import hashlib
import importlib
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from harness import cohort_executor
from harness import runtime_smoke_probes as probes
from harness import runtime_smoke_runner as runner


IMAGE = "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "e" * 64
REVISION = "b" * 40
TREE = "c" * 40
REQUIRED_IDS = (
    "positive_provenance_and_import",
    "expected_nonzero_exit",
    "network_denial",
    "protected_write_denial",
    "exact_clean_environment",
    "stdout_overflow_rejection",
    "workspace_input_rejection_overflow",
    "export_workspace_overflow",
    "timeout",
)


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def provenance_labels() -> tuple[dict[str, str], str]:
    source_sha256s = {
        item["path"]: item["sha256"]
        for item in runner._source_manifest()["entries"]
    }
    prefix = "org.sunnyday.marb."
    labels = {
        prefix + "runtime-contract": probes.EXPECTED_RUNTIME["runtime_contract"],
        prefix + "runtime-contract-sha256": source_sha256s[
            "harness/container/runtime-contract.v0.13.json"
        ],
        prefix + "cadclaw-version": probes.EXPECTED_RUNTIME["cadclaw_version"],
        prefix + "cadclaw-commit": probes.EXPECTED_RUNTIME["cadclaw_commit"],
        prefix + "cadclaw-gate-spec-version": probes.EXPECTED_RUNTIME[
            "cadclaw_gate_spec_version"
        ],
        prefix + "cadclaw-gate-registry-version": probes.EXPECTED_RUNTIME[
            "cadclaw_gate_registry_version"
        ],
        prefix + "cadclaw-pin-basis": probes.EXPECTED_PIN_BASIS,
        prefix + "cadclaw-source-manifest-sha256": probes.EXPECTED_RUNTIME[
            "cadclaw_source_manifest_sha256"
        ],
        prefix + "cadclaw-calibration-sha256": source_sha256s[
            "harness/container/cadclaw-calibration.fad0dd55.json"
        ],
        prefix + "cadquery-version": probes.EXPECTED_RUNTIME["cadquery_version"],
        prefix + "cadquery-ocp-version": probes.EXPECTED_RUNTIME[
            "cadquery_ocp_version"
        ],
        prefix + "base-image": "docker.io/library/python@sha256:" + "9" * 64,
        prefix + "requirements-lock-sha256": source_sha256s[
            "harness/container/requirements.lock"
        ],
        prefix + "wheelhouse-manifest-sha256": "1" * 64,
        prefix + "cadclaw-wheel-sha256": "2" * 64,
        prefix + "run-limiter-sha256": source_sha256s[
            "harness/container/run_limited.py"
        ],
        prefix + "native-deb-lock-sha256": source_sha256s[
            "harness/container/native-debs.lock.json"
        ],
        prefix + "native-deb-manifest-sha256": "3" * 64,
        prefix + "native-bundle-verifier-sha256": source_sha256s[
            "harness/container/verify_native_bundle.py"
        ],
        prefix + "native-deb-package-count": "39",
        prefix + "native-deb-total-bytes": "48570480",
        prefix + "dockerfile-sha256": source_sha256s[
            "harness/container/Dockerfile"
        ],
        prefix + "context-dockerignore-sha256": source_sha256s[
            "harness/container/build-context.dockerignore"
        ],
        prefix + "build-context-manifest-sha256": "4" * 64,
    }
    build_provenance = runner._build_provenance_from_labels(labels)
    if build_provenance is None:
        raise AssertionError("test provenance labels are incomplete")
    return labels, hashlib.sha256(
        runner._canonical_json(build_provenance, newline=True)
    ).hexdigest()


class FixedClock:
    def __init__(self) -> None:
        self.current = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)
        self.tick = 100.0

    def now(self) -> dt.datetime:
        value = self.current
        self.current += dt.timedelta(microseconds=1)
        return value

    def monotonic(self) -> float:
        value = self.tick
        self.tick += 0.25
        return value


class FakeIsolationError(RuntimeError):
    def __init__(
        self,
        code: str,
        stage: str,
        *,
        ordinal: int,
        cleanup_attempted: bool,
        cleanup_verified: bool | None,
        absence: bool | None,
        staging_removed: bool | None,
        stdout: str = "",
        stdout_truncated: bool = False,
        stderr: str = "",
    ) -> None:
        super().__init__("bounded fake isolation failure")
        self.code = code
        self.stage = stage
        self.cleanup_attempted = cleanup_attempted
        self.cleanup_verified = cleanup_verified
        self.container_absence_verified = absence
        self.export_staging_removed = staging_removed
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_truncated = stdout_truncated
        self.stderr_truncated = False
        if code == "workspace_entry_limit_exceeded":
            self.returncode = runner.isolation.LIMITER_EXIT
        elif code == "output_limit_exceeded":
            self.returncode = 0
        else:
            self.returncode = None
        self.container_name = (
            f"marb-isolated-{ordinal:032x}" if cleanup_attempted else None
        )
        self.container_id = f"{ordinal:064x}" if cleanup_attempted else None
        self.image = IMAGE if cleanup_attempted else None
        self.image_id = IMAGE_ID if cleanup_attempted else None
        self.primary_error_type = (
            "TimeoutExpired" if code == "execution_timeout" else "IsolationError"
        )
        self.cleanup_error_type = None


class FakeEngine:
    def __init__(
        self,
        case: probes.ProbeCase,
        ordinal: int,
        limiter_sha256: str,
        engines: list["FakeEngine"],
        *,
        unexpected: bool = False,
    ) -> None:
        self.case = case
        self.ordinal = ordinal
        self.limiter_sha256 = limiter_sha256
        self.docker_command_count = 0
        self.execute_calls = 0
        self.unexpected = unexpected
        engines.append(self)

    def _result(self, stdout: str = "", returncode: int = 0):
        return types.SimpleNamespace(
            cleanup_verified=True,
            container_absence_verified=True,
            container_id=f"{self.ordinal:064x}",
            container_name=f"marb-isolated-{self.ordinal:032x}",
            export_staging_removed=True,
            image=IMAGE,
            image_id=IMAGE_ID,
            returncode=returncode,
            stderr="",
            stderr_truncated=False,
            stdout=stdout,
            stdout_truncated=False,
        )

    def inspect_image_provenance(self):
        self.docker_command_count += 1
        labels, _build_provenance_sha256 = provenance_labels()
        return {
            "image": IMAGE,
            "image_id": IMAGE_ID,
            "labels": labels,
            "repo_digests": [IMAGE],
        }

    def execute(self, workspace, relative_script, **_kwargs):
        self.execute_calls += 1
        self.assert_probe(workspace, relative_script)
        if self.unexpected:
            self.docker_command_count += 4
            raise FakeIsolationError(
                "unexpected_failure",
                "container_start",
                ordinal=self.ordinal,
                cleanup_attempted=True,
                cleanup_verified=True,
                absence=True,
                staging_removed=True,
            )
        case_id = self.case.case_id
        if case_id == "workspace_input_rejection_overflow":
            self.assertEqual(
                sum(1 for _ in Path(workspace).rglob("*")),
                runner.isolation.MAX_WORKSPACE_ENTRIES + 1,
            )
            raise FakeIsolationError(
                "workspace_input_limit_exceeded",
                "validation",
                ordinal=self.ordinal,
                cleanup_attempted=False,
                cleanup_verified=None,
                absence=None,
                staging_removed=None,
            )
        self.docker_command_count += 8
        if case_id == "stdout_overflow_rejection":
            marker = runner.isolation._OUTPUT_TRUNCATION_MARKER
            bounded_stdout = (
                b"x" * (runner.isolation.MAX_STDOUT_BYTES - len(marker)) + marker
            ).decode("ascii")
            raise FakeIsolationError(
                "output_limit_exceeded",
                "container_start",
                ordinal=self.ordinal,
                cleanup_attempted=True,
                cleanup_verified=True,
                absence=True,
                staging_removed=True,
                stdout=bounded_stdout,
                stdout_truncated=True,
            )
        if case_id == "export_workspace_overflow":
            raise FakeIsolationError(
                "workspace_entry_limit_exceeded",
                "workspace_limiter",
                ordinal=self.ordinal,
                cleanup_attempted=True,
                cleanup_verified=True,
                absence=True,
                staging_removed=True,
                stderr=runner.isolation.WORKSPACE_ENTRY_LIMIT_REASON,
            )
        if case_id == "timeout":
            raise FakeIsolationError(
                "execution_timeout",
                "container_start",
                ordinal=self.ordinal,
                cleanup_attempted=True,
                cleanup_verified=True,
                absence=True,
                staging_removed=True,
            )
        if case_id == "positive_provenance_and_import":
            _labels, build_provenance_sha256 = provenance_labels()
            value = {
                **probes.EXPECTED_RUNTIME,
                "cadclaw_pin_basis": probes.EXPECTED_PIN_BASIS,
                "capabilities_zero": True,
                "build_provenance_sha256": build_provenance_sha256,
                "cwd": "/workspace",
                "docker_socket_absent": True,
                "environment_keys": sorted(probes.EXPECTED_ENVIRONMENT),
                "gid": 65532,
                "kit_read_only": True,
                "network_interfaces": ["lo"],
                "no_new_privileges": True,
                "root_read_only": True,
                "run_limiter_sha256": self.limiter_sha256,
                "seccomp_filtered": True,
                "staged_input_root": "/marb-input",
                "staged_inputs_read_only": True,
                "uid": 65532,
            }
            return self._result(canonical(value))
        if case_id == "expected_nonzero_exit":
            return self._result(returncode=probes.NONZERO_EXIT_CODE)
        if case_id == "network_denial":
            return self._result(
                canonical(
                    {
                        "docker_socket_absent": True,
                        "network_interfaces": ["lo"],
                        "outbound_denied": True,
                        "schema": probes.PROBE_SCHEMA,
                    }
                )
            )
        if case_id == "protected_write_denial":
            return self._result(
                canonical(
                    {
                        "export_tmpfs_writable": True,
                        "protected_denials": {
                            "host_workspace": True,
                            "kit": True,
                            "root": True,
                            "staged_input": True,
                        },
                        "schema": probes.PROBE_SCHEMA,
                        "tmp_writable": True,
                        "workspace_writable": True,
                    }
                )
            )
        if case_id == "exact_clean_environment":
            return self._result(
                canonical(
                    {
                        "environment_keys": sorted(probes.EXPECTED_ENVIRONMENT),
                        "schema": probes.PROBE_SCHEMA,
                    }
                )
            )
        raise AssertionError(f"unhandled case {case_id}")

    def assert_probe(self, workspace, relative_script) -> None:
        observed = (Path(workspace) / relative_script).read_bytes()
        if observed != self.case.source:
            raise AssertionError("runner changed the exact probe bytes")

    def assertEqual(self, left, right) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class RuntimeSmokeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-runtime-smoke-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.docker = self.root / "docker.exe"
        self.docker.write_bytes(b"synthetic Docker executable\n")
        self.docker_sha256 = hashlib.sha256(self.docker.read_bytes()).hexdigest()
        self.git = self.root / "git.exe"
        self.git.write_bytes(b"synthetic Git executable\n")
        self.git_sha256 = hashlib.sha256(self.git.read_bytes()).hexdigest()
        self.engines: list[FakeEngine] = []
        self.limiter_sha256 = hashlib.sha256(
            (Path(runner.__file__).resolve().parent / "container" / "run_limited.py").read_bytes()
        ).hexdigest()

    def factory(self, case, ordinal, _input_root):
        return FakeEngine(case, ordinal, self.limiter_sha256, self.engines)

    def source_verifier(
        self,
        source_root,
        revision,
        tree,
        git_executable,
        git_sha256,
        source_manifest,
    ):
        self.assertEqual(Path(source_root), self.root)
        self.assertEqual(revision, REVISION)
        self.assertEqual(tree, TREE)
        self.assertEqual(git_executable, str(self.git))
        self.assertEqual(git_sha256, self.git_sha256)
        return {
            "clean_tracked_checkout": True,
            "committed_blob_manifest_sha256": source_manifest["manifest_sha256"],
            "git_executable": {
                "label": self.git.name,
                "path_sha256": hashlib.sha256(str(self.git).encode("utf-8")).hexdigest(),
                "sha256": self.git_sha256,
            },
            "literal_head": REVISION,
            "revision": REVISION,
            "tree": TREE,
        }

    def run_smoke_evidence(self, factory=None):
        clock = FixedClock()
        with mock.patch.object(runner.isolation, "MAX_WORKSPACE_ENTRIES", 3):
            return runner.run_smoke(
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
                now=clock.now,
                monotonic=clock.monotonic,
            )

    def test_exact_nine_cases_pass_and_emit_safe_canonical_evidence(self) -> None:
        envelope = self.run_smoke_evidence()
        evidence = envelope["evidence"]
        self.assertEqual(tuple(evidence["case_order"]), REQUIRED_IDS)
        self.assertEqual([item["case_id"] for item in evidence["cases"]], list(REQUIRED_IDS))
        self.assertEqual(evidence["not_run_case_ids"], [])
        self.assertEqual(evidence["status"], "pass")
        self.assertTrue(all(item["status"] == "pass" for item in evidence["cases"]))
        self.assertTrue(all(item["pass"] is True for item in evidence["cases"]))
        self.assertTrue(
            all(item["bindings"]["image_repo_digest"] == IMAGE for item in evidence["cases"])
        )
        self.assertTrue(
            all(item["bindings"]["image_id"] == IMAGE_ID for item in evidence["cases"])
        )
        self.assertTrue(
            all(
                item["bindings"]["docker_executable_sha256"] == self.docker_sha256
                and item["bindings"]["source_revision"] == REVISION
                and item["bindings"]["source_tree"] == TREE
                and len(item["bindings"]["input_manifest_sha256"]) == 64
                for item in evidence["cases"]
            )
        )
        self.assertEqual(len(self.engines), 9)
        self.assertEqual(
            [item["docker_commands"] for item in evidence["cases"]],
            [9, 8, 8, 8, 8, 8, 0, 8, 8],
        )
        self.assertEqual(
            evidence["container"]["provenance"]["build_provenance_sha256"],
            evidence["positive_attestation"]["build_provenance_sha256"],
        )
        self.assertEqual(
            set(evidence["container"]["provenance"]["labels"]),
            set(runner.isolation.MARB_IMAGE_PROVENANCE_LABELS),
        )
        case6 = evidence["cases"][5]
        self.assertEqual(case6["exception_category"], "output_limit_exceeded")
        self.assertTrue(case6["checks"]["output_rejected"])
        self.assertTrue(case6["checks"]["runtime_identity_exact"])
        self.assertTrue(case6["checks"]["failure_runtime_invariants"])
        self.assertEqual(case6["bindings"]["image_id_source"], "failure_evidence")
        self.assertEqual(case6["failure"]["stdout"]["bytes"], 1_048_576)
        marker = runner.isolation._OUTPUT_TRUNCATION_MARKER
        expected_bounded = b"x" * (1_048_576 - len(marker)) + marker
        self.assertEqual(
            case6["failure"]["stdout"]["sha256"],
            hashlib.sha256(expected_bounded).hexdigest(),
        )
        self.assertNotIn("stdout_body", case6["failure"])
        case7 = evidence["cases"][6]
        self.assertEqual(case7["docker_commands"], 0)
        self.assertTrue(case7["checks"]["pre_docker"])
        self.assertTrue(case7["checks"]["runtime_not_inspected"])
        self.assertEqual(case7["bindings"]["image_id_source"], "prior_positive_case")
        self.assertEqual(self.engines[6].execute_calls, 1)
        case8 = evidence["cases"][7]
        self.assertTrue(case8["checks"]["workspace_preserved"])
        self.assertTrue(case8["checks"]["staging_removed"])
        self.assertTrue(case8["checks"]["container_absent"])
        self.assertTrue(case8["checks"]["runtime_identity_exact"])
        self.assertTrue(case8["checks"]["entry_overflow_exact"])
        self.assertTrue(evidence["cases"][8]["checks"]["timeout_rejected"])
        canonical_raw = runner._canonical_json(envelope, newline=True)
        self.assertEqual(json.loads(canonical_raw), envelope)
        self.assertNotIn("bounded fake isolation failure", canonical_raw.decode("ascii"))
        self.assertNotIn("x" * 100, canonical_raw.decode("ascii"))

        source_entries = evidence["source"]["implementation"]["entries"]
        for identity in source_entries:
            path = Path(runner.__file__).resolve().parents[1] / identity["path"]
            self.assertEqual(identity["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_failure_stops_later_cases_and_lists_exact_not_run_ids(self) -> None:
        def factory(case, ordinal, input_root):
            del input_root
            return FakeEngine(
                case,
                ordinal,
                self.limiter_sha256,
                self.engines,
                unexpected=case.case_id == "network_denial",
            )

        envelope = self.run_smoke_evidence(factory)
        evidence = envelope["evidence"]
        self.assertEqual([item["case_id"] for item in evidence["cases"]], list(REQUIRED_IDS[:3]))
        self.assertEqual(evidence["not_run_case_ids"], list(REQUIRED_IDS[3:]))
        self.assertEqual(evidence["status"], "fail")
        self.assertEqual(len(self.engines), 3)

    def test_evidence_write_is_canonical_and_never_clobbers(self) -> None:
        envelope = {
            "evidence": {"schema": runner.EVIDENCE_SCHEMA, "status": "fail"},
            "evidence_sha256": "0" * 64,
            "schema": runner.ENVELOPE_SCHEMA,
        }
        output = self.root / "smoke-evidence.json"
        runner.write_evidence(output, envelope)
        self.assertEqual(output.read_bytes(), runner._canonical_json(envelope, newline=True))
        with self.assertRaises(FileExistsError):
            runner.write_evidence(output, envelope)

    def test_docker_executable_digest_mismatch_rejects_before_engine(self) -> None:
        self.docker.write_bytes(b"changed synthetic Docker executable\n")
        with self.assertRaisesRegex(runner.SmokeRunnerError, "digest does not match"):
            self.run_smoke_evidence()
        self.assertEqual(self.engines, [])

    def test_production_entry_limits_and_case_order_are_literal(self) -> None:
        self.assertEqual(runner.isolation.MAX_WORKSPACE_ENTRIES, 4_096)
        self.assertEqual(probes.WORKSPACE_OVERFLOW_ENTRIES, 4_096)
        self.assertEqual(probes.REQUIRED_CASE_IDS, REQUIRED_IDS)
        self.assertIn("time.sleep(3600)", probes.EXPORT_WORKSPACE_OVERFLOW_SOURCE)

        limiter_path = Path(runner.__file__).resolve().parent / "container" / "run_limited.py"
        tree = ast.parse(limiter_path.read_text(encoding="utf-8"))
        values = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id
            in {"MAX_ENTRIES", "LIMIT_EXIT", "WORKSPACE_ENTRY_LIMIT_REASON"}
        }
        self.assertEqual(values["MAX_ENTRIES"], 4_096)
        self.assertEqual(values["LIMIT_EXIT"], runner.isolation.LIMITER_EXIT)
        self.assertEqual(
            values["WORKSPACE_ENTRY_LIMIT_REASON"],
            runner.isolation.WORKSPACE_ENTRY_LIMIT_REASON,
        )
        self.assertEqual(
            probes.STDOUT_OVERFLOW_BYTES,
            runner.isolation.MAX_STDOUT_BYTES + 1,
        )

    def test_bounded_stdout_contract_replaces_the_tail_with_marker(self) -> None:
        capture = runner.isolation._BoundedCapture(runner.isolation.MAX_STDOUT_BYTES)
        stream = mock.Mock()
        stream.read = mock.Mock(
            side_effect=[b"x" * probes.STDOUT_OVERFLOW_BYTES, b""]
        )
        capture.consume(stream)
        marker = runner.isolation._OUTPUT_TRUNCATION_MARKER
        expected = b"x" * (runner.isolation.MAX_STDOUT_BYTES - len(marker)) + marker
        self.assertTrue(capture.truncated)
        self.assertEqual(capture.value(), expected)

    def test_post_case_wrapper_preserves_output_identity_without_body(self) -> None:
        bounded = "x" * 64 + runner.isolation._OUTPUT_TRUNCATION_MARKER.decode("ascii")
        prior = runner.isolation.IsolationError(
            "synthetic bounded failure",
            code="output_limit_exceeded",
            stage="container_start",
            stdout=bounded,
            stdout_truncated=True,
            policy_predicate="network_mode_mismatch",
        )
        wrapped = runner.SmokeRunnerError(
            "Docker executable changed during a smoke case",
            code="docker_executable_drift",
            stage="post_case_readback",
            prior=prior,
        )
        summary = runner._failure_summary(wrapped)
        self.assertEqual(summary["stdout"]["bytes"], len(bounded.encode("utf-8")))
        self.assertEqual(
            summary["stdout"]["sha256"],
            hashlib.sha256(bounded.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(wrapped.stdout, runner.isolation._OUTPUT_TRUNCATION_MARKER.decode("ascii"))
        self.assertEqual(summary["policy_predicate"], "network_mode_mismatch")
        self.assertNotIn("x" * 16, runner._canonical_json(summary).decode("ascii"))

    def test_policy_predicate_summary_is_allowlisted_and_optional(self) -> None:
        accepted = runner.isolation.IsolationError(
            "fixed public failure",
            stage="container_policy_readback",
            policy_predicate="security_option_mismatch",
        )
        accepted_summary = runner._failure_summary(accepted)
        self.assertEqual(
            accepted_summary["policy_predicate"],
            "security_option_mismatch",
        )

        sentinel = "syntactically_safe_unlisted_predicate"
        accepted.policy_predicate = sentinel
        rejected_summary = runner._failure_summary(accepted)
        self.assertNotIn("policy_predicate", rejected_summary)
        self.assertNotIn(
            sentinel,
            runner._canonical_json(rejected_summary).decode("ascii"),
        )

        legacy = RuntimeError("legacy failure without predicate")
        legacy_summary = runner._failure_summary(legacy)
        self.assertNotIn("policy_predicate", legacy_summary)

    def test_wrapper_drops_non_allowlisted_prior_policy_predicate(self) -> None:
        sentinel = "syntactically_safe_unlisted_predicate"
        prior = FakeIsolationError(
            "unexpected_failure",
            "container_policy_readback",
            ordinal=1,
            cleanup_attempted=True,
            cleanup_verified=True,
            absence=True,
            staging_removed=True,
        )
        prior.policy_predicate = sentinel
        wrapped = runner.SmokeRunnerError(
            "fixed public wrapper",
            code="docker_executable_drift",
            stage="post_case_readback",
            prior=prior,
        )
        summary = runner._failure_summary(wrapped)
        self.assertFalse(hasattr(wrapped, "policy_predicate"))
        self.assertNotIn("policy_predicate", summary)
        self.assertNotIn(sentinel, runner._canonical_json(summary).decode("ascii"))

    def test_docker_replacement_after_case_fails_and_stops(self) -> None:
        class MutatingEngine(FakeEngine):
            def execute(inner_self, workspace, relative_script, **kwargs):
                result = super(MutatingEngine, inner_self).execute(
                    workspace, relative_script, **kwargs
                )
                self.docker.write_bytes(b"replacement Docker executable\n")
                return result

        def factory(case, ordinal, _input_root):
            return MutatingEngine(case, ordinal, self.limiter_sha256, self.engines)

        evidence = self.run_smoke_evidence(factory)["evidence"]
        self.assertEqual(evidence["status"], "fail")
        self.assertEqual(len(evidence["cases"]), 1)
        self.assertEqual(evidence["cases"][0]["exception_category"], "docker_executable_drift")
        self.assertEqual(evidence["not_run_case_ids"], list(REQUIRED_IDS[1:]))

    def test_source_proof_mismatch_rejects_before_engine(self) -> None:
        original = self.source_verifier

        def mismatched(*args):
            proof = original(*args)
            proof["revision"] = "d" * 40
            return proof

        clock = FixedClock()
        with self.assertRaisesRegex(runner.SmokeRunnerError, "source preflight proof"):
            runner.run_smoke(
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
                source_verifier=mismatched,
                now=clock.now,
                monotonic=clock.monotonic,
            )
        self.assertEqual(self.engines, [])

    def test_literal_head_mismatch_rejects_before_engine(self) -> None:
        original = self.source_verifier

        def mismatched(*args):
            proof = original(*args)
            proof["literal_head"] = "d" * 40
            return proof

        clock = FixedClock()
        with self.assertRaisesRegex(runner.SmokeRunnerError, "source preflight proof"):
            runner.run_smoke(
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
                source_verifier=mismatched,
                now=clock.now,
                monotonic=clock.monotonic,
            )
        self.assertEqual(self.engines, [])

    def test_default_source_verifier_binds_literal_head_and_raw_source_bytes(self) -> None:
        source_root = Path(runner.__file__).resolve().parents[1]
        manifest = runner._source_manifest()

        @contextmanager
        def locked_git(_authorization):
            yield {"path": str(self.git), "sha256": self.git_sha256}

        def completed(argv, **_kwargs):
            if argv[-1] == "HEAD":
                stdout = (REVISION + "\n").encode("ascii")
            elif argv[-1] == "HEAD^{commit}":
                stdout = (REVISION + "\n").encode("ascii")
            elif argv[-1] == "HEAD^{tree}":
                stdout = (TREE + "\n").encode("ascii")
            else:
                stdout = b""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

        def committed(_authorization, _root, _revision, public_path):
            return source_root.joinpath(*public_path.split("/")).read_bytes()

        with (
            mock.patch.object(cohort_executor, "_locked_git_spawn_identity", locked_git),
            mock.patch.object(runner.subprocess, "run", side_effect=completed),
            mock.patch.object(
                cohort_executor,
                "_read_committed_blob_with_git",
                side_effect=committed,
            ),
            mock.patch.object(
                cohort_executor,
                "_verify_host_git_executable",
                return_value={"sha256": self.git_sha256},
            ),
        ):
            proof = runner._verify_source_checkout(
                source_root,
                REVISION,
                TREE,
                str(self.git),
                self.git_sha256,
                manifest,
            )
        self.assertEqual(proof["literal_head"], REVISION)
        self.assertEqual(proof["revision"], REVISION)
        self.assertEqual(proof["tree"], TREE)
        self.assertEqual(
            {item["path"] for item in manifest["entries"]}, set(runner.SOURCE_FILES)
        )
        for item in manifest["entries"]:
            with self.subTest(path=item["path"]):
                path = source_root.joinpath(*item["path"].split("/"))
                self.assertEqual(item["hash_mode"], "raw")
                self.assertEqual(item["bytes"], len(path.read_bytes()))
                self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_image_provenance_binds_effective_context_ignore_template(self) -> None:
        source_root = Path(runner.__file__).resolve().parents[1]
        labels, _ = provenance_labels()
        effective = (
            source_root / "harness" / "container" / "build-context.dockerignore"
        ).read_bytes()
        source_deny_all = (
            source_root / "harness" / "container" / ".dockerignore"
        ).read_bytes()
        label = labels["org.sunnyday.marb.context-dockerignore-sha256"]
        self.assertEqual(label, hashlib.sha256(effective).hexdigest())
        self.assertNotEqual(label, hashlib.sha256(source_deny_all).hexdigest())

    def test_default_source_verifier_rejects_raw_drift_for_every_bound_file(self) -> None:
        source_root = Path(runner.__file__).resolve().parents[1]
        manifest = runner._source_manifest()

        @contextmanager
        def locked_git(_authorization):
            yield {"path": str(self.git), "sha256": self.git_sha256}

        def completed(argv, **_kwargs):
            values = {
                "HEAD": REVISION,
                "HEAD^{commit}": REVISION,
                "HEAD^{tree}": TREE,
            }
            stdout = (
                (values[argv[-1]] + "\n").encode("ascii")
                if argv[-1] in values
                else b""
            )
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

        for target in runner.SOURCE_FILES:
            with self.subTest(path=target):
                def committed(_authorization, _root, _revision, public_path):
                    raw = source_root.joinpath(*public_path.split("/")).read_bytes()
                    return raw + b"\x00" if public_path == target else raw

                with (
                    mock.patch.object(
                        cohort_executor, "_locked_git_spawn_identity", locked_git
                    ),
                    mock.patch.object(runner.subprocess, "run", side_effect=completed),
                    mock.patch.object(
                        cohort_executor,
                        "_read_committed_blob_with_git",
                        side_effect=committed,
                    ),
                ):
                    with self.assertRaisesRegex(
                        runner.SmokeRunnerError,
                        "executing smoke source differs from committed HEAD",
                    ):
                        runner._verify_source_checkout(
                            source_root,
                            REVISION,
                            TREE,
                            str(self.git),
                            self.git_sha256,
                            manifest,
                        )

    def test_success_requires_explicit_absence_and_staging_flags(self) -> None:
        for field in ("container_absence_verified", "export_staging_removed"):
            for mode in ("false", "missing"):
                with self.subTest(field=field, mode=mode):
                    self.engines.clear()

                    class MissingSuccessEvidenceEngine(FakeEngine):
                        def _result(inner_self, stdout="", returncode=0):
                            result = super(MissingSuccessEvidenceEngine, inner_self)._result(
                                stdout, returncode
                            )
                            if mode == "missing":
                                delattr(result, field)
                            else:
                                setattr(result, field, False)
                            return result

                    def factory(case, ordinal, _input_root):
                        return MissingSuccessEvidenceEngine(
                            case, ordinal, self.limiter_sha256, self.engines
                        )

                    evidence = self.run_smoke_evidence(factory)["evidence"]
                    self.assertEqual(evidence["status"], "fail")
                    self.assertEqual(len(evidence["cases"]), 1)
                    self.assertFalse(evidence["cases"][0]["pass"])

    def test_image_provenance_tampering_fails_closed(self) -> None:
        variants = (
            "missing_label",
            "extra_marb_label",
            "altered_label",
            "wrong_repo_digest",
            "wrong_image_id",
            "wrong_tracked_file_hash",
            "wrong_in_image_hash",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.engines.clear()

                class TamperedProvenanceEngine(FakeEngine):
                    def inspect_image_provenance(inner_self):
                        value = dict(super(TamperedProvenanceEngine, inner_self).inspect_image_provenance())
                        value["labels"] = dict(value["labels"])
                        prefix = "org.sunnyday.marb."
                        if variant == "missing_label":
                            value["labels"].pop(prefix + "runtime-contract")
                        elif variant == "extra_marb_label":
                            value["labels"][prefix + "unexpected"] = "public-but-unbound"
                        elif variant == "altered_label":
                            value["labels"][prefix + "cadclaw-version"] = "0.10.1"
                        elif variant == "wrong_repo_digest":
                            value["repo_digests"] = [
                                "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "f" * 64
                            ]
                        elif variant == "wrong_image_id":
                            value["image_id"] = "sha256:" + "f" * 64
                        elif variant == "wrong_tracked_file_hash":
                            value["labels"][prefix + "dockerfile-sha256"] = "f" * 64
                        return value

                    def execute(inner_self, workspace, relative_script, **kwargs):
                        result = super(TamperedProvenanceEngine, inner_self).execute(
                            workspace, relative_script, **kwargs
                        )
                        if (
                            variant == "wrong_in_image_hash"
                            and inner_self.case.case_id == "positive_provenance_and_import"
                        ):
                            payload = json.loads(result.stdout)
                            payload["build_provenance_sha256"] = "f" * 64
                            result.stdout = canonical(payload)
                        return result

                def factory(case, ordinal, _input_root):
                    return TamperedProvenanceEngine(
                        case, ordinal, self.limiter_sha256, self.engines
                    )

                evidence = self.run_smoke_evidence(factory)["evidence"]
                self.assertEqual(evidence["status"], "fail")
                self.assertEqual([item["case_id"] for item in evidence["cases"]], [REQUIRED_IDS[0]])
                self.assertEqual(evidence["not_run_case_ids"], list(REQUIRED_IDS[1:]))
                self.assertFalse(evidence["cases"][0]["checks"]["image_provenance_exact"])

    def test_embedded_provenance_validator_rejects_noncanonical_bytes_and_key_drift(self) -> None:
        tree = ast.parse(probes.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE)
        selected: list[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and target.id in {"provenance_raw", "provenance"}
                for target in node.targets
            ):
                selected.append(node)
            elif isinstance(node, ast.Assert):
                rendered = ast.unparse(node.test)
                if "provenance_raw ==" in rendered or "set(provenance) ==" in rendered:
                    selected.append(node)
        self.assertEqual(len(selected), 4)

        provenance_path = self.root / "build-provenance.json"

        class RewritePath(ast.NodeTransformer):
            def visit_Constant(self, node):
                if node.value == "/opt/marb/build-provenance.json":
                    return ast.copy_location(ast.Constant(str(provenance_path)), node)
                return node

        validator = compile(
            ast.fix_missing_locations(
                RewritePath().visit(ast.Module(body=selected, type_ignores=[]))
            ),
            "<embedded-provenance-validator>",
            "exec",
        )
        labels, _ = provenance_labels()
        value = runner._build_provenance_from_labels(labels)
        self.assertIsNotNone(value)
        assert value is not None
        canonical_raw = runner._canonical_json(value, newline=True)
        provenance_path.write_bytes(canonical_raw)
        exec(validator, {"Path": Path, "json": json})

        missing = dict(value)
        missing.pop("schema")
        extra = {**value, "unexpected": "value"}
        variants = (
            json.dumps(value, indent=2, sort_keys=True).encode("ascii") + b"\n",
            canonical_raw.replace(b"\n", b"\r\n"),
            runner._canonical_json(missing, newline=True),
            runner._canonical_json(extra, newline=True),
        )
        for raw in variants:
            with self.subTest(raw_sha256=hashlib.sha256(raw).hexdigest()):
                provenance_path.write_bytes(raw)
                with self.assertRaises(AssertionError):
                    exec(validator, {"Path": Path, "json": json})

    def test_protected_and_environment_cases_require_exact_zero_exit_payloads(self) -> None:
        mutations = (
            ("protected_write_denial", "extra"),
            ("protected_write_denial", "nonzero"),
            ("exact_clean_environment", "extra"),
            ("exact_clean_environment", "nonzero"),
        )
        for target, mutation in mutations:
            with self.subTest(target=target, mutation=mutation):
                self.engines.clear()

                class AdversarialEngine(FakeEngine):
                    def execute(inner_self, workspace, relative_script, **kwargs):
                        result = super(AdversarialEngine, inner_self).execute(
                            workspace, relative_script, **kwargs
                        )
                        if inner_self.case.case_id == target:
                            if mutation == "nonzero":
                                result.returncode = 1
                            else:
                                payload = json.loads(result.stdout)
                                payload["unexpected"] = True
                                result.stdout = canonical(payload)
                        return result

                def factory(case, ordinal, _input_root):
                    return AdversarialEngine(
                        case, ordinal, self.limiter_sha256, self.engines
                    )

                evidence = self.run_smoke_evidence(factory)["evidence"]
                self.assertEqual(evidence["status"], "fail")
                self.assertEqual(evidence["cases"][-1]["case_id"], target)
                self.assertFalse(evidence["cases"][-1]["pass"])

    def test_wrong_limiter_reason_and_prestart_timeout_cannot_pass(self) -> None:
        variants = (
            ("export_workspace_overflow", "container_limiter_rejected", "container_start"),
            ("timeout", "execution_timeout", "container_creation"),
        )
        for target, code, stage in variants:
            with self.subTest(target=target):
                self.engines.clear()

                class WrongFailureEngine(FakeEngine):
                    def execute(inner_self, workspace, relative_script, **kwargs):
                        if inner_self.case.case_id != target:
                            return super(WrongFailureEngine, inner_self).execute(
                                workspace, relative_script, **kwargs
                            )
                        inner_self.execute_calls += 1
                        inner_self.assert_probe(workspace, relative_script)
                        inner_self.docker_command_count += 8
                        raise FakeIsolationError(
                            code,
                            stage,
                            ordinal=inner_self.ordinal,
                            cleanup_attempted=True,
                            cleanup_verified=True,
                            absence=True,
                            staging_removed=True,
                        )

                def factory(case, ordinal, _input_root):
                    return WrongFailureEngine(
                        case, ordinal, self.limiter_sha256, self.engines
                    )

                evidence = self.run_smoke_evidence(factory)["evidence"]
                self.assertEqual(evidence["status"], "fail")
                self.assertEqual(evidence["cases"][-1]["case_id"], target)
                self.assertFalse(evidence["cases"][-1]["pass"])

    def test_failure_invariant_drift_is_rejected(self) -> None:
        evidence = self.run_smoke_evidence()["evidence"]
        base = evidence["cases"][5]["failure"]
        case = probes.CASES[5]
        mutations = {
            "cleanup_verified": False,
            "container_absence_verified": False,
            "container_name": None,
            "container_id": None,
            "export_staging_removed": False,
            "image": "ghcr.io/invalid/replacement@sha256:" + "f" * 64,
            "image_id": "sha256:" + "f" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                failure = dict(base)
                failure[field] = value
                checks, _parsed = runner._case_checks(
                    case,
                    None,
                    failure,
                    image=IMAGE,
                    limiter_sha256=self.limiter_sha256,
                    docker_commands=8,
                    expected_image_id=IMAGE_ID,
                    workspace_unchanged=True,
                    inputs_unchanged=True,
                    input_contract_exact=True,
                    probe_bytes_exact=True,
                    docker_executable_stable=True,
                    staging_removed=True,
                )
                self.assertFalse(all(checks.values()))

    def test_postflight_source_drift_makes_overall_evidence_fail(self) -> None:
        calls = 0

        def drifting(*args):
            nonlocal calls
            calls += 1
            proof = self.source_verifier(*args)
            if calls == 2:
                proof["tree"] = "d" * 40
            return proof

        clock = FixedClock()
        with mock.patch.object(runner.isolation, "MAX_WORKSPACE_ENTRIES", 3):
            envelope = runner.run_smoke(
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
                source_verifier=drifting,
                now=clock.now,
                monotonic=clock.monotonic,
            )
        self.assertEqual(envelope["evidence"]["status"], "fail")
        self.assertFalse(envelope["evidence"]["postflight_checks"]["source_checkout_unchanged"])

    def test_import_is_inert_and_executor_uses_shared_positive_probe(self) -> None:
        with mock.patch.object(
            runner.isolation,
            "IsolatedDockerPython",
            side_effect=AssertionError("engine construction during import"),
        ):
            importlib.reload(runner)
        self.assertIs(
            cohort_executor.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE,
            probes.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE,
        )
        compile(probes.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE, "<positive-probe>", "exec")


if __name__ == "__main__":
    unittest.main()
