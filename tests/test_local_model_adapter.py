"""No-download/no-model-call tests for immutable local model profiles."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import local_model_adapter as ADAPTER


REVISION = "a" * 40
IMAGE = "nvcr.io/nvidia/vllm:26.05-py3@sha256:" + "b" * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self.raw = json.dumps(payload).encode("utf-8")

    def getheader(self, name: str):
        return str(len(self.raw)) if name == "Content-Length" else None

    def read(self, limit: int) -> bytes:
        return self.raw[:limit]


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[object, ...]] = []
        self.closed = False

    def request(self, *args, **kwargs) -> None:
        self.requests.append((args, kwargs))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class LocalModelAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-local-model-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def profile(self) -> tuple[dict[str, object], bytes, str]:
        launch_config = {
            "schema": ADAPTER.LAUNCH_CONFIG_SCHEMA,
            "model_revision": REVISION,
            "tokenizer_revision": REVISION,
            "offline": True,
            "trust_remote_code": False,
            "max_model_len": 32_768,
            "max_num_seqs": 1,
            "gpu_memory_utilization": "0.80",
            "dtype": "auto",
            "quantization": "nvfp4",
            "tool_call_parser": "qwen3_coder",
            "reasoning_parser": "nemotron_v3",
            "chat_template_sha256": None,
            "container_port": 8_000,
        }
        launch_digest = hashlib.sha256(
            json.dumps(
                launch_config,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        model_id = ADAPTER.expected_served_model_id(
            "nvidia/example-model", REVISION, launch_digest
        )
        payload = {
            "source": {
                "kind": "huggingface",
                "repository_id": "nvidia/example-model",
                "revision": REVISION,
            },
            "provider": {
                "protocol": ADAPTER.PROVIDER_PROTOCOL,
                "served_model_id": model_id,
                "endpoint": "http://127.0.0.1:8000/v1",
                "credential_env": None,
                "billing_mode": "local-no-charge",
            },
            "runtime": {
                "kind": "docker",
                "engine": "vllm",
                "engine_version": "0.27.1",
                "hardware_class": ADAPTER.HARDWARE_CLASS,
                "topology": {
                    "mode": "single-node",
                    "node_count": 1,
                    "tensor_parallel_size": 1,
                    "pipeline_parallel_size": 1,
                    "nodes": [
                        {
                            "ordinal": 1,
                            "role": "api",
                            "host_label": "gx10-1",
                            "container_name": "marb-model-fixture",
                            "container_image": IMAGE,
                        }
                    ],
                },
                "launch_config": launch_config,
                "launch_config_sha256": launch_digest,
            },
        }
        envelope = ADAPTER.make_profile_envelope(payload)
        return payload, canonical(envelope), envelope["profile_sha256"]

    def test_import_surface_has_no_download_launch_or_model_call_edges(self) -> None:
        tree = ast.parse(Path(ADAPTER.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertTrue(imported.isdisjoint({"requests", "subprocess", "huggingface_hub"}))
        call_names = {
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(
            call_names.isdisjoint(
                {"snapshot_download", "hf_hub_download", "Popen", "run", "create"}
            )
        )

    def test_profile_round_trip_binds_revision_bearing_served_identity(self) -> None:
        payload, raw, digest = self.profile()
        verified = ADAPTER.verify_profile_envelope(raw, expected_sha256=digest)
        self.assertEqual(verified["profile"], payload)
        self.assertEqual(
            verified["profile"]["provider"]["served_model_id"],
            ADAPTER.expected_served_model_id(
                "nvidia/example-model",
                REVISION,
                payload["runtime"]["launch_config_sha256"],
            ),
        )

    def test_digest_unknown_field_and_noncanonical_tampering_fail_closed(self) -> None:
        payload, raw, digest = self.profile()
        tampered = json.loads(raw)
        tampered["profile"]["source"]["revision"] = "d" * 40
        unknown = copy.deepcopy(payload)
        unknown["unexpected"] = True
        unknown_envelope = ADAPTER.make_profile_envelope(unknown)
        for candidate, expected in (
            (canonical(tampered), digest),
            (raw, "0" * 64),
            (canonical(unknown_envelope), unknown_envelope["profile_sha256"]),
            (raw.rstrip(b"\n"), digest),
        ):
            with self.subTest(expected=expected), self.assertRaises(
                ADAPTER.ModelProfileError
            ):
                ADAPTER.verify_profile_envelope(candidate, expected_sha256=expected)

    def test_profile_rejects_alias_external_endpoint_mutable_image_and_topology_drift(self) -> None:
        payload, _raw, _digest = self.profile()
        mutations = (
            (("source", "kind"), "other"),
            (("source", "repository_id"), "one-part"),
            (("source", "revision"), "A" * 40),
            (("provider", "protocol"), "other"),
            (("provider", "served_model_id"), "friendly-alias"),
            (("provider", "endpoint"), "http://127.0.0.1:8000/v1/"),
            (("provider", "credential_env"), "HF_TOKEN"),
            (("provider", "billing_mode"), "metered"),
            (("runtime", "kind"), "host"),
            (("runtime", "engine"), "sglang"),
            (("runtime", "engine_version"), "bad version"),
            (("runtime", "hardware_class"), "other"),
            (("runtime", "topology", "mode"), "dual-node-tp2"),
            (("runtime", "topology", "node_count"), 2),
            (("runtime", "topology", "tensor_parallel_size"), 2),
            (("runtime", "topology", "pipeline_parallel_size"), True),
            (("runtime", "topology", "nodes", 0, "container_image"), "nvcr.io/nvidia/vllm:latest"),
            (("runtime", "topology", "nodes", 0, "container_name"), "other"),
            (("runtime", "launch_config", "model_revision"), "d" * 40),
            (("runtime", "launch_config", "tokenizer_revision"), "short"),
            (("runtime", "launch_config", "offline"), False),
            (("runtime", "launch_config", "trust_remote_code"), True),
            (("runtime", "launch_config", "max_model_len"), True),
            (("runtime", "launch_config", "max_num_seqs"), 0),
            (("runtime", "launch_config", "gpu_memory_utilization"), "0.99"),
            (("runtime", "launch_config", "dtype"), "bad dtype"),
            (("runtime", "launch_config", "quantization"), "BAD"),
            (("runtime", "launch_config", "tool_call_parser"), "bad parser"),
            (("runtime", "launch_config", "chat_template_sha256"), "short"),
            (("runtime", "launch_config", "container_port"), 0),
            (("runtime", "launch_config_sha256"), "c" * 64),
        )
        for path, value in mutations:
            changed = copy.deepcopy(payload)
            cursor = changed
            for component in path[:-1]:
                cursor = cursor[component]
            cursor[path[-1]] = value
            envelope = ADAPTER.make_profile_envelope(changed)
            with self.subTest(path=path), self.assertRaises(
                ADAPTER.ModelProfileError
            ):
                ADAPTER.verify_profile_envelope(
                    canonical(envelope), expected_sha256=envelope["profile_sha256"]
                )

    def test_cache_inventory_reads_only_public_repo_revision_and_ref_identities(self) -> None:
        cache = self.root / "hub"
        model = cache / "models--nvidia--example-model"
        revision = "d" * 40
        (model / "snapshots" / revision).mkdir(parents=True)
        (model / "refs").mkdir()
        (model / "refs" / "main").write_text(revision + "\n", encoding="ascii")
        blob = model / "snapshots" / revision / "weights.safetensors"
        blob.write_bytes(b"not read by inventory")
        before = {
            path.relative_to(cache).as_posix(): path.read_bytes()
            for path in cache.rglob("*")
            if path.is_file()
        }

        inventory = ADAPTER.inventory_cache(cache)

        after = {
            path.relative_to(cache).as_posix(): path.read_bytes()
            for path in cache.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(inventory["network_accessed"], False)
        self.assertEqual(
            inventory["models"],
            [
                {
                    "repository_id": "nvidia/example-model",
                    "revisions": [revision],
                    "refs": {"main": revision},
                }
            ],
        )

    def test_endpoint_probe_uses_only_get_models_and_requires_exact_id(self) -> None:
        payload, _raw, _digest = self.profile()
        model_id = payload["provider"]["served_model_id"]
        connection = FakeConnection(FakeResponse({"data": [{"id": model_id}]}))
        result = ADAPTER.probe_models_endpoint(
            "http://127.0.0.1:8000/v1",
            expected_model_id=model_id,
            connection_factory=lambda _parsed, _timeout: connection,
        )
        self.assertTrue(result["expected_model_present"])
        self.assertFalse(result["model_call_performed"])
        self.assertEqual(connection.requests[0][0][0:2], ("GET", "/v1/models"))
        self.assertTrue(connection.closed)

    def test_endpoint_probe_rejects_external_hosts_and_malformed_inventory(self) -> None:
        payload, _raw, _digest = self.profile()
        model_id = payload["provider"]["served_model_id"]
        for endpoint in (
            "https://api.example.test/v1",
            "http://localhost:8000/v1",
            "http://127.0.0.2:8000/v1",
            "http://127.0.0.1:0/v1",
            "http://127.0.0.1:8000/v1/",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(
                ADAPTER.ModelProfileError
            ):
                ADAPTER.probe_models_endpoint(endpoint, expected_model_id=model_id)
        connection = FakeConnection(FakeResponse({"unexpected": []}))
        with self.assertRaises(ADAPTER.ModelProfileError):
            ADAPTER.probe_models_endpoint(
                "http://127.0.0.1:8000/v1",
                expected_model_id=model_id,
                connection_factory=lambda _parsed, _timeout: connection,
            )

    def test_runtime_attestation_binds_container_mount_command_and_models_endpoint(self) -> None:
        payload, raw, digest = self.profile()
        model_id = payload["provider"]["served_model_id"]
        connection = FakeConnection(FakeResponse({"data": [{"id": model_id}]}))

        def observer(expected):
            return {
                "container_id": "d" * 64,
                "profile_sha256": expected["profile_sha256"],
                "container_name": expected["container_name"],
                "running": True,
                "image_repo_digest": expected["container_image"],
                "image_id": "sha256:" + "e" * 64,
                "engine": expected["engine"],
                "engine_version": expected["engine_version"],
                "hardware_class": expected["hardware_class"],
                "repository_id": expected["repository_id"],
                "revision": expected["revision"],
                "launch_config_sha256": expected["launch_config_sha256"],
                "server_command_sha256": expected["server_command_sha256"],
                "model_mount_read_only": True,
                "host_ip": expected["host_ip"],
                "host_port": expected["host_port"],
                "container_port": expected["container_port"],
                "environment_keys": ["HF_HUB_OFFLINE", "PATH"],
            }

        attestation = ADAPTER.attest_runtime(
            raw,
            expected_profile_sha256=digest,
            observer=observer,
            connection_factory=lambda _parsed, _timeout: connection,
        )

        self.assertEqual(attestation["status"], "passed")
        self.assertEqual(attestation["scope"], ADAPTER.RUNTIME_ATTESTATION_SCOPE)
        self.assertEqual(attestation["profile_sha256"], digest)
        self.assertFalse(attestation["model_call_performed"])
        self.assertEqual(attestation["model_mount"]["revision"], REVISION)

    def test_runtime_attestation_rejects_drift_and_credential_environment_names(self) -> None:
        _payload, raw, digest = self.profile()

        def observed(expected, *, secret=False):
            return {
                "container_id": "d" * 64,
                "profile_sha256": expected["profile_sha256"],
                "container_name": expected["container_name"],
                "running": True,
                "image_repo_digest": expected["container_image"],
                "image_id": "sha256:" + "e" * 64,
                "engine": expected["engine"],
                "engine_version": expected["engine_version"],
                "hardware_class": expected["hardware_class"],
                "repository_id": expected["repository_id"],
                "revision": expected["revision"],
                "launch_config_sha256": expected["launch_config_sha256"],
                "server_command_sha256": "0" * 64,
                "model_mount_read_only": True,
                "host_ip": expected["host_ip"],
                "host_port": expected["host_port"],
                "container_port": expected["container_port"],
                "environment_keys": ["HF_TOKEN" if secret else "PATH"],
            }

        for observer in (
            lambda expected: observed(expected),
            lambda expected: {
                **observed(expected),
                "server_command_sha256": expected["server_command_sha256"],
                "environment_keys": ["HF_TOKEN"],
            },
        ):
            with self.subTest(observer=observer), self.assertRaises(
                ADAPTER.ModelProfileError
            ):
                ADAPTER.attest_runtime(
                    raw,
                    expected_profile_sha256=digest,
                    observer=observer,
                    connection_factory=lambda *_args: FakeConnection(
                        FakeResponse({"data": []})
                    ),
                )

    def test_profile_template_and_seal_cli_never_call_endpoint(self) -> None:
        payload_path = self.root / "profile-payload.json"
        sealed_path = self.root / "profile.json"
        with mock.patch.object(
            ADAPTER,
            "probe_models_endpoint",
            side_effect=AssertionError("profile preparation must not probe or call a model"),
        ) as probe:
            code = ADAPTER.main(
                [
                    "profile-template",
                    "--repository-id",
                    "nvidia/example-model",
                    "--revision",
                    REVISION,
                    "--endpoint",
                    "http://127.0.0.1:8000/v1",
                    "--engine",
                    "vllm",
                    "--engine-version",
                    "0.27.1",
                    "--container-image",
                    IMAGE,
                    "--host-label",
                    "gx10-1",
                    "--container-name",
                    "marb-model-fixture",
                    "--tokenizer-revision",
                    REVISION,
                    "--max-model-len",
                    "32768",
                    "--dtype",
                    "auto",
                    "--quantization",
                    "nvfp4",
                    "--tool-call-parser",
                    "qwen3_coder",
                    "--container-port",
                    "8000",
                    "--output",
                    str(payload_path),
                ]
            )
            self.assertEqual(code, 0)
            code = ADAPTER.main(
                [
                    "seal-profile",
                    "--payload",
                    str(payload_path),
                    "--output",
                    str(sealed_path),
                ]
            )
            self.assertEqual(code, 0)
            probe.assert_not_called()
        sealed = json.loads(sealed_path.read_bytes())
        self.assertEqual(
            sealed["profile"]["provider"]["served_model_id"],
            ADAPTER.expected_served_model_id(
                "nvidia/example-model",
                REVISION,
                sealed["profile"]["runtime"]["launch_config_sha256"],
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
