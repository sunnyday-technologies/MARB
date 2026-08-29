"""Regressions for the local, exact-pin CADCLAW calibration utility."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.calibrate_h2b_cadclaw import (
    CANDIDATE_COMMIT,
    FROZEN_COMMIT,
    NIST_TEST_RUNNER,
    REVISION_PROBE,
    SYNTHETIC_GENERATOR,
    CalibrationError,
    _LocalGit,
    _canonical_json,
    _new_output_path,
    _normalize_report,
    _require_expected_commits,
    _safe_environment,
    _snapshot_aggregate,
)


class TestCadclawCalibration(unittest.TestCase):
    def test_exact_versioned_pins_are_required(self) -> None:
        _require_expected_commits(FROZEN_COMMIT, CANDIDATE_COMMIT)
        with self.assertRaisesRegex(CalibrationError, "unexpected_frozen_commit"):
            _require_expected_commits("0" * 40, CANDIDATE_COMMIT)
        with self.assertRaisesRegex(CalibrationError, "unexpected_candidate_commit"):
            _require_expected_commits(FROZEN_COMMIT, "f" * 40)
        with self.assertRaisesRegex(CalibrationError, "unexpected_candidate_commit"):
            _require_expected_commits(FROZEN_COMMIT, CANDIDATE_COMMIT[:12])

    def test_output_must_be_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "evidence.json"
            self.assertEqual(_new_output_path(target), target)
            target.write_text("historical evidence\n", encoding="utf-8")
            with self.assertRaisesRegex(CalibrationError, "output_must_be_new"):
                _new_output_path(target)

    def test_canonical_json_is_sorted_indented_lf(self) -> None:
        payload = _canonical_json({"z": 1, "a": {"b": True}})
        self.assertEqual(payload, b'{\n  "a": {\n    "b": true\n  },\n  "z": 1\n}\n')

    def test_snapshot_aggregate_uses_path_sorted_manifest_bytes(self) -> None:
        entries = [("z.step", "2" * 64), ("a.step", "1" * 64)]
        expected = hashlib.sha256(
            (f"{'1' * 64}  a.step\n{'2' * 64}  z.step\n").encode("utf-8")
        ).hexdigest()
        self.assertEqual(_snapshot_aggregate(entries), expected)

    def test_report_normalization_removes_only_timing_and_known_paths(self) -> None:
        report = {
            "duration_ms": 1.25,
            "overall": "fail",
            "meta": {
                "rules": "C:\\temp\\case.yaml",
                "gate_spec_version": "0.13.0",
                "gate_registry": {"version": "harness-gates.v1"},
            },
            "findings": [
                {
                    "id": "interference.clip",
                    "duration_ms": 0.5,
                    "evidence": {"status": "fail"},
                }
            ],
        }
        normalized = _normalize_report(report, {"C:\\temp": "<temp>"})
        self.assertNotIn("duration_ms", normalized)
        self.assertNotIn("duration_ms", normalized["findings"][0])
        self.assertEqual(normalized["meta"]["rules"], "<temp>\\case.yaml")
        self.assertEqual(normalized["meta"]["gate_spec_version"], "0.13.0")
        self.assertEqual(
            normalized["meta"]["gate_registry"]["version"], "harness-gates.v1"
        )
        self.assertEqual(normalized["findings"][0]["id"], "interference.clip")

    def test_local_git_reader_ignores_replace_refs(self) -> None:
        git_executable = shutil.which("git")
        if git_executable is None:
            self.skipTest("git executable is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*arguments: str) -> bytes:
                completed = subprocess.run(
                    [git_executable, "-C", str(root), *arguments],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
                return completed.stdout

            git("init", "-q")
            git("config", "user.name", "Calibration Test")
            git("config", "user.email", "calibration@example.invalid")
            fixture = root / "sample.txt"
            fixture.write_bytes(b"original\n")
            git("add", "sample.txt")
            git("commit", "-q", "-m", "original")
            original_commit = git("rev-parse", "HEAD").decode("ascii").strip()
            original_tree = git("rev-parse", "HEAD^{tree}").decode("ascii").strip()
            fixture.write_bytes(b"replacement\n")
            git("add", "sample.txt")
            git("commit", "-q", "-m", "replacement")
            replacement_commit = git("rev-parse", "HEAD").decode("ascii").strip()
            git("replace", original_commit, replacement_commit)

            environment = _safe_environment(root)
            self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
            reader = _LocalGit(Path(git_executable).resolve(), root, environment)
            self.assertEqual(reader.blob(original_commit, "sample.txt"), b"original\n")
            self.assertEqual(
                reader.text(
                    "rev-parse",
                    f"{original_commit}^{{tree}}",
                    reason_code="test_tree_lookup_failed",
                ),
                original_tree,
            )

    def test_subprocess_helpers_block_network_socket_operations(self) -> None:
        for helper in (SYNTHETIC_GENERATOR, REVISION_PROBE, NIST_TEST_RUNNER):
            with self.subTest(helper_sha256=hashlib.sha256(helper.encode()).hexdigest()):
                self.assertIn("socket.create_connection = blocked", helper)
                self.assertIn("socket.getaddrinfo = blocked", helper)
                for method in (
                    "connect",
                    "connect_ex",
                    "send",
                    "sendall",
                    "sendto",
                    "sendmsg",
                ):
                    self.assertIn(f'"{method}"', helper)


if __name__ == "__main__":
    unittest.main()
