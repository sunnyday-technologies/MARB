"""Host-only, non-qualifying Docker policy-readback diagnostic.

Importing this module is inert.  A real Docker command is reached only after
``run_policy_probe`` is called.  The diagnostic creates the positive runtime
smoke layout, asks :class:`IsolatedDockerPython` to inspect an inert container,
and removes that container without starting model-authored code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

try:  # package import in tests; script import for the CLI
    from . import cohort_executor as executor
    from . import isolated_container as isolation
    from . import runtime_smoke_probes as probes
except ImportError:  # pragma: no cover - exercised by script-mode use
    import cohort_executor as executor  # type: ignore[no-redef]
    import isolated_container as isolation  # type: ignore[no-redef]
    import runtime_smoke_probes as probes  # type: ignore[no-redef]


SCHEMA = "marb_container_policy_probe.v1"
PROBE_ID = "container_policy_readback"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SCRIPT_NAME = "probe.py"
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
MAX_GIT_IDENTITY_BYTES = 4096
SOURCE_FILES = (
    "harness/cohort_executor.py",
    "harness/container_policy_probe.py",
    "harness/isolated_container.py",
    "harness/runtime_smoke_probes.py",
)


def _execution_boundary() -> dict[str, bool]:
    """Return the fixed non-execution assertions carried by every receipt."""
    return {
        "benchmark_executed": False,
        "container_started": False,
        "model_invoked": False,
        "provider_invoked": False,
        "qualification_attempt_consumed": False,
    }


class PolicyProbeError(RuntimeError):
    """Fixed-text host policy-probe failure."""


class PolicyProbeEngine(Protocol):
    def probe_policy_readback(
        self,
        workspace: Path,
        relative_script: str,
        *,
        inspect_timeout: float | None = 30.0,
        execution_timeout: float | None = 60.0,
    ) -> Any: ...


EngineFactory = Callable[[str, str, Path], PolicyProbeEngine]
SourceVerifier = Callable[
    [Path, str, str, str, str, Mapping[str, Any]], dict[str, Any]
]


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _write_exact(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        written = handle.write(raw)
        if written != len(raw):
            raise PolicyProbeError("policy-probe input write was incomplete")


def _source_identity(path: Path, public_path: str) -> dict[str, Any]:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or path.is_symlink()
            or before.st_size > executor.MAX_COMMITTED_BLOB_BYTES
        ):
            raise OSError
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise PolicyProbeError("policy-probe source cannot be read safely") from exc
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if (
        tuple(getattr(before, key, None) for key in identity_fields)
        != tuple(getattr(after, key, None) for key in identity_fields)
        or len(raw) != before.st_size
    ):
        raise PolicyProbeError("policy-probe source changed while being read")
    return {
        "bytes": len(raw),
        "hash_mode": "raw",
        "path": public_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _source_manifest() -> dict[str, Any]:
    repository = Path(__file__).resolve(strict=True).parents[1]
    entries = [
        _source_identity(repository.joinpath(*path.split("/")), path)
        for path in SOURCE_FILES
    ]
    return {
        "entries": entries,
        "manifest_sha256": hashlib.sha256(
            _canonical_json({"entries": entries}).encode("ascii")
        ).hexdigest(),
    }


def _capture_git_identity(
    repository: Path,
    authorization: Mapping[str, Any],
    arguments: Sequence[str],
    *,
    expect_empty: bool = False,
) -> str:
    try:
        with executor._locked_git_spawn_identity(authorization) as git_identity:
            completed = subprocess.run(
                [
                    git_identity["path"],
                    "--no-replace-objects",
                    "-c",
                    f"safe.directory={repository.as_posix()}",
                    *arguments,
                ],
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15.0,
                check=False,
                shell=False,
                env=executor._minimal_git_env(),
            )
    except (OSError, subprocess.SubprocessError, executor.ExecutorError) as exc:
        raise PolicyProbeError("policy-probe source checkout cannot be verified") from exc
    raw = completed.stdout
    if completed.returncode != 0 or len(raw) > MAX_GIT_IDENTITY_BYTES:
        raise PolicyProbeError("policy-probe source checkout cannot be verified")
    if expect_empty:
        if raw:
            raise PolicyProbeError("policy-probe source checkout has tracked changes")
        return ""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PolicyProbeError("policy-probe source identity is malformed") from exc
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if "\r" in text or "\n" in text:
        raise PolicyProbeError("policy-probe source identity is malformed")
    return text


def _verify_source_checkout(
    source_root: Path,
    source_revision: str,
    source_tree: str,
    git_executable: str,
    git_executable_sha256: str,
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(source_revision, str) or not HEX40.fullmatch(source_revision):
        raise PolicyProbeError("policy-probe source revision is malformed")
    if not isinstance(source_tree, str) or not HEX40.fullmatch(source_tree):
        raise PolicyProbeError("policy-probe source tree is malformed")
    if not isinstance(git_executable_sha256, str) or not HEX64.fullmatch(
        git_executable_sha256
    ):
        raise PolicyProbeError("Git executable digest is malformed")
    try:
        repository = Path(source_root).resolve(strict=True)
        executing_root = Path(__file__).resolve(strict=True).parents[1]
    except OSError as exc:
        raise PolicyProbeError("policy-probe source root is unavailable") from exc
    if repository != executing_root or not repository.is_dir():
        raise PolicyProbeError("source root does not contain the executing policy probe")
    authorization = {
        "git_executable": git_executable,
        "git_executable_sha256": git_executable_sha256,
    }
    literal_head = _capture_git_identity(
        repository, authorization, ("rev-parse", "--verify", "HEAD")
    )
    commit = _capture_git_identity(
        repository, authorization, ("rev-parse", "--verify", "HEAD^{commit}")
    )
    tree = _capture_git_identity(
        repository, authorization, ("rev-parse", "--verify", "HEAD^{tree}")
    )
    if (
        literal_head != source_revision
        or commit != source_revision
        or tree != source_tree
    ):
        raise PolicyProbeError("policy-probe source HEAD or tree does not match")
    _capture_git_identity(
        repository,
        authorization,
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        expect_empty=True,
    )
    committed_entries: list[dict[str, Any]] = []
    for item in source_manifest.get("entries", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise PolicyProbeError("policy-probe source manifest is malformed")
        try:
            raw = executor._read_committed_blob_with_git(
                authorization,
                repository,
                source_revision,
                item["path"],
            )
        except executor.ExecutorError as exc:
            raise PolicyProbeError("committed policy-probe source cannot be verified") from exc
        identity = {
            "bytes": len(raw),
            "hash_mode": "raw",
            "path": item["path"],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if identity != dict(item):
            raise PolicyProbeError("executing policy-probe source differs from committed HEAD")
        committed_entries.append(identity)
    try:
        git_identity = executor._verify_host_git_executable(authorization)
    except executor.ExecutorError as exc:
        raise PolicyProbeError("Git executable is unavailable or unsafe") from exc
    return {
        "clean_tracked_checkout": True,
        "git_executable_sha256": git_identity["sha256"],
        "revision": commit,
        "source_manifest_sha256": source_manifest.get("manifest_sha256"),
        "tree": tree,
    }


def _source_proof_matches(
    proof: Any,
    *,
    revision: str,
    tree: str,
    git_sha256: str,
    source_manifest_sha256: str,
) -> bool:
    return bool(
        isinstance(proof, Mapping)
        and proof.get("clean_tracked_checkout") is True
        and proof.get("git_executable_sha256") == git_sha256
        and proof.get("revision") == revision
        and proof.get("source_manifest_sha256") == source_manifest_sha256
        and proof.get("tree") == tree
    )


def _materialize_positive_layout(workspace: Path, input_root: Path) -> None:
    if not probes.CASES or probes.CASES[0].case_id != "positive_provenance_and_import":
        raise PolicyProbeError("positive policy-probe contract is unavailable")
    workspace.mkdir()
    _write_exact(workspace / SCRIPT_NAME, probes.CASES[0].source)
    input_root.mkdir()
    for relative, raw in probes.RUNTIME_SMOKE_INPUTS:
        _write_exact(input_root.joinpath(*relative.split("/")), raw)


def _positive_layout_is_exact(workspace: Path, input_root: Path) -> bool:
    expected_files = {
        (workspace / SCRIPT_NAME): probes.CASES[0].source,
        **{
            input_root.joinpath(*relative.split("/")): raw
            for relative, raw in probes.RUNTIME_SMOKE_INPUTS
        },
    }
    observed_files: set[Path] = set()
    for root in (workspace, input_root):
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in directories:
                candidate = current_path / name
                try:
                    info = candidate.lstat()
                except OSError:
                    return False
                if not stat.S_ISDIR(info.st_mode) or candidate.is_symlink():
                    return False
            for name in files:
                observed_files.add(current_path / name)
    if observed_files != set(expected_files):
        return False
    for path, expected in expected_files.items():
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or path.is_symlink()
                or path.read_bytes() != expected
            ):
                return False
        except OSError:
            return False
    return True


def _verify_docker_executable(path_text: str, expected_sha256: str) -> tuple[Any, ...]:
    if not isinstance(expected_sha256, str) or not HEX64.fullmatch(expected_sha256):
        raise PolicyProbeError("Docker executable digest is malformed")
    path = Path(path_text)
    if not path.is_absolute():
        raise PolicyProbeError("Docker executable path is not absolute")
    try:
        isolation._assert_path_chain_is_plain(path, "Docker executable")
        before = path.lstat()
    except (OSError, isolation.IsolationError) as exc:
        raise PolicyProbeError("Docker executable is unavailable or unsafe") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_size > MAX_EXECUTABLE_BYTES
    ):
        raise PolicyProbeError("Docker executable is unavailable or unsafe")
    digest = hashlib.sha256()
    measured = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                measured += len(chunk)
                digest.update(chunk)
        after = path.lstat()
    except OSError as exc:
        raise PolicyProbeError("Docker executable changed during hashing") from exc
    stat_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    identity = tuple(getattr(before, field, None) for field in stat_fields)
    if (
        identity != tuple(getattr(after, field, None) for field in stat_fields)
        or measured != before.st_size
        or digest.hexdigest() != expected_sha256
    ):
        raise PolicyProbeError("Docker executable digest does not match")
    return (*identity, measured, digest.hexdigest())


def _default_engine_factory(
    image: str, docker_executable: str, input_root: Path
) -> PolicyProbeEngine:
    return isolation.IsolatedDockerPython(
        image,
        input_root=input_root,
        docker_executable=docker_executable,
    )


def run_policy_probe(
    *,
    image: str,
    docker_executable: str,
    docker_executable_sha256: str,
    git_executable: str,
    git_executable_sha256: str,
    source_revision: str,
    source_root: Path,
    source_tree: str,
    work_root: Path,
    engine_factory: EngineFactory | None = None,
    source_verifier: SourceVerifier | None = None,
) -> dict[str, Any]:
    """Run one inert-container policy readback; never start the container."""
    isolation.validate_image_reference(image)
    source_manifest = _source_manifest()
    verifier = source_verifier or _verify_source_checkout
    source_preflight = verifier(
        Path(source_root),
        source_revision,
        source_tree,
        git_executable,
        git_executable_sha256,
        source_manifest,
    )
    if not _source_proof_matches(
        source_preflight,
        revision=source_revision,
        tree=source_tree,
        git_sha256=git_executable_sha256,
        source_manifest_sha256=source_manifest["manifest_sha256"],
    ):
        raise PolicyProbeError("policy-probe source preflight proof is mismatched")
    root = Path(work_root).resolve(strict=True)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise PolicyProbeError("policy-probe work root is unavailable") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        raise PolicyProbeError("policy-probe work root is unavailable or unsafe")

    docker_before = _verify_docker_executable(
        docker_executable, docker_executable_sha256
    )
    primary_error: Exception | None = None
    result: Any | None = None
    layout_after = False
    factory = engine_factory or _default_engine_factory
    try:
        with tempfile.TemporaryDirectory(
            prefix="marb-policy-probe-", dir=root
        ) as temporary:
            case_root = Path(temporary).resolve(strict=True)
            workspace = case_root / "workspace"
            input_root = case_root / "inputs"
            _materialize_positive_layout(workspace, input_root)
            if not _positive_layout_is_exact(workspace, input_root):
                raise PolicyProbeError("positive policy-probe layout is malformed")
            engine = factory(image, docker_executable, input_root)
            result = engine.probe_policy_readback(
                workspace,
                SCRIPT_NAME,
                inspect_timeout=30.0,
                execution_timeout=60.0,
            )
            layout_after = _positive_layout_is_exact(workspace, input_root)
    except Exception as exc:
        primary_error = exc

    postflight_error: Exception | None = None
    try:
        docker_after = _verify_docker_executable(
            docker_executable, docker_executable_sha256
        )
        if docker_before != docker_after:
            raise PolicyProbeError("Docker executable changed during policy probe")
    except Exception as exc:
        postflight_error = PolicyProbeError(
            "Docker executable changed during policy probe"
        )
        postflight_error.__cause__ = exc
    source_postflight: Mapping[str, Any] | None = None
    try:
        source_manifest_after = _source_manifest()
        if source_manifest_after != source_manifest:
            raise PolicyProbeError("policy-probe source changed during diagnostic")
        source_postflight = verifier(
            Path(source_root),
            source_revision,
            source_tree,
            git_executable,
            git_executable_sha256,
            source_manifest_after,
        )
        if not _source_proof_matches(
            source_postflight,
            revision=source_revision,
            tree=source_tree,
            git_sha256=git_executable_sha256,
            source_manifest_sha256=source_manifest["manifest_sha256"],
        ):
            raise PolicyProbeError("policy-probe source postflight proof is mismatched")
    except Exception as exc:
        postflight_error = PolicyProbeError(
            "policy-probe source changed during diagnostic"
        )
        postflight_error.__cause__ = exc
    if postflight_error is not None:
        raise postflight_error.with_traceback(None) from None
    if primary_error is not None:
        raise primary_error.with_traceback(None) from None
    if result is None:
        raise PolicyProbeError("policy probe returned no result")

    checks = {
        "container_absence_verified": getattr(
            result, "container_absence_verified", None
        )
        is True,
        "docker_executable_stable": True,
        "export_staging_removed": getattr(result, "export_staging_removed", None)
        is True,
        "host_layout_unchanged": layout_after,
        "inert_container_cleanup_verified": getattr(result, "cleanup_verified", None)
        is True,
        "policy_readback_verified": getattr(result, "policy_readback_verified", None)
        is True,
        "runtime_binding_exact": bool(
            getattr(result, "image", None) == image
            and getattr(result, "docker_executable", None) == docker_executable
            and getattr(result, "script", None) == SCRIPT_NAME
        ),
    }
    if not all(checks.values()):
        raise PolicyProbeError("policy probe did not satisfy its diagnostic contract")
    return {
        "bindings": {
            "docker_executable_sha256": docker_executable_sha256,
            "git_executable_sha256": git_executable_sha256,
            "image_repo_digest": image,
            "source_manifest_sha256": source_manifest["manifest_sha256"],
            "source_revision": source_revision,
            "source_tree": source_tree,
        },
        "checks": checks,
        "execution_policy": _execution_boundary(),
        "non_qualifying": True,
        "probe": PROBE_ID,
        "qualification_eligible": False,
        "schema": SCHEMA,
        "status": "diagnostic_pass",
    }


def _safe_policy_predicate(exc: BaseException) -> str | None:
    candidate = getattr(exc, "policy_predicate", None)
    if candidate is None:
        evidence = getattr(exc, "evidence", None)
        if isinstance(evidence, Mapping):
            candidate = evidence.get("policy_predicate")
    return (
        candidate
        if isinstance(candidate, str)
        and candidate in isolation.CONTAINER_POLICY_PREDICATES
        else None
    )


def _rejected_payload(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "execution_policy": _execution_boundary(),
        "non_qualifying": True,
        "probe": PROBE_ID,
        "qualification_eligible": False,
        "schema": SCHEMA,
        "status": "diagnostic_rejected",
    }
    predicate = _safe_policy_predicate(exc)
    if predicate is not None:
        payload["policy_predicate"] = predicate
    return payload


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PolicyProbeError("policy-probe arguments were rejected")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        add_help=False,
        description="Inspect an inert MARB container's isolation policy",
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker-executable", required=True)
    parser.add_argument("--docker-executable-sha256", required=True)
    parser.add_argument("--git-executable", required=True)
    parser.add_argument("--git-executable-sha256", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        payload = run_policy_probe(
            image=args.image,
            docker_executable=args.docker_executable,
            docker_executable_sha256=args.docker_executable_sha256,
            git_executable=args.git_executable,
            git_executable_sha256=args.git_executable_sha256,
            source_revision=args.source_revision,
            source_root=args.source_root,
            source_tree=args.source_tree,
            work_root=args.work_root,
        )
    except Exception as exc:
        payload = _rejected_payload(exc)
        return_code = 2
    else:
        return_code = 0
    print(_canonical_json(payload), end="")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EngineFactory",
    "PolicyProbeError",
    "SCHEMA",
    "SourceVerifier",
    "main",
    "run_policy_probe",
]
