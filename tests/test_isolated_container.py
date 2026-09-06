"""Regressions for the digest-pinned, attest-before-start Docker sandbox."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock

import harness.isolated_container as isolated
from harness.isolated_container import (
    CONTAINER_EXPORT,
    CONTAINER_HOST_WORKSPACE,
    CONTAINER_INPUT,
    CONTAINER_INPUT_ROOT,
    CONTAINER_LIMITER,
    CONTAINER_PYTHON,
    CONTAINER_WORKSPACE,
    CPU_LIMIT,
    EXPORT_TMPFS_SPEC,
    MAX_WORKSPACE_FILE_BYTES,
    MEMORY_LIMIT,
    MEMORY_LIMIT_BYTES,
    MINIMAL_ENV,
    NANO_CPU_LIMIT,
    NOFILE_LIMIT,
    OWNERSHIP_LABEL,
    PID_LIMIT,
    STATUS_MEMBER,
    TMPFS_SPEC,
    WORKSPACE_TMPFS_SPEC,
    IsolatedDockerPython,
    IsolationError,
    PolicyReadbackResult,
    validate_image_reference,
)


IMAGE = "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
CONTAINER_ID = "c" * 64
FIXED_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
CONTAINER_NAME = "marb-isolated-" + FIXED_UUID.hex
DOCKER_EXE = isolated.DEFAULT_DOCKER_EXECUTABLE
HOST_SOURCE = {
    "PATH": "C:\\safe-bin",
    "SYSTEMROOT": "C:\\Windows",
    "OPENAI_API_KEY": "must-not-cross-process-boundary",
    "MARB_GATED_READ_TOKEN": "must-not-cross-process-boundary",
    "HTTPS_PROXY": "must-not-cross-process-boundary",
}


class FakeRunner:
    def __init__(
        self,
        responses: list[
            subprocess.CompletedProcess[str]
            | BaseException
            | Callable[[list[str]], subprocess.CompletedProcess[str]]
        ],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[str], float | None, dict[str, str]]] = []

    def __call__(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        self.calls.append((command, timeout, dict(env or {})))
        if not self.responses:
            raise AssertionError("unexpected command invocation")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(command)
        return response


def completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def image_metadata(*, volumes: object = None, labels: object = None) -> str:
    return json.dumps(
        {
            "Id": IMAGE_ID,
            "RepoDigests": [IMAGE],
            "Config": {"Labels": labels, "Volumes": volumes},
        }
    )


def expected_container_command(script: str = "tool.py") -> list[str]:
    return [
        "-i",
        *MINIMAL_ENV,
        CONTAINER_PYTHON,
        "-I",
        "-B",
        CONTAINER_LIMITER,
        script,
    ]


def write_workspace_tar(
    path: Path,
    *,
    child_returncode: int = 0,
    files: Mapping[str, bytes] | None = None,
) -> None:
    status = (
        json.dumps(
            {
                "schema": "marb_container_execution_status.v1",
                "returncode": child_returncode,
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    members = {"tool.py": b"print('exported')\n"} if files is None else dict(files)
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        status_info = tarfile.TarInfo(STATUS_MEMBER)
        status_info.size = len(status)
        status_info.mode = 0o600
        status_info.mtime = 0
        archive.addfile(status_info, io.BytesIO(status))
        for name, raw in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))


def container_metadata(
    workspace: Path,
    input_root: Path,
    export_archive: Path,
) -> dict[str, object]:
    command = expected_container_command()
    tmpfs = {
        item.split(":", 1)[0]: item.split(":", 1)[1]
        for item in (TMPFS_SPEC, WORKSPACE_TMPFS_SPEC, EXPORT_TMPFS_SPEC)
    }
    return {
        "Id": CONTAINER_ID,
        "Name": f"/{CONTAINER_NAME}",
        "Image": IMAGE_ID,
        "Path": "/usr/bin/env",
        "Args": command,
        "State": {"Status": "created", "Running": False},
        "Config": {
            "Image": IMAGE,
            "User": "65532:65532",
            "WorkingDir": CONTAINER_WORKSPACE,
            "Entrypoint": ["/usr/bin/env"],
            "Cmd": command,
            "Volumes": None,
            "ExposedPorts": None,
            "Healthcheck": {"Test": ["NONE"]},
            "Labels": {OWNERSHIP_LABEL: CONTAINER_NAME},
        },
        "HostConfig": {
            "NetworkMode": "none",
            "IpcMode": "none",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "AutoRemove": False,
            "PidsLimit": int(PID_LIMIT),
            "Memory": MEMORY_LIMIT_BYTES,
            "MemorySwap": MEMORY_LIMIT_BYTES,
            "NanoCpus": NANO_CPU_LIMIT,
            "PublishAllPorts": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges"],
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "LogConfig": {"Type": "none", "Config": {}},
            "Tmpfs": tmpfs,
            "Ulimits": [
                {"Name": "nofile", "Soft": 1024, "Hard": 1024},
                {
                    "Name": "fsize",
                    "Soft": isolated.MAX_EXPORT_TAR_BYTES,
                    "Hard": isolated.MAX_EXPORT_TAR_BYTES,
                },
            ],
            "Binds": None,
            "VolumesFrom": None,
            "Devices": [],
            "DeviceRequests": None,
            "DeviceCgroupRules": None,
            "PortBindings": {},
            "Links": None,
            "ExtraHosts": None,
        },
        "NetworkSettings": {"Ports": {}},
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(workspace),
                "Destination": CONTAINER_HOST_WORKSPACE,
                "RW": False,
                "Propagation": "rprivate",
            },
            {
                "Type": "bind",
                "Source": str(input_root),
                "Destination": CONTAINER_INPUT_ROOT,
                "RW": False,
                "Propagation": "rprivate",
            },
            {
                "Type": "bind",
                "Source": str(input_root / "kit"),
                "Destination": CONTAINER_INPUT,
                "RW": False,
                "Propagation": "rprivate",
            },
            {
                "Type": "bind",
                "Source": str(export_archive),
                "Destination": f"{CONTAINER_EXPORT}/workspace.tar",
                "RW": True,
                "Propagation": "rprivate",
            },
            *[
                {"Type": "tmpfs", "Destination": destination, "RW": True}
                for destination in tmpfs
            ],
        ],
    }


def execution_responses(
    workspace: Path,
    input_root: Path,
    *,
    start: subprocess.CompletedProcess[str]
    | BaseException
    | Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
    metadata: dict[str, object] | None = None,
    child_returncode: int = 0,
    export_files: Mapping[str, bytes] | None = None,
) -> list[
    subprocess.CompletedProcess[str]
    | BaseException
    | Callable[[list[str]], subprocess.CompletedProcess[str]]
]:
    export_archive = workspace.parent / f".marb-export-{CONTAINER_NAME}.tar"
    if isinstance(start, BaseException):
        start_response = start
    else:
        def start_response(command: list[str]) -> subprocess.CompletedProcess[str]:
            response = start(command) if callable(start) else (start or completed(stdout="ok\n"))
            if response.returncode == 0 and export_archive.stat().st_size == 0:
                write_workspace_tar(
                    export_archive,
                    child_returncode=child_returncode,
                    files=export_files,
                )
            return response

    return [
        completed(stdout=image_metadata()),
        completed(stdout=CONTAINER_ID + "\n"),
        completed(
            stdout=json.dumps(
                metadata or container_metadata(workspace, input_root, export_archive)
            )
        ),
        start_response,
        completed(returncode=1, stderr="already stopped"),
        completed(returncode=1, stderr="already stopped"),
        completed(stdout=CONTAINER_ID + "\n"),
        completed(returncode=1, stderr="no such container"),
    ]


def policy_probe_responses(
    workspace: Path,
    input_root: Path,
    *,
    metadata: dict[str, object] | None = None,
) -> list[subprocess.CompletedProcess[str]]:
    export_archive = workspace.parent / f".marb-export-{CONTAINER_NAME}.tar"
    return [
        completed(stdout=image_metadata()),
        completed(stdout=CONTAINER_ID + "\n"),
        completed(
            stdout=json.dumps(
                metadata or container_metadata(workspace, input_root, export_archive)
            )
        ),
        completed(returncode=1, stderr="already stopped"),
        completed(returncode=1, stderr="already stopped"),
        completed(stdout=CONTAINER_ID + "\n"),
        completed(returncode=1, stderr="no such container"),
    ]


class IsolatedContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-isolated-test-")
        self.addCleanup(temporary.cleanup)
        self.run_root = Path(temporary.name).resolve() / "attempt"
        self.run_root.mkdir()
        self.workspace = self.run_root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "tool.py").write_text("print('ok')\n", encoding="utf-8")
        self.input_root = self.run_root / "inputs"
        (self.input_root / "kit").mkdir(parents=True)
        (self.input_root / "README.md").write_text("public brief\n", encoding="utf-8")
        (self.input_root / "kit" / "part.step").write_text("STEP\n", encoding="utf-8")
        self.export_archive = self.run_root / f".marb-export-{CONTAINER_NAME}.tar"

    def sandbox(
        self,
        runner: FakeRunner,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> IsolatedDockerPython:
        keywords: dict[str, object] = {}
        if monotonic is not None:
            keywords["monotonic"] = monotonic
        return IsolatedDockerPython(
            IMAGE,
            input_root=self.input_root,
            docker_executable=DOCKER_EXE,
            command_runner=runner,
            uuid_factory=lambda: FIXED_UUID,
            host_environ=HOST_SOURCE,
            **keywords,
        )

    def assert_output_body_released(self, error: IsolationError, sentinel: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        observed_isolated_frame = False
        trace = error.__traceback__
        while trace is not None:
            frame = trace.tb_frame
            if frame.f_globals.get("__name__") == isolated.__name__:
                observed_isolated_frame = True
                for name in ("stdout", "stderr"):
                    value = frame.f_locals.get(name)
                    if isinstance(value, bytes):
                        self.assertNotIn(sentinel.encode("ascii"), value)
                    elif isinstance(value, str):
                        self.assertNotIn(sentinel, value)
                for name in ("create_result", "start_result"):
                    result = frame.f_locals.get(name)
                    for stream in ("stdout", "stderr"):
                        value = getattr(result, stream, None)
                        if isinstance(value, bytes):
                            self.assertNotIn(sentinel.encode("ascii"), value)
                        elif isinstance(value, str):
                            self.assertNotIn(sentinel, value)
            trace = trace.tb_next
        self.assertTrue(observed_isolated_frame)

    def test_image_reference_requires_lowercase_repository_digest(self) -> None:
        self.assertEqual(validate_image_reference(IMAGE), IMAGE)
        self.assertEqual(
            validate_image_reference(
                "registry.example:5000/team/worker@sha256:" + "b" * 64
            ),
            "registry.example:5000/team/worker@sha256:" + "b" * 64,
        )
        rejected = [
            "ghcr.io/team/worker:latest",
            "ghcr.io/team/worker@sha256:" + "A" * 64,
            "GHCR.io/team/worker@sha256:" + "a" * 64,
            "https://ghcr.io/team/worker@sha256:" + "a" * 64,
            "ghcr.io/team/worker@sha256:" + "a" * 63,
            "ghcr.io/team/worker@sha256:" + "a" * 64 + "@sha256:" + "b" * 64,
            "registry.example:70000/team/worker@sha256:" + "a" * 64,
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(IsolationError):
                validate_image_reference(value)

    def test_image_inspect_requires_exact_digest_and_no_declared_volumes(self) -> None:
        runner = FakeRunner([completed(stdout=image_metadata())])
        self.assertEqual(self.sandbox(runner).inspect_local_image(), (IMAGE,))
        self.assertEqual(
            runner.calls[0][0],
            [
                DOCKER_EXE,
                "image",
                "inspect",
                "--format",
                "{{json .}}",
                IMAGE,
            ],
        )
        self.assertNotIn("pull", runner.calls[0][0])

    def test_image_inspect_fails_closed_on_bad_metadata_or_declared_volume(self) -> None:
        wrong_digest = json.dumps(
            {
                "Id": IMAGE_ID,
                "RepoDigests": ["ghcr.io/other/worker@sha256:" + "a" * 64],
                "Config": {"Volumes": None},
            }
        )
        cases = [
            completed(returncode=1, stderr="not found"),
            completed(stdout="not-json"),
            completed(stdout="null"),
            completed(stdout=wrong_digest),
            completed(stdout=image_metadata(volumes={"/data": {}})),
        ]
        for response in cases:
            with self.subTest(response=response):
                runner = FakeRunner([response])
                with self.assertRaises(IsolationError):
                    self.sandbox(runner).inspect_local_image()
                self.assertEqual(len(runner.calls), 1)

    def test_image_provenance_returns_only_the_fixed_public_label_allowlist(self) -> None:
        labels = {
            label: f"public-value-{index}"
            for index, label in enumerate(isolated.MARB_IMAGE_PROVENANCE_LABELS)
        }
        labels["org.example.unrelated"] = "not-retained"
        runner = FakeRunner([completed(stdout=image_metadata(labels=labels))])
        observed = self.sandbox(runner).inspect_local_image_provenance()
        self.assertEqual(observed["image"], IMAGE)
        self.assertEqual(observed["image_id"], IMAGE_ID)
        self.assertEqual(observed["repo_digests"], [IMAGE])
        self.assertEqual(
            observed["labels"],
            {
                label: labels[label]
                for label in isolated.MARB_IMAGE_PROVENANCE_LABELS
            },
        )

        incomplete = dict(labels)
        incomplete.pop(isolated.MARB_IMAGE_PROVENANCE_LABELS[0])
        with self.assertRaisesRegex(IsolationError, "provenance labels"):
            self.sandbox(
                FakeRunner([completed(stdout=image_metadata(labels=incomplete))])
            ).inspect_local_image_provenance()

        extra_marb = dict(labels)
        extra_marb["org.sunnyday.marb.unreviewed"] = "not-allowed"
        with self.assertRaisesRegex(IsolationError, "allowlist"):
            self.sandbox(
                FakeRunner([completed(stdout=image_metadata(labels=extra_marb))])
            ).inspect_local_image_provenance()

    def test_post_inspect_preparation_failure_binds_live_image_identity(self) -> None:
        runner = FakeRunner([completed(stdout=image_metadata())])
        sandbox = IsolatedDockerPython(
            IMAGE,
            input_root=self.input_root,
            docker_executable=DOCKER_EXE,
            command_runner=runner,
            uuid_factory=lambda: "not-a-uuid",  # type: ignore[arg-type]
            host_environ=HOST_SOURCE,
        )
        with self.assertRaises(IsolationError) as caught:
            sandbox.execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.stage, "post_image_preparation")
        self.assertEqual(error.image, IMAGE)
        self.assertEqual(error.image_id, IMAGE_ID)
        self.assertFalse(error.cleanup_attempted)
        self.assertIsNone(error.cleanup_verified)
        self.assertEqual(runner.responses, [])

    def test_create_command_is_exactly_hardened_and_named(self) -> None:
        self.export_archive.touch()
        command = self.sandbox(FakeRunner([])).build_create_command(
            self.workspace, "tool.py", CONTAINER_NAME, self.export_archive
        )
        self.assertEqual(command[:2], [DOCKER_EXE, "create"])
        required_pairs = {
            "--name": CONTAINER_NAME,
            "--label": f"{OWNERSHIP_LABEL}={CONTAINER_NAME}",
            "--pull": "never",
            "--network": "none",
            "--ipc": "none",
            "--cgroupns": "private",
            "--cap-drop": "ALL",
            "--security-opt": "no-new-privileges",
            "--user": "65532:65532",
            "--pids-limit": PID_LIMIT,
            "--memory": MEMORY_LIMIT,
            "--memory-swap": MEMORY_LIMIT,
            "--cpus": CPU_LIMIT,
            "--workdir": CONTAINER_WORKSPACE,
            "--entrypoint": "/usr/bin/env",
            "--log-driver": "none",
        }
        for flag, expected in required_pairs.items():
            with self.subTest(flag=flag):
                index = command.index(flag)
                self.assertEqual(command[index + 1], expected)
        self.assertIn("--read-only", command)
        self.assertIn("--no-healthcheck", command)
        tmpfs_values = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--tmpfs"
        ]
        self.assertEqual(
            tmpfs_values,
            [TMPFS_SPEC, WORKSPACE_TMPFS_SPEC, EXPORT_TMPFS_SPEC],
        )
        mount_values = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--mount"
        ]
        self.assertEqual(
            mount_values,
            [
                f"type=bind,source={self.workspace},target={CONTAINER_HOST_WORKSPACE},"
                "readonly,bind-propagation=rprivate",
                f"type=bind,source={self.input_root},target={CONTAINER_INPUT_ROOT},"
                "readonly,bind-propagation=rprivate",
                f"type=bind,source={self.input_root / 'kit'},target={CONTAINER_INPUT},"
                "readonly,bind-propagation=rprivate",
                f"type=bind,source={self.export_archive},"
                f"target={CONTAINER_EXPORT}/workspace.tar,bind-propagation=rprivate",
            ],
        )
        self.assertIn(
            f"fsize={isolated.MAX_EXPORT_TAR_BYTES}:{isolated.MAX_EXPORT_TAR_BYTES}",
            command,
        )
        self.assertNotIn("run", command)
        self.assertNotIn("--rm", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)
        self.assertFalse(any("docker.sock" in argument.casefold() for argument in command))
        self.assertFalse(any("docker_engine" in argument.casefold() for argument in command))
        self.assertNotIn("--env", command)
        image_index = command.index(IMAGE)
        self.assertEqual(command[image_index + 1 :], expected_container_command())

    def test_every_docker_cli_call_gets_only_the_minimal_host_environment(self) -> None:
        runner = FakeRunner(execution_responses(self.workspace, self.input_root))
        self.sandbox(runner).execute(self.workspace, "tool.py")
        for command, _, environment in runner.calls:
            with self.subTest(command=command):
                self.assertEqual(command[0], DOCKER_EXE)
                self.assertEqual(
                    environment,
                    {
                        "PATH": HOST_SOURCE["PATH"],
                        "SYSTEMROOT": HOST_SOURCE["SYSTEMROOT"],
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                    },
                )
                self.assertNotIn("OPENAI_API_KEY", environment)
                self.assertNotIn("MARB_GATED_READ_TOKEN", environment)
                self.assertNotIn("HTTPS_PROXY", environment)

    def test_execute_create_inspect_start_and_verified_cleanup(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=completed(
                    returncode=0,
                    stdout="partial output\n",
                    stderr="tool failed\n",
                ),
                child_returncode=7,
            )
        )
        result = self.sandbox(runner).execute(
            self.workspace,
            "tool.py",
            inspect_timeout=11.0,
            execution_timeout=22.0,
        )
        verbs = [call[0][1:3] for call in runner.calls]
        self.assertEqual(verbs[0], ["image", "inspect"])
        self.assertEqual(runner.calls[1][0][:2], [DOCKER_EXE, "create"])
        self.assertEqual(runner.calls[2][0][:3], [DOCKER_EXE, "container", "inspect"])
        self.assertEqual(runner.calls[3][0], [DOCKER_EXE, "start", "--attach", CONTAINER_ID])
        self.assertEqual(runner.calls[4][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(
            runner.calls[5][0], [DOCKER_EXE, "stop", "--time", "0", CONTAINER_ID]
        )
        self.assertEqual(runner.calls[6][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])
        self.assertEqual(runner.calls[7][0][-1], CONTAINER_ID)
        self.assertEqual(runner.calls[0][1], 11.0)
        self.assertGreater(runner.calls[3][1], 0)
        self.assertLessEqual(runner.calls[3][1], 22.0)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "partial output\n")
        self.assertEqual(result.stderr, "tool failed\n")
        self.assertEqual(result.image_id, IMAGE_ID)
        self.assertEqual(result.container_name, CONTAINER_NAME)
        self.assertEqual(result.container_id, CONTAINER_ID)
        self.assertTrue(result.cleanup_verified)
        self.assertTrue(result.container_absence_verified)
        self.assertTrue(result.export_staging_removed)
        self.assertTrue(self.workspace.exists())
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('exported')\n",
        )
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])

    def test_policy_probe_exact_command_trace_never_starts_container(self) -> None:
        runner = FakeRunner(policy_probe_responses(self.workspace, self.input_root))
        result = self.sandbox(runner).probe_policy_readback(
            self.workspace,
            "tool.py",
            inspect_timeout=11.0,
            execution_timeout=22.0,
        )
        self.assertIsInstance(result, PolicyReadbackResult)
        self.assertEqual(
            [call[0] for call in runner.calls],
            [
                [
                    DOCKER_EXE,
                    "image",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    IMAGE,
                ],
                list(result.create_command),
                [
                    DOCKER_EXE,
                    "container",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    CONTAINER_ID,
                ],
                [DOCKER_EXE, "kill", CONTAINER_ID],
                [DOCKER_EXE, "stop", "--time", "0", CONTAINER_ID],
                [DOCKER_EXE, "rm", "--force", CONTAINER_ID],
                [
                    DOCKER_EXE,
                    "container",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    CONTAINER_ID,
                ],
            ],
        )
        self.assertFalse(
            any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
        )
        self.assertTrue(result.policy_readback_verified)
        self.assertTrue(result.cleanup_verified)
        self.assertTrue(result.container_absence_verified)
        self.assertTrue(result.export_staging_removed)
        self.assertEqual(result.image_id, IMAGE_ID)
        self.assertEqual(result.container_name, CONTAINER_NAME)
        self.assertEqual(result.container_id, CONTAINER_ID)
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_policy_probe_mismatch_is_safe_and_cleanup_is_verified(self) -> None:
        sentinel = "observed-policy-value-must-not-serialize"
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        metadata["HostConfig"]["NetworkMode"] = sentinel  # type: ignore[index]
        runner = FakeRunner(
            policy_probe_responses(
                self.workspace, self.input_root, metadata=metadata
            )
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).probe_policy_readback(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.stage, "container_policy_readback")
        self.assertEqual(error.policy_predicate, "network_mode_mismatch")
        self.assertTrue(error.cleanup_attempted)
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertNotIn(sentinel, json.dumps(error.evidence, sort_keys=True))
        self.assertFalse(
            any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
        )
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_policy_probe_cleanup_failure_fails_closed(self) -> None:
        responses = policy_probe_responses(self.workspace, self.input_root)
        responses[5] = completed(returncode=1, stderr="cannot remove")
        runner = FakeRunner(responses[:6])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).probe_policy_readback(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "cleanup_verification_failed")
        self.assertEqual(error.stage, "container_cleanup")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(error.cleanup_error_type, "IsolationError")
        self.assertFalse(
            any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
        )
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_policy_probe_predicate_survives_cleanup_failure(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        metadata["HostConfig"]["NetworkMode"] = "bridge"  # type: ignore[index]
        responses = policy_probe_responses(
            self.workspace, self.input_root, metadata=metadata
        )
        responses[5] = completed(returncode=1, stderr="cannot remove")
        runner = FakeRunner(responses[:6])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).probe_policy_readback(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "isolation_contract_violation")
        self.assertEqual(error.stage, "container_policy_readback")
        self.assertEqual(error.policy_predicate, "network_mode_mismatch")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(error.cleanup_error_type, "IsolationError")
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_policy_probe_never_removes_foreign_container(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        metadata["Config"]["Labels"] = {  # type: ignore[index]
            OWNERSHIP_LABEL: "someone-else"
        }
        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                completed(stdout=json.dumps(metadata)),
                completed(stdout=json.dumps(metadata)),
            ]
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).probe_policy_readback(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.policy_predicate, "container_ownership_mismatch")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(runner.calls[3][0][-1], CONTAINER_NAME)
        self.assertFalse(
            any(call[0][1] in {"kill", "stop", "rm"} for call in runner.calls)
        )
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_policy_probe_removes_staging_and_leaves_workspace_unchanged(self) -> None:
        nested = self.workspace / "nested"
        nested.mkdir()
        (nested / "payload.bin").write_bytes(b"immutable workspace sentinel\x00")
        before = {
            path.relative_to(self.workspace).as_posix(): (
                "directory" if path.is_dir() else path.read_bytes()
            )
            for path in sorted(self.workspace.rglob("*"))
        }
        runner = FakeRunner(policy_probe_responses(self.workspace, self.input_root))
        result = self.sandbox(runner).probe_policy_readback(
            self.workspace, "tool.py"
        )
        after = {
            path.relative_to(self.workspace).as_posix(): (
                "directory" if path.is_dir() else path.read_bytes()
            )
            for path in sorted(self.workspace.rglob("*"))
        }
        self.assertEqual(after, before)
        self.assertTrue(result.export_staging_removed)
        self.assertFalse(self.export_archive.exists())
        self.assertFalse(
            any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
        )
        self.assertEqual(runner.responses, [])

    def test_policy_probe_never_removes_unowned_staging_collision(self) -> None:
        sentinel = b"pre-existing unowned staging bytes\n"
        self.export_archive.write_bytes(sentinel)
        runner = FakeRunner([completed(stdout=image_metadata())])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).probe_policy_readback(self.workspace, "tool.py")
        self.assertEqual(caught.exception.stage, "export_staging")
        self.assertFalse(caught.exception.cleanup_attempted)
        self.assertIsNone(caught.exception.export_staging_removed)
        self.assertEqual(self.export_archive.read_bytes(), sentinel)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.responses, [])

    def test_one_absolute_deadline_is_consumed_across_preflight_and_start(self) -> None:
        class AdvancingClock:
            def __init__(self) -> None:
                self.value = 100.0

            def __call__(self) -> float:
                observed = self.value
                self.value += 0.25
                return observed

        runner = FakeRunner(execution_responses(self.workspace, self.input_root))
        self.sandbox(runner, monotonic=AdvancingClock()).execute(
            self.workspace,
            "tool.py",
            inspect_timeout=None,
            execution_timeout=30.0,
        )
        normal_work_timeouts = [runner.calls[index][1] for index in range(4)]
        self.assertTrue(all(value is not None for value in normal_work_timeouts))
        numeric = [float(value) for value in normal_work_timeouts if value is not None]
        self.assertTrue(all(value > 0 for value in numeric))
        self.assertTrue(
            all(left > right for left, right in zip(numeric, numeric[1:]))
        )
        cleanup_timeouts = [timeout for _, timeout, _ in runner.calls[4:]]
        self.assertTrue(all(timeout is not None and timeout <= 15.0 for timeout in cleanup_timeouts))

    def test_export_requires_one_canonical_status_member(self) -> None:
        cases: tuple[tuple[str, bytes | None], ...] = (
            ("missing", None),
            (
                "malformed",
                b'{"schema": "marb_container_execution_status.v1", "returncode": 0}',
            ),
        )
        for expected, status_raw in cases:
            with self.subTest(expected=expected):
                def write_invalid_export(
                    _command: list[str], raw: bytes | None = status_raw
                ) -> subprocess.CompletedProcess[str]:
                    with tarfile.open(
                        self.export_archive, "w", format=tarfile.USTAR_FORMAT
                    ) as archive:
                        if raw is not None:
                            status = tarfile.TarInfo(STATUS_MEMBER)
                            status.size = len(raw)
                            status.mode = 0o600
                            status.mtime = 0
                            archive.addfile(status, io.BytesIO(raw))
                        source = b"print('untrusted export')\n"
                        tool = tarfile.TarInfo("tool.py")
                        tool.size = len(source)
                        tool.mode = 0o600
                        tool.mtime = 0
                        archive.addfile(tool, io.BytesIO(source))
                    return completed()

                runner = FakeRunner(
                    execution_responses(
                        self.workspace,
                        self.input_root,
                        start=write_invalid_export,
                    )
                )
                with self.assertRaisesRegex(IsolationError, f"status is {expected}"):
                    self.sandbox(runner).execute(self.workspace, "tool.py")
                self.assertEqual(
                    (self.workspace / "tool.py").read_text(encoding="utf-8"),
                    "print('ok')\n",
                )
                self.assertFalse(self.export_archive.exists())
                self.assertEqual(
                    runner.calls[6][0],
                    [DOCKER_EXE, "rm", "--force", CONTAINER_ID],
                )

    def test_config_readback_rejects_security_drift_before_start(self) -> None:
        mutations: dict[
            str, tuple[Callable[[dict[str, object]], None], str]
        ] = {
            "network": (
                lambda item: item["HostConfig"].__setitem__("NetworkMode", "bridge"),  # type: ignore[union-attr]
                "network_mode_mismatch",
            ),
            "read-write-root": (
                lambda item: item["HostConfig"].__setitem__("ReadonlyRootfs", False),  # type: ignore[union-attr]
                "readonly_rootfs_mismatch",
            ),
            "capability": (
                lambda item: item["HostConfig"].__setitem__("CapAdd", ["SYS_ADMIN"]),  # type: ignore[union-attr]
                "cap_add_present",
            ),
            "ipc": (
                lambda item: item["HostConfig"].__setitem__("IpcMode", "private"),  # type: ignore[union-attr]
                "ipc_mode_mismatch",
            ),
            "cgroupns": (
                lambda item: item["HostConfig"].__setitem__("CgroupnsMode", "host"),  # type: ignore[union-attr]
                "cgroupns_mode_mismatch",
            ),
            "device": (
                lambda item: item["HostConfig"].__setitem__("Devices", [{"PathOnHost": "/dev/sda"}]),  # type: ignore[union-attr]
                "devices_present",
            ),
            "port": (
                lambda item: item["HostConfig"].__setitem__("PortBindings", {"80/tcp": [{}]}),  # type: ignore[union-attr]
                "port_bindings_present",
            ),
            "logging": (
                lambda item: item["HostConfig"].__setitem__("LogConfig", {"Type": "json-file"}),  # type: ignore[union-attr]
                "logging_policy_mismatch",
            ),
            "healthcheck": (
                lambda item: item["Config"].__setitem__("Healthcheck", {"Test": ["CMD", "true"]}),  # type: ignore[union-attr]
                "healthcheck_not_disabled",
            ),
            "declared-volume": (
                lambda item: item["Config"].__setitem__("Volumes", {"/data": {}}),  # type: ignore[union-attr]
                "declared_volume_present",
            ),
            "wrong-image": (
                lambda item: item.__setitem__("Image", "sha256:" + "d" * 64),
                "container_image_id_mismatch",
            ),
            "root-user": (
                lambda item: item["Config"].__setitem__("User", "0:0"),  # type: ignore[union-attr]
                "container_user_mismatch",
            ),
        }
        for label, (mutate, expected_predicate) in mutations.items():
            with self.subTest(label=label):
                metadata = copy.deepcopy(
                    container_metadata(self.workspace, self.input_root, self.export_archive)
                )
                mutate(metadata)
                runner = FakeRunner(
                    [
                        completed(stdout=image_metadata()),
                        completed(stdout=CONTAINER_ID),
                        completed(stdout=json.dumps(metadata)),
                        completed(returncode=1),
                        completed(returncode=1),
                        completed(),
                        completed(returncode=1, stderr="no such container"),
                    ]
                )
                with self.assertRaises(IsolationError) as caught:
                    self.sandbox(runner).execute(self.workspace, "tool.py")
                self.assertEqual(
                    caught.exception.policy_predicate, expected_predicate
                )
                self.assertEqual(
                    caught.exception.evidence["policy_predicate"],
                    expected_predicate,
                )
                self.assertFalse(
                    any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
                )
                self.assertTrue(any(call[0][1] == "rm" for call in runner.calls))

    def test_every_container_policy_validator_rejection_has_one_fixed_token(self) -> None:
        sentinel = "observed-metadata-sentinel-must-not-serialize"

        def assign(path: tuple[str, ...], value: object) -> Callable[[dict[str, object]], None]:
            def mutate(metadata: dict[str, object]) -> None:
                target: Any = metadata
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value

            return mutate

        path_cases: list[
            tuple[str, Callable[[dict[str, object]], None]]
        ] = [
            ("container_identity_mismatch", assign(("Id",), sentinel)),
            ("container_image_id_mismatch", assign(("Image",), sentinel)),
            ("container_state_malformed", assign(("State",), [])),
            ("container_not_inert", assign(("State", "Running"), True)),
            ("container_config_malformed", assign(("Config",), [])),
            ("configured_image_mismatch", assign(("Config", "Image"), sentinel)),
            ("container_user_mismatch", assign(("Config", "User"), sentinel)),
            ("container_workdir_mismatch", assign(("Config", "WorkingDir"), sentinel)),
            ("container_command_mismatch", assign(("Config", "Cmd"), [sentinel])),
            ("declared_volume_present", assign(("Config", "Volumes"), {sentinel: {}})),
            ("exposed_port_present", assign(("Config", "ExposedPorts"), {"1/tcp": {}})),
            ("healthcheck_not_disabled", assign(("Config", "Healthcheck"), {"Test": [sentinel]})),
            ("ownership_label_mismatch", assign(("Config", "Labels"), {OWNERSHIP_LABEL: sentinel})),
            ("host_config_malformed", assign(("HostConfig",), [])),
        ]
        exact_host_fields = {
            "NetworkMode": "network_mode_mismatch",
            "IpcMode": "ipc_mode_mismatch",
            "CgroupnsMode": "cgroupns_mode_mismatch",
            "PidMode": "pid_mode_mismatch",
            "UTSMode": "uts_mode_mismatch",
            "ReadonlyRootfs": "readonly_rootfs_mismatch",
            "Privileged": "privileged_mode_mismatch",
            "AutoRemove": "auto_remove_mismatch",
            "PidsLimit": "pids_limit_mismatch",
            "Memory": "memory_limit_mismatch",
            "MemorySwap": "memory_swap_limit_mismatch",
            "NanoCpus": "cpu_limit_mismatch",
            "PublishAllPorts": "publish_all_ports_mismatch",
        }
        path_cases.extend(
            (predicate, assign(("HostConfig", key), sentinel))
            for key, predicate in exact_host_fields.items()
        )
        path_cases.extend(
            [
                ("cap_drop_mismatch", assign(("HostConfig", "CapDrop"), [])),
                ("cap_add_present", assign(("HostConfig", "CapAdd"), [sentinel])),
                ("security_option_mismatch", assign(("HostConfig", "SecurityOpt"), [sentinel])),
                ("restart_policy_mismatch", assign(("HostConfig", "RestartPolicy"), {"Name": sentinel})),
                ("logging_policy_mismatch", assign(("HostConfig", "LogConfig"), {"Type": sentinel})),
                ("tmpfs_metadata_malformed", assign(("HostConfig", "Tmpfs"), [])),
            ]
        )

        def mutate_tmpfs_destinations(metadata: dict[str, object]) -> None:
            tmpfs = metadata["HostConfig"]["Tmpfs"]  # type: ignore[index]
            tmpfs.pop("/tmp")
            tmpfs[sentinel] = "rw"

        path_cases.append(("tmpfs_destinations_mismatch", mutate_tmpfs_destinations))
        for destination, predicate in (
            ("/tmp", "tmpfs_tmp_options_mismatch"),
            (CONTAINER_WORKSPACE, "tmpfs_workspace_options_mismatch"),
            (CONTAINER_EXPORT, "tmpfs_export_options_mismatch"),
        ):
            path_cases.append(
                (predicate, assign(("HostConfig", "Tmpfs", destination), sentinel))
            )
        path_cases.append(
            ("ulimits_metadata_malformed", assign(("HostConfig", "Ulimits"), {}))
        )

        def mutate_ulimit_entry(metadata: dict[str, object]) -> None:
            metadata["HostConfig"]["Ulimits"][0] = sentinel  # type: ignore[index]

        def mutate_ulimit_names(metadata: dict[str, object]) -> None:
            metadata["HostConfig"]["Ulimits"][0]["Name"] = sentinel  # type: ignore[index]

        def mutate_ulimit_value(name: str) -> Callable[[dict[str, object]], None]:
            def mutate(metadata: dict[str, object]) -> None:
                for item in metadata["HostConfig"]["Ulimits"]:  # type: ignore[index]
                    if item["Name"] == name:
                        item["Soft"] = sentinel

            return mutate

        path_cases.extend(
            [
                ("ulimit_entry_malformed", mutate_ulimit_entry),
                ("ulimit_names_mismatch", mutate_ulimit_names),
                ("ulimit_nofile_mismatch", mutate_ulimit_value("nofile")),
                ("ulimit_fsize_mismatch", mutate_ulimit_value("fsize")),
            ]
        )
        host_resources = {
            "Binds": "binds_present",
            "VolumesFrom": "volumes_from_present",
            "Devices": "devices_present",
            "DeviceRequests": "device_requests_present",
            "DeviceCgroupRules": "device_cgroup_rules_present",
            "PortBindings": "port_bindings_present",
            "Links": "links_present",
            "ExtraHosts": "extra_hosts_present",
        }
        path_cases.extend(
            (predicate, assign(("HostConfig", key), [sentinel]))
            for key, predicate in host_resources.items()
        )
        path_cases.extend(
            [
                ("network_settings_malformed", assign(("NetworkSettings",), [])),
                ("published_ports_present", assign(("NetworkSettings", "Ports"), {"1/tcp": [{}]})),
                ("mounts_metadata_malformed", assign(("Mounts",), {})),
            ]
        )

        def mutate_mount(
            destination: str, key: str, value: object
        ) -> Callable[[dict[str, object]], None]:
            def mutate(metadata: dict[str, object]) -> None:
                for item in metadata["Mounts"]:  # type: ignore[union-attr]
                    if item.get("Destination") == destination:
                        item[key] = value
                        return
                raise AssertionError("fixture mount is unavailable")

            return mutate

        def append_mount(metadata: dict[str, object]) -> None:
            metadata["Mounts"].append(  # type: ignore[union-attr]
                {"Type": "tmpfs", "Destination": sentinel, "RW": True}
            )

        path_cases.extend(
            [
                ("bind_mount_count_mismatch", mutate_mount(CONTAINER_HOST_WORKSPACE, "Type", "volume")),
                ("bind_destinations_mismatch", mutate_mount(CONTAINER_HOST_WORKSPACE, "Destination", sentinel)),
                ("workspace_bind_mismatch", mutate_mount(CONTAINER_HOST_WORKSPACE, "RW", True)),
                ("immutable_input_bind_mismatch", mutate_mount(CONTAINER_INPUT_ROOT, "RW", True)),
                ("kit_bind_mismatch", mutate_mount(CONTAINER_INPUT, "RW", True)),
                ("export_bind_mismatch", mutate_mount(f"{CONTAINER_EXPORT}/workspace.tar", "RW", False)),
                ("extra_mount_count_mismatch", append_mount),
                ("tmpfs_mount_destinations_mismatch", mutate_mount("/tmp", "Destination", sentinel)),
                ("docker_control_socket_present", mutate_mount("/tmp", "Source", "/var/run/docker.sock")),
                ("effective_entrypoint_mismatch", assign(("Path",), sentinel)),
                ("effective_args_mismatch", assign(("Args",), [sentinel])),
            ]
        )

        observed: set[str] = set()
        sandbox = self.sandbox(FakeRunner([]))
        for expected_predicate, mutate in path_cases:
            with self.subTest(policy_predicate=expected_predicate):
                metadata = copy.deepcopy(
                    container_metadata(
                        self.workspace, self.input_root, self.export_archive
                    )
                )
                mutate(metadata)
                with self.assertRaises(IsolationError) as caught:
                    sandbox._validate_container_metadata(
                        metadata,
                        container_id=CONTAINER_ID,
                        container_name=CONTAINER_NAME,
                        image_id=IMAGE_ID,
                        workspace=self.workspace,
                        input_root=self.input_root,
                        export_archive=self.export_archive,
                        script="tool.py",
                    )
                error = caught.exception
                self.assertEqual(error.policy_predicate, expected_predicate)
                serialized = json.dumps(error.evidence, sort_keys=True)
                self.assertIn(expected_predicate, serialized)
                self.assertNotIn(sentinel, serialized)
                observed.add(expected_predicate)

        procedural = {
            "policy_readback_deadline",
            "container_inspect_output_limit",
            "container_inspect_json_malformed",
            "container_metadata_malformed",
            "container_missing_before_readback",
            "container_ownership_mismatch",
        }
        self.assertEqual(
            observed,
            set(isolated.CONTAINER_POLICY_PREDICATES) - procedural,
        )

    def test_policy_predicate_allowlist_rejects_dynamic_diagnostic_text(self) -> None:
        sentinel = "syntactically_safe_unlisted_predicate"
        rejected = IsolationError(
            "fixed public failure message", policy_predicate=sentinel
        )
        self.assertIsNone(rejected.policy_predicate)
        self.assertNotIn("policy_predicate", rejected.evidence)
        self.assertNotIn(sentinel, json.dumps(rejected.evidence, sort_keys=True))

        unhashable = IsolationError(
            "fixed public failure message",
            policy_predicate=[sentinel],  # type: ignore[arg-type]
        )
        self.assertIsNone(unhashable.policy_predicate)
        self.assertNotIn("policy_predicate", unhashable.evidence)

        accepted = IsolationError(
            "fixed public failure message",
            policy_predicate="network_mode_mismatch",
        )
        self.assertEqual(
            accepted.evidence["policy_predicate"], "network_mode_mismatch"
        )
        self.assertNotIn("fixed public failure message", json.dumps(accepted.evidence))

    def test_policy_readback_procedural_failures_use_fixed_tokens(self) -> None:
        raw_inspect_sentinel = "raw-inspect-body-must-not-serialize"
        procedural = {
            "policy_readback_deadline",
            "container_inspect_output_limit",
            "container_inspect_json_malformed",
            "container_metadata_malformed",
            "container_missing_before_readback",
            "container_ownership_mismatch",
        }
        observed: set[str] = set()
        inspect_cases = (
            (
                "container_inspect_output_limit",
                completed(stdout="x" * (isolated.MAX_STDOUT_BYTES + 1)),
                None,
            ),
            (
                "container_inspect_json_malformed",
                completed(stdout="{" + raw_inspect_sentinel),
                raw_inspect_sentinel,
            ),
            ("container_metadata_malformed", completed(stdout="[]"), None),
        )
        for expected_predicate, response, forbidden in inspect_cases:
            with self.subTest(policy_predicate=expected_predicate):
                runner = FakeRunner([response])
                with self.assertRaises(IsolationError) as caught:
                    self.sandbox(runner)._read_container_metadata(
                        CONTAINER_ID,
                        timeout=30.0,
                        policy_readback=True,
                    )
                self.assertEqual(
                    caught.exception.policy_predicate, expected_predicate
                )
                serialized = json.dumps(caught.exception.evidence, sort_keys=True)
                if forbidden is not None:
                    self.assertNotIn(forbidden, serialized)
                observed.add(expected_predicate)

        deadline_runner = FakeRunner([])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(
                deadline_runner, monotonic=lambda: 2.0
            )._remaining_timeout(
                1.0,
                30.0,
                label="Docker policy readback",
                policy_predicate="policy_readback_deadline",
            )
        self.assertEqual(
            caught.exception.policy_predicate, "policy_readback_deadline"
        )
        observed.add("policy_readback_deadline")

        missing_runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                completed(returncode=1, stderr="no such container"),
                completed(returncode=1, stderr="no such container"),
            ]
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(missing_runner).execute(self.workspace, "tool.py")
        self.assertEqual(
            caught.exception.policy_predicate,
            "container_missing_before_readback",
        )
        observed.add("container_missing_before_readback")

        foreign = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        foreign["Config"]["Labels"] = {OWNERSHIP_LABEL: "foreign"}  # type: ignore[index]
        owned = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        ownership_runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                completed(stdout=json.dumps(foreign)),
                completed(stdout=json.dumps(owned)),
                completed(returncode=1, stderr="already stopped"),
                completed(returncode=1, stderr="already stopped"),
                completed(),
                completed(returncode=1, stderr="no such container"),
            ]
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(ownership_runner).execute(self.workspace, "tool.py")
        self.assertEqual(
            caught.exception.policy_predicate,
            "container_ownership_mismatch",
        )
        self.assertTrue(caught.exception.cleanup_verified)
        self.assertTrue(caught.exception.container_absence_verified)
        observed.add("container_ownership_mismatch")
        self.assertEqual(observed, procedural)

    def test_policy_predicate_survives_a_separate_cleanup_failure(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        metadata["HostConfig"]["NetworkMode"] = "bridge"  # type: ignore[index]
        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                completed(stdout=json.dumps(metadata)),
                completed(returncode=1, stderr="already stopped"),
                completed(returncode=1, stderr="already stopped"),
                completed(returncode=1, stderr="cannot remove"),
            ]
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.policy_predicate, "network_mode_mismatch")
        self.assertEqual(error.cleanup_error_type, "IsolationError")
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)

    def test_extra_bind_and_socket_mounts_are_rejected_before_start(self) -> None:
        for source in (str(self.workspace.parent), "/var/run/docker.sock"):
            with self.subTest(source=source):
                metadata = container_metadata(
                    self.workspace, self.input_root, self.export_archive
                )
                mounts = metadata["Mounts"]
                assert isinstance(mounts, list)
                mounts.append(
                    {
                        "Type": "bind",
                        "Source": source,
                        "Destination": "/extra",
                        "RW": True,
                        "Propagation": "rprivate",
                    }
                )
                runner = FakeRunner(
                    [
                        completed(stdout=image_metadata()),
                        completed(stdout=CONTAINER_ID),
                        completed(stdout=json.dumps(metadata)),
                        completed(returncode=1),
                        completed(returncode=1),
                        completed(),
                        completed(returncode=1, stderr="no such container"),
                    ]
                )
                with self.assertRaisesRegex(IsolationError, "mount|bind"):
                    self.sandbox(runner).execute(self.workspace, "tool.py")

    def test_timeout_still_kills_stops_removes_and_reads_back_absence(self) -> None:
        sentinel = "child-output-body-must-be-released"
        timeout_stdout = f"{sentinel}:stdout\n".encode("ascii")
        timeout_stderr = f"{sentinel}:stderr\n".encode("ascii")
        timeout = subprocess.TimeoutExpired(
            [DOCKER_EXE, "start"],
            3.0,
            output=timeout_stdout,
            stderr=timeout_stderr,
        )
        runner = FakeRunner(
            execution_responses(self.workspace, self.input_root, start=timeout)
        )
        try:
            self.sandbox(runner).execute(
                self.workspace, "tool.py", execution_timeout=3.0
            )
        except IsolationError as observed:
            self.assert_output_body_released(observed, sentinel)
            error = observed
        else:
            self.fail("expected timeout isolation failure")
        self.assertEqual(error.code, "execution_timeout")
        self.assertEqual(error.stage, "container_start")
        self.assertEqual(error.image, IMAGE)
        self.assertEqual(error.image_id, IMAGE_ID)
        self.assertEqual(error.primary_error_type, "TimeoutExpired")
        self.assertTrue(error.cleanup_attempted)
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(error.stdout, "")
        self.assertEqual(error.stderr, "")
        self.assertEqual(error.stdout_bytes, len(timeout_stdout))
        self.assertEqual(error.stderr_bytes, len(timeout_stderr))
        self.assertEqual(error.stdout_sha256, hashlib.sha256(timeout_stdout).hexdigest())
        self.assertEqual(error.stderr_sha256, hashlib.sha256(timeout_stderr).hexdigest())
        self.assertEqual(runner.calls[4][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(runner.calls[5][0][1], "stop")
        self.assertEqual(runner.calls[6][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])
        self.assertFalse(self.export_archive.exists())

    def test_attached_run_timeout_is_separate_from_setup_deadline(self) -> None:
        timeout = subprocess.TimeoutExpired([DOCKER_EXE, "start"], 1.0)
        runner = FakeRunner(
            execution_responses(self.workspace, self.input_root, start=timeout)
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(
                self.workspace,
                "tool.py",
                execution_timeout=60.0,
                attached_run_timeout=1.0,
            )
        self.assertEqual(caught.exception.code, "execution_timeout")
        self.assertEqual(caught.exception.stage, "container_start")
        self.assertGreater(runner.calls[0][1], 1.0)
        self.assertEqual(runner.calls[3][1], 1.0)
        self.assertEqual(runner.responses, [])

    def test_generic_runner_exception_message_is_not_serialized(self) -> None:
        sentinel = "sensitive-runner-detail-must-not-be-serialized"
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=RuntimeError(sentinel),
            )
        )
        try:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        except IsolationError as observed:
            self.assert_output_body_released(observed, sentinel)
            error = observed
        else:
            self.fail("expected output-limit isolation failure")
        self.assertEqual(error.code, "execution_failed")
        self.assertEqual(error.primary_error_type, "RuntimeError")
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertNotIn(sentinel, json.dumps(error.evidence, sort_keys=True))
        self.assertEqual(runner.responses, [])

    def test_malformed_create_identity_cleans_up_only_owned_named_container(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout="not-a-container-id"),
                completed(stdout=json.dumps(metadata)),
                completed(returncode=1),
                completed(returncode=1),
                completed(),
                completed(returncode=1, stderr="no such container"),
            ]
        )
        with self.assertRaisesRegex(IsolationError, "malformed container identity"):
            self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertEqual(runner.calls[2][0][-1], CONTAINER_NAME)
        self.assertEqual(runner.calls[3][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(runner.calls[5][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])

    def test_failed_create_does_not_remove_a_foreign_name_collision(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        config = metadata["Config"]
        assert isinstance(config, dict)
        config["Labels"] = {OWNERSHIP_LABEL: "someone-else"}
        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(returncode=1, stderr="name already in use"),
                completed(stdout=json.dumps(metadata)),
            ]
        )
        with self.assertRaisesRegex(IsolationError, "creation failed"):
            self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(runner.calls[2][0][-1], CONTAINER_NAME)
        self.assertFalse(any(call[0][1] in {"kill", "stop", "rm"} for call in runner.calls))

    def test_foreign_cid_readback_is_never_killed_or_removed(self) -> None:
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )
        config = metadata["Config"]
        assert isinstance(config, dict)
        config["Labels"] = {OWNERSHIP_LABEL: "someone-else"}
        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                completed(stdout=json.dumps(metadata)),
                completed(stdout=json.dumps(metadata)),
            ]
        )
        with self.assertRaisesRegex(IsolationError, "ownership readback"):
            self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertEqual(runner.calls[3][0][-1], CONTAINER_NAME)
        self.assertFalse(any(call[0][1] in {"kill", "stop", "rm"} for call in runner.calls))

    def test_cleanup_failure_fails_closed(self) -> None:
        responses = execution_responses(self.workspace, self.input_root)
        responses[6] = completed(returncode=1, stderr="cannot remove")
        runner = FakeRunner(responses[:7])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "cleanup_verification_failed")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(error.cleanup_error_type, "IsolationError")
        self.assertEqual(runner.responses, [])

    def test_false_cleanup_readback_fails_before_workspace_restore(self) -> None:
        original_workspace = (self.workspace / "tool.py").read_bytes()
        runner = FakeRunner(execution_responses(self.workspace, self.input_root)[:4])
        sandbox = self.sandbox(runner)
        with mock.patch.object(sandbox, "_cleanup_container", return_value=False):
            with self.assertRaises(IsolationError) as caught:
                sandbox.execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "cleanup_verification_failed")
        self.assertEqual(error.stage, "container_cleanup")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertEqual((self.workspace / "tool.py").read_bytes(), original_workspace)
        self.assertFalse(self.export_archive.exists())
        self.assertEqual(runner.responses, [])

    def test_cleanup_requires_explicit_absence_readback(self) -> None:
        responses = execution_responses(self.workspace, self.input_root)
        responses[7] = completed(returncode=1, stderr="daemon unavailable")
        runner = FakeRunner(responses)
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "cleanup_verification_failed")
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(runner.responses, [])

    def test_primary_failure_is_preserved_when_cleanup_also_fails(self) -> None:
        responses = execution_responses(
            self.workspace,
            self.input_root,
            start=completed(
                returncode=70,
                stdout="bounded child output\n",
                stderr="workspace limit reached\n",
            ),
        )
        responses[6] = completed(returncode=1, stderr="cannot remove")
        runner = FakeRunner(responses[:7])
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "container_limiter_rejected")
        self.assertEqual(error.returncode, 70)
        self.assertEqual(error.stdout, "")
        self.assertEqual(error.stderr, "")
        self.assertEqual(error.stdout_bytes, len(b"bounded child output\n"))
        self.assertEqual(error.stderr_bytes, len(b"workspace limit reached\n"))
        self.assertEqual(
            error.stdout_sha256,
            hashlib.sha256(b"bounded child output\n").hexdigest(),
        )
        self.assertEqual(
            error.stderr_sha256,
            hashlib.sha256(b"workspace limit reached\n").hexdigest(),
        )
        self.assertTrue(error.cleanup_attempted)
        self.assertFalse(error.cleanup_verified)
        self.assertFalse(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(error.cleanup_error_type, "IsolationError")
        self.assertEqual(runner.responses, [])

    def test_stdout_and_stderr_overflow_is_byte_bounded_marked_and_rejected(self) -> None:
        sentinel = "child-output-body-must-be-released"
        stdout_body = sentinel + "\u00e9" * (isolated.MAX_STDOUT_BYTES // 2 + 100)
        stderr_body = sentinel + "\u754c" * (isolated.MAX_STDERR_BYTES // 3 + 100)
        bounded_stdout, _ = isolated._bounded_text(
            stdout_body, isolated.MAX_STDOUT_BYTES
        )
        bounded_stderr, _ = isolated._bounded_text(
            stderr_body, isolated.MAX_STDERR_BYTES
        )
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=completed(
                    stdout=stdout_body,
                    stderr=stderr_body,
                ),
            )
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "output_limit_exceeded")
        self.assertEqual(error.returncode, 0)
        self.assertEqual(error.image, IMAGE)
        self.assertEqual(error.image_id, IMAGE_ID)
        self.assertLessEqual(error.stdout_bytes, isolated.MAX_STDOUT_BYTES)
        self.assertLessEqual(error.stderr_bytes, isolated.MAX_STDERR_BYTES)
        self.assertTrue(error.stdout_truncated)
        self.assertTrue(error.stderr_truncated)
        marker = isolated._OUTPUT_TRUNCATION_MARKER.decode("ascii")
        self.assertEqual(error.stdout, marker)
        self.assertEqual(error.stderr, marker)
        self.assertEqual(error.stdout_bytes, len(bounded_stdout.encode("utf-8")))
        self.assertEqual(error.stderr_bytes, len(bounded_stderr.encode("utf-8")))
        self.assertEqual(
            error.stdout_sha256,
            hashlib.sha256(bounded_stdout.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            error.stderr_sha256,
            hashlib.sha256(bounded_stderr.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(error.cleanup_attempted)
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        evidence = error.evidence
        self.assertEqual(evidence["schema"], "marb_isolation_error.v1")
        self.assertEqual(evidence["image"], IMAGE)
        self.assertEqual(evidence["image_id"], IMAGE_ID)
        self.assertEqual(evidence["stdout"]["bytes"], error.stdout_bytes)
        self.assertEqual(evidence["stderr"]["bytes"], error.stderr_bytes)
        self.assertEqual(len(evidence["stdout"]["sha256"]), 64)
        self.assertEqual(len(evidence["stderr"]["sha256"]), 64)
        self.assertTrue(evidence["stdout"]["truncated"])
        self.assertTrue(evidence["stderr"]["truncated"])
        self.assertNotIn("message", evidence)
        serialized = json.dumps(
            evidence, ensure_ascii=True, allow_nan=False, sort_keys=True
        )
        self.assertNotIn(error.stdout, serialized)
        self.assertNotIn(error.stderr, serialized)
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('ok')\n",
        )
        self.assertFalse(self.export_archive.exists())

    def test_workspace_limits_reject_before_any_docker_call(self) -> None:
        cases = (
            ("MAX_WORKSPACE_ENTRIES", 1, "second.py", b"x"),
            ("MAX_WORKSPACE_FILE_BYTES", 4, "large.py", b"x" * 5),
            ("MAX_WORKSPACE_TOTAL_BYTES", 16, "total.py", b"x" * 16),
        )
        for constant, limit, filename, content in cases:
            with self.subTest(constant=constant):
                candidate = self.workspace / filename
                candidate.write_bytes(content)
                if constant == "MAX_WORKSPACE_ENTRIES":
                    self.assertGreater(
                        sum(1 for _ in self.workspace.rglob("*")),
                        limit,
                    )
                elif constant == "MAX_WORKSPACE_FILE_BYTES":
                    self.assertGreater(candidate.stat().st_size, limit)
                else:
                    self.assertGreater(
                        sum(
                            item.stat().st_size
                            for item in self.workspace.rglob("*")
                            if item.is_file()
                        ),
                        limit,
                    )
                runner = FakeRunner([])
                with mock.patch.object(isolated, constant, limit):
                    with self.assertRaisesRegex(IsolationError, "limit") as caught:
                        self.sandbox(runner).execute(self.workspace, "tool.py")
                error = caught.exception
                self.assertEqual(error.code, "workspace_input_limit_exceeded")
                self.assertIsNone(error.image)
                self.assertIsNone(error.image_id)
                self.assertFalse(error.cleanup_attempted)
                self.assertIsNone(error.cleanup_verified)
                self.assertIsNone(error.container_absence_verified)
                self.assertEqual(runner.calls, [])
                candidate.unlink()

    def test_workspace_limiter_failure_preserves_prior_state_and_proves_absence(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=completed(
                    returncode=70,
                    stdout="probe started\n",
                    stderr="workspace aggregate limit exceeded\n",
                ),
            )
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "container_limiter_rejected")
        self.assertEqual(error.returncode, 70)
        self.assertEqual(error.stdout, "")
        self.assertEqual(error.stderr, "")
        self.assertEqual(error.stdout_bytes, len(b"probe started\n"))
        self.assertEqual(
            error.stdout_sha256,
            hashlib.sha256(b"probe started\n").hexdigest(),
        )
        self.assertEqual(error.stderr_bytes, len(b"workspace aggregate limit exceeded\n"))
        self.assertEqual(
            error.stderr_sha256,
            hashlib.sha256(b"workspace aggregate limit exceeded\n").hexdigest(),
        )
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('ok')\n",
        )
        self.assertFalse(self.export_archive.exists())

    def test_exact_workspace_entry_reason_gets_specific_safe_code(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=completed(
                    returncode=isolated.LIMITER_EXIT,
                    stderr=isolated.WORKSPACE_ENTRY_LIMIT_REASON,
                ),
            )
        )
        with self.assertRaises(IsolationError) as caught:
            self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "workspace_entry_limit_exceeded")
        self.assertEqual(error.stage, "workspace_limiter")
        self.assertEqual(error.returncode, isolated.LIMITER_EXIT)
        self.assertEqual(error.stderr, "")
        reason_raw = isolated.WORKSPACE_ENTRY_LIMIT_REASON.encode("utf-8")
        self.assertEqual(error.stderr_bytes, len(reason_raw))
        self.assertEqual(error.stderr_sha256, hashlib.sha256(reason_raw).hexdigest())
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(runner.responses, [])

    def test_oversized_workspace_export_is_rejected_after_container_cleanup(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                export_files={"tool.py": b"pass\n", "generated.bin": b"z" * 200},
            )
        )
        with mock.patch.object(isolated, "MAX_WORKSPACE_TOTAL_BYTES", 100):
            with self.assertRaisesRegex(IsolationError, "total-size limit") as caught:
                self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "workspace_export_limit_exceeded")
        self.assertEqual(error.stage, "workspace_restore")
        self.assertTrue(error.cleanup_attempted)
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(runner.calls[4][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(runner.calls[6][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])
        self.assertFalse((self.workspace / "generated.bin").exists())
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('ok')\n",
        )
        self.assertFalse(self.export_archive.exists())

    def test_oversized_export_archive_is_rejected_before_restore_after_cleanup(self) -> None:
        with mock.patch.object(isolated, "MAX_EXPORT_TAR_BYTES", 1024):
            runner = FakeRunner(execution_responses(self.workspace, self.input_root))
            with self.assertRaises(IsolationError) as caught:
                self.sandbox(runner).execute(self.workspace, "tool.py")
        error = caught.exception
        self.assertEqual(error.code, "workspace_export_limit_exceeded")
        self.assertEqual(error.stage, "export_validation")
        self.assertTrue(error.cleanup_verified)
        self.assertTrue(error.container_absence_verified)
        self.assertTrue(error.export_staging_removed)
        self.assertEqual(
            runner.calls[7][0],
            [
                DOCKER_EXE,
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                CONTAINER_ID,
            ],
        )
        self.assertEqual(runner.responses, [])
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('ok')\n",
        )
        self.assertFalse(self.export_archive.exists())

    def test_inspect_rejection_prevents_container_creation(self) -> None:
        runner = FakeRunner([completed(stdout=image_metadata(volumes={"/cache": {}}))])
        with self.assertRaises(IsolationError):
            self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertEqual(len(runner.calls), 1)

    def test_python_path_must_be_relative_existing_and_py_only(self) -> None:
        sandbox = self.sandbox(FakeRunner([]))
        rejected = [
            "../tool.py",
            "/workspace/tool.py",
            "C:/tool.py",
            "nested\\tool.py",
            "missing.py",
            "tool.txt",
            "./tool.py",
            "secret/tool.py",
            "answer_key.py",
            "CON.py",
            "aux.py",
            "NUL.py",
            "lpt9.py",
        ]
        for relative in rejected:
            with self.subTest(relative=relative), self.assertRaises(IsolationError):
                sandbox.build_create_command(
                    self.workspace, relative, CONTAINER_NAME, self.export_archive
                )

    def test_windows_reserved_names_are_rejected_without_touching_the_filesystem(self) -> None:
        for member in ("CON.py", "aux/report.py", "nested/NUL.txt", "LPT9"):
            with self.subTest(member=member), self.assertRaisesRegex(
                IsolationError, "unsafe path"
            ):
                isolated._safe_export_member(member)
        with self.assertRaisesRegex(IsolationError, "Docker executable"):
            IsolatedDockerPython(
                IMAGE,
                input_root=self.input_root,
                docker_executable="C:/AUX/docker.exe",
                command_runner=FakeRunner([]),
            )

    def test_workspace_must_be_absolute_existing_and_mount_safe(self) -> None:
        sandbox = self.sandbox(FakeRunner([]))
        with self.assertRaises(IsolationError):
            sandbox.build_create_command(
                Path("relative-workspace"), "tool.py", CONTAINER_NAME, self.export_archive
            )
        with self.assertRaises(IsolationError):
            sandbox.build_create_command(
                self.workspace / "missing", "tool.py", CONTAINER_NAME, self.export_archive
            )
        comma_workspace = self.workspace.parent / "comma,workspace"
        comma_workspace.mkdir()
        (comma_workspace / "tool.py").write_text("pass\n", encoding="utf-8")
        with self.assertRaises(IsolationError):
            sandbox.build_create_command(
                comma_workspace, "tool.py", CONTAINER_NAME, self.export_archive
            )

    def test_workspace_rejects_protected_descendants(self) -> None:
        sandbox = self.sandbox(FakeRunner([]))
        protected = self.workspace / ".env"
        protected.write_text("KEY=redacted\n", encoding="utf-8")
        with self.assertRaisesRegex(IsolationError, "protected"):
            sandbox.build_create_command(
                self.workspace, "tool.py", CONTAINER_NAME, self.export_archive
            )

    def test_workspace_rejects_symlinked_entries_and_script(self) -> None:
        target = self.workspace / "target.py"
        target.write_text("pass\n", encoding="utf-8")
        link = self.workspace / "linked.py"
        try:
            os.symlink(target.name, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable in this environment")
        sandbox = self.sandbox(FakeRunner([]))
        with self.assertRaisesRegex(IsolationError, "symlink or reparse"):
            sandbox.build_create_command(
                self.workspace, "linked.py", CONTAINER_NAME, self.export_archive
            )

    def test_full_input_root_swap_is_revalidated_before_start(self) -> None:
        replacement = self.run_root / "replacement-inputs"
        (replacement / "kit").mkdir(parents=True)
        (replacement / "kit" / "part.step").write_text("replacement\n", encoding="utf-8")
        (replacement / ".env").write_text("REDACTED\n", encoding="utf-8")
        original = self.run_root / "inputs-before-swap"
        metadata = container_metadata(
            self.workspace, self.input_root, self.export_archive
        )

        def swap_input_root(
            _command: list[str],
        ) -> subprocess.CompletedProcess[str]:
            os.replace(self.input_root, original)
            os.replace(replacement, self.input_root)
            return completed(stdout=json.dumps(metadata))

        runner = FakeRunner(
            [
                completed(stdout=image_metadata()),
                completed(stdout=CONTAINER_ID),
                swap_input_root,
                completed(returncode=1, stderr="already stopped"),
                completed(returncode=1, stderr="already stopped"),
                completed(),
                completed(returncode=1, stderr="no such container"),
            ]
        )
        with self.assertRaisesRegex(IsolationError, "protected"):
            self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertFalse(
            any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
        )
        self.assertTrue(any(call[0][1] == "rm" for call in runner.calls))
        self.assertFalse(self.export_archive.exists())

    def test_default_runner_kills_waits_and_joins_on_base_exception(self) -> None:
        class InterruptingProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"bounded stdout")
                self.stderr = io.BytesIO(b"bounded stderr")
                self.killed = False
                self.wait_timeouts: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                self.wait_timeouts.append(timeout)
                if len(self.wait_timeouts) == 1:
                    raise KeyboardInterrupt()
                return -9

            def poll(self) -> int | None:
                return None

            def kill(self) -> None:
                self.killed = True

        process = InterruptingProcess()
        with mock.patch.object(isolated.subprocess, "Popen", return_value=process) as popen:
            with self.assertRaises(KeyboardInterrupt):
                isolated._default_command_runner(
                    [DOCKER_EXE, "version"],
                    timeout=1.0,
                    env={"PATH": HOST_SOURCE["PATH"]},
                )
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_timeouts, [1.0, None])
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        invocation = popen.call_args
        self.assertEqual(invocation.args[0][0], DOCKER_EXE)
        self.assertFalse(invocation.kwargs["shell"])

    def test_nonpositive_timeouts_are_rejected_before_inspect(self) -> None:
        for keyword in (
            {"execution_timeout": 0},
            {"inspect_timeout": 0},
            {"attached_run_timeout": 0},
        ):
            with self.subTest(keyword=keyword):
                runner = FakeRunner([])
                with self.assertRaises(IsolationError):
                    self.sandbox(runner).execute(
                        self.workspace, "tool.py", **keyword
                    )
                self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
