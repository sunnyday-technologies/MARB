"""Regressions for the digest-pinned, attest-before-start Docker sandbox."""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Callable, Mapping
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
    MAX_EXPORT_TAR_BYTES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
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


def image_metadata(*, volumes: object = None) -> str:
    return json.dumps(
        {
            "Id": IMAGE_ID,
            "RepoDigests": [IMAGE],
            "Config": {"Volumes": volumes},
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
                    "Soft": MAX_EXPORT_TAR_BYTES,
                    "Hard": MAX_EXPORT_TAR_BYTES,
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
        self.assertIn(f"fsize={MAX_EXPORT_TAR_BYTES}:{MAX_EXPORT_TAR_BYTES}", command)
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
        self.assertEqual(result.container_name, CONTAINER_NAME)
        self.assertEqual(result.container_id, CONTAINER_ID)
        self.assertTrue(result.cleanup_verified)
        self.assertTrue(self.workspace.exists())
        self.assertEqual(
            (self.workspace / "tool.py").read_text(encoding="utf-8"),
            "print('exported')\n",
        )
        self.assertFalse(self.export_archive.exists())

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
        mutations: dict[str, Callable[[dict[str, object]], None]] = {
            "network": lambda item: item["HostConfig"].__setitem__("NetworkMode", "bridge"),  # type: ignore[union-attr]
            "read-write-root": lambda item: item["HostConfig"].__setitem__("ReadonlyRootfs", False),  # type: ignore[union-attr]
            "capability": lambda item: item["HostConfig"].__setitem__("CapAdd", ["SYS_ADMIN"]),  # type: ignore[union-attr]
            "ipc": lambda item: item["HostConfig"].__setitem__("IpcMode", "private"),  # type: ignore[union-attr]
            "cgroupns": lambda item: item["HostConfig"].__setitem__("CgroupnsMode", "host"),  # type: ignore[union-attr]
            "device": lambda item: item["HostConfig"].__setitem__("Devices", [{"PathOnHost": "/dev/sda"}]),  # type: ignore[union-attr]
            "port": lambda item: item["HostConfig"].__setitem__("PortBindings", {"80/tcp": [{}]}),  # type: ignore[union-attr]
            "logging": lambda item: item["HostConfig"].__setitem__("LogConfig", {"Type": "json-file"}),  # type: ignore[union-attr]
            "healthcheck": lambda item: item["Config"].__setitem__("Healthcheck", {"Test": ["CMD", "true"]}),  # type: ignore[union-attr]
            "declared-volume": lambda item: item["Config"].__setitem__("Volumes", {"/data": {}}),  # type: ignore[union-attr]
            "wrong-image": lambda item: item.__setitem__("Image", "sha256:" + "d" * 64),
            "root-user": lambda item: item["Config"].__setitem__("User", "0:0"),  # type: ignore[union-attr]
        }
        for label, mutate in mutations.items():
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
                with self.assertRaises(IsolationError):
                    self.sandbox(runner).execute(self.workspace, "tool.py")
                self.assertFalse(
                    any(call[0][1:3] == ["start", "--attach"] for call in runner.calls)
                )
                self.assertTrue(any(call[0][1] == "rm" for call in runner.calls))

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
        timeout = subprocess.TimeoutExpired([DOCKER_EXE, "start"], 3.0)
        runner = FakeRunner(
            execution_responses(self.workspace, self.input_root, start=timeout)
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            self.sandbox(runner).execute(
                self.workspace, "tool.py", execution_timeout=3.0
            )
        self.assertEqual(runner.calls[4][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(runner.calls[5][0][1], "stop")
        self.assertEqual(runner.calls[6][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])
        self.assertEqual(runner.calls[7][0][-1], CONTAINER_ID)
        self.assertFalse(self.export_archive.exists())

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
        with self.assertRaisesRegex(IsolationError, "cleanup failed"):
            self.sandbox(runner).execute(self.workspace, "tool.py")

    def test_cleanup_requires_explicit_absence_readback(self) -> None:
        responses = execution_responses(self.workspace, self.input_root)
        responses[7] = completed(returncode=1, stderr="daemon unavailable")
        runner = FakeRunner(responses)
        with self.assertRaisesRegex(IsolationError, "absence could not be verified"):
            self.sandbox(runner).execute(self.workspace, "tool.py")

    def test_stdout_and_stderr_are_bounded_and_marked(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                start=completed(
                    stdout="x" * (MAX_STDOUT_BYTES + 100),
                    stderr="y" * (MAX_STDERR_BYTES + 100),
                ),
            )
        )
        result = self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertLessEqual(len(result.stdout), MAX_STDOUT_BYTES)
        self.assertLessEqual(len(result.stderr), MAX_STDERR_BYTES)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertIn("MARB output truncated", result.stdout)
        self.assertIn("MARB output truncated", result.stderr)

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
                    with self.assertRaisesRegex(IsolationError, "limit"):
                        self.sandbox(runner).execute(self.workspace, "tool.py")
                self.assertEqual(runner.calls, [])
                candidate.unlink()

    def test_oversized_workspace_export_is_rejected_after_container_cleanup(self) -> None:
        runner = FakeRunner(
            execution_responses(
                self.workspace,
                self.input_root,
                export_files={"tool.py": b"pass\n", "generated.bin": b"z" * 200},
            )
        )
        with mock.patch.object(isolated, "MAX_WORKSPACE_TOTAL_BYTES", 100):
            with self.assertRaisesRegex(IsolationError, "total-size limit"):
                self.sandbox(runner).execute(self.workspace, "tool.py")
        self.assertEqual(runner.calls[4][0], [DOCKER_EXE, "kill", CONTAINER_ID])
        self.assertEqual(runner.calls[6][0], [DOCKER_EXE, "rm", "--force", CONTAINER_ID])
        self.assertFalse((self.workspace / "generated.bin").exists())
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
        for keyword in ({"execution_timeout": 0}, {"inspect_timeout": 0}):
            with self.subTest(keyword=keyword):
                runner = FakeRunner([])
                with self.assertRaises(IsolationError):
                    self.sandbox(runner).execute(
                        self.workspace, "tool.py", **keyword
                    )
                self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
