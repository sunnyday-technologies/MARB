"""Task-aware registry compatibility tests (no answer-key geometry required)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from grader.run_registry import load_manifest


class TestTaskRegistry(unittest.TestCase):
    def _write(self, body):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(body, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        return Path(tmp.name)

    def test_v1_defaults_to_l1(self):
        path = self._write({
            "reference_step": "ref.step", "spec": "ref.yaml",
            "marb_scoring_version": "v0.9",
            "runs": [{"cell": "A", "seed": "01", "step": "a.step"}],
        })
        cells, _, ref, spec, task, version = load_manifest(path)
        self.assertEqual(list(cells), ["A"])
        self.assertEqual((ref, spec, task, version),
                         ("ref.step", "ref.yaml", "L1-ASSEMBLE", "v0.9"))

    def test_tasks_with_same_display_name_do_not_blend(self):
        path = self._write({
            "default_task": "L1-ASSEMBLE",
            "tasks": {
                "L1-ASSEMBLE": {"reference_step": "l1.step", "spec": "l1.yaml", "scoring_version": "v0.9"},
                "L2-RESOLVE": {"reference_step": "l2.step", "spec": "l2.yaml", "scoring_version": "v0.11"},
            },
            "runs": [
                {"cell": "Same display", "cell_id": "l1-a", "task": "L1-ASSEMBLE", "seed": "01", "step": "before.step"},
                {"cell": "Same display", "cell_id": "l2-a", "task": "L2-RESOLVE", "seed": "01", "step": "after.step"},
            ],
        })
        cells, _, ref, spec, task, version = load_manifest(path, "L2-RESOLVE")
        self.assertEqual([str(item[0]) for item in cells["Same display"]], ["after.step"])
        self.assertEqual((ref, spec, task, version),
                         ("l2.step", "l2.yaml", "L2-RESOLVE", "v0.11"))

    def test_legacy_and_current_versions_require_selection(self):
        path = self._write({
            "default_task": "L1-ASSEMBLE",
            "legacy_defaults": {"scoring_version": "v0.9"},
            "tasks": {"L1-ASSEMBLE": {"reference_step": "r.step", "spec": "r.yaml", "scoring_version": "v0.10"}},
            "runs": [
                {"cell": "Legacy", "seed": "01", "step": "old.step"},
                {"cell": "Current", "seed": "01", "step": "new.step", "scoring_version": "v0.10"},
            ],
        })
        with self.assertRaisesRegex(ValueError, "multiple scoring versions"):
            load_manifest(path)
        cells, *_, version = load_manifest(path, scoring_version="v0.10")
        self.assertEqual(list(cells), ["Current"])
        self.assertEqual(version, "v0.10")

    def test_provenance_retains_change_loop_fields(self):
        path = self._write({
            "default_task": "L2-RESOLVE",
            "tasks": {"L2-RESOLVE": {"reference_step": "r.step", "spec": "r.yaml"}},
            "runs": [{
                "run_id": "run-1", "cell_id": "cell-1", "cell": "A",
                "task": "L2-RESOLVE", "seed": "01", "baseline_step": "before.step",
                "step": "after.step",
            }],
        })
        cells, *_ = load_manifest(path)
        provenance = cells["A"][0][1]
        self.assertEqual(provenance["baseline_step"], "before.step")
        self.assertEqual(provenance["run_id"], "run-1")

    def test_duplicate_display_labels_within_task_fail(self):
        path = self._write({
            "runs": [
                {"cell": "A", "cell_id": "one", "seed": "01", "step": "1.step"},
                {"cell": "A", "cell_id": "two", "seed": "02", "step": "2.step"},
            ],
        })
        with self.assertRaisesRegex(ValueError, "reuses display label"):
            load_manifest(path)

    def test_legacy_task_uses_legacy_default_not_current_default(self):
        path = self._write({
            "default_task": "L2-RESOLVE",
            "legacy_defaults": {"task": "L1-ASSEMBLE", "scoring_version": "v0.9"},
            "tasks": {
                "L1-ASSEMBLE": {"reference_step": "l1.step", "spec": "l1.yaml", "scoring_version": "v0.9"},
                "L2-RESOLVE": {"reference_step": "l2.step", "spec": "l2.yaml", "scoring_version": "v0.11"},
            },
            "runs": [
                {"cell": "Legacy", "seed": "01", "step": "old.step"},
                {"cell": "Resolve", "cell_id": "l2-a", "task": "L2-RESOLVE", "seed": "01", "step": "new.step"},
            ],
        })
        l1_cells, *_, l1_task, l1_version = load_manifest(path, "L1-ASSEMBLE")
        self.assertEqual(list(l1_cells), ["Legacy"])
        self.assertEqual((l1_task, l1_version), ("L1-ASSEMBLE", "v0.9"))
        l2_cells, *_, l2_task, l2_version = load_manifest(path, "L2-RESOLVE")
        self.assertEqual(list(l2_cells), ["Resolve"])
        self.assertEqual((l2_task, l2_version), ("L2-RESOLVE", "v0.11"))

    def test_requested_task_with_no_runs_fails(self):
        path = self._write({
            "default_task": "L1-ASSEMBLE",
            "tasks": {
                "L1-ASSEMBLE": {"reference_step": "l1.step", "spec": "l1.yaml"},
                "L2-RESOLVE": {"reference_step": "l2.step", "spec": "l2.yaml"},
            },
            "runs": [{"cell": "A", "seed": "01", "step": "a.step"}],
        })
        with self.assertRaisesRegex(ValueError, "no runs match task 'L2-RESOLVE'"):
            load_manifest(path, "L2-RESOLVE")

    def test_requested_scoring_version_with_no_runs_fails(self):
        path = self._write({
            "marb_scoring_version": "v0.9",
            "runs": [{"cell": "A", "seed": "01", "step": "a.step"}],
        })
        with self.assertRaisesRegex(ValueError, "scoring version 'v0.10'"):
            load_manifest(path, scoring_version="v0.10")

    def test_nonlegacy_task_cannot_fall_back_to_legacy_answer_key(self):
        path = self._write({
            "default_task": "L2-RESOLVE",
            "legacy_defaults": {"task": "L1-ASSEMBLE"},
            "reference_step": "l1.step",
            "spec": "l1.yaml",
            "tasks": {
                "L1-ASSEMBLE": {"reference_step": "l1.step", "spec": "l1.yaml"},
                "L2-RESOLVE": {"scoring_version": "v0.11"},
            },
            "runs": [{
                "run_id": "l2-01", "cell_id": "l2-cell", "cell": "Resolve",
                "task": "L2-RESOLVE", "seed": "01", "step": "after.step",
            }],
        })
        with self.assertRaisesRegex(ValueError, "task-local routing"):
            load_manifest(path, "L2-RESOLVE")

    def test_task_aware_run_requires_stable_cell_id(self):
        path = self._write({
            "default_task": "L1-ASSEMBLE",
            "tasks": {
                "L1-ASSEMBLE": {"reference_step": "l1.step", "spec": "l1.yaml"},
            },
            "runs": [{
                "run_id": "l1-01", "cell": "Current", "task": "L1-ASSEMBLE",
                "seed": "01", "step": "current.step",
            }],
        })
        with self.assertRaisesRegex(ValueError, "missing cell_id"):
            load_manifest(path)


if __name__ == "__main__":
    unittest.main()
