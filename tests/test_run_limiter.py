"""Fake-only policy tests for the in-container workspace limiter."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


LIMITER_PATH = (
    Path(__file__).resolve().parents[1] / "harness" / "container" / "run_limited.py"
)
SPEC = importlib.util.spec_from_file_location("marb_test_run_limiter", LIMITER_PATH)
assert SPEC is not None and SPEC.loader is not None
LIMITER = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(
    sys.modules,
    {"resource": types.SimpleNamespace(RLIMIT_FSIZE=1, setrlimit=lambda *_args: None)},
):
    SPEC.loader.exec_module(LIMITER)


class FakeProcess:
    pid = 77
    returncode = 0

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class RunLimiterTests(unittest.TestCase):
    def test_ustar_member_boundary_is_checked_before_export(self) -> None:
        LIMITER._assert_ustar_member_name("directory/short.txt")
        with self.assertRaisesRegex(LIMITER.LimitViolation, "USTAR"):
            LIMITER._assert_ustar_member_name("a" * 101)

    def test_quiescence_requires_repeated_empty_readbacks(self) -> None:
        process = FakeProcess()
        snapshots = [[88], [89], [], [], []]
        with (
            mock.patch.object(LIMITER.signal, "SIGKILL", 9, create=True),
            mock.patch.object(LIMITER.os, "killpg", create=True),
            mock.patch.object(LIMITER.os, "kill") as kill,
            mock.patch.object(LIMITER.time, "sleep"),
            mock.patch.object(LIMITER, "_other_container_pids", side_effect=snapshots) as readback,
        ):
            LIMITER._kill_processes(process)
        self.assertEqual(readback.call_count, 5)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [88, 89])

    def test_quiescence_fails_closed_for_respawning_descendant(self) -> None:
        process = FakeProcess()
        with (
            mock.patch.object(LIMITER.signal, "SIGKILL", 9, create=True),
            mock.patch.object(LIMITER.os, "killpg", create=True),
            mock.patch.object(LIMITER.os, "kill"),
            mock.patch.object(LIMITER.time, "sleep"),
            mock.patch.object(LIMITER, "_other_container_pids", return_value=[88]),
        ):
            with self.assertRaisesRegex(LIMITER.LimitViolation, "quiescence"):
                LIMITER._kill_processes(process)

    def test_export_contains_status_outside_workspace_entry_allowance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="marb-limiter-test-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "solution.py").write_text("print('ok')\n", encoding="ascii")
            export = root / "workspace.tar"
            with (
                mock.patch.object(LIMITER, "WORKSPACE", workspace),
                mock.patch.object(LIMITER, "EXPORT", export),
                mock.patch.object(LIMITER, "MAX_ENTRIES", 1),
            ):
                LIMITER._export_workspace(23)
            import tarfile

            with tarfile.open(export, "r:") as archive:
                self.assertEqual(
                    [member.name for member in archive],
                    [LIMITER.STATUS_MEMBER, "solution.py"],
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
