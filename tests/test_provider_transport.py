"""No-network tests for the H2b OpenAI-compatible transport boundary."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import unittest
from unittest import mock

from harness import cohort_executor as EXECUTOR


def response_bytes(**changes: object) -> bytes:
    value: dict[str, object] = {
        "id": "response-1",
        "model": "synthetic-model-v1",
        "system_fingerprint": "fingerprint-1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        "cost_usd": "0",
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def settings() -> dict[str, object]:
    return {
        "temperature": 0,
        "max_output_tokens": 128,
        "timeout_seconds": 2.5,
        "seed": 17,
    }


class ProviderTransportTests(unittest.TestCase):
    def test_session_binds_seed_headers_origin_and_transport_hashes(self) -> None:
        observed: dict[str, object] = {}

        def runner(endpoint, origin, headers, request_raw, timeout):
            observed.update(
                endpoint=endpoint,
                origin=origin,
                header_names=sorted(headers),
                bearer_present=headers.get("Authorization", "").startswith("Bearer "),
                request=json.loads(request_raw),
                timeout=timeout,
            )
            raw = response_bytes()
            return "https://api.example.test/v1/chat/completions", "request-1", raw

        session = EXECUTOR.OpenAICompatibleSession(
            "https://API.EXAMPLE.TEST:443/v1",
            "synthetic-model-v1",
            "x" * 24,
            transport_runner=runner,
        )
        result = session.complete(
            [{"role": "user", "content": "hello"}], [], settings()
        )
        self.assertEqual(observed["endpoint"], "https://API.EXAMPLE.TEST:443/v1/chat/completions")
        self.assertEqual(observed["origin"], "https://api.example.test")
        self.assertEqual(observed["request"]["seed"], 17)
        self.assertTrue(observed["bearer_present"])
        self.assertEqual(observed["header_names"], ["Authorization", "Content-Type"])
        self.assertEqual(result.response_model, "synthetic-model-v1")
        self.assertEqual(result.request_id, "request-1")
        self.assertEqual(result.cost_usd, "0")
        self.assertRegex(result.transport_request_sha256 or "", r"^[0-9a-f]{64}$")
        self.assertEqual(
            result.transport_response_sha256,
            hashlib.sha256(response_bytes()).hexdigest(),
        )

    def test_origin_change_and_oversized_response_fail_closed(self) -> None:
        changed = EXECUTOR.OpenAICompatibleSession(
            "https://api.example.test/v1",
            "synthetic-model-v1",
            None,
            transport_runner=lambda *_args: (
                "https://other.example.test/v1/chat/completions",
                None,
                response_bytes(),
            ),
        )
        with self.assertRaisesRegex(EXECUTOR.ProviderCallError, "origin"):
            changed.complete([], [], settings())

        oversized = EXECUTOR.OpenAICompatibleSession(
            "https://api.example.test/v1",
            "synthetic-model-v1",
            None,
            transport_runner=lambda *_args: (
                "https://api.example.test/v1/chat/completions",
                None,
                b"x" * 16_000_001,
            ),
        )
        with self.assertRaisesRegex(EXECUTOR.ProviderCallError, "size"):
            oversized.complete([], [], settings())

    def test_malformed_present_metadata_and_tool_objects_are_rejected(self) -> None:
        cases = (
            {"usage": "five"},
            {"cost_usd": 1},
            {"model": ""},
            {"id": 7},
            {"system_fingerprint": {}},
            {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"id": "call-1", "type": "function", "function": None}],
                        },
                        "finish_reason": "tool_calls",
                        "logprobs": None,
                    }
                ]
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                session = EXECUTOR.OpenAICompatibleSession(
                    "https://api.example.test/v1",
                    "synthetic-model-v1",
                    None,
                    transport_runner=lambda *_args, value=changes: (
                        "https://api.example.test/v1/chat/completions",
                        None,
                        response_bytes(**value),
                    ),
                )
                with self.assertRaises(EXECUTOR.ProviderCallError):
                    session.complete([], [], settings())

    def test_default_port_policy_is_canonical_and_plaintext_is_loopback_only(self) -> None:
        self.assertEqual(
            EXECUTOR.sanitize_endpoint("https://Example.TEST:443/v1/"),
            ("https://example.test/v1", "https://example.test"),
        )
        with self.assertRaisesRegex(EXECUTOR.ExecutorError, "loopback"):
            EXECUTOR.sanitize_endpoint("http://example.test/v1")

    def test_transport_child_is_killed_and_waited_on_base_exception(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"")
                self.returncode = None
                self.killed = False
                self.waits = 0

            def wait(self, timeout=None):
                del timeout
                self.waits += 1
                if self.waits == 1:
                    raise KeyboardInterrupt
                self.returncode = -9
                return -9

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = FakeProcess()
        with mock.patch.object(EXECUTOR.subprocess, "Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                EXECUTOR._subprocess_provider_transport(
                    "https://api.example.test/v1/chat/completions",
                    "https://api.example.test",
                    {"Content-Type": "application/json"},
                    b"{}",
                    1.0,
                )
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.waits, 2)

    def test_transport_helper_stdout_is_bounded_before_json_parse(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"x" * 64)
                self.returncode = 0

            def wait(self, timeout=None):
                del timeout
                return 0

            def poll(self):
                return 0

        with (
            mock.patch.object(EXECUTOR, "MAX_PROVIDER_HELPER_STDOUT_BYTES", 32),
            mock.patch.object(EXECUTOR.subprocess, "Popen", return_value=FakeProcess()),
        ):
            with self.assertRaisesRegex(EXECUTOR.ProviderCallError, "rejected"):
                EXECUTOR._subprocess_provider_transport(
                    "https://api.example.test/v1/chat/completions",
                    "https://api.example.test",
                    {"Content-Type": "application/json"},
                    b"{}",
                    1.0,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
