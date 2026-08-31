"""Execution-path regressions for the explicitly authorized H2b cohort executor."""
from __future__ import annotations

import copy
import contextlib
import datetime as dt
import hashlib
import inspect
import io
import itertools
import json
import os
import subprocess
import tempfile
import threading
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

from harness import cohort_executor as EXECUTOR
from harness import cohort_runner as RUNNER
from tests.test_cohort_runner import SyntheticRepo


FIXED_NOW = dt.datetime(2026, 8, 28, 12, 0, 0, tzinfo=dt.timezone.utc)
IMAGE = "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "a" * 64
ATTEMPT_UUID = uuid.UUID("11111111-1111-4111-8111-111111111111")
CONTINUITY_UUID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"


class UUIDSequence:
    def __init__(self, *values: uuid.UUID) -> None:
        self.values = list(values)

    def __call__(self) -> uuid.UUID:
        if not self.values:
            raise AssertionError("unexpected UUID allocation")
        return self.values.pop(0)


class FakeSandbox:
    def __init__(self, workspace: Path, trace: list[str], *, fail_start: bool = False) -> None:
        self.workspace = workspace
        self.trace = trace
        self.fail_start = fail_start
        self.runs = 0
        self.closed = False

    def start(self, timeout_seconds: int) -> dict[str, object]:
        self.trace.append("sandbox-start")
        if timeout_seconds < 1:
            raise AssertionError("sandbox timeout was not clamped safely")
        if self.fail_start:
            raise RuntimeError("deliberately sanitized fake failure")
        return {
            **EXECUTOR.EXPECTED_RUNTIME,
            "cadclaw_pin_basis": EXECUTOR.CADCLAW_PIN_BASIS,
            "uid": 65532,
            "gid": 65532,
            "capabilities_zero": True,
            "no_new_privileges": True,
            "seccomp_filtered": True,
            "root_read_only": True,
            "network_interfaces": ["lo"],
            "docker_socket_absent": True,
            "environment_keys": EXECUTOR.EXPECTED_CONTAINER_ENV_KEYS,
            "cwd": "/workspace",
            "kit_read_only": True,
            "staged_inputs_read_only": True,
            "staged_input_root": "/marb-input",
            "image": IMAGE,
            "docker_executable": "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
            "docker_executable_sha256": "b" * 64,
            "run_limiter_sha256": hashlib.sha256(
                (Path(EXECUTOR.__file__).resolve().parent / "container" / "run_limited.py").read_bytes()
            ).hexdigest(),
            "container_config_readback": True,
            "cleanup_verified": True,
        }

    def run_python(self, relative_path: str, timeout_seconds: int) -> dict[str, str]:
        self.trace.append(f"python:{relative_path}")
        self.runs += 1
        (self.workspace / "export.step").write_bytes(
            f"ISO-10303-21 fake phase {self.runs}\n".encode("ascii")
        )
        return {"status": "ok", "output": f"phase {self.runs} complete"}

    def close(self) -> None:
        self.trace.append("sandbox-close")
        self.closed = True


class FakeProvider:
    def __init__(self, trace: list[str], request_marker: str) -> None:
        self.trace = trace
        self.request_marker = request_marker
        self.message_identity: int | None = None
        self.snapshots: list[str] = []
        self.ordinal = 0

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        settings: dict[str, object],
    ) -> EXECUTOR.ProviderResponse:
        self.trace.append("provider-call")
        if self.message_identity is None:
            self.message_identity = id(messages)
        else:
            if id(messages) != self.message_identity:
                raise AssertionError("conversation object changed across phases")
        self.snapshots.append(json.dumps(messages, sort_keys=True))
        self.ordinal += 1
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if self.ordinal in {1, 3}:
            source = "print('baseline')\n" if self.ordinal == 1 else "print('changed')\n"
            return EXECUTOR.ProviderResponse(
                content="working",
                tool_calls=(
                    EXECUTOR.ProviderToolCall(
                        call_id=f"write-{self.ordinal}",
                        name="write_file",
                        arguments={"path": "solution.py", "content": source},
                    ),
                    EXECUTOR.ProviderToolCall(
                        call_id=f"run-{self.ordinal}",
                        name="run_python",
                        arguments={"path": "solution.py"},
                    ),
                ),
                usage=usage,
                response_model="synthetic-model-v1",
            )
        return EXECUTOR.ProviderResponse(
            content="done",
            tool_calls=(),
            usage=usage,
            response_model="synthetic-model-v1",
        )


class FailingProvider:
    def complete(self, messages, tools, settings):
        raise EXECUTOR.ProviderCallError("sanitized failure")


class OneShotProvider:
    def __init__(self, response: EXECUTOR.ProviderResponse) -> None:
        self.response = response

    def complete(self, messages, tools, settings):
        return self.response


class FakeGitProcess:
    """In-memory Popen substitute for Git boundary tests."""

    def __init__(
        self,
        output: bytes = b"",
        *,
        returncode: int = 0,
        timeout_once: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(output)
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._timeout_once = timeout_once
        self.kills = 0
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.kills += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self._timeout_once and timeout is not None:
            self._timeout_once = False
            raise subprocess.TimeoutExpired("synthetic-git", timeout)
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode


class CohortExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-cohort-executor-test-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.fixture = SyntheticRepo(self.repo)
        self.brief_payload = "Synthetic public CadQuery brief payload\n"
        self.fixture._write_bytes(
            self.fixture.brief_path,
            (
                "Synthetic non-prompt preamble\n"
                "`=== BEGIN ===`\n"
                + self.brief_payload
                + "`=== END ===`\n"
                "Synthetic non-prompt suffix\n"
            ).encode("utf-8"),
        )
        brief_digest = self.fixture.text_hash(self.fixture.brief_path)
        self.fixture.write_l1_contract()
        for task_id in ("L2-RESOLVE", "L4-ECO"):
            self.fixture.registry["tasks"][task_id]["allowed_driver_briefs"][
                self.fixture.brief_path
            ] = brief_digest
        self.fixture.write_registry()
        self.fixture._write_bytes(
            "harness/cohort_executor.py", Path(EXECUTOR.__file__).resolve().read_bytes()
        )
        self.fixture._write_bytes(
            "harness/isolated_container.py",
            Path(EXECUTOR.__file__).resolve().with_name("isolated_container.py").read_bytes(),
        )
        self.fixture._write_bytes(
            "harness/provider_transport.py",
            Path(EXECUTOR.__file__).resolve().with_name("provider_transport.py").read_bytes(),
        )
        self.fixture._write_bytes(
            "harness/runtime_smoke_probes.py",
            Path(EXECUTOR.__file__).resolve().with_name("runtime_smoke_probes.py").read_bytes(),
        )
        self.fixture._write_bytes(
            "harness/container/run_limited.py",
            (
                Path(EXECUTOR.__file__).resolve().parent
                / "container"
                / "run_limited.py"
            ).read_bytes(),
        )
        for relative, source in (
            ("harness/container/runtime-contract.v0.13.json", EXECUTOR.RUNTIME_CONTRACT_PATH),
            (
                "harness/container/runtime-contract.v0.12.json",
                EXECUTOR.HISTORICAL_RUNTIME_CONTRACT_PATH,
            ),
            (
                "harness/container/cadclaw-calibration.fad0dd55.json",
                EXECUTOR.CADCLAW_CALIBRATION_PATH,
            ),
        ):
            self.fixture._write_bytes(relative, source.read_bytes())
        git_temporary = tempfile.TemporaryDirectory(
            prefix="marb-authorized-git-test-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.addCleanup(git_temporary.cleanup)
        git_name = "git.exe" if os.name == "nt" else "git"
        self.git_path = Path(git_temporary.name).resolve() / "trusted-tools" / git_name
        self.git_path.parent.mkdir(parents=True)
        self.git_bytes = b"synthetic native Git executable identity\n"
        self.git_path.write_bytes(self.git_bytes)
        self.git_path_text = self.git_path.as_posix()
        self.git_sha256 = hashlib.sha256(self.git_bytes).hexdigest()
        self.git_process_calls: list[tuple[list[str], dict[str, object]]] = []
        self.trace: list[str] = []

    def executing_module_paths(self) -> dict[str, Path]:
        return {
            "planner": self.fixture.path("harness/cohort_runner.py"),
            "executor": self.fixture.path("harness/cohort_executor.py"),
            "isolated_container": self.fixture.path("harness/isolated_container.py"),
            "provider_transport": self.fixture.path("harness/provider_transport.py"),
            "runtime_smoke_probes": self.fixture.path(
                "harness/runtime_smoke_probes.py"
            ),
            "run_limiter": self.fixture.path("harness/container/run_limited.py"),
            "runtime_contract": self.fixture.path(
                "harness/container/runtime-contract.v0.13.json"
            ),
            "historical_runtime_contract": self.fixture.path(
                "harness/container/runtime-contract.v0.12.json"
            ),
            "cadclaw_calibration": self.fixture.path(
                "harness/container/cadclaw-calibration.fad0dd55.json"
            ),
        }

    def ready_plan(self, task_id: str = "L2-RESOLVE") -> dict[str, object]:
        tasks = self.fixture.registry["tasks"]
        task = tasks[task_id]
        task["evidence_status"] = "complete"
        task["answer_key_status"] = "ready"
        if task_id == "L4-ECO":
            task["gated_distribution_revision"] = "f" * 64
            task.update(EXECUTOR.SUPPORTED_L4_GRADE_CONTRACT)
        self.fixture.write_registry()
        return self.fixture.build(task_id)

    def authorization(
        self,
        plan: dict[str, object],
        *,
        run_id: str | None = None,
        endpoint: str = "https://api.example.test/v1",
        expires: str = "2026-08-28T13:00:00Z",
    ) -> tuple[bytes, str, str]:
        body = plan["plan"]
        selected = body["runs"][0]["run_id"] if run_id is None else run_id
        limiter_digest = EXECUTOR._text_source_identity(
            self.fixture.path("harness/container/run_limited.py"),
            "harness/container/run_limited.py",
            "run limiter source",
        )["sha256"]
        payload = {
            "authorization_id": "33333333-3333-4333-8333-333333333333",
            "approved_by": "MARB operator",
            "issued_utc": "2026-08-28T11:55:00Z",
            "expires_utc": expires,
            "plan_sha256": plan["plan_sha256"],
            "planned_run_id": selected,
            "execution": {
                "execute": True,
                "model_calls": True,
                "provider_access": True,
                "spend_authorized": True,
            },
            "provider": {
                "protocol": EXECUTOR.PROVIDER_PROTOCOL,
                "model_id": body["cohort"]["model"]["id"],
                "endpoint": endpoint,
                "credential_env": None,
                "billing_mode": "local-no-charge",
                "settings": {
                    "temperature": 0,
                    "max_output_tokens": 4096,
                    "max_total_turns": 8,
                    "max_total_tool_calls": 32,
                    "max_run_python_calls": 8,
                    "max_request_bytes": 1_000_000,
                    "max_transcript_bytes": 4_000_000,
                    "max_wall_clock_seconds": 300,
                    "timeout_seconds": 30,
                    "seed": None,
                },
            },
            "container": {
                "image": IMAGE,
                "docker_executable": "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
                "docker_executable_sha256": "b" * 64,
                "cadclaw_pin_basis": EXECUTOR.CADCLAW_PIN_BASIS,
                "run_limiter_sha256": limiter_digest,
                **EXECUTOR.EXPECTED_RUNTIME,
            },
            "spend": {
                "currency": "USD",
                "max_cost_usd": None,
                "zero_cost_attested": True,
            },
            "implementation": {
                "source_revision": body["source"]["revision"],
                "planner_sha256": body["source"]["planner"]["sha256"],
                "executor_sha256": EXECUTOR._executor_source_identity()["sha256"],
                "isolated_container_sha256": EXECUTOR._text_source_identity(
                    Path(EXECUTOR.__file__).with_name("isolated_container.py"),
                    "harness/isolated_container.py",
                    "isolated container source",
                )["sha256"],
                "provider_transport_sha256": EXECUTOR._text_source_identity(
                    self.fixture.path("harness/provider_transport.py"),
                    "harness/provider_transport.py",
                    "provider transport source",
                )["sha256"],
                "run_limiter_sha256": limiter_digest,
                "runtime_contract_sha256": EXECUTOR.RUNTIME_CONTRACT_SHA256,
                "historical_runtime_contract_sha256": (
                    EXECUTOR.HISTORICAL_RUNTIME_CONTRACT_SHA256
                ),
                "cadclaw_calibration_sha256": EXECUTOR.CADCLAW_CALIBRATION_SHA256,
                "git_executable": self.git_path_text,
                "git_executable_sha256": self.git_sha256,
            },
        }
        envelope = EXECUTOR.make_authorization_envelope(payload)
        raw = canonical(envelope)
        digest = envelope["authorization_sha256"]
        literal = f"{EXECUTOR.EXECUTE_LITERAL_PREFIX}:{plan['plan_sha256']}:{selected}"
        return raw, digest, literal

    def fake_git_popen(self, argv, **kwargs) -> FakeGitProcess:
        arguments = list(argv)
        self.assertEqual(arguments[0], self.git_path_text)
        self.assertEqual(
            arguments[1:6],
            [
                "--no-replace-objects",
                "-c",
                f"safe.directory={self.repo.as_posix()}",
                "cat-file",
                "blob",
            ],
        )
        revision, separator, public_path = arguments[-1].partition(":")
        self.assertEqual(separator, ":")
        self.assertRegex(revision, r"^[0-9a-f]{40}$")
        self.assertEqual(kwargs["cwd"], self.repo)
        self.assertEqual(kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertNotIn("PATH", kwargs["env"])
        self.git_process_calls.append((arguments, kwargs))
        return FakeGitProcess(self.fixture.path(public_path).read_bytes())

    def git_authorization(self) -> dict[str, str]:
        return {
            "git_executable": self.git_path_text,
            "git_executable_sha256": self.git_sha256,
        }

    def verify_authorization_for_plan(
        self, plan: dict[str, object], raw: bytes, digest: str
    ) -> dict[str, object]:
        body = plan["plan"]
        return EXECUTOR.verify_authorization(
            raw,
            expected_sha256=digest,
            expected_plan_sha256=plan["plan_sha256"],
            expected_run_id=body["runs"][0]["run_id"],
            expected_model_id=body["cohort"]["model"]["id"],
            now=FIXED_NOW,
        )

    def reseal_authorization_payload(
        self, payload: dict[str, object]
    ) -> tuple[bytes, str]:
        envelope = EXECUTOR.make_authorization_envelope(payload)
        return canonical(envelope), envelope["authorization_sha256"]

    def execute_ready(
        self,
        plan: dict[str, object],
        *,
        provider=None,
        sandbox=None,
    ) -> tuple[dict[str, object], FakeProvider | object, FakeSandbox | object]:
        raw = canonical(plan)
        auth_raw, auth_digest, literal = self.authorization(plan)
        run_id = plan["plan"]["runs"][0]["run_id"]
        request_path = plan["plan"]["frozen_public_inputs"].get("change_request")
        marker = ""
        if request_path:
            marker = self.fixture.path(request_path["path"]).read_text(encoding="utf-8").strip()
        provider = provider or FakeProvider(self.trace, marker)
        sandbox = sandbox or FakeSandbox(self.repo / "placeholder", self.trace)

        def provider_factory(config, credential):
            self.trace.append("provider-created")
            self.assertIsNone(credential)
            return provider

        def sandbox_factory(workspace, input_root, config, attempt_id):
            self.assertEqual(input_root, workspace.parent / "inputs")
            self.assertEqual(config["image"], IMAGE)
            self.assertEqual(attempt_id, ATTEMPT_UUID)
            if isinstance(sandbox, FakeSandbox):
                sandbox.workspace = workspace
            return sandbox

        ticks = itertools.count(100.0, 0.25)
        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", side_effect=self.executing_module_paths
        ), mock.patch.object(
            EXECUTOR.subprocess, "Popen", side_effect=self.fake_git_popen
        ):
            result = EXECUTOR.execute_plan(
                self.repo,
                plan_raw=raw,
                expected_plan_sha256=plan["plan_sha256"],
                authorization_raw=auth_raw,
                expected_authorization_sha256=auth_digest,
                authorization_literal=literal,
                planned_run_id=run_id,
                provider_factory=provider_factory,
                sandbox_factory=sandbox_factory,
                now=lambda: FIXED_NOW,
                monotonic=lambda: next(ticks),
                uuid_factory=UUIDSequence(ATTEMPT_UUID, CONTINUITY_UUID),
                environ={},
            )
        return result, provider, sandbox

    def test_l2_uses_one_session_and_freezes_baseline_before_request(self) -> None:
        plan = self.ready_plan("L2-RESOLVE")
        registry_before = self.fixture.path("results/marb_runs.json").read_bytes()
        board = self.fixture.path("hf/space/board.json")
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b'{"sentinel":true}\n')
        board_before = board.read_bytes()

        result, provider, sandbox = self.execute_ready(plan)
        log = result["run_log"]
        self.assertEqual(log["status"], "completed_ungraded")
        self.assertEqual(log["driver_continuity_id"], str(CONTINUITY_UUID))
        self.assertEqual(
            [item["phase"] for item in log["phase_history"]], ["baseline", "change"]
        )
        self.assertEqual(
            {"baseline_step", "baseline_editable_source", "changed_step", "changed_editable_source"},
            set(log["artifacts"]),
        )
        self.assertEqual(log["provider"]["endpoint"], "https://api.example.test")
        self.assertEqual(log["provider"]["credential"], {"env_name": None, "status": "not_required"})
        self.assertEqual(log["usage"]["tokens"]["status"], "reported")
        self.assertEqual(log["usage"]["tokens"]["total_tokens"], 60)
        self.assertEqual(log["usage"]["cost"]["status"], "not_billed_attested")
        self.assertEqual(log["usage"]["cost"]["amount"], "0")
        self.assertEqual(log["workspace_inputs"]["container_root"], "/marb-input")
        self.assertEqual(log["workspace_inputs"]["kit_compatibility_root"], "/workspace/kit")
        self.assertIn("executor_owned_outputs", log["output_contract"])
        self.assertNotIn("executor_produced", log["output_contract"])
        self.assertNotIn("executor_produced_outputs", log["output_contract"])
        self.assertFalse(log["publication"]["registry_mutated"])
        self.assertFalse(log["publication"]["board_mutated"])
        self.assertTrue(sandbox.closed)
        self.assertLess(self.trace.index("sandbox-start"), self.trace.index("provider-created"))
        self.assertNotIn(provider.request_marker, provider.snapshots[0])
        self.assertNotIn(provider.request_marker, provider.snapshots[1])
        self.assertIn(provider.request_marker, provider.snapshots[2])
        self.assertIn(self.brief_payload.strip(), provider.snapshots[0])
        self.assertNotIn("Synthetic non-prompt preamble", provider.snapshots[0])
        self.assertNotIn("Synthetic non-prompt suffix", provider.snapshots[0])
        first_messages = json.loads(provider.snapshots[0])
        self.assertEqual(first_messages[1], {"role": "user", "content": self.brief_payload})
        baseline = result["run_dir"] / "evidence" / "baseline.step"
        changed = result["run_dir"] / "evidence" / "changed.step"
        self.assertNotEqual(baseline.read_bytes(), changed.read_bytes())
        self.assertEqual(self.fixture.path("results/marb_runs.json").read_bytes(), registry_before)
        self.assertEqual(board.read_bytes(), board_before)
        digest = hashlib.sha256((result["run_dir"] / "run_log.json").read_bytes()).hexdigest()
        self.assertEqual(result["run_log_sha256"], digest)
        self.assertEqual((result["run_dir"] / "run_log.sha256").read_text().strip(), digest)

    def test_l4_routes_two_phases_and_records_exact_runtime(self) -> None:
        plan = self.ready_plan("L4-ECO")
        result, _provider, _sandbox = self.execute_ready(plan)
        log = result["run_log"]
        self.assertEqual(log["task"]["id"], "L4-ECO")
        self.assertEqual(log["task"]["runtime_contract"], plan["plan"]["task"]["runtime_contract"])
        for key, value in EXECUTOR.EXPECTED_RUNTIME.items():
            self.assertEqual(log["container"][key], value)
            self.assertEqual(log["container"]["attestation"][key], value)

    def test_l1_routes_baseline_only_with_null_request(self) -> None:
        plan = self.fixture.build("L1-ASSEMBLE")
        result, provider, _sandbox = self.execute_ready(plan)
        log = result["run_log"]
        self.assertEqual(log["task"]["id"], "L1-ASSEMBLE")
        self.assertIsNone(log["task"]["change_request"])
        self.assertEqual([item["phase"] for item in log["phase_history"]], ["baseline"])
        self.assertEqual(len(provider.snapshots), 2)
        self.assertIn("final_step", log["artifacts"])
        self.assertNotIn("changed_step", log["artifacts"])

    def test_missing_literal_rejects_before_directory_provider_or_sandbox(self) -> None:
        plan = self.ready_plan()
        auth_raw, auth_digest, _literal = self.authorization(plan)
        run_id = plan["plan"]["runs"][0]["run_id"]
        bomb = mock.Mock(side_effect=AssertionError("must not be constructed"))
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "confirmation literal"):
            EXECUTOR.execute_plan(
                self.repo,
                plan_raw=canonical(plan),
                expected_plan_sha256=plan["plan_sha256"],
                authorization_raw=auth_raw,
                expected_authorization_sha256=auth_digest,
                authorization_literal="",
                planned_run_id=run_id,
                provider_factory=bomb,
                sandbox_factory=bomb,
                now=lambda: FIXED_NOW,
                environ={},
            )
        self.assertFalse((self.repo / "runs").exists())
        bomb.assert_not_called()

    def test_authorization_digest_tamper_expiry_and_endpoint_fail_closed(self) -> None:
        plan = self.ready_plan()
        run_id = plan["plan"]["runs"][0]["run_id"]
        raw, digest, _literal = self.authorization(plan)
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "independent approval"):
            EXECUTOR.verify_authorization(
                raw,
                expected_sha256="0" * 64,
                expected_plan_sha256=plan["plan_sha256"],
                expected_run_id=run_id,
                expected_model_id=plan["plan"]["cohort"]["model"]["id"],
                now=FIXED_NOW,
            )
        expired, expired_digest, _ = self.authorization(plan, expires="2026-08-28T11:59:59Z")
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "currently valid"):
            EXECUTOR.verify_authorization(
                expired,
                expected_sha256=expired_digest,
                expected_plan_sha256=plan["plan_sha256"],
                expected_run_id=run_id,
                expected_model_id=plan["plan"]["cohort"]["model"]["id"],
                now=FIXED_NOW,
            )
        for endpoint in (
            "https://user:password@example.test/v1",
            "https://example.test/v1?token=redacted",
            "https://example.test/v1#fragment",
        ):
            bad_raw, bad_digest, _ = self.authorization(plan, endpoint=endpoint)
            with self.subTest(endpoint=endpoint), self.assertRaises(EXECUTOR.ExecutorError):
                EXECUTOR.verify_authorization(
                    bad_raw,
                    expected_sha256=bad_digest,
                    expected_plan_sha256=plan["plan_sha256"],
                    expected_run_id=run_id,
                    expected_model_id=plan["plan"]["cohort"]["model"]["id"],
                    now=FIXED_NOW,
                )
        self.assertFalse((self.repo / "runs").exists())

    def test_runtime_contract_pin_hash_and_calibration_mismatches_fail_closed(self) -> None:
        plan = self.ready_plan()
        raw, _digest, _literal = self.authorization(plan)
        payload = json.loads(raw)["authorization"]
        cases = (
            ("runtime_contract", "marb-v0.12-h2b"),
            ("runtime_contract_sha256", "1" * 64),
            ("cadclaw_commit", EXECUTOR.SUPPORTED_L4_GRADE_CONTRACT["cadclaw_commit"]),
            (
                "cadclaw_source_manifest_sha256",
                EXECUTOR.HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256,
            ),
            ("cadclaw_calibration_sha256", "2" * 64),
            ("cadclaw_pin_basis", "marb_v0.12_frozen_functional_core"),
        )
        for key, value in cases:
            changed = copy.deepcopy(payload)
            changed["container"][key] = value
            changed_raw, changed_digest = self.reseal_authorization_payload(changed)
            with self.subTest(key=key), self.assertRaisesRegex(
                EXECUTOR.ExecutorError, "runtime|pin basis"
            ):
                self.verify_authorization_for_plan(
                    plan, changed_raw, changed_digest
                )

    def test_calibration_semantic_mutations_fail_closed_even_if_rehashed(self) -> None:
        contract = json.loads(EXECUTOR.RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8"))
        historical = json.loads(
            EXECUTOR.HISTORICAL_RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        calibration = json.loads(
            EXECUTOR.CADCLAW_CALIBRATION_PATH.read_text(encoding="utf-8")
        )
        EXECUTOR._validate_runtime_contract_content(
            contract, historical, calibration
        )

        mutations = {
            "incompatible": lambda value: value.__setitem__(
                "classification", "incompatible"
            ),
            "failed-check": lambda value: value.__setitem__(
                "failed_checks", ["candidate_runtime_exact"]
            ),
            "relabeled-scope": lambda value: value.__setitem__(
                "compatibility_scope", "all CADCLAW behavior"
            ),
            "stale-candidate": lambda value: value["source_control"].__setitem__(
                "candidate_commit", EXECUTOR.FROZEN_CADCLAW_COMMIT
            ),
            "wrong-candidate-tree": lambda value: value["source_control"].__setitem__(
                "candidate_tree", EXECUTOR.FROZEN_CADCLAW_TREE
            ),
            "stale-source-manifest": lambda value: value["source_manifests"][
                "source_to_wheel_inputs"
            ]["candidate"].__setitem__(
                "manifest_sha256",
                EXECUTOR.HISTORICAL_CADCLAW_SOURCE_MANIFEST_SHA256,
            ),
            "weakened-overlap-gate": lambda value: value[
                "configured_harness_calibration"
            ]["cases"][1]["candidate"].__setitem__("exit_code", 0),
        }
        for label, mutate in mutations.items():
            changed = copy.deepcopy(calibration)
            mutate(changed)
            with self.subTest(label=label), self.assertRaises(
                EXECUTOR.ExecutorError
            ):
                EXECUTOR._validate_runtime_contract_content(
                    contract, historical, changed
                )

    def test_l4_plan_keeps_exact_historical_grade_contract(self) -> None:
        plan = self.ready_plan("L4-ECO")
        self.assertEqual(
            plan["plan"]["task"]["runtime_contract"],
            EXECUTOR.SUPPORTED_L4_GRADE_CONTRACT,
        )
        EXECUTOR._assert_execution_ready(plan)
        for key, value in (
            ("cadclaw_commit", EXECUTOR.EXPECTED_RUNTIME["cadclaw_commit"]),
            ("invariant_gate_version", "marb_l4_eco_invariant.v0.13.0"),
            ("requested_change_grade_method", "marb_task_local_reference.v0.13"),
        ):
            changed = copy.deepcopy(plan)
            changed["plan"]["task"]["runtime_contract"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                EXECUTOR.ExecutorError, "historical contract"
            ):
                EXECUTOR._assert_execution_ready(changed)

    def test_git_authorization_rejects_bare_relative_wrong_basename_and_digest(self) -> None:
        plan = self.ready_plan()
        raw, _digest, _literal = self.authorization(plan)
        payload = json.loads(raw)["authorization"]
        wrong_name = self.git_path.with_name(
            "git-wrapper.exe" if os.name == "nt" else "git-wrapper"
        ).as_posix()
        cases = (
            ("git_executable", "git"),
            ("git_executable", "relative/git"),
            ("git_executable", wrong_name),
            ("git_executable_sha256", "0" * 63),
        )
        for key, value in cases:
            changed = copy.deepcopy(payload)
            changed["implementation"][key] = value
            changed_raw, changed_digest = self.reseal_authorization_payload(changed)
            with self.subTest(key=key, value=value), self.assertRaises(
                EXECUTOR.ExecutorError
            ):
                self.verify_authorization_for_plan(plan, changed_raw, changed_digest)

    def test_git_reader_uses_exact_absolute_binary_and_ignores_repo_shadow(self) -> None:
        shadow_name = "git.exe" if os.name == "nt" else "git"
        shadow = self.repo / shadow_name
        shadow.write_bytes(b"repo-root shadow must never execute\n")
        calls: list[tuple[list[str], dict[str, object]]] = []
        processes: list[FakeGitProcess] = []

        def factory(argv, **kwargs):
            calls.append((list(argv), kwargs))
            process = FakeGitProcess(b"authenticated committed bytes")
            processes.append(process)
            return process

        revision = "a" * 40
        blob = EXECUTOR._read_committed_blob_with_git(
            self.git_authorization(),
            self.repo,
            revision,
            "harness/cohort_executor.py",
            process_factory=factory,
        )
        self.assertEqual(blob, b"authenticated committed bytes")
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], self.git_path_text)
        self.assertNotEqual(argv[0], shadow.as_posix())
        self.assertEqual(
            argv,
            [
                self.git_path_text,
                "--no-replace-objects",
                "-c",
                f"safe.directory={self.repo.as_posix()}",
                "cat-file",
                "blob",
                f"{revision}:harness/cohort_executor.py",
            ],
        )
        self.assertNotIn("safe.directory=*", argv)
        self.assertEqual(kwargs["cwd"], self.repo)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertNotIn("PATH", kwargs["env"])
        self.assertEqual(processes[0].kills, 0)

    def test_git_digest_and_path_chain_drift_reject_before_spawn(self) -> None:
        bomb = mock.Mock(side_effect=AssertionError("Git process must not start"))
        self.git_path.write_bytes(b"changed executable bytes\n")
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "digest does not match"):
            EXECUTOR._read_committed_blob_with_git(
                self.git_authorization(),
                self.repo,
                "a" * 40,
                "README.md",
                process_factory=bomb,
            )
        bomb.assert_not_called()

        self.git_path.write_bytes(self.git_bytes)
        original_link_check = EXECUTOR._is_link_like

        def linked_component(path: Path) -> bool:
            if Path(path) == self.git_path.parent:
                return True
            return original_link_check(path)

        with mock.patch.object(
            EXECUTOR, "_is_link_like", side_effect=linked_component
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "unavailable or unsafe"):
            EXECUTOR._verify_host_git_executable(self.git_authorization())
        bomb.assert_not_called()

    def test_git_reader_reverifies_each_spawn_and_suppresses_replace_refs(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def replacement_aware_factory(argv, **kwargs):
            calls.append((list(argv), kwargs))
            guarded = (
                "--no-replace-objects" in argv
                and kwargs["env"].get("GIT_NO_REPLACE_OBJECTS") == "1"
            )
            return FakeGitProcess(b"original" if guarded else b"replacement")

        original_lock = EXECUTOR._locked_git_spawn_identity
        with mock.patch.object(
            EXECUTOR, "_locked_git_spawn_identity", wraps=original_lock
        ) as lock:
            first = EXECUTOR._read_committed_blob_with_git(
                self.git_authorization(),
                self.repo,
                "a" * 40,
                "README.md",
                process_factory=replacement_aware_factory,
            )
            second = EXECUTOR._read_committed_blob_with_git(
                self.git_authorization(),
                self.repo,
                "b" * 40,
                "README.md",
                process_factory=replacement_aware_factory,
            )
        self.assertEqual((first, second), (b"original", b"original"))
        self.assertEqual(lock.call_count, 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            all(call[0][0] == self.git_path_text for call in calls)
        )

    @unittest.skipUnless(os.name == "nt", "Windows file-share lock semantics only")
    def test_windows_git_identity_lock_denies_atomic_executable_replacement(self) -> None:
        replacement = self.git_path.with_name("replacement-git.exe")
        replacement.write_bytes(b"hostile replacement executable\n")
        attempt = {"denied": False, "argv0": None}

        def replacement_attempt_factory(argv, **_kwargs):
            attempt["argv0"] = argv[0]
            try:
                os.replace(replacement, self.git_path)
            except OSError:
                attempt["denied"] = True
            return FakeGitProcess(b"original committed bytes")

        blob = EXECUTOR._read_committed_blob_with_git(
            self.git_authorization(),
            self.repo,
            "a" * 40,
            "README.md",
            process_factory=replacement_attempt_factory,
        )
        self.assertEqual(blob, b"original committed bytes")
        self.assertTrue(attempt["denied"])
        self.assertEqual(attempt["argv0"], self.git_path_text)
        self.assertEqual(self.git_path.read_bytes(), self.git_bytes)
        self.assertTrue(replacement.is_file())

    def test_git_timeout_and_stdout_overflow_kill_wait_and_fail_closed(self) -> None:
        timed_out = FakeGitProcess(timeout_once=True)
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "cannot be read"):
            EXECUTOR._read_committed_blob_with_git(
                self.git_authorization(),
                self.repo,
                "a" * 40,
                "README.md",
                process_factory=lambda *_args, **_kwargs: timed_out,
            )
        self.assertEqual(timed_out.kills, 1)
        self.assertIn(EXECUTOR.GIT_CLEANUP_TIMEOUT_SECONDS, timed_out.wait_timeouts)

        overflow = FakeGitProcess(b"123456789")
        with mock.patch.object(
            EXECUTOR, "MAX_COMMITTED_BLOB_BYTES", 8
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "cannot be read"):
            EXECUTOR._read_committed_blob_with_git(
                self.git_authorization(),
                self.repo,
                "a" * 40,
                "README.md",
                process_factory=lambda *_args, **_kwargs: overflow,
            )
        self.assertGreaterEqual(overflow.kills, 1)
        self.assertTrue(overflow.wait_timeouts)

    def test_execute_plan_has_no_blob_reader_seam_and_records_authorized_git(self) -> None:
        parameters = inspect.signature(EXECUTOR.execute_plan).parameters
        self.assertNotIn("commit_blob_reader", parameters)
        self.assertNotIn("test_commit_blob_reader", parameters)
        plan = self.ready_plan()
        result, _provider, _sandbox = self.execute_ready(plan)
        self.assertGreater(len(self.git_process_calls), 0)
        self.assertEqual(
            result["run_log"]["source"]["git_executable"]["reader"],
            "authorized_git",
        )
        committed_paths = {
            call[0][-1].split(":", 1)[1] for call in self.git_process_calls
        }
        self.assertTrue(
            {
                "harness/container/runtime-contract.v0.13.json",
                "harness/container/runtime-contract.v0.12.json",
                "harness/container/cadclaw-calibration.fad0dd55.json",
            }.issubset(committed_paths)
        )
        implementation = result["run_log"]["source"]["implementation"]
        for key in (
            "runtime_contract",
            "historical_runtime_contract",
            "cadclaw_calibration",
        ):
            with self.subTest(key=key):
                self.assertEqual(implementation[key]["hash_mode"], "raw")

    def test_committed_runtime_evidence_drift_fails_before_provider(self) -> None:
        plan = self.ready_plan()
        raw, digest, _literal = self.authorization(plan)
        authorization = self.verify_authorization_for_plan(plan, raw, digest)

        def drifted_reader(repo_root: Path, revision: str, public_path: str) -> bytes:
            del repo_root, revision
            if public_path == "harness/container/cadclaw-calibration.fad0dd55.json":
                return b'{"classification":"incompatible"}\n'
            return self.fixture.path(public_path).read_bytes()

        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", side_effect=self.executing_module_paths
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "exact committed blob"):
            EXECUTOR._verify_committed_implementation(
                self.repo,
                plan["plan"],
                authorization,
                drifted_reader,
            )

    def test_committed_shared_runtime_probe_drift_fails_before_provider(self) -> None:
        plan = self.ready_plan()
        raw, digest, _literal = self.authorization(plan)
        authorization = self.verify_authorization_for_plan(plan, raw, digest)

        def drifted_reader(repo_root: Path, revision: str, public_path: str) -> bytes:
            del repo_root, revision
            if public_path == "harness/runtime_smoke_probes.py":
                return b"POSITIVE_PROVENANCE_AND_IMPORT_SOURCE = 'weakened'\n"
            return self.fixture.path(public_path).read_bytes()

        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", side_effect=self.executing_module_paths
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "exact committed blob"):
            EXECUTOR._verify_committed_implementation(
                self.repo,
                plan["plan"],
                authorization,
                drifted_reader,
            )

    def test_current_task_blocker_prevents_run_and_provider_activity(self) -> None:
        plan = self.fixture.build("L4-ECO")
        auth_raw, auth_digest, literal = self.authorization(plan)
        run_id = plan["plan"]["runs"][0]["run_id"]
        bomb = mock.Mock(side_effect=AssertionError("must not be constructed"))
        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", side_effect=self.executing_module_paths
        ), mock.patch.object(
            EXECUTOR.subprocess, "Popen", side_effect=self.fake_git_popen
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "evidence is not ready"):
            EXECUTOR.execute_plan(
                self.repo,
                plan_raw=canonical(plan),
                expected_plan_sha256=plan["plan_sha256"],
                authorization_raw=auth_raw,
                expected_authorization_sha256=auth_digest,
                authorization_literal=literal,
                planned_run_id=run_id,
                provider_factory=bomb,
                sandbox_factory=bomb,
                now=lambda: FIXED_NOW,
                environ={},
            )
        self.assertFalse((self.repo / "runs").exists())
        bomb.assert_not_called()

    def test_unregistered_local_collision_is_preserved_and_rejected(self) -> None:
        plan = self.ready_plan()
        run_id = plan["plan"]["runs"][0]["run_id"]
        collision = self.repo / "runs" / run_id
        collision.mkdir(parents=True)
        sentinel = collision / "keep.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        auth_raw, auth_digest, literal = self.authorization(plan)
        bomb = mock.Mock(side_effect=AssertionError("must not be constructed"))
        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", side_effect=self.executing_module_paths
        ), mock.patch.object(
            EXECUTOR.subprocess, "Popen", side_effect=self.fake_git_popen
        ), self.assertRaisesRegex(EXECUTOR.ExecutorError, "unregistered local evidence"):
            EXECUTOR.execute_plan(
                self.repo,
                plan_raw=canonical(plan),
                expected_plan_sha256=plan["plan_sha256"],
                authorization_raw=auth_raw,
                expected_authorization_sha256=auth_digest,
                authorization_literal=literal,
                planned_run_id=run_id,
                provider_factory=bomb,
                sandbox_factory=bomb,
                now=lambda: FIXED_NOW,
                environ={},
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertEqual(list((self.repo / "runs").iterdir()), [collision])

    def test_provider_failure_retains_failed_run_and_safe_log(self) -> None:
        plan = self.ready_plan()
        registry_before = self.fixture.path("results/marb_runs.json").read_bytes()
        with self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan, provider=FailingProvider())
        run_dir = caught.exception.run_dir
        self.assertTrue(run_dir.is_dir())
        log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        self.assertEqual(log["status"], "failed")
        self.assertEqual(log["failure"], {"category": "provider_failure", "phase": "baseline"})
        self.assertIsNone(log["usage"]["tokens"]["total_tokens"])
        self.assertEqual(log["usage"]["tokens"]["status"], "attempted_not_reported")
        self.assertEqual(log["usage"]["cost"]["status"], "attempted_not_reported")
        self.assertEqual(log["provider"]["calls"][0]["status"], "attempted_not_reported")
        self.assertEqual(log["artifacts"], {})
        contract = log["output_contract"]
        self.assertIn("executor_owned_outputs", contract)
        self.assertNotIn("executor_produced", contract)
        self.assertNotIn("executor_produced_outputs", contract)
        missing_owned = [
            path
            for path in contract["executor_owned_outputs"].values()
            if not (self.repo / path).exists()
        ]
        self.assertTrue(missing_owned)
        self.assertTrue((run_dir / "events.jsonl").is_file())
        self.assertTrue((self.repo / log["logical_slot_claim"]["path"]).is_file())
        self.assertEqual(self.fixture.path("results/marb_runs.json").read_bytes(), registry_before)

    def test_missing_baseline_artifact_blocks_change_and_retains_run(self) -> None:
        class NoArtifactProvider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, messages, tools, settings):
                self.calls += 1
                return EXECUTOR.ProviderResponse(
                    content="done",
                    tool_calls=(),
                    response_model="synthetic-model-v1",
                )

        plan = self.ready_plan()
        provider = NoArtifactProvider()
        with self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan, provider=provider)
        self.assertEqual(provider.calls, 1)
        log = json.loads((caught.exception.run_dir / "run_log.json").read_text())
        self.assertEqual(log["failure"]["category"], "artifact_failure")
        self.assertNotIn("changed_step", log["artifacts"])

    def test_step_replacement_after_discovery_retains_artifact_failure(self) -> None:
        plan = self.fixture.build("L1-ASSEMBLE")
        original_find = EXECUTOR._find_step_output

        def replace_after_discovery(workspace, deadline_check=None):
            discovered = original_find(workspace, deadline_check=deadline_check)
            replacement = workspace / ".replacement.step"
            replacement.write_bytes(discovered.path.read_bytes())
            os.replace(replacement, discovered.path)
            return discovered

        with mock.patch.object(
            EXECUTOR, "_find_step_output", side_effect=replace_after_discovery
        ), self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan)

        log = json.loads((caught.exception.run_dir / "run_log.json").read_text())
        self.assertEqual(log["failure"]["category"], "artifact_failure")
        self.assertNotIn("baseline_step", log["artifacts"])
        self.assertFalse((caught.exception.run_dir / "evidence" / "baseline.step").exists())

    def test_partial_run_never_labels_uncaptured_owned_outputs_as_produced(self) -> None:
        class FailOnChangeProvider(FakeProvider):
            def complete(self, messages, tools, settings):
                if self.ordinal >= 2:
                    raise EXECUTOR.ProviderCallError("sanitized change failure")
                return super().complete(messages, tools, settings)

        plan = self.ready_plan()
        provider = FailOnChangeProvider(self.trace, "synthetic change")
        with self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan, provider=provider)
        log = json.loads((caught.exception.run_dir / "run_log.json").read_text())
        self.assertEqual(log["status"], "partial")
        self.assertIn("baseline_step", log["artifacts"])
        self.assertNotIn("changed_step", log["artifacts"])
        contract = log["output_contract"]
        self.assertIn("executor_owned_outputs", contract)
        self.assertFalse(any("produced" in key for key in contract))
        changed_path = contract["executor_owned_outputs"]["changed_step"]
        self.assertFalse((self.repo / changed_path).exists())

    def test_usage_null_semantics_and_real_zero_are_distinct(self) -> None:
        empty = EXECUTOR._usage_summary([], "metered", False)
        self.assertEqual(empty["tokens"]["status"], "not_incurred")
        self.assertIsNone(empty["tokens"]["total_tokens"])
        missing = EXECUTOR._usage_summary(
            [{"usage": None, "cost_usd": None}], "metered", False
        )
        self.assertEqual(missing["tokens"]["status"], "provider_not_reported")
        self.assertIsNone(missing["cost"]["amount"])
        zero = EXECUTOR._usage_summary(
            [
                {
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "cost_usd": "0",
                }
            ],
            "metered",
            False,
        )
        self.assertEqual(zero["tokens"]["total_tokens"], 0)
        self.assertEqual(zero["cost"], {"status": "reported", "currency": "USD", "amount": "0"})

    def test_deterministic_source_zip_and_protected_write_paths(self) -> None:
        run_dir = self.repo / "runs" / "zip-test--11111111-1111-4111-8111-111111111111"
        workspace = run_dir / "workspace"
        evidence = run_dir / "evidence"
        workspace.mkdir(parents=True)
        evidence.mkdir()
        (workspace / "z.py").write_text("print('z')\n", encoding="utf-8")
        (workspace / "a.py").write_text("print('a')\n", encoding="utf-8")
        first = EXECUTOR._deterministic_source_zip(
            workspace, set(), evidence / "one.zip", run_dir
        )
        second = EXECUTOR._deterministic_source_zip(
            workspace, set(), evidence / "two.zip", run_dir
        )
        self.assertEqual((evidence / "one.zip").read_bytes(), (evidence / "two.zip").read_bytes())
        self.assertEqual(first["sha256"], second["sha256"])
        with zipfile.ZipFile(evidence / "one.zip") as archive:
            self.assertEqual(
                archive.namelist(), ["MARB_SOURCE_MANIFEST.json", "a.py", "z.py"]
            )
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
        collision = workspace / self.fixture.member_path
        collision.parent.mkdir(parents=True)
        collision.write_text("model-authored collision\n", encoding="utf-8")
        collision_zip = EXECUTOR._deterministic_source_zip(
            workspace,
            {self.fixture.member_path},
            evidence / "collision.zip",
            run_dir,
        )
        self.assertIn(
            self.fixture.member_path,
            {item["path"] for item in collision_zip["manifest"]["files"]},
        )
        written: set[str] = set()
        for unsafe in (
            "../publishing/site.py",
            ".git/config.py",
            "C:/escape.py",
            "nested/.MARB_probe.py",
            "nested/.MaRb_probe.py",
        ):
            result = EXECUTOR._write_model_file(
                workspace, {"path": unsafe, "content": "redacted"}, written
            )
            self.assertTrue(result.startswith("error:"))
        self.assertFalse((self.repo / "publishing").exists())

        reserved = workspace / "nested" / ".MARB_probe.py"
        reserved.parent.mkdir(exist_ok=True)
        reserved.write_text("print('reserved')\n", encoding="utf-8")
        with self.assertRaisesRegex(
            EXECUTOR.ArtifactError, "executor-reserved file"
        ):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "case-reserved.zip", run_dir
            )
        self.assertFalse((evidence / "case-reserved.zip").exists())
        reserved.unlink()

    def test_source_zip_rejects_inventory_member_races(self) -> None:
        run_dir = self.repo / "runs" / "zip-race--11111111-1111-4111-8111-111111111111"
        workspace = run_dir / "workspace"
        evidence = run_dir / "evidence"
        workspace.mkdir(parents=True)
        evidence.mkdir()
        source = workspace / "solution.py"
        source.write_bytes(b"print('original')\n")
        original_inventory = EXECUTOR._inventory_workspace

        def inventory_then_remove(root: Path, *, deadline_check=None, content_bound=False):
            inventory = original_inventory(
                root, deadline_check=deadline_check, content_bound=content_bound
            )
            source.unlink()
            return inventory

        with mock.patch.object(
            EXECUTOR, "_inventory_workspace", side_effect=inventory_then_remove
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "changed after workspace inventory"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "disappeared.zip", run_dir
            )
        self.assertFalse((evidence / "disappeared.zip").exists())

        source.write_bytes(b"print('original')\n")

        def inventory_then_resize(root: Path, *, deadline_check=None, content_bound=False):
            inventory = original_inventory(
                root, deadline_check=deadline_check, content_bound=content_bound
            )
            source.write_bytes(b"print('changed and larger')\n")
            return inventory

        with mock.patch.object(
            EXECUTOR, "_inventory_workspace", side_effect=inventory_then_resize
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "changed after workspace inventory"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "resized.zip", run_dir
            )
        self.assertFalse((evidence / "resized.zip").exists())

        original_bytes = b"print('original')\n"
        replacement_bytes = b"print('tampered')\n"
        self.assertEqual(len(original_bytes), len(replacement_bytes))
        source.write_bytes(original_bytes)

        def inventory_then_same_size_replace(
            root: Path, *, deadline_check=None, content_bound=False
        ):
            inventory = original_inventory(
                root, deadline_check=deadline_check, content_bound=content_bound
            )
            identity = next(item for item in inventory if item["path"] == "solution.py")
            source.write_bytes(replacement_bytes)
            current = source.stat()
            os.utime(source, ns=(current.st_atime_ns, identity["mtime_ns"]))
            after = source.stat()
            self.assertEqual(
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                (
                    identity["device"],
                    identity["inode"],
                    identity["bytes"],
                    identity["mtime_ns"],
                ),
            )
            return inventory

        with mock.patch.object(
            EXECUTOR, "_inventory_workspace", side_effect=inventory_then_same_size_replace
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "changed after workspace inventory"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "same-size-replaced.zip", run_dir
            )
        self.assertFalse((evidence / "same-size-replaced.zip").exists())

        source.write_bytes(original_bytes)
        late = workspace / "late-safe.txt"
        inventory_calls = 0

        def inventory_then_add(root: Path, *, deadline_check=None, content_bound=False):
            nonlocal inventory_calls
            inventory_calls += 1
            inventory = original_inventory(
                root, deadline_check=deadline_check, content_bound=content_bound
            )
            if inventory_calls == 1:
                late.write_text("late safe file\n", encoding="utf-8")
            return inventory

        with mock.patch.object(
            EXECUTOR, "_inventory_workspace", side_effect=inventory_then_add
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "workspace changed during capture"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "late-added.zip", run_dir
            )
        self.assertTrue(late.is_file())
        self.assertFalse((evidence / "late-added.zip").exists())

    def test_source_zip_final_content_inventory_rejects_post_read_mutation(self) -> None:
        run_dir = self.repo / "runs" / "zip-post-read--11111111-1111-4111-8111-111111111111"
        workspace = run_dir / "workspace"
        evidence = run_dir / "evidence"
        workspace.mkdir(parents=True)
        evidence.mkdir()
        source = workspace / "a.py"
        original_bytes = b"print('original')\n"
        replacement_bytes = b"print('tampered')\n"
        source.write_bytes(original_bytes)
        initial = source.stat()
        original_read = EXECUTOR._stream_read_bounded
        mutated = False

        def read_then_mutate(path: Path, label: str, max_bytes: int, *, deadline_check=None):
            nonlocal mutated
            raw = original_read(
                path, label, max_bytes, deadline_check=deadline_check
            )
            if path == source and not mutated:
                mutated = True
                source.write_bytes(replacement_bytes)
                current = source.stat()
                os.utime(source, ns=(current.st_atime_ns, initial.st_mtime_ns))
            return raw

        with mock.patch.object(
            EXECUTOR, "_stream_read_bounded", side_effect=read_then_mutate
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "workspace changed during capture"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), evidence / "post-read-mutated.zip", run_dir
            )
        self.assertTrue(mutated)
        self.assertFalse((evidence / "post-read-mutated.zip").exists())

    def test_source_zip_publication_failures_leave_no_target_or_temporary(self) -> None:
        run_dir = self.repo / "runs" / "zip-publish--11111111-1111-4111-8111-111111111111"
        workspace = run_dir / "workspace"
        evidence = run_dir / "evidence"
        workspace.mkdir(parents=True)
        evidence.mkdir()
        (workspace / "solution.py").write_text("print('safe')\n", encoding="utf-8")

        def paths(name: str) -> tuple[Path, Path]:
            target = evidence / name
            return target, target.with_name(f".{target.name}.marb-tmp")

        original_path_open = Path.open
        short_target, short_temporary = paths("short-write.zip")

        class ShortWriter:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, payload: bytes) -> int:
                return len(payload) - 1

        def short_open(path: Path, *args, **kwargs):
            if path == short_temporary and args and args[0] == "xb":
                return ShortWriter()
            return original_path_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", new=short_open), self.assertRaisesRegex(
            EXECUTOR.ArtifactError, "cannot be published safely"
        ):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), short_target, run_dir
            )
        self.assertFalse(short_target.exists())
        self.assertFalse(short_temporary.exists())

        fsync_target, fsync_temporary = paths("fsync.zip")
        with mock.patch.object(
            EXECUTOR.os, "fsync", side_effect=OSError("injected fsync failure")
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "cannot be published safely"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), fsync_target, run_dir
            )
        self.assertFalse(fsync_target.exists())
        self.assertFalse(fsync_temporary.exists())

        hash_target, hash_temporary = paths("hash.zip")
        original_digest = EXECUTOR._stream_file_digest

        def fail_temporary_digest(path: Path, *args, **kwargs):
            if path == hash_temporary:
                raise EXECUTOR.ArtifactError("injected hash failure")
            return original_digest(path, *args, **kwargs)

        with mock.patch.object(
            EXECUTOR, "_stream_file_digest", side_effect=fail_temporary_digest
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "injected hash failure"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), hash_target, run_dir
            )
        self.assertFalse(hash_target.exists())
        self.assertFalse(hash_temporary.exists())

        deadline_target, deadline_temporary = paths("deadline.zip")

        def expire_after_temporary_write() -> None:
            if deadline_temporary.exists():
                raise EXECUTOR.ExecutorError("injected deadline")

        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "injected deadline"):
            EXECUTOR._deterministic_source_zip(
                workspace,
                set(),
                deadline_target,
                run_dir,
                deadline_check=expire_after_temporary_write,
            )
        self.assertFalse(deadline_target.exists())
        self.assertFalse(deadline_temporary.exists())

        identity_target, identity_temporary = paths("identity.zip")
        with mock.patch.object(
            EXECUTOR,
            "_file_identity",
            side_effect=EXECUTOR.ArtifactError("injected identity failure"),
        ), self.assertRaisesRegex(EXECUTOR.ArtifactError, "injected identity failure"):
            EXECUTOR._deterministic_source_zip(
                workspace, set(), identity_target, run_dir
            )
        self.assertFalse(identity_target.exists())
        self.assertFalse(identity_temporary.exists())

    def test_fusion_route_fails_closed(self) -> None:
        envelope = self.ready_plan()
        forged = copy.deepcopy(envelope)
        forged["plan"]["cohort"]["driver"]["id"] = "fusion"
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "CadQuery"):
            EXECUTOR._assert_execution_ready(forged)

    def test_permanent_slot_claim_is_atomic_and_survives_missing_attempt_dir(self) -> None:
        runs_root = self.repo / "runs"
        slot = "synthetic-concurrent-slot-01"
        barrier = threading.Barrier(2)
        original_check = EXECUTOR._assert_no_local_slot_collision
        outcomes: list[tuple[str, object]] = []

        def synchronized_check(root: Path, run_id: str) -> None:
            original_check(root, run_id)
            barrier.wait(timeout=5)

        def allocate(attempt: uuid.UUID) -> None:
            try:
                outcomes.append(("ok", EXECUTOR._allocate_run_dir(runs_root, slot, attempt)))
            except BaseException as exc:
                outcomes.append(("error", exc))

        attempts = (
            uuid.UUID("44444444-4444-4444-8444-444444444444"),
            uuid.UUID("55555555-5555-4555-8555-555555555555"),
        )
        with mock.patch.object(
            EXECUTOR, "_assert_no_local_slot_collision", side_effect=synchronized_check
        ):
            threads = [threading.Thread(target=allocate, args=(attempt,)) for attempt in attempts]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        winners = [value for status, value in outcomes if status == "ok"]
        failures = [value for status, value in outcomes if status == "error"]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], EXECUTOR.ExecutorError)
        self.assertIn("permanent claim", str(failures[0]))
        _attempt, attempt_dir, claim = winners[0]
        claim_path = self.repo / claim["path"]
        self.assertTrue(claim_path.is_file())
        attempt_dir.rmdir()
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "permanent claim"):
            EXECUTOR._allocate_run_dir(
                runs_root,
                slot,
                uuid.UUID("66666666-6666-4666-8666-666666666666"),
            )
        self.assertTrue(claim_path.is_file())

    def test_journal_final_event_precedes_and_is_bound_into_retained_inventory(self) -> None:
        run_dir = self.repo / "runs" / "journal-test--11111111-1111-4111-8111-111111111111"
        run_dir.mkdir(parents=True)
        events_path = run_dir / "events.jsonl"
        EXECUTOR._append_event(events_path, 1, "run_directory_created", "preparing", "2026-08-28T12:00:00.000Z")
        evidence = run_dir / "evidence"
        evidence.mkdir()
        (evidence / "retained.txt").write_text("retained\n", encoding="utf-8")
        run_log = {"status": "failed", "artifacts": {}, "failure": {"category": "test"}}
        digest, sequence = EXECUTOR._seal_run_journal(
            run_dir,
            run_log,
            events_path,
            1,
            phase="preparing",
            ended_utc="2026-08-28T12:00:01.000Z",
            forbidden_secrets=(),
        )
        self.assertEqual(sequence, 2)
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "run_finalized")
        inventory = json.loads((run_dir / "artifact_inventory.json").read_text())
        retained = {item["path"]: item for item in inventory["files"]}
        self.assertEqual(retained["events.jsonl"]["sha256"], run_log["journal"]["events"]["sha256"])
        self.assertEqual(
            run_log["journal"]["retained_inventory"]["sha256"],
            hashlib.sha256((run_dir / "artifact_inventory.json").read_bytes()).hexdigest(),
        )
        self.assertEqual(digest, hashlib.sha256((run_dir / "run_log.json").read_bytes()).hexdigest())

    def test_early_setup_failure_is_retained_and_sealed(self) -> None:
        plan = self.ready_plan()
        with mock.patch.object(
            EXECUTOR,
            "_bind_planned_outputs",
            side_effect=EXECUTOR.ArtifactError("sanitized setup failure"),
        ), self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan)
        run_dir = caught.exception.run_dir
        log = json.loads((run_dir / "run_log.json").read_text())
        self.assertEqual(log["failure"], {"category": "artifact_failure", "phase": "preparing"})
        self.assertEqual(log["journal"]["status"], "sealed")
        self.assertTrue((run_dir / "artifact_inventory.json").is_file())
        self.assertTrue((self.repo / log["logical_slot_claim"]["path"]).is_file())
        self.assertEqual(self.trace, [])

    def test_cli_retained_failure_is_structured_relative_and_has_no_traceback(self) -> None:
        plan_path = self.repo / "plan.json"
        auth_path = self.repo / "authorization.json"
        plan_path.write_bytes(b"{}\n")
        auth_path.write_bytes(b"{}\n")
        retained = self.repo / "runs" / "cli-test--11111111-1111-4111-8111-111111111111"
        retained.mkdir(parents=True)
        stderr = io.StringIO()
        with mock.patch.object(
            EXECUTOR,
            "execute_plan",
            side_effect=EXECUTOR.RunExecutionError("provider_failure", retained),
        ), contextlib.redirect_stderr(stderr):
            status = EXECUTOR.main(
                [
                    "execute",
                    "--plan", str(plan_path),
                    "--expected-plan-sha256", "a" * 64,
                    "--authorization", str(auth_path),
                    "--expected-authorization-sha256", "b" * 64,
                    "--authorize-execution", "synthetic",
                    "--slot", "cli-test",
                    "--repo-root", str(self.repo),
                ]
            )
        output = json.loads(stderr.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(output["run_dir"], retained.relative_to(self.repo).as_posix())
        self.assertNotIn(str(self.repo), stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_oserror_omits_absolute_path_and_traceback(self) -> None:
        absolute = str(self.repo / "private" / "authorization.json")
        stderr = io.StringIO()
        with mock.patch.object(EXECUTOR, "_stable_read", side_effect=OSError(absolute)), contextlib.redirect_stderr(stderr):
            status = EXECUTOR.main(
                [
                    "seal-authorization",
                    "--payload", absolute,
                    "--output", str(self.repo / "sealed.json"),
                ]
            )
        output = json.loads(stderr.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(output["category"], "filesystem_error")
        self.assertNotIn(absolute, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_exact_executing_module_binding_rejects_one_outside_path(self) -> None:
        plan = self.ready_plan()
        auth_raw, auth_digest, literal = self.authorization(plan)
        run_id = plan["plan"]["runs"][0]["run_id"]
        paths = self.executing_module_paths()
        paths["provider_transport"] = Path(EXECUTOR.__file__).resolve().with_name("provider_transport.py")
        bomb = mock.Mock(side_effect=AssertionError("must not be constructed"))
        with mock.patch.object(
            EXECUTOR, "_executing_module_paths", return_value=paths
        ), mock.patch.object(
            EXECUTOR.subprocess, "Popen", side_effect=self.fake_git_popen
        ), self.assertRaisesRegex(
            EXECUTOR.ExecutorError, "executing provider_transport module is outside"
        ):
            EXECUTOR.execute_plan(
                self.repo,
                plan_raw=canonical(plan),
                expected_plan_sha256=plan["plan_sha256"],
                authorization_raw=auth_raw,
                expected_authorization_sha256=auth_digest,
                authorization_literal=literal,
                planned_run_id=run_id,
                provider_factory=bomb,
                sandbox_factory=bomb,
                now=lambda: FIXED_NOW,
                environ={},
            )
        bomb.assert_not_called()
        self.assertFalse((self.repo / "runs").exists())

    def test_provider_model_mismatch_is_rejected_and_retained(self) -> None:
        plan = self.ready_plan()
        provider = OneShotProvider(
            EXECUTOR.ProviderResponse(
                content="done",
                tool_calls=(),
                response_model="different-model",
            )
        )
        with self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan, provider=provider)
        log = json.loads((caught.exception.run_dir / "run_log.json").read_text())
        self.assertEqual(log["failure"]["category"], "provider_failure")
        self.assertEqual(log["provider"]["calls"][0]["status"], "response_rejected")

    def test_malformed_provider_transport_metadata_is_rejected_and_retained(self) -> None:
        plan = self.ready_plan()
        provider = OneShotProvider(
            EXECUTOR.ProviderResponse(
                content="done",
                tool_calls=(),
                response_model="synthetic-model-v1",
                transport_request_sha256="not-a-digest",
            )
        )
        with self.assertRaises(EXECUTOR.RunExecutionError) as caught:
            self.execute_ready(plan, provider=provider)
        log = json.loads((caught.exception.run_dir / "run_log.json").read_text())
        self.assertEqual(log["failure"]["category"], "provider_failure")
        self.assertEqual(log["provider"]["calls"][0]["status"], "response_rejected")

    def test_oversized_read_and_python_are_rejected_before_read_or_sandbox(self) -> None:
        workspace = self.repo / "bounded-tools"
        workspace.mkdir()
        oversized_read = workspace / "large.txt"
        oversized_read.write_bytes(b"x" * (EXECUTOR.MAX_READBACK_BYTES + 1))
        with mock.patch.object(
            EXECUTOR, "_stream_read_bounded", side_effect=AssertionError("must not read")
        ):
            self.assertIn(
                "readback size limit",
                EXECUTOR._read_model_output(workspace, {"path": "large.txt"}),
            )
        oversized_script = workspace / "large.py"
        oversized_script.write_bytes(b"x" * (EXECUTOR.MAX_TOOL_TEXT_BYTES + 1))
        sandbox = mock.Mock()
        self.assertEqual(
            EXECUTOR._run_model_python(
                workspace,
                sandbox,
                {"path": "large.py"},
                {"large.py"},
                1,
            ),
            "error: isolated Python execution failed",
        )
        sandbox.run_python.assert_not_called()

    def test_staged_kit_extra_root_member_and_case_variant_step_fail_closed(self) -> None:
        staged = self.repo / "staged"
        staged.mkdir()
        expected = staged / "kit.step"
        expected.write_bytes(b"expected\n")
        manifest = {
            "files": [
                {
                    "path": "kit.step",
                    "sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
                    "bytes": expected.stat().st_size,
                }
            ]
        }
        original = self.repo / "staged-original"
        staged.rename(original)
        replacement = self.repo / "staged-replacement"
        replacement.mkdir()
        (replacement / "kit.step").write_bytes(b"expected\n")
        (replacement / "unexpected.txt").write_text("swap residue\n", encoding="utf-8")
        replacement.rename(staged)
        with self.assertRaisesRegex(EXECUTOR.ArtifactError, "inventory changed"):
            EXECUTOR._verify_staged_kit(staged, manifest)
        workspace = self.repo / "step-case"
        workspace.mkdir()
        (workspace / "Export.step").write_bytes(b"ISO-10303-21\n")
        with self.assertRaisesRegex(EXECUTOR.ArtifactError, "exact published spelling"):
            EXECUTOR._find_step_output(workspace)

    def test_discovered_step_copy_accepts_unchanged_identity(self) -> None:
        workspace = self.repo / "step-copy-unchanged"
        workspace.mkdir()
        source = workspace / "export.step"
        payload = b"ISO-10303-21\nUNCHANGED\n"
        source.write_bytes(payload)
        run_dir = self.repo / "runs" / "unchanged"
        target = run_dir / "evidence" / "baseline.step"
        target.parent.mkdir(parents=True)

        discovered = EXECUTOR._find_step_output(workspace)
        self.assertEqual(discovered.path, source)
        record = EXECUTOR._copy_discovered_step(discovered, target, run_dir)

        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(record["bytes"], len(payload))

    def test_discovered_step_copy_rejects_same_inode_rewrite_with_restored_mtime(
        self,
    ) -> None:
        workspace = self.repo / "step-copy-rewritten"
        workspace.mkdir()
        source = workspace / "export.step"
        original = b"ISO-10303-21\nORIGINAL\n"
        replacement = b"ISO-10303-21\nREPLACED\n"
        self.assertEqual(len(original), len(replacement))
        source.write_bytes(original)
        discovered = EXECUTOR._find_step_output(workspace)
        original_stat = source.lstat()

        with source.open("r+b") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.utime(
            source,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        rewritten_stat = source.lstat()
        self.assertEqual(rewritten_stat.st_ino, original_stat.st_ino)
        self.assertEqual(rewritten_stat.st_size, original_stat.st_size)
        self.assertEqual(rewritten_stat.st_mtime_ns, original_stat.st_mtime_ns)

        run_dir = self.repo / "runs" / "rewritten"
        target = run_dir / "evidence" / "baseline.step"
        target.parent.mkdir(parents=True)
        with self.assertRaises(EXECUTOR.ArtifactError):
            EXECUTOR._copy_discovered_step(discovered, target, run_dir)
        self.assertFalse(target.exists())

    def test_discovered_step_copy_rejects_atomic_identical_content_replacement(
        self,
    ) -> None:
        workspace = self.repo / "step-copy-replaced"
        workspace.mkdir()
        source = workspace / "export.step"
        payload = b"ISO-10303-21\nIDENTICAL\n"
        source.write_bytes(payload)
        discovered = EXECUTOR._find_step_output(workspace)
        original_stat = source.lstat()
        replacement = workspace / "replacement.tmp"
        replacement.write_bytes(payload)
        os.replace(replacement, source)
        replaced_stat = source.lstat()
        self.assertNotEqual(replaced_stat.st_ino, original_stat.st_ino)
        self.assertEqual(source.read_bytes(), payload)

        run_dir = self.repo / "runs" / "replaced"
        target = run_dir / "evidence" / "baseline.step"
        target.parent.mkdir(parents=True)
        with self.assertRaises(EXECUTOR.ArtifactError):
            EXECUTOR._copy_discovered_step(discovered, target, run_dir)
        self.assertFalse(target.exists())

    def test_late_nonzero_response_records_both_violations_with_cost_first(self) -> None:
        immutable_inputs = self.repo / "late-inputs"
        immutable_inputs.mkdir()
        workspace = self.repo / "late-workspace"
        workspace.mkdir()
        state = {"late": False}

        class LateCostProvider:
            def complete(self, messages, tools, settings):
                state["late"] = True
                return EXECUTOR.ProviderResponse(
                    content="done",
                    tool_calls=(),
                    cost_usd="1",
                    response_model="synthetic-model-v1",
                )

        calls: list[dict[str, object]] = []
        budget = EXECUTOR.ExecutionBudget(
            max_total_turns=1,
            max_total_tool_calls=1,
            max_run_python_calls=1,
            max_request_bytes=1_000_000,
            max_transcript_bytes=1_000_000,
            deadline_monotonic=300,
            billing_mode="local-no-charge",
            max_cost_usd=None,
            zero_cost_attested=True,
        )
        with self.assertRaisesRegex(EXECUTOR.BudgetError, "zero-cost authorization"):
            EXECUTOR._phase_loop(
                LateCostProvider(),
                mock.Mock(),
                workspace,
                [{"role": "user", "content": "synthetic"}],
                {"timeout_seconds": 30},
                set(),
                calls,
                "baseline",
                budget,
                "synthetic-model-v1",
                lambda: FIXED_NOW,
                lambda: 301.0 if state["late"] else 0.0,
                {"files": []},
                immutable_inputs,
                (),
                lambda _event, _phase: None,
            )
        self.assertEqual(
            calls[0]["policy_violations"],
            ["wall_deadline", "zero_cost_attestation"],
        )
        self.assertEqual(calls[0]["status"], "zero_cost_attestation_contradicted")


if __name__ == "__main__":
    unittest.main()
