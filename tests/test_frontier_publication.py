"""Synthetic and checked-in-board tests for frontier publication policy."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def _l4_fixtures():
    board, registry = _fixtures()
    digest = "a" * 64
    commit = "b" * 40
    gated_revision = "c" * 40
    implementation_hash = "d" * 64
    requirements_hash = "e" * 64
    key_step_hash = "f" * 64
    key_spec_hash = "9" * 64
    repo_root = Path(tempfile.mkdtemp(prefix="marb-l4-evidence-"))
    task_path = "tasks/l4/task.yaml"
    kit_path = "kits/l4.zip"
    prompt_path = "prompts/l4.md"
    brief_path = "prompts/l4-driver.md"
    eco_path = "tasks/l4/ECO_REQUEST.md"
    row = board["rows"][0]
    row["task"] = "L4-ECO"
    row["spec_version"] = "v0.12"
    registry["tasks"] = {
        "L4-ECO": {
            "scoring_version": "v0.12",
            "status": "measured",
            "provenance_contract": "l4_eco.v1",
            "task_revision": "second-top-cross-spreader-midright-r1",
            "task_definition": task_path,
            "task_definition_sha256": digest,
            "allowed_kits": {kit_path: digest},
            "prompt": prompt_path,
            "prompt_sha256": digest,
            "allowed_driver_briefs": {brief_path: digest},
            "eco_request": eco_path,
            "eco_request_sha256": digest,
            "eco_request_id": "second-top-cross-spreader-midright-r1",
            "cadclaw_commit": commit,
            "cadclaw_version": "0.10.0",
            "cadquery_version": "2.7.0",
            "cadquery_ocp_version": "7.8.1.1.post1",
            "invariant_gate_version": "marb_l4_eco_invariant.v0.12.0",
            "invariant_validation_mode": "recompute-registered-step-pair",
            "invariant_gate_implementation_sha256": implementation_hash,
            "invariant_gate_requirements_sha256": requirements_hash,
            "requested_change_grade_method": "marb_task_local_reference.v0.12",
            "gated_requested_change_grade_schema": "marb_l4_requested_change_grade.v1",
            "gated_read_credential_env": "MARB_GATED_READ_TOKEN",
            "gated_dataset_id": "SunnydayTech/marb-m3-crete-answer-key",
            "gated_distribution_revision": gated_revision,
            "answer_key_step_sha256": key_step_hash,
            "answer_key_spec_sha256": key_spec_hash,
            "answer_key_status": "ready",
            "evidence_status": "complete",
        }
    }
    for index, run in enumerate(registry["runs"], start=1):
        run.update({
            "task": "L4-ECO", "scoring_version": "v0.12",
            "task_definition": task_path, "task_definition_sha256": digest,
            "kit": kit_path, "kit_sha256": digest,
            "prompt": prompt_path, "prompt_sha256": digest,
            "driver_brief": brief_path, "driver_brief_sha256": digest,
            "baseline_step": f"runs/run-{index}/before.step",
            "baseline_artifact_sha256": f"{index + 20:064x}",
            "baseline_editable_source": f"runs/run-{index}/before.FCStd",
            "baseline_editable_source_sha256": f"{index + 30:064x}",
            "changed_editable_source": f"runs/run-{index}/after.FCStd",
            "changed_editable_source_sha256": f"{index + 40:064x}",
            "eco_request": eco_path, "eco_request_sha256": digest,
            "eco_request_id": "second-top-cross-spreader-midright-r1",
            "driver_continuity_id": f"opaque-{index}",
            "invariant_gate_status": "pass",
            "invariant_gate_version": "marb_l4_eco_invariant.v0.12.0",
            "requested_change_status": "pass",
            "requested_change_grade_method": "marb_task_local_reference.v0.12",
            "cadclaw_commit": commit,
            "cadclaw_version": "0.10.0",
            "cadquery_version": "2.7.0",
            "cadquery_ocp_version": "7.8.1.1.post1",
            "invariant_gate_implementation_sha256": implementation_hash,
            "invariant_gate_requirements_sha256": requirements_hash,
            "gated_distribution_revision": gated_revision,
            "answer_key_step_sha256": key_step_hash,
            "answer_key_spec_sha256": key_spec_hash,
            "gated_requested_grade_report_path": f"grades/run-{index}/requested.json",
            "gated_requested_grade_report_sha256": f"{index + 70:064x}",
        })
        for path_field, hash_field, payload in (
            ("baseline_step", "baseline_artifact_sha256", f"baseline-step-{index}".encode()),
            ("step", "artifact_sha256", f"changed-step-{index}".encode()),
            (
                "baseline_editable_source",
                "baseline_editable_source_sha256",
                f"baseline-source-{index}".encode(),
            ),
            (
                "changed_editable_source",
                "changed_editable_source_sha256",
                f"changed-source-{index}".encode(),
            ),
            ("run_log", "run_log_sha256", f"run-log-{index}".encode()),
        ):
            target = repo_root / run[path_field]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            run[hash_field] = hashlib.sha256(payload).hexdigest()
        invariant = {
            "schema": "marb_eco_public_invariant.v1",
            "gate_version": "marb_l4_eco_invariant.v0.12.0",
            "task": "L4-ECO",
            "task_revision": "second-top-cross-spreader-midright-r1",
            "cadclaw_audited_commit": commit,
            "cadclaw_audited_version": "0.10.0",
            "public_gate_status": "pass",
            "counts": {
                "baseline_renderable_shapes": 100,
                "changed_renderable_shapes": 101,
                "matched_invariant_shapes": 100,
                "missing_baseline_shapes": 0,
                "unmatched_changed_shapes": 1,
            },
            "checks": [
                {"id": check_id, "status": "pass"}
                for check_id in (
                    "instance-count-delta",
                    "baseline-aabb-pose-preservation",
                    "added-stock-signature",
                    "added-axis-orientation",
                )
            ],
            "tolerances": {
                "center_mm": 0.05,
                "axis_bbox_mm": 0.05,
                "bbox_volume_relative": 0.000001,
                "bbox_volume_absolute_mm3": 0.001,
                "requested_axis_bbox_mm": 0.5,
            },
            "private_requested_placement_check": "required_separately",
            "publication_requirement": "private requested-placement grade and repeat-run policy apply separately",
            "limitations": [
                "Anonymous AABB matching cannot observe swaps between geometrically identical instances.",
                "A rotation that preserves an axis-aligned bounding box may be unobservable.",
                "Coincident solids may be hidden by CADCLAW's 0.1 mm rounded-bbox deduplication.",
                "This gate does not prove topology, feature history, PMI, material, suppression state, or physical validity.",
            ],
            "input_artifacts": {
                "baseline_sha256": run["baseline_artifact_sha256"],
                "changed_sha256": run["artifact_sha256"],
            },
            "runtime_attestation": {
                "cadclaw_version": "0.10.0",
                "cadclaw_commit": commit,
                "cadclaw_provenance": "vcs-direct-url",
                "cadquery_version": "2.7.0",
                "cadquery_ocp_version": "7.8.1.1.post1",
            },
            "gate_source": {
                "implementation_sha256": implementation_hash,
                "requirements_sha256": requirements_hash,
            },
        }
        gated_requested = {
            "schema": "marb_l4_requested_change_grade.v1",
            "task": "L4-ECO",
            "task_revision": "second-top-cross-spreader-midright-r1",
            "scoring_version": "v0.12",
            "run_id": run["run_id"],
            "eco_request_id": "second-top-cross-spreader-midright-r1",
            "grade_method": "marb_task_local_reference.v0.12",
            "requested_change_status": "pass",
            "artifact_sha256": run["artifact_sha256"],
            "reference_step_sha256": key_step_hash,
            "placement_spec_sha256": key_spec_hash,
        }
        gated_payload = (
            json.dumps(gated_requested, sort_keys=True) + "\n"
        ).encode("utf-8")
        gated_target = (
            repo_root / "_test_gated_readback"
            / run["gated_requested_grade_report_path"]
        )
        gated_target.parent.mkdir(parents=True, exist_ok=True)
        gated_target.write_bytes(gated_payload)
        run["gated_requested_grade_report_sha256"] = hashlib.sha256(
            gated_payload
        ).hexdigest()

        requested = {
            "schema": "marb_l4_requested_change_attestation.v1",
            "task": "L4-ECO",
            "task_revision": "second-top-cross-spreader-midright-r1",
            "scoring_version": "v0.12",
            "grade_method": "marb_task_local_reference.v0.12",
            "requested_change_status": "pass",
            "artifact_sha256": run["artifact_sha256"],
            "attestation": {
                "source": "gated-dataset-immutable-readback",
                "dataset_id": "SunnydayTech/marb-m3-crete-answer-key",
                "gated_distribution_revision": gated_revision,
                "grade_report_path": run["gated_requested_grade_report_path"],
                "grade_report_sha256": run[
                    "gated_requested_grade_report_sha256"
                ],
                "reference_step_sha256": key_step_hash,
                "placement_spec_sha256": key_spec_hash,
            },
        }
        for kind, document in (("invariant", invariant), ("requested", requested)):
            relative = Path(f"runs/run-{index}/{kind}.json")
            target = repo_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
            target.write_bytes(payload)
            run[f"{kind if kind == 'invariant' else 'requested_change'}_report"] = relative.as_posix()
            run[f"{kind if kind == 'invariant' else 'requested_change'}_report_sha256"] = hashlib.sha256(payload).hexdigest()
    return board, registry, repo_root


def _validate_l4_fixture(board, registry, repo_root):
    """Exercise policy logic with explicit stand-ins for trusted external work."""
    invariant_paths = {
        (repo_root / run["step"]).resolve(): repo_root / run["invariant_report"]
        for run in registry["runs"]
        if run.get("outcome") == "graded"
        and run.get("step")
        and run.get("invariant_report")
    }

    def recompute(_baseline: Path, changed: Path):
        report_path = invariant_paths[changed.resolve()]
        return json.loads(report_path.read_text(encoding="utf-8"))

    def gated_readback(_dataset_id: str, _revision: str, relative_path: str):
        return (repo_root / "_test_gated_readback" / relative_path).read_bytes()

    with mock.patch(
        "grader.l4_evidence.run_gate", side_effect=recompute
    ), mock.patch(
        "grader.l4_evidence._fetch_gated_report_bytes",
        side_effect=gated_readback,
    ):
        return MODULE.validate(board, registry, repo_root=repo_root)


class TestFrontierPublication(unittest.TestCase):
    def test_checked_in_board_passes(self):
        board = json.loads((REPO / "hf/space/board.json").read_text(encoding="utf-8"))
        registry = json.loads((REPO / "results/marb_runs.json").read_text(encoding="utf-8"))
        sources = MODULE.load_grade_sources(board, REPO)
        self.assertEqual(MODULE.validate(board, registry, sources), [])

    def test_checked_in_unmeasured_l2_has_no_board_row(self):
        board = json.loads((REPO / "hf/space/board.json").read_text(encoding="utf-8"))
        self.assertEqual(board["scoring_version"], "v0.12")
        self.assertFalse(any(row.get("task") == "L2-RESOLVE" for row in board["rows"]))
        self.assertFalse(any(row.get("task") == "L4-ECO" for row in board["rows"]))

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

    def test_l4_both_gate_provenance_passes(self):
        board, registry, repo_root = _l4_fixtures()
        self.assertEqual(_validate_l4_fixture(board, registry, repo_root), [])

    def test_l4_fabricated_pass_report_over_invalid_step_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        errors = MODULE.validate(board, registry, repo_root=repo_root)
        self.assertTrue(any(
            "could not be recomputed from the registered STEP files" in error
            for error in errors
        ))

    def test_l4_requested_grade_requires_authenticated_gated_readback(self):
        board, registry, repo_root = _l4_fixtures()
        invariant_paths = {
            (repo_root / run["step"]).resolve(): repo_root / run["invariant_report"]
            for run in registry["runs"]
        }

        def recompute(_baseline: Path, changed: Path):
            return json.loads(
                invariant_paths[changed.resolve()].read_text(encoding="utf-8")
            )

        with mock.patch(
            "grader.l4_evidence.run_gate", side_effect=recompute
        ), mock.patch.dict("os.environ", {}, clear=True):
            errors = MODULE.validate(board, registry, repo_root=repo_root)
        self.assertTrue(any(
            "authenticated gated readback credential is unavailable" in error
            for error in errors
        ))

    def test_l4_tampered_gated_readback_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        run = registry["runs"][0]
        gated = (
            repo_root / "_test_gated_readback"
            / run["gated_requested_grade_report_path"]
        )
        gated.write_bytes(b"{}\n")
        errors = _validate_l4_fixture(board, registry, repo_root)
        self.assertTrue(any(
            "gated requested-change report digest does not match readback" in error
            for error in errors
        ))

    def test_l4_failed_public_invariant_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0]["invariant_gate_status"] = "fail"
        self.assertTrue(any(
            "public invariant gate" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_l4_missing_requested_change_grade_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0].pop("requested_change_report")
        self.assertTrue(any(
            "requested_change_report" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_l4_wrong_cadclaw_commit_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0]["cadclaw_commit"] = "c" * 40
        self.assertTrue(any(
            "cadclaw_commit" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_l4_identical_before_after_step_identity_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0]["artifact_sha256"] = registry["runs"][0][
            "baseline_artifact_sha256"
        ]
        self.assertTrue(any(
            "before/after STEP identities" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_l4_reused_gate_report_path_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0]["requested_change_report"] = registry["runs"][0][
            "invariant_report"
        ]
        self.assertTrue(any(
            "gate-report and run-log paths" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_defined_unmeasured_l4_cannot_publish(self):
        board, registry, repo_root = _l4_fixtures()
        registry["tasks"]["L4-ECO"]["status"] = "defined_unmeasured"
        self.assertTrue(any(
            "defined-unmeasured" in error
            for error in _validate_l4_fixture(board, registry, repo_root)
        ))

    def test_l4_fabricated_report_path_is_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        registry["runs"][0]["invariant_report"] = "runs/missing/invariant.json"
        registry["runs"][0]["invariant_report_sha256"] = "8" * 64
        errors = _validate_l4_fixture(board, registry, repo_root)
        self.assertTrue(any("evidence file is unavailable" in error for error in errors))

    def test_l4_tampered_report_bytes_are_rejected(self):
        board, registry, repo_root = _l4_fixtures()
        report = repo_root / registry["runs"][0]["invariant_report"]
        report.write_text("{}\n", encoding="utf-8")
        errors = _validate_l4_fixture(board, registry, repo_root)
        self.assertTrue(any("digest does not match" in error for error in errors))

    def test_l4_missing_or_tampered_artifacts_are_rejected(self):
        for field, action, expected in (
            ("step", "remove", "unavailable"),
            ("changed_editable_source", "tamper", "digest does not match"),
            ("run_log", "remove", "unavailable"),
        ):
            with self.subTest(field=field):
                board, registry, repo_root = _l4_fixtures()
                target = repo_root / registry["runs"][0][field]
                if action == "remove":
                    target.unlink()
                else:
                    target.write_bytes(b"tampered")
                errors = _validate_l4_fixture(board, registry, repo_root)
                self.assertTrue(any(expected in error for error in errors))

    def test_l4_four_attempts_three_graded_pass(self):
        board, registry, repo_root = _l4_fixtures()
        failed = json.loads(json.dumps(registry["runs"][0]))
        failed.update({
            "run_id": "run-04",
            "seed": "04",
            "outcome": "failed-export",
            "baseline_step": "runs/run-4/before.step",
            "baseline_artifact_sha256": "7" * 64,
            "baseline_editable_source": "runs/run-4/before.FCStd",
            "baseline_editable_source_sha256": "6" * 64,
            "driver_continuity_id": "opaque-4",
            "run_log": "runs/run-4/run_log.json",
            "run_log_sha256": "5" * 64,
        })
        for field in (
            "step", "artifact_sha256", "changed_editable_source",
            "changed_editable_source_sha256", "invariant_report",
            "invariant_report_sha256", "invariant_gate_status",
            "requested_change_report", "requested_change_report_sha256",
            "requested_change_status",
        ):
            failed.pop(field, None)
        for path_field, hash_field, payload in (
            ("baseline_step", "baseline_artifact_sha256", b"failed-baseline-step"),
            (
                "baseline_editable_source",
                "baseline_editable_source_sha256",
                b"failed-baseline-source",
            ),
            ("run_log", "run_log_sha256", b"failed-run-log"),
        ):
            target = repo_root / failed[path_field]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            failed[hash_field] = hashlib.sha256(payload).hexdigest()
        registry["runs"].append(failed)
        board["rows"][0]["reporting"]["n_attempted"] = 4
        board["rows"][0]["reporting"]["seed_ids"] = ["01", "02", "03", "04"]
        self.assertEqual(_validate_l4_fixture(board, registry, repo_root), [])

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
