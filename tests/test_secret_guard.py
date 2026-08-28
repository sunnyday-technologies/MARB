"""Dependency-light regression tests for the value-suppressing secret guard."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_no_secrets.py"
SPEC = importlib.util.spec_from_file_location("check_no_secrets", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TestSecretGuard(unittest.TestCase):
    def test_high_confidence_token_is_detected_without_value_in_finding(self):
        synthetic = ("AK" + "IA" + "A" * 16).encode()
        findings = []
        MODULE._scan("synthetic.txt", synthetic, "test", findings)
        self.assertEqual(findings, [("synthetic.txt", "aws-access-key", "test")])
        self.assertNotIn(synthetic.decode(), repr(findings))

    def test_binary_and_placeholders_are_ignored(self):
        findings = []
        MODULE._scan("binary.bin", b"\0" + ("AK" + "IA" + "A" * 16).encode(), "test", findings)
        MODULE._scan("config.txt", b"token=${SECRET_FROM_STORE}", "test", findings)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
