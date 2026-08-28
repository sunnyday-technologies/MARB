"""Synthetic and checked-in-board tests for frontier publication policy."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_frontier_publication.py"
SPEC = importlib.util.spec_from_file_location("validate_frontier_publication", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _fixtures():
    cell_id = "new-frontier"
    seeds = ["01", "02", "03"]
    board = {
        "publication_policy": {
            "effective_date": "2026-08-28",
            "new_frontier_min_seeded_runs": 3,
            "center": "median",
            "spread": "population_std",
            "legacy_frontier_cell_ids": [],
        },
        "rows": [{
            "cell_id": cell_id,
            "registry_cell": "New model · CadQuery",
            "model": "New model",
            "tool": "CadQuery",
            "model_id": "new-model",
            "driver_tool": "CadQuery",
            "driver_version": "2.7.0",
            "track": "frontier",
            "task": "L1-ASSEMBLE",
            "spec_version": "v0.10",
            "published_date": "2026-08-28",
            "reporting": {
                "mode": "repeat-run",
                "n_attempted": 3,
                "n_graded": 3,
                "seed_ids": seeds,
                "seed_basis": "independent-run-ordinal",
                "center": "median",
                "spread": "population_std",
            },
            "gap_display": "2.0 ± 0.5 mm",
            "orient_display": "50 ± 4%",
            "pos_display": "40 ± 3 mm",
            "gap_median_mm": 2.0,
            "gap_spread": 0.5,
            "orient_pct": 50.0,
            "orient_spread": 4.0,
            "pos_rel_median_mm": 40.0,
            "pos_spread": 3.0,
        }],
    }
    registry = {
        "default_task": "L1-ASSEMBLE",
        "legacy_defaults": {"task": "L1-ASSEMBLE", "scoring_version": "v0.9"},
        "tasks": {"L1-ASSEMBLE": {"scoring_version": "v0.10"}},
        "publication_policy": {
            "effective_date": "2026-08-28",
            "frontier_min_distinct_runs": 3,
            "summary": "median",
            "spread": "population_std",
            "legacy_frontier_cell_ids": [],
        },
        "runs": [{
            "run_id": f"run-{seed}",
            "cell_id": cell_id, "cell": "New model · CadQuery", "task": "L1-ASSEMBLE",
            "scoring_version": "v0.10", "seed": seed,
            "track": "frontier", "outcome": "graded",
            "model": {"id": "new-model", "name": "New model"},
            "driver": {"tool": "CadQuery", "version": "2.7.0"},
            "kit_version": "v1.3", "prompt_variant": "frozen-core",
            "cohort_id": "frontier-v1",
            "step": f"runs/run-{seed}/export.step",
            "artifact_sha256": f"{int(seed):064x}",
            "run_log": f"runs/run-{seed}/run_log.json",
            "run_log_sha256": f"{int(seed) + 10:064x}",
        } for seed in seeds],
    }
    return board, registry


def _grade_sources(board, registry):
    row = board["rows"][0]
    row["grade_source"] = "grade.json"
    row["grade_cell"] = "New cell"
    provenance = [{
        "run_id": run["run_id"],
        "step": run["step"],
        "artifact_sha256": run["artifact_sha256"],
        "run_log_sha256": run["run_log_sha256"],
    } for run in registry["runs"]]
    return {
        "grade.json": {
            "runs": {
                "New cell": {
                    "_n": 3,
                    "_provenance": provenance,
                    "_agg": {
                        "gap_median_mm": {
                            "n": 3, "values": [1.387628, 2.0, 2.612372],
                            "median": 2.0, "std": 0.5,
                        },
                        "orient_aligned_pct": {
                            "n": 3, "values": [45.101021, 50.0, 54.898979],
                            "median": 50.0, "std": 4.0,
                        },
                        "pos_rel_mm": {
                            "n": 3, "values": [36.325765, 40.0, 43.674235],
                            "median": 40.0, "std": 3.0,
                        },
                    },
                }
            }
        }
    }


def _l2_fixtures():
    board, registry = _fixtures()
    request_hash = "f" * 64
    definition_hash = "e" * 64
    kit_hash = "d" * 64
    prompt_hash = "c" * 64
    brief_hash = "b" * 64
    task_path = "tasks/l2/task.yaml"
    kit_path = "kits/l2.zip"
    prompt_path = "prompts/l2.md"
    brief_path = "prompts/l2-driver.md"
    row = board["rows"][0]
    row["task"] = "L2-RESOLVE"
    row["spec_version"] = "v0.11"
    registry["tasks"] = {
        "L2-RESOLVE": {
            "scoring_version": "v0.11",
            "status": "measured",
            "provenance_contract": "l2_change_loop.v1",
            "task_definition": task_path,
            "task_definition_sha256": definition_hash,
            "allowed_kits": {kit_path: kit_hash},
            "prompt": prompt_path,
            "prompt_sha256": prompt_hash,
            "allowed_driver_briefs": {brief_path: brief_hash},
            "change_request": "tasks/l2/CHANGE_REQUEST.md",
            "change_request_sha256": request_hash,
            "change_request_id": "top-spreader-x+200-r1",
            "answer_key_status": "ready",
            "evidence_status": "complete",
        }
    }
    for index, run in enumerate(registry["runs"], start=1):
        run.update({
            "task": "L2-RESOLVE",
            "scoring_version": "v0.11",
            "task_definition": task_path,
            "task_definition_sha256": definition_hash,
            "kit": kit_path,
            "kit_sha256": kit_hash,
            "prompt": prompt_path,
            "prompt_sha256": prompt_hash,
            "driver_brief": brief_path,
            "driver_brief_sha256": brief_hash,
            "baseline_step": f"runs/run-{index}/before.step",
            "baseline_artifact_sha256": f"{index + 20:064x}",
            "baseline_editable_source": f"runs/run-{index}/before.FCStd",
            "baseline_editable_source_sha256": f"{index + 30:064x}",
            "changed_editable_source": f"runs/run-{index}/after.FCStd",
            "changed_editable_source_sha256": f"{index + 40:064x}",
            "change_request": "tasks/l2/CHANGE_REQUEST.md",
            "change_request_sha256": request_hash,
            "change_request_id": "top-spreader-x+200-r1",
            "driver_continuity_id": f"opaque-{index}",
        })
    return board, registry


class TestFrontierPublication(unittest.TestCase):
    def test_checked_in_board_passes(self):
        board = json.loads((REPO / "hf/space/board.json").read_text(encoding="utf-8"))
        registry = json.loads((REPO / "results/marb_runs.json").read_text(encoding="utf-8"))
        sources = MODULE.load_grade_sources(board, REPO)
        self.assertEqual(MODULE.validate(board, registry, sources), [])

    def test_checked_in_unmeasured_l2_has_no_board_row(self):
        board = json.loads((REPO / "hf/space/board.json").read_text(encoding="utf-8"))
        self.assertEqual(board["scoring_version"], "v0.11")
        self.assertFalse(any(row.get("task") == "L2-RESOLVE" for row in board["rows"]))

    def test_three_independent_runs_pass(self):
        board, registry = _fixtures()
        self.assertEqual(MODULE.validate(board, registry), [])

    def test_post_policy_grade_provenance_passes(self):
        board, registry = _fixtures()
        self.assertEqual(
            MODULE.validate(board, registry, _grade_sources(board, registry)),
            [],
        )

    def test_post_policy_grade_provenance_must_match_registry(self):
        board, registry = _fixtures()
        sources = _grade_sources(board, registry)
        sources["grade.json"]["runs"]["New cell"]["_provenance"][0]["run_id"] = "other"
        self.assertTrue(any(
            "grade-source run identities" in e
            for e in MODULE.validate(board, registry, sources)
        ))

    def test_l2_change_loop_provenance_passes(self):
        board, registry = _l2_fixtures()
        self.assertEqual(MODULE.validate(board, registry), [])

    def test_l2_missing_editable_source_is_rejected(self):
        board, registry = _l2_fixtures()
        registry["runs"][0].pop("baseline_editable_source")
        self.assertTrue(any(
            "baseline_editable_source" in error
            for error in MODULE.validate(board, registry)
        ))

    def test_l2_wrong_task_definition_is_rejected(self):
        board, registry = _l2_fixtures()
        registry["runs"][0]["task_definition_sha256"] = "0" * 64
        self.assertTrue(any(
            "task_definition_sha256" in error
            for error in MODULE.validate(board, registry)
        ))

    def test_l2_wrong_kit_is_rejected(self):
        board, registry = _l2_fixtures()
        registry["runs"][0]["kit_sha256"] = "0" * 64
        self.assertTrue(any(
            "frozen kit" in error
            for error in MODULE.validate(board, registry)
        ))

    def test_defined_unmeasured_l2_cannot_publish(self):
        board, registry = _l2_fixtures()
        registry["tasks"]["L2-RESOLVE"]["status"] = "defined_unmeasured"
        self.assertTrue(any(
            "defined-unmeasured" in error
            for error in MODULE.validate(board, registry)
        ))

    def test_two_runs_fail(self):
        board, registry = _fixtures()
        row = board["rows"][0]
        row["reporting"]["n_attempted"] = 2
        row["reporting"]["n_graded"] = 2
        row["reporting"]["seed_ids"] = ["01", "02"]
        registry["runs"] = registry["runs"][:2]
        self.assertTrue(any("at least 3" in e for e in MODULE.validate(board, registry)))

    def test_duplicate_seed_fails(self):
        board, registry = _fixtures()
        board["rows"][0]["reporting"]["seed_ids"] = ["01", "01", "03"]
        self.assertTrue(any("unique" in e for e in MODULE.validate(board, registry)))

    def test_track_label_cannot_bypass_post_policy_minimum(self):
        board, registry = _fixtures()
        row = board["rows"][0]
        row["track"] = "local"
        row["reporting"] = {
            "mode": "legacy-single-run", "n_attempted": 1, "n_graded": 1,
            "seed_ids": ["01"], "seed_basis": "independent-run-ordinal",
        }
        for field in ("gap_spread", "orient_spread", "pos_spread"):
            row.pop(field)
        row["gap_display"] = "2.0 mm"
        row["orient_display"] = "50%"
        row["pos_display"] = "40 mm"
        registry["runs"] = registry["runs"][:1]
        registry["runs"][0]["track"] = "local"
        self.assertTrue(any(
            "post-policy" in e for e in MODULE.validate(board, registry)
        ))

    def test_cloned_run_log_identity_fails(self):
        board, registry = _fixtures()
        registry["runs"][1]["run_log_sha256"] = registry["runs"][0]["run_log_sha256"]
        self.assertTrue(any(
            "run-log digests" in e for e in MODULE.validate(board, registry)
        ))

    def test_post_policy_run_ids_are_required(self):
        board, registry = _fixtures()
        registry["runs"][1].pop("run_id")
        self.assertTrue(any(
            "run_id" in e for e in MODULE.validate(board, registry)
        ))

    def test_new_single_run_cannot_claim_legacy(self):
        board, registry = _fixtures()
        row = board["rows"][0]
        row["reporting"] = {
            "mode": "legacy-single-run", "n_attempted": 1, "n_graded": 1,
            "seed_ids": ["01"], "seed_basis": "independent-run-ordinal",
        }
        registry["runs"] = registry["runs"][:1]
        self.assertTrue(any("not allowlisted" in e for e in MODULE.validate(board, registry)))

    def test_spread_is_required_in_each_display(self):
        board, registry = _fixtures()
        board["rows"][0]["orient_display"] = "50%"
        self.assertTrue(any("orient_display" in e for e in MODULE.validate(board, registry)))

    def test_displayed_center_cannot_drift_from_numeric_value(self):
        board, registry = _fixtures()
        board["rows"][0]["gap_display"] = "999 ± 0.5 mm"
        self.assertTrue(any(
            "gap_display center" in e for e in MODULE.validate(board, registry)
        ))

    def test_displayed_spread_cannot_drift_from_numeric_value(self):
        board, registry = _fixtures()
        board["rows"][0]["pos_display"] = "40 ± 999 mm"
        self.assertTrue(any(
            "pos_display spread" in e for e in MODULE.validate(board, registry)
        ))

    def test_allowlist_cannot_hide_stale_entries(self):
        board, registry = _fixtures()
        board["publication_policy"]["legacy_frontier_cell_ids"] = ["not-on-board"]
        registry["publication_policy"]["legacy_frontier_cell_ids"] = ["not-on-board"]
        self.assertTrue(any("exactly match" in e for e in MODULE.validate(board, registry)))

    def test_repeat_cell_cannot_mix_prompt_variants(self):
        board, registry = _fixtures()
        registry["runs"][2]["prompt_variant"] = "different"
        self.assertTrue(any("prompt_variant" in e for e in MODULE.validate(board, registry)))

    def test_stable_cell_cannot_spoof_model_identity(self):
        board, registry = _fixtures()
        board["rows"][0]["model_id"] = "different-model"
        self.assertTrue(any("model_id" in e for e in MODULE.validate(board, registry)))

    def test_published_model_label_cannot_spoof_identity(self):
        board, registry = _fixtures()
        board["rows"][0]["model"] = "Different model"
        self.assertTrue(any(
            "published model label" in e for e in MODULE.validate(board, registry)
        ))

    def test_repeat_grade_source_requires_aggregate_statistics(self):
        board, registry = _fixtures()
        sources = {
            "grade.json": {
                "runs": {"New cell": {"_n": 3}}
            }
        }
        row = board["rows"][0]
        row["grade_source"] = "grade.json"
        row["grade_cell"] = "New cell"
        self.assertTrue(any(
            "aggregate statistics" in e
            for e in MODULE.validate(board, registry, sources)
        ))

    def test_taskless_legacy_run_uses_legacy_task_not_current_default(self):
        board, registry = _fixtures()
        row = board["rows"][0]
        row["cell_id"] = "legacy-l1"
        row["registry_cell"] = "Legacy model · CadQuery"
        row["published_date"] = "2026-08-27"
        row["spec_version"] = "v0.9"
        row["reporting"] = {
            "mode": "legacy-single-run", "n_attempted": 1, "n_graded": 1,
            "seed_ids": ["01"], "seed_basis": "independent-run-ordinal",
        }
        for field in ("gap_spread", "orient_spread", "pos_spread"):
            row.pop(field)
        row["gap_display"] = "2.0 mm"
        row["orient_display"] = "50%"
        row["pos_display"] = "40 mm"
        registry["default_task"] = "L2-RESOLVE"
        registry["legacy_cell_map"] = {"legacy-l1": "Legacy model · CadQuery"}
        registry["runs"] = [{
            "cell": "Legacy model · CadQuery", "seed": "01",
            "model": {"id": "new-model", "name": "New model"},
            "driver": {"tool": "CadQuery", "version": "2.7.0"},
        }]
        board["publication_policy"]["legacy_frontier_cell_ids"] = ["legacy-l1"]
        registry["publication_policy"]["legacy_frontier_cell_ids"] = ["legacy-l1"]
        self.assertEqual(MODULE.validate(board, registry), [])

    def test_checked_in_board_value_cannot_drift_from_grade_source(self):
        board = json.loads((REPO / "hf/space/board.json").read_text(encoding="utf-8"))
        registry = json.loads((REPO / "results/marb_runs.json").read_text(encoding="utf-8"))
        board["rows"][0]["gap_median_mm"] = 999
        sources = MODULE.load_grade_sources(board, REPO)
        self.assertTrue(any("gap_median_mm" in e for e in MODULE.validate(board, registry, sources)))


if __name__ == "__main__":
    unittest.main()
