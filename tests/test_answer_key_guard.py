"""Regressions for answer-key path, raw-digest, and LFS-oid guards."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "check_no_answer_keys.sh"
GUARD_IMPL = REPO / "scripts" / "check_no_answer_keys.py"


def _find_sh() -> str:
    discovered = shutil.which("sh")
    if discovered:
        return discovered
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git" / "usr" / "bin" / "sh.exe",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files"))
        / "Git" / "usr" / "bin" / "sh.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise unittest.SkipTest("a POSIX sh implementation is required")


SH = _find_sh()


class TestAnswerKeyGuard(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "results").mkdir()
        shutil.copyfile(GUARD, self.root / "scripts" / GUARD.name)
        shutil.copyfile(GUARD_IMPL, self.root / "scripts" / GUARD_IMPL.name)

        # Synthetic bytes stand in for a private artifact. The guard must never
        # print either these bytes or their published test digest.
        self.synthetic_key = b"synthetic answer-key fixture; not real geometry\n"
        self.key_digest = hashlib.sha256(self.synthetic_key).hexdigest()
        self.spec_digest = hashlib.sha256(b"synthetic private spec fixture\n").hexdigest()
        registry = {
            "tasks": {
                "L4-ECO": {
                    "answer_key_step_sha256": self.key_digest,
                    "answer_key_spec_sha256": self.spec_digest,
                }
            }
        }
        (self.root / "results" / "marb_runs.json").write_text(
            json.dumps(registry, indent=2) + "\n", encoding="utf-8"
        )

        self._git("init", "-q")
        self._git("config", "user.name", "Guard Test")
        self._git("config", "user.email", "guard@example.invalid")
        self._git("add", "results/marb_runs.json")
        self._git("commit", "-q", "-m", "base registry")
        self.base = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", *args),
            cwd=self.root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _guard(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        # Directly launching Git for Windows' sh does not initialize its usual
        # usr/bin PATH; hooks and CI do. Reproduce that tool availability here.
        environment["PATH"] = (
            str(Path(SH).resolve().parent)
            + os.pathsep
            + environment.get("PATH", "")
        )
        environment["MARB_PYTHON"] = sys.executable
        return subprocess.run(
            (SH, "scripts/check_no_answer_keys.sh", *args),
            cwd=self.root,
            check=False,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _commit(self, message: str) -> str:
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _assert_blocked(
        self,
        result: subprocess.CompletedProcess[str],
        path: str,
        match_type: str,
    ) -> None:
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn(path, rendered)
        self.assertIn(match_type, rendered)
        self.assertNotIn(self.key_digest, rendered)
        self.assertNotIn(self.synthetic_key.decode("utf-8").strip(), rendered)

    def _write_lfs_pointer(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid sha256:{self.key_digest}\n"
            f"size {len(self.synthetic_key)}\n",
            encoding="utf-8",
        )

    def test_tree_blocks_renamed_raw_key_blob(self) -> None:
        relative = "artifacts/cache.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(self.synthetic_key)
        self._git("add", relative)

        self._assert_blocked(self._guard("--tree"), relative, "raw-digest-match")

    def test_tree_blocks_renamed_lfs_pointer(self) -> None:
        relative = "artifacts/cache.pointer"
        self._write_lfs_pointer(self.root / relative)
        self._git("add", relative)

        self._assert_blocked(self._guard("--tree"), relative, "lfs-oid-match")

    def test_tree_scans_staged_blob_when_worktree_bytes_are_replaced(self) -> None:
        relative = "artifacts/staged.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(self.synthetic_key)
        self._git("add", relative)
        target.write_bytes(b"benign unstaged replacement\n")

        self._assert_blocked(self._guard("--tree"), relative, "raw-digest-match")

    def test_tree_retains_head_digests_when_staged_registry_removes_them(self) -> None:
        relative = "artifacts/hidden-by-registry-edit.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(self.synthetic_key)
        (self.root / "results" / "marb_runs.json").write_text(
            json.dumps({"tasks": {}}, indent=2) + "\n", encoding="utf-8"
        )
        self._git("add", relative, "results/marb_runs.json")

        result = self._guard("--tree")
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn("not removed or replaced", rendered)
        self.assertNotIn(self.key_digest, rendered)

    def test_tree_rejects_identity_removal_without_a_key_blob(self) -> None:
        (self.root / "results" / "marb_runs.json").write_text(
            json.dumps({"tasks": {}}, indent=2) + "\n", encoding="utf-8"
        )
        self._git("add", "results/marb_runs.json")
        result = self._guard("--tree")
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn("not removed or replaced", rendered)
        self.assertNotIn(self.key_digest, rendered)

    @unittest.skipIf(os.name == "nt", "Windows forbids newline characters in filenames")
    def test_tree_enumeration_is_nul_safe_for_newline_names(self) -> None:
        relative = "artifacts/odd\nname.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(self.synthetic_key)
        self._git("add", relative)
        result = self._guard("--tree")
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn("odd\\nname.bin", rendered)
        self.assertIn("raw-digest-match", rendered)

    def test_range_blocks_transient_renamed_raw_key_blob(self) -> None:
        relative = "artifacts/transient.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(self.synthetic_key)
        self._git("add", relative)
        self._commit("add renamed blob")
        target.unlink()
        self._git("add", "-u")
        head = self._commit("remove renamed blob")

        self._assert_blocked(
            self._guard("--range", f"{self.base}..{head}"),
            relative,
            "raw-digest-match",
        )

    def test_range_blocks_transient_renamed_lfs_pointer(self) -> None:
        relative = "artifacts/transient.pointer"
        target = self.root / relative
        self._write_lfs_pointer(target)
        self._git("add", relative)
        self._commit("add renamed pointer")
        target.unlink()
        self._git("add", "-u")
        head = self._commit("remove renamed pointer")

        self._assert_blocked(
            self._guard("--range", f"{self.base}..{head}"),
            relative,
            "lfs-oid-match",
        )

    def test_range_rejects_published_identity_replacement(self) -> None:
        registry = {
            "tasks": {
                "L4-ECO": {
                    "answer_key_step_sha256": "0" * 64,
                    "answer_key_spec_sha256": "1" * 64,
                }
            }
        }
        (self.root / "results" / "marb_runs.json").write_text(
            json.dumps(registry, indent=2) + "\n", encoding="utf-8"
        )
        self._git("add", "results/marb_runs.json")
        head = self._commit("replace published identity")
        result = self._guard("--range", f"{self.base}..{head}")
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn("not removed or replaced", rendered)
        self.assertNotIn(self.key_digest, rendered)

    def test_existing_reference_path_rule_remains_blocking(self) -> None:
        relative = "tasks/example_reference.step"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"benign path-policy fixture\n")
        self._git("add", relative)

        result = self._guard("--tree")
        rendered = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, rendered)
        self.assertIn(relative, rendered)
        self.assertIn("answer-key path", rendered)

    def test_unrelated_blobs_pass_tree_and_range_scans(self) -> None:
        relative = "artifacts/unrelated.bin"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"unrelated public fixture\n")
        self._git("add", relative)
        head = self._commit("add unrelated blob")

        tree = self._guard("--tree")
        self.assertEqual(tree.returncode, 0, tree.stdout + tree.stderr)
        ranged = self._guard("--range", f"{self.base}..{head}")
        self.assertEqual(ranged.returncode, 0, ranged.stdout + ranged.stderr)


if __name__ == "__main__":
    unittest.main()
