"""Immutable Hugging Face provenance for local MARB model servers.

The adapter inventories an existing Hugging Face cache, validates or creates
reviewable model-profile documents, and can probe a loopback OpenAI-compatible
``/v1/models`` endpoint.  It never downloads a model, launches a server, calls
a model, reads credentials, grades a run, or mutates MARB publication state.
Importing this module performs no file, environment, subprocess, network, or
model action.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import sys
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, NoReturn, Sequence


PROFILE_SCHEMA = "marb_hf_local_model_profile.v1"
LAUNCH_CONFIG_SCHEMA = "marb_local_model_launch_config.v1"
RUNTIME_ATTESTATION_SCHEMA = "marb_local_model_runtime_attestation.v1"
RUNTIME_ATTESTATION_SCOPE = "same-host-docker-and-served-identity"
INVENTORY_SCHEMA = "marb_hf_cache_inventory.v1"
PROVIDER_PROTOCOL = "openai-compatible-chat-completions"
HARDWARE_CLASS = "nvidia-dgx-spark-gb10"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HF_REPOSITORY = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
)
SAFE_MODEL_ID = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}[A-Za-z0-9]|[A-Za-z0-9]"
)
SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
SAFE_QUANTIZATION = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
SAFE_HOST_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]|[a-z0-9]")
SAFE_CONTAINER_NAME = re.compile(r"marb-model-[a-z0-9][a-z0-9._-]{0,50}[a-z0-9]")
OCI_DIGEST = re.compile(
    r"[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"@sha256:[0-9a-f]{64}"
)
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SENSITIVE_ENV_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|AUTH|BEARER|CREDENTIAL|PASSWORD|SECRET|TOKEN)(?:$|_)",
    re.IGNORECASE,
)


class ModelProfileError(ValueError):
    """A local model profile, cache, or endpoint failed closed."""


def _fail(message: str) -> NoReturn:
    raise ModelProfileError(message)


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(f"{label} has unknown or missing fields")
    return value


def _decode_canonical(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail(f"{label} is not canonical JSON")
    if not isinstance(value, dict) or raw != _canonical_json(value, newline=True):
        _fail(f"{label} must be a canonical JSON object with one LF terminator")
    return value


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return path.is_symlink()
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _real_directory(path: Path, label: str) -> Path:
    candidate = path.absolute()
    cursor = Path(candidate.anchor)
    try:
        if _is_link_like(cursor):
            raise OSError
        for part in candidate.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            if not stat.S_ISDIR(info.st_mode) or _is_link_like(cursor):
                raise OSError
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail(f"{label} must be a real directory with no linked path components")
    return resolved


def _safe_regular_files(root: Path, label: str) -> list[Path]:
    files: list[Path] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > 16:
            _fail(f"{label} nesting is too deep")
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            _fail(f"{label} cannot be enumerated safely")
        for child in children:
            try:
                info = child.lstat()
            except OSError:
                _fail(f"{label} cannot be enumerated safely")
            if _is_link_like(child):
                _fail(f"{label} contains a linked path")
            if stat.S_ISDIR(info.st_mode):
                pending.append((child, depth + 1))
            elif stat.S_ISREG(info.st_mode):
                files.append(child)
                if len(files) > 4_096:
                    _fail(f"{label} contains too many files")
            else:
                _fail(f"{label} contains an unsupported filesystem entry")
    return sorted(files, key=lambda item: item.as_posix())


def _stable_read(path: Path, label: str, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(path)
            or before.st_size > max_bytes
        ):
            raise OSError
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        after = path.lstat()
    except OSError:
        _fail(f"{label} cannot be read safely")
    if (
        len(raw) > max_bytes
        or len(raw) != after.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        _fail(f"{label} changed while it was being read")
    return raw


def _safe_token(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not SAFE_TOKEN.fullmatch(value):
        _fail(f"{label} is not a safe public token")
    return value


def _canonical_loopback_endpoint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or CONTROL.search(value)
    ):
        _fail("local model endpoint is malformed")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("local model endpoint is malformed")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.netloc
        or "%" in parsed.path
        or parsed.scheme != "http"
        or parsed.path != "/v1"
        or port is None
        or not 1 <= port <= 65535
    ):
        _fail("local model endpoint must be an explicit-port credential-free loopback http /v1 URL")
    host = parsed.hostname.casefold()
    if host not in {"127.0.0.1", "::1"}:
        _fail("local model endpoint must use the exact 127.0.0.1 or ::1 loopback literal")
    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{port}/v1"


def canonical_loopback_endpoint(value: Any) -> str:
    """Return the one canonical local-model endpoint form or fail closed."""
    return _canonical_loopback_endpoint(value)


def expected_served_model_id(
    repository_id: str, revision: str, launch_config_sha256: str
) -> str:
    """Return the exact revision-bearing ID the server must report."""
    if not isinstance(repository_id, str) or not HF_REPOSITORY.fullmatch(repository_id):
        _fail("Hugging Face repository_id is malformed")
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        _fail("Hugging Face revision must be a full lowercase commit identity")
    if not isinstance(launch_config_sha256, str) or not HEX64.fullmatch(
        launch_config_sha256
    ):
        _fail("local model launch configuration digest is malformed")
    repository_sha256 = hashlib.sha256(repository_id.encode("ascii")).hexdigest()
    model_id = (
        f"marb-hf/repository/{repository_sha256}/revision/{revision}/"
        f"launch/{launch_config_sha256}"
    )
    if not SAFE_MODEL_ID.fullmatch(model_id):
        _fail("revision-bearing served model identity exceeds the H2b limit")
    return model_id


def _validated_launch_config(
    value: Any, *, source_revision: str
) -> tuple[dict[str, Any], str]:
    launch = _exact_keys(
        value,
        {
            "schema",
            "model_revision",
            "tokenizer_revision",
            "offline",
            "trust_remote_code",
            "max_model_len",
            "max_num_seqs",
            "gpu_memory_utilization",
            "dtype",
            "quantization",
            "tool_call_parser",
            "reasoning_parser",
            "chat_template_sha256",
            "container_port",
        },
        "local model launch configuration",
    )
    if (
        launch.get("schema") != LAUNCH_CONFIG_SCHEMA
        or launch.get("model_revision") != source_revision
        or not isinstance(launch.get("tokenizer_revision"), str)
        or not HEX40.fullmatch(launch["tokenizer_revision"])
        or launch.get("offline") is not True
        or launch.get("trust_remote_code") is not False
    ):
        _fail("local model launch revisions and offline policy are inconsistent")
    max_model_len = launch.get("max_model_len")
    max_num_seqs = launch.get("max_num_seqs")
    container_port = launch.get("container_port")
    if (
        not isinstance(max_model_len, int)
        or isinstance(max_model_len, bool)
        or not 256 <= max_model_len <= 1_000_000
        or not isinstance(max_num_seqs, int)
        or isinstance(max_num_seqs, bool)
        or not 1 <= max_num_seqs <= 1_024
        or not isinstance(container_port, int)
        or isinstance(container_port, bool)
        or not 1 <= container_port <= 65_535
    ):
        _fail("local model launch numeric limits are malformed")
    utilization = launch.get("gpu_memory_utilization")
    if (
        not isinstance(utilization, str)
        or not re.fullmatch(r"0\.[1-9][0-9]", utilization)
        or float(utilization) > 0.98
    ):
        _fail("local model GPU-memory utilization must be a two-decimal value from 0.10 through 0.98")
    _safe_token(launch.get("dtype"), "local model dtype")
    quantization = launch.get("quantization")
    if quantization is not None and (
        not isinstance(quantization, str)
        or not SAFE_QUANTIZATION.fullmatch(quantization)
    ):
        _fail("local model quantization is malformed")
    _safe_token(
        launch.get("tool_call_parser"),
        "local model tool_call_parser",
        nullable=True,
    )
    _safe_token(
        launch.get("reasoning_parser"),
        "local model reasoning_parser",
        nullable=True,
    )
    template_digest = launch.get("chat_template_sha256")
    if template_digest is not None and (
        not isinstance(template_digest, str) or not HEX64.fullmatch(template_digest)
    ):
        _fail("local model chat-template digest is malformed")
    return launch, hashlib.sha256(_canonical_json(launch)).hexdigest()


def _validated_topology(value: Any) -> dict[str, Any]:
    topology = _exact_keys(
        value,
        {
            "mode",
            "node_count",
            "tensor_parallel_size",
            "pipeline_parallel_size",
            "nodes",
        },
        "local model topology",
    )
    if (
        topology.get("mode") != "single-node"
        or any(
            not isinstance(topology.get(key), int)
            or isinstance(topology.get(key), bool)
            for key in (
                "node_count",
                "tensor_parallel_size",
                "pipeline_parallel_size",
            )
        )
        or topology.get("node_count") != 1
        or topology.get("tensor_parallel_size") != 1
        or topology.get("pipeline_parallel_size") != 1
        or not isinstance(topology.get("nodes"), list)
        or len(topology["nodes"]) != 1
    ):
        _fail("local model profile v1 supports exactly one Docker node")
    node = _exact_keys(
        topology["nodes"][0],
        {
            "ordinal",
            "role",
            "host_label",
            "container_name",
            "container_image",
        },
        "local model topology node",
    )
    if (
        not isinstance(node.get("ordinal"), int)
        or isinstance(node.get("ordinal"), bool)
        or node.get("ordinal") != 1
        or node.get("role") != "api"
        or not isinstance(node.get("host_label"), str)
        or not SAFE_HOST_LABEL.fullmatch(node["host_label"])
        or not isinstance(node.get("container_name"), str)
        or not SAFE_CONTAINER_NAME.fullmatch(node["container_name"])
        or not isinstance(node.get("container_image"), str)
        or len(node["container_image"]) > 400
        or not OCI_DIGEST.fullmatch(node["container_image"])
    ):
        _fail("local model topology node identity is malformed")
    return topology


def make_profile_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Digest-wrap a reviewed profile; this does not launch or approve a model."""
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return {
        "schema": PROFILE_SCHEMA,
        "profile_sha256": digest,
        "profile": payload,
    }


def verify_profile_envelope(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    value = _exact_keys(
        _decode_canonical(raw, "local model profile"),
        {"schema", "profile_sha256", "profile"},
        "local model profile envelope",
    )
    if value.get("schema") != PROFILE_SCHEMA:
        _fail("local model profile schema is unsupported")
    profile = value.get("profile")
    if not isinstance(profile, dict):
        _fail("local model profile payload is malformed")
    digest = hashlib.sha256(_canonical_json(profile)).hexdigest()
    if (
        not isinstance(expected_sha256, str)
        or not HEX64.fullmatch(expected_sha256)
        or value.get("profile_sha256") != digest
        or expected_sha256 != digest
    ):
        _fail("local model profile digest does not match independent review")
    profile = _exact_keys(
        profile,
        {"source", "provider", "runtime"},
        "local model profile payload",
    )
    source = _exact_keys(
        profile.get("source"),
        {"kind", "repository_id", "revision"},
        "local model source",
    )
    if source.get("kind") != "huggingface":
        _fail("local model source must be Hugging Face")
    repository_id = source.get("repository_id")
    revision = source.get("revision")
    if not isinstance(repository_id, str) or not HF_REPOSITORY.fullmatch(
        repository_id
    ):
        _fail("Hugging Face repository_id is malformed")
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        _fail("Hugging Face revision must be a full lowercase commit identity")
    runtime = _exact_keys(
        profile.get("runtime"),
        {
            "kind",
            "engine",
            "engine_version",
            "hardware_class",
            "topology",
            "launch_config",
            "launch_config_sha256",
        },
        "local model runtime",
    )
    if runtime.get("kind") != "docker":
        _fail("local model runtime kind is unsupported")
    if runtime.get("engine") != "vllm":
        _fail("local model profile v1 runtime engine must be vLLM")
    _safe_token(runtime.get("engine_version"), "local model engine_version")
    if runtime.get("hardware_class") != HARDWARE_CLASS:
        _fail("local model hardware class is unsupported")
    _validated_topology(runtime.get("topology"))
    launch, measured_launch_digest = _validated_launch_config(
        runtime.get("launch_config"), source_revision=revision
    )
    if runtime.get("launch_config_sha256") != measured_launch_digest:
        _fail("local model launch configuration digest does not bind its exact bytes")
    expected_id = expected_served_model_id(
        repository_id, revision, measured_launch_digest
    )
    provider = _exact_keys(
        profile.get("provider"),
        {
            "protocol",
            "served_model_id",
            "endpoint",
            "credential_env",
            "billing_mode",
        },
        "local model provider",
    )
    if (
        provider.get("protocol") != PROVIDER_PROTOCOL
        or provider.get("served_model_id") != expected_id
        or provider.get("credential_env") is not None
        or provider.get("billing_mode") != "local-no-charge"
    ):
        _fail("local model provider identity is not automation-safe")
    canonical_endpoint = _canonical_loopback_endpoint(provider.get("endpoint"))
    if provider.get("endpoint") != canonical_endpoint:
        _fail("local model endpoint must use its exact canonical form")
    endpoint_port = urllib.parse.urlsplit(canonical_endpoint).port
    if endpoint_port != launch["container_port"]:
        _fail("local model endpoint port must match the declared API container port")
    return value


def expected_server_command(profile: dict[str, Any]) -> list[str]:
    """Derive the exact public vLLM command bound by a verified profile."""
    provider = profile["provider"]
    runtime = profile["runtime"]
    launch = runtime["launch_config"]
    command = [
        "vllm",
        "serve",
        "/models",
        "--served-model-name",
        provider["served_model_id"],
        "--host",
        "0.0.0.0",
        "--port",
        str(launch["container_port"]),
        "--tensor-parallel-size",
        str(runtime["topology"]["tensor_parallel_size"]),
        "--pipeline-parallel-size",
        str(runtime["topology"]["pipeline_parallel_size"]),
        "--max-model-len",
        str(launch["max_model_len"]),
        "--max-num-seqs",
        str(launch["max_num_seqs"]),
        "--gpu-memory-utilization",
        launch["gpu_memory_utilization"],
        "--dtype",
        launch["dtype"],
    ]
    if launch["quantization"] is not None:
        command.extend(["--quantization", launch["quantization"]])
    if launch["tool_call_parser"] is not None:
        command.extend(
            ["--enable-auto-tool-choice", "--tool-call-parser", launch["tool_call_parser"]]
        )
    if launch["reasoning_parser"] is not None:
        command.extend(["--reasoning-parser", launch["reasoning_parser"]])
    return command


def validate_runtime_attestation(
    value: Any,
    *,
    profile_raw: bytes,
    expected_profile_sha256: str,
) -> dict[str, Any]:
    """Validate a retained runtime attestation against the exact profile."""
    envelope = verify_profile_envelope(
        profile_raw, expected_sha256=expected_profile_sha256
    )
    profile = envelope["profile"]
    runtime = profile["runtime"]
    provider = profile["provider"]
    source = profile["source"]
    node = runtime["topology"]["nodes"][0]
    attestation = _exact_keys(
        value,
        {
            "schema",
            "status",
            "scope",
            "profile_sha256",
            "container",
            "model_mount",
            "runtime",
            "endpoint_probe",
            "model_call_performed",
        },
        "local model runtime attestation",
    )
    if (
        attestation.get("schema") != RUNTIME_ATTESTATION_SCHEMA
        or attestation.get("status") != "passed"
        or attestation.get("scope") != RUNTIME_ATTESTATION_SCOPE
        or attestation.get("profile_sha256") != expected_profile_sha256
        or attestation.get("model_call_performed") is not False
    ):
        _fail("local model runtime attestation identity is inconsistent")

    container = _exact_keys(
        attestation.get("container"),
        {
            "id_sha256",
            "name",
            "image_repo_digest",
            "image_id",
            "server_command_sha256",
            "environment_keys",
        },
        "local model runtime attestation container",
    )
    environment_keys = container.get("environment_keys")
    expected_command_sha256 = hashlib.sha256(
        _canonical_json(expected_server_command(profile))
    ).hexdigest()
    if (
        not isinstance(container.get("id_sha256"), str)
        or not HEX64.fullmatch(container["id_sha256"])
        or container.get("name") != node["container_name"]
        or container.get("image_repo_digest") != node["container_image"]
        or not isinstance(container.get("image_id"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", container["image_id"])
        or container.get("server_command_sha256") != expected_command_sha256
        or not isinstance(environment_keys, list)
        or environment_keys != sorted(environment_keys)
        or len(environment_keys) > 256
        or len(set(environment_keys)) != len(environment_keys)
        or not all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name)
            and not SENSITIVE_ENV_NAME.search(name)
            for name in environment_keys
        )
    ):
        _fail("local model runtime attestation container is inconsistent")

    model_mount = _exact_keys(
        attestation.get("model_mount"),
        {"repository_id", "revision", "read_only"},
        "local model runtime attestation mount",
    )
    if model_mount != {
        "repository_id": source["repository_id"],
        "revision": source["revision"],
        "read_only": True,
    }:
        _fail("local model runtime attestation mount is inconsistent")

    attested_runtime = _exact_keys(
        attestation.get("runtime"),
        {"engine", "engine_version", "hardware_class", "launch_config_sha256"},
        "local model runtime attestation runtime",
    )
    if attested_runtime != {
        "engine": runtime["engine"],
        "engine_version": runtime["engine_version"],
        "hardware_class": runtime["hardware_class"],
        "launch_config_sha256": runtime["launch_config_sha256"],
    }:
        _fail("local model runtime attestation runtime is inconsistent")

    probe = _exact_keys(
        attestation.get("endpoint_probe"),
        {
            "schema",
            "endpoint",
            "model_call_performed",
            "served_model_ids",
            "expected_model_id",
            "expected_model_present",
        },
        "local model runtime attestation endpoint probe",
    )
    served_model_ids = probe.get("served_model_ids")
    if (
        probe.get("schema") != "marb_local_model_endpoint_probe.v1"
        or probe.get("endpoint") != provider["endpoint"]
        or probe.get("model_call_performed") is not False
        or not isinstance(served_model_ids, list)
        or served_model_ids != sorted(served_model_ids)
        or len(served_model_ids) > 256
        or len(set(served_model_ids)) != len(served_model_ids)
        or not all(
            isinstance(model_id, str) and SAFE_MODEL_ID.fullmatch(model_id)
            for model_id in served_model_ids
        )
        or probe.get("expected_model_id") != provider["served_model_id"]
        or probe.get("expected_model_present") is not True
        or provider["served_model_id"] not in served_model_ids
    ):
        _fail("local model runtime attestation endpoint probe is inconsistent")
    return attestation


def attest_runtime(
    profile_raw: bytes,
    *,
    expected_profile_sha256: str,
    observer: Callable[[dict[str, Any]], dict[str, Any]],
    connection_factory: Any | None = None,
) -> dict[str, Any]:
    """Verify one already-running same-host Docker server without a model call.

    ``observer`` is the narrow Docker boundary.  It must return only the
    allowlisted public identity fields below; environment values are never
    requested or retained.
    """
    envelope = verify_profile_envelope(
        profile_raw, expected_sha256=expected_profile_sha256
    )
    profile = envelope["profile"]
    runtime = profile["runtime"]
    node = runtime["topology"]["nodes"][0]
    launch = runtime["launch_config"]
    endpoint = _canonical_loopback_endpoint(profile["provider"]["endpoint"])
    endpoint_parts = urllib.parse.urlsplit(endpoint)
    command_sha256 = hashlib.sha256(
        _canonical_json(expected_server_command(profile))
    ).hexdigest()
    expected = {
        "profile_sha256": expected_profile_sha256,
        "container_name": node["container_name"],
        "container_image": node["container_image"],
        "engine": runtime["engine"],
        "engine_version": runtime["engine_version"],
        "hardware_class": runtime["hardware_class"],
        "repository_id": profile["source"]["repository_id"],
        "revision": profile["source"]["revision"],
        "launch_config_sha256": runtime["launch_config_sha256"],
        "server_command_sha256": command_sha256,
        "host_ip": endpoint_parts.hostname,
        "host_port": endpoint_parts.port,
        "container_port": launch["container_port"],
        "served_model_id": profile["provider"]["served_model_id"],
        "model_mount_target": "/models",
    }
    observed = _exact_keys(
        observer(dict(expected)),
        {
            "container_id",
            "profile_sha256",
            "container_name",
            "running",
            "image_repo_digest",
            "image_id",
            "engine",
            "engine_version",
            "hardware_class",
            "repository_id",
            "revision",
            "launch_config_sha256",
            "server_command_sha256",
            "model_mount_read_only",
            "host_ip",
            "host_port",
            "container_port",
            "environment_keys",
        },
        "local model runtime observation",
    )
    container_id = observed.get("container_id")
    image_id = observed.get("image_id")
    environment_keys = observed.get("environment_keys")
    if (
        not isinstance(container_id, str)
        or not HEX64.fullmatch(container_id)
        or not isinstance(image_id, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        or observed.get("running") is not True
        or observed.get("model_mount_read_only") is not True
        or not isinstance(environment_keys, list)
        or len(environment_keys) > 256
        or not all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name)
            and not SENSITIVE_ENV_NAME.search(name)
            for name in environment_keys
        )
        or len(set(environment_keys)) != len(environment_keys)
    ):
        _fail("local model runtime observation is malformed or credential-bearing")
    equality = {
        "profile_sha256": "profile_sha256",
        "container_name": "container_name",
        "image_repo_digest": "container_image",
        "engine": "engine",
        "engine_version": "engine_version",
        "hardware_class": "hardware_class",
        "repository_id": "repository_id",
        "revision": "revision",
        "launch_config_sha256": "launch_config_sha256",
        "server_command_sha256": "server_command_sha256",
        "host_ip": "host_ip",
        "host_port": "host_port",
        "container_port": "container_port",
    }
    if any(observed[left] != expected[right] for left, right in equality.items()):
        _fail("local model runtime observation does not match the approved profile")
    probe = probe_models_endpoint(
        endpoint,
        expected_model_id=profile["provider"]["served_model_id"],
        connection_factory=connection_factory,
    )
    if not probe["expected_model_present"]:
        _fail("local model endpoint does not serve the approved revision/config identity")
    attestation = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "status": "passed",
        "scope": RUNTIME_ATTESTATION_SCOPE,
        "profile_sha256": expected_profile_sha256,
        "container": {
            "id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
            "name": observed["container_name"],
            "image_repo_digest": observed["image_repo_digest"],
            "image_id": image_id,
            "server_command_sha256": observed["server_command_sha256"],
            "environment_keys": sorted(environment_keys),
        },
        "model_mount": {
            "repository_id": observed["repository_id"],
            "revision": observed["revision"],
            "read_only": True,
        },
        "runtime": {
            "engine": observed["engine"],
            "engine_version": observed["engine_version"],
            "hardware_class": observed["hardware_class"],
            "launch_config_sha256": observed["launch_config_sha256"],
        },
        "endpoint_probe": probe,
        "model_call_performed": False,
    }
    return validate_runtime_attestation(
        attestation,
        profile_raw=profile_raw,
        expected_profile_sha256=expected_profile_sha256,
    )


def _cache_model_identity(name: str) -> str | None:
    if not name.startswith("models--"):
        return None
    parts = name[len("models--") :].split("--")
    if len(parts) != 2:
        return None
    candidate = "/".join(parts)
    return candidate if HF_REPOSITORY.fullmatch(candidate) else None


def inventory_cache(cache_root: Path) -> dict[str, Any]:
    """Inventory public repository/revision identities without following blobs."""
    root = _real_directory(cache_root, "Hugging Face cache root")
    models: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        _fail("Hugging Face cache root cannot be enumerated safely")
    for candidate in children:
        repository_id = _cache_model_identity(candidate.name)
        if repository_id is None:
            continue
        if not candidate.is_dir() or _is_link_like(candidate):
            _fail("Hugging Face cached model root is unsafe")
        revisions: list[str] = []
        snapshots = candidate / "snapshots"
        if snapshots.exists() or snapshots.is_symlink():
            if not snapshots.is_dir() or _is_link_like(snapshots):
                _fail("Hugging Face snapshots root is unsafe")
            for snapshot in sorted(snapshots.iterdir(), key=lambda item: item.name):
                if HEX40.fullmatch(snapshot.name):
                    if not snapshot.is_dir() or _is_link_like(snapshot):
                        _fail("Hugging Face snapshot directory is unsafe")
                    revisions.append(snapshot.name)
        refs: dict[str, str] = {}
        refs_root = candidate / "refs"
        if refs_root.exists() or refs_root.is_symlink():
            if not refs_root.is_dir() or _is_link_like(refs_root):
                _fail("Hugging Face refs root is unsafe")
            ref_paths = _safe_regular_files(refs_root, "Hugging Face refs")
            for ref_path in ref_paths:
                if _is_link_like(ref_path):
                    _fail("Hugging Face ref is unsafe")
                ref_name = ref_path.relative_to(refs_root).as_posix()
                if CONTROL.search(ref_name) or ".." in Path(ref_name).parts:
                    _fail("Hugging Face ref name is unsafe")
                try:
                    revision = _stable_read(ref_path, "Hugging Face ref", max_bytes=256).decode(
                        "ascii"
                    ).strip()
                except UnicodeError:
                    _fail("Hugging Face ref is malformed")
                if not HEX40.fullmatch(revision):
                    _fail("Hugging Face ref does not contain a full revision")
                refs[ref_name] = revision
        models.append(
            {
                "repository_id": repository_id,
                "revisions": sorted(set(revisions)),
                "refs": dict(sorted(refs.items())),
            }
        )
    return {
        "schema": INVENTORY_SCHEMA,
        "network_accessed": False,
        "models": models,
    }


def probe_models_endpoint(
    endpoint: str,
    *,
    expected_model_id: str,
    timeout_seconds: int = 5,
    connection_factory: Any | None = None,
) -> dict[str, Any]:
    """Read only ``/v1/models`` on loopback; no completion is requested."""
    canonical = _canonical_loopback_endpoint(endpoint)
    if not isinstance(expected_model_id, str) or not SAFE_MODEL_ID.fullmatch(expected_model_id):
        _fail("expected served model identity is malformed")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 30
    ):
        _fail("endpoint probe timeout is outside the allowed range")
    parsed = urllib.parse.urlsplit(canonical)
    if connection_factory is None:
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(parsed.hostname, parsed.port, timeout=timeout_seconds)
    else:
        connection = connection_factory(parsed, timeout_seconds)
    try:
        connection.request(
            "GET",
            parsed.path.rstrip("/") + "/models",
            headers={"Accept": "application/json", "User-Agent": "marb-local-profile/1"},
        )
        response = connection.getresponse()
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if int(length) > 1_000_000:
                    _fail("local model inventory response is too large")
            except ValueError:
                _fail("local model inventory response length is malformed")
        raw = response.read(1_000_001)
        if response.status != 200 or len(raw) > 1_000_000:
            _fail("local model inventory endpoint did not return a bounded success response")
    except (OSError, http.client.HTTPException):
        _fail("local model inventory endpoint is unavailable")
    finally:
        try:
            connection.close()
        except OSError:
            pass
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("local model inventory endpoint returned malformed JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        _fail("local model inventory endpoint returned an unsupported schema")
    model_ids: set[str] = set()
    for item in payload["data"]:
        if not isinstance(item, dict):
            _fail("local model inventory contains a malformed entry")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not SAFE_MODEL_ID.fullmatch(model_id):
            _fail("local model inventory contains an unsafe identity")
        model_ids.add(model_id)
    ordered = sorted(model_ids)
    return {
        "schema": "marb_local_model_endpoint_probe.v1",
        "endpoint": canonical,
        "model_call_performed": False,
        "served_model_ids": ordered,
        "expected_model_id": expected_model_id,
        "expected_model_present": expected_model_id in model_ids,
    }


def _write_canonical_exclusive(path: Path, value: Any) -> None:
    try:
        parent = _real_directory(path.parent, "local model profile output directory")
        if path.name in {"", ".", ".."} or CONTROL.search(path.name):
            raise OSError
        target = parent / path.name
        if target.exists() or target.is_symlink():
            raise OSError
        with target.open("xb") as handle:
            handle.write(_canonical_json(value, newline=True))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        _fail("local model profile output must be a new safe file")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory and bind an immutable Hugging Face local model profile."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory-cache")
    inventory.add_argument("--cache-root", type=Path, required=True)
    probe = commands.add_parser("probe-endpoint")
    probe.add_argument("--endpoint", required=True)
    probe.add_argument("--expected-model-id", required=True)
    probe.add_argument("--timeout-seconds", type=int, default=5)
    template = commands.add_parser("profile-template")
    template.add_argument("--repository-id", required=True)
    template.add_argument("--revision", required=True)
    template.add_argument("--endpoint", required=True)
    template.add_argument("--engine", choices=("vllm",), required=True)
    template.add_argument("--engine-version", required=True)
    template.add_argument("--container-image", required=True)
    template.add_argument("--host-label", required=True)
    template.add_argument("--container-name", required=True)
    template.add_argument("--tokenizer-revision", required=True)
    template.add_argument("--max-model-len", type=int, required=True)
    template.add_argument("--max-num-seqs", type=int, default=1)
    template.add_argument("--gpu-memory-utilization", default="0.80")
    template.add_argument("--dtype", required=True)
    template.add_argument("--quantization")
    template.add_argument("--tool-call-parser")
    template.add_argument("--reasoning-parser")
    template.add_argument("--chat-template-sha256")
    template.add_argument("--container-port", type=int, required=True)
    template.add_argument("--output", type=Path, required=True)
    seal = commands.add_parser("seal-profile")
    seal.add_argument("--payload", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate-profile")
    validate.add_argument("--profile", type=Path, required=True)
    validate.add_argument("--expected-profile-sha256", required=True)
    return parser


def _template_payload(args: argparse.Namespace) -> dict[str, Any]:
    launch_config = {
        "schema": LAUNCH_CONFIG_SCHEMA,
        "model_revision": args.revision,
        "tokenizer_revision": args.tokenizer_revision,
        "offline": True,
        "trust_remote_code": False,
        "max_model_len": args.max_model_len,
        "max_num_seqs": args.max_num_seqs,
        "gpu_memory_utilization": args.gpu_memory_utilization,
        "dtype": args.dtype,
        "quantization": args.quantization,
        "tool_call_parser": args.tool_call_parser,
        "reasoning_parser": args.reasoning_parser,
        "chat_template_sha256": args.chat_template_sha256,
        "container_port": args.container_port,
    }
    _launch, launch_digest = _validated_launch_config(
        launch_config, source_revision=args.revision
    )
    model_id = expected_served_model_id(
        args.repository_id, args.revision, launch_digest
    )
    payload = {
        "source": {
            "kind": "huggingface",
            "repository_id": args.repository_id,
            "revision": args.revision,
        },
        "provider": {
            "protocol": PROVIDER_PROTOCOL,
            "served_model_id": model_id,
            "endpoint": args.endpoint,
            "credential_env": None,
            "billing_mode": "local-no-charge",
        },
        "runtime": {
            "kind": "docker",
            "engine": args.engine,
            "engine_version": args.engine_version,
            "hardware_class": HARDWARE_CLASS,
            "topology": {
                "mode": "single-node",
                "node_count": 1,
                "tensor_parallel_size": 1,
                "pipeline_parallel_size": 1,
                "nodes": [
                    {
                        "ordinal": 1,
                        "role": "api",
                        "host_label": args.host_label,
                        "container_name": args.container_name,
                        "container_image": args.container_image,
                    }
                ],
            },
            "launch_config": launch_config,
            "launch_config_sha256": launch_digest,
        },
    }
    trial = make_profile_envelope(payload)
    verify_profile_envelope(
        _canonical_json(trial, newline=True), expected_sha256=trial["profile_sha256"]
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inventory-cache":
            result = inventory_cache(args.cache_root)
        elif args.command == "probe-endpoint":
            result = probe_models_endpoint(
                args.endpoint,
                expected_model_id=args.expected_model_id,
                timeout_seconds=args.timeout_seconds,
            )
            if not result["expected_model_present"]:
                _fail("expected revision-bearing model identity is not served")
        elif args.command == "profile-template":
            payload = _template_payload(args)
            _write_canonical_exclusive(args.output, payload)
            result = {
                "schema": "marb_local_model_profile_status.v1",
                "status": "profile_template_written",
                "output": args.output.name,
                "served_model_id": payload["provider"]["served_model_id"],
                "model_call_performed": False,
            }
        elif args.command == "seal-profile":
            payload = _decode_canonical(
                _stable_read(args.payload, "local model profile payload"),
                "local model profile payload",
            )
            envelope = make_profile_envelope(payload)
            verify_profile_envelope(
                _canonical_json(envelope, newline=True),
                expected_sha256=envelope["profile_sha256"],
            )
            _write_canonical_exclusive(args.output, envelope)
            result = {
                "schema": "marb_local_model_profile_status.v1",
                "status": "profile_sealed",
                "output": args.output.name,
                "profile_sha256": envelope["profile_sha256"],
                "model_call_performed": False,
            }
        else:
            envelope = verify_profile_envelope(
                _stable_read(args.profile, "local model profile"),
                expected_sha256=args.expected_profile_sha256,
            )
            result = {
                "schema": "marb_local_model_profile_status.v1",
                "status": "profile_valid",
                "profile_sha256": envelope["profile_sha256"],
                "served_model_id": envelope["profile"]["provider"]["served_model_id"],
                "model_call_performed": False,
            }
    except ModelProfileError as exc:
        print(f"Local model profile error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical_json(result, newline=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
