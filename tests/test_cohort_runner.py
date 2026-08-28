"""Regressions for the deterministic, plan-only MARB cohort runner."""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO / "harness" / "cohort_runner.py"
L1_CONTRACT_PATH = REPO / "harness" / "l1_execution_contract.json"
BOARD_PATH = REPO / "hf" / "space" / "board.json"
REGISTRY_PATH = REPO / "results" / "marb_runs.json"

SPEC = importlib.util.spec_from_file_location("marb_cohort_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _canonical_text(raw: bytes) -> bytes:
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _text_sha256(path: Path) -> str:
    return hashlib.sha256(_canonical_text(path.read_bytes())).hexdigest()


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _independent_canonical_json(value: object, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


class SyntheticRepo:
    """Small public-only repository fixture matching the runner contract."""

    revision = "a" * 40
    kit_path = "kits/m3_cadquery_blind_kit.zip"
    brief_path = "prompts/CADQUERY_DRIVER_BRIEF.md"
    fusion_kit_path = "kits/m3_fusion_blind_kit_v1.3.zip"
    fusion_brief_path = "prompts/FUSION_DRIVER_BRIEF.md"
    member_path = "kit/V-Slot 20x40x1000 Linear Rail.step"
    member_payload = b"synthetic public authored rail\n"

    def __init__(self, root: Path) -> None:
        self.root = root
        self._write_bytes(".git/HEAD", (self.revision + "\n").encode("ascii"))
        self._write_bytes("harness/cohort_runner.py", RUNNER_PATH.read_bytes())
        self._write_bytes("tasks/m3_crete_l2_resolve/task.yaml", b"schema: synthetic-l2\n")
        self._write_bytes(
            "tasks/m3_crete_l2_resolve/CHANGE_REQUEST.md", b"# Synthetic L2 request\n"
        )
        self._write_bytes("tasks/m3_crete_l4_eco/task.yaml", b"schema: synthetic-l4\n")
        self._write_bytes(
            "tasks/m3_crete_l4_eco/ECO_REQUEST.md", b"# Synthetic L4 request\n"
        )
        self._write_bytes("prompts/standard_prompt.md", b"Synthetic public prompt\n")
        self._write_bytes(self.brief_path, b"Synthetic public CadQuery brief\n")
        self._write_bytes(self.fusion_brief_path, b"Synthetic public Fusion brief\n")
        self._write_bytes(
            "tasks/m3_crete/m3_connector_metadata.yaml", b"connectors: []\n"
        )
        self._write_bytes("grader/eco_invariant.py", b"GATE_VERSION = 'synthetic'\n")
        self._write_bytes("requirements-l4-eco.txt", b"synthetic-package==1.0\n")
        self.write_kit([(self.member_path, self.member_payload, None)])
        self.write_kit(
            [(self.member_path, self.member_payload, None)],
            relative=self.fusion_kit_path,
        )
        self.write_l1_contract()
        self.registry = self._new_registry()
        self.write_registry()

    def _write_bytes(self, relative: str, payload: bytes) -> Path:
        target = self.root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    def path(self, relative: str) -> Path:
        return self.root.joinpath(*relative.split("/"))

    def text_hash(self, relative: str) -> str:
        return _text_sha256(self.path(relative))

    def raw_hash(self, relative: str) -> str:
        return _raw_sha256(self.path(relative))

    def write_kit(
        self,
        entries: list[tuple[str, bytes, int | None]],
        *,
        relative: str | None = None,
    ) -> None:
        target = self.path(relative or self.kit_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload, unix_mode in entries:
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                if unix_mode is not None:
                    info.create_system = 3
                    info.external_attr = unix_mode << 16
                archive.writestr(info, payload)

    def write_l1_contract(self, **changes: object) -> None:
        def identity(relative: str, *, text: bool) -> dict[str, object]:
            path = self.path(relative)
            payload = _canonical_text(path.read_bytes()) if text else path.read_bytes()
            return {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "hash_mode": "utf8-lf" if text else "raw-bytes",
            }

        contract: dict[str, object] = {
            "schema": RUNNER.L1_CONTRACT_SCHEMA,
            "task": {
                "id": "L1-ASSEMBLE",
                "rung": "L1",
                "scoring_version": "v0.10",
                "task_revision": "synthetic-l1-r1",
                "provenance_contract": "l1_assemble.v1",
                "status": "measured",
            },
            "change_request": None,
            "prompt": identity("prompts/standard_prompt.md", text=True),
            "drivers": {
                "cadquery": {
                    "tool": "CadQuery",
                    "kit_version": "v1.1",
                    "kit": identity(self.kit_path, text=False),
                    "driver_brief": identity(self.brief_path, text=True),
                },
                "fusion": {
                    "tool": "Autodesk Fusion",
                    "kit_version": "v1.3",
                    "kit": identity(self.fusion_kit_path, text=False),
                    "driver_brief": identity(self.fusion_brief_path, text=True),
                },
            },
            "outputs": {
                "editable_source_capture": RUNNER.L1_EDITABLE_SOURCE_CAPTURE,
                **RUNNER.L1_OUTPUT_NAMES,
            },
        }
        contract.update(changes)
        payload = json.dumps(contract, indent=2, sort_keys=True) + "\n"
        self._write_bytes(RUNNER.L1_CONTRACT_PATH, payload.encode("utf-8"))

    def _common_task(self, *, rung: str, version: str, revision: str) -> dict[str, object]:
        return {
            "rung": rung,
            "scoring_version": version,
            "status": "defined_unmeasured",
            "task_revision": revision,
            "allowed_kits": {self.kit_path: self.raw_hash(self.kit_path)},
            "prompt": "prompts/standard_prompt.md",
            "prompt_sha256": self.text_hash("prompts/standard_prompt.md"),
            "allowed_driver_briefs": {
                self.brief_path: self.text_hash(self.brief_path),
            },
            "answer_key_status": "private_validated_distribution_pending",
            "evidence_status": "pending",
        }

    def _new_registry(self) -> dict[str, object]:
        l2 = self._common_task(
            rung="L2", version="v0.11", revision="synthetic-l2-r1"
        )
        l2.update(
            {
                "provenance_contract": "l2_change_loop.v1",
                "task_definition": "tasks/m3_crete_l2_resolve/task.yaml",
                "task_definition_sha256": self.text_hash(
                    "tasks/m3_crete_l2_resolve/task.yaml"
                ),
                "change_request": "tasks/m3_crete_l2_resolve/CHANGE_REQUEST.md",
                "change_request_sha256": self.text_hash(
                    "tasks/m3_crete_l2_resolve/CHANGE_REQUEST.md"
                ),
                "change_request_id": "synthetic-l2-r1",
            }
        )
        l4 = self._common_task(
            rung="L4", version="v0.12", revision="synthetic-l4-r1"
        )
        l4.update(
            {
                "provenance_contract": "l4_eco.v1",
                "task_definition": "tasks/m3_crete_l4_eco/task.yaml",
                "task_definition_sha256": self.text_hash(
                    "tasks/m3_crete_l4_eco/task.yaml"
                ),
                "eco_request": "tasks/m3_crete_l4_eco/ECO_REQUEST.md",
                "eco_request_sha256": self.text_hash(
                    "tasks/m3_crete_l4_eco/ECO_REQUEST.md"
                ),
                "eco_request_id": "synthetic-l4-r1",
                "connector_metadata": "tasks/m3_crete/m3_connector_metadata.yaml",
                "connector_metadata_sha256": self.text_hash(
                    "tasks/m3_crete/m3_connector_metadata.yaml"
                ),
                "added_source_path": self.member_path,
                "added_source_sha256": hashlib.sha256(self.member_payload).hexdigest(),
                "added_source_bytes": len(self.member_payload),
                "cadclaw_version": "0.10.0",
                "cadclaw_commit": "b" * 40,
                "cadquery_version": "2.7.0",
                "cadquery_ocp_version": "7.8.1.1.post1",
                "invariant_gate_version": "marb_l4_eco_invariant.v0.12.0",
                "invariant_gate_implementation_sha256": self.text_hash(
                    "grader/eco_invariant.py"
                ),
                "invariant_gate_requirements_sha256": self.text_hash(
                    "requirements-l4-eco.txt"
                ),
                "requested_change_grade_method": "marb_task_local_reference.v0.12",
                "gated_distribution_revision": None,
                "answer_key_step_sha256": "d" * 64,
                "answer_key_spec_sha256": "e" * 64,
            }
        )
        return {
            "schema": "marb_runs.v2",
            "default_task": "L1-ASSEMBLE",
            "legacy_defaults": {"task": "L1-ASSEMBLE", "scoring_version": "v0.9"},
            "tasks": {
                "L1-ASSEMBLE": {"rung": "L1", "scoring_version": "v0.10"},
                "L2-RESOLVE": l2,
                "L4-ECO": l4,
            },
            "publication_policy": {
                "effective_date": "2026-08-28",
                "frontier_min_distinct_runs": 3,
                "summary": "median",
                "spread": "population_std",
            },
            "runs": [],
        }

    def write_registry(self) -> None:
        payload = json.dumps(self.registry, indent=2, sort_keys=True) + "\n"
        self._write_bytes("results/marb_runs.json", payload.encode("utf-8"))

    def refresh_kit_digest(self) -> None:
        digest = self.raw_hash(self.kit_path)
        tasks = self.registry["tasks"]
        assert isinstance(tasks, dict)
        for task in tasks.values():
            assert isinstance(task, dict)
            allowed = task.get("allowed_kits")
            if allowed is None:
                continue
            assert isinstance(allowed, dict)
            allowed[self.kit_path] = digest
        self.write_registry()

    def kwargs(self, task_id: str = "L4-ECO") -> dict[str, object]:
        slug = {
            "L1-ASSEMBLE": "l1",
            "L2-RESOLVE": "l2",
            "L4-ECO": "l4",
        }[task_id]
        return {
            "task_id": task_id,
            "source_revision": self.revision,
            "cell_id": f"synthetic-{slug}-cell",
            "cell_label": f"Synthetic {slug.upper()} - CadQuery",
            "cohort_id": f"synthetic-{slug}-cohort",
            "model_id": "synthetic-model-v1",
            "model_name": "Synthetic Model V1",
            "driver_id": "cadquery",
            "driver_version": "2.7.0",
            "prompt_variant": "frozen-core",
            "seed_basis": "independent-run-ordinal",
            "seeds": ["01", "02", "03"],
        }

    def build(self, task_id: str = "L4-ECO", **changes: object) -> dict[str, object]:
        kwargs = self.kwargs(task_id)
        kwargs.update(changes)
        return RUNNER.build_plan(self.root, **kwargs)


class CohortRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-cohort-runner-test-")
        self.addCleanup(temporary.cleanup)
        self.fixture = SyntheticRepo(Path(temporary.name))

    @staticmethod
    def _actual_kwargs(task_id: str) -> dict[str, object]:
        task_slug = {
            "L1-ASSEMBLE": "l1-assemble",
            "L2-RESOLVE": "l2-resolve",
            "L4-ECO": "l4-eco",
        }[task_id]
        return {
            "task_id": task_id,
            "source_revision": RUNNER._read_git_head(REPO),
            "cell_id": f"test-{task_slug}-deterministic-cell",
            "cell_label": f"Test {task_id} - CadQuery",
            "cohort_id": f"test-{task_slug}-deterministic-cohort",
            "model_id": "synthetic-model-v1",
            "model_name": "Synthetic Model V1",
            "driver_id": "cadquery",
            "driver_version": "2.7.0",
            "prompt_variant": "frozen-core",
            "seed_basis": "independent-run-ordinal",
            "seeds": ["01", "02", "03"],
        }

    def test_actual_repo_l1_l2_and_l4_plans_are_deterministic_and_read_only(self) -> None:
        registry_before = REGISTRY_PATH.read_bytes()
        board_before = BOARD_PATH.read_bytes()

        for task_id in ("L1-ASSEMBLE", "L2-RESOLVE", "L4-ECO"):
            with self.subTest(task=task_id):
                kwargs = self._actual_kwargs(task_id)
                first = RUNNER.build_plan(REPO, **kwargs)
                second = RUNNER.build_plan(REPO, **kwargs)
                self.assertEqual(first, second)
                self.assertEqual(
                    _independent_canonical_json(first, newline=True),
                    RUNNER._canonical_json(first, newline=True),
                )
                self.assertEqual(first["plan"]["cohort"]["planned_attempts"], 3)
                self.assertEqual(
                    [run["seed"] for run in first["plan"]["runs"]],
                    ["01", "02", "03"],
                )

        self.assertEqual(REGISTRY_PATH.read_bytes(), registry_before)
        self.assertEqual(BOARD_PATH.read_bytes(), board_before)

    def test_l1_plan_uses_only_the_public_execution_contract(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        registry_task = registry["tasks"]["L1-ASSEMBLE"]
        envelope = RUNNER.build_plan(REPO, **self._actual_kwargs("L1-ASSEMBLE"))
        plan = envelope["plan"]

        self.assertEqual(plan["task"]["id"], "L1-ASSEMBLE")
        self.assertEqual(plan["task"]["provenance_contract"], "l1_assemble.v1")
        self.assertIsNone(plan["task"]["change_request_id"])
        self.assertIsNone(plan["task"]["change_request"])
        self.assertEqual(plan["task"]["runtime_contract"], {})
        self.assertEqual(
            set(plan["frozen_public_inputs"]),
            {"task_definition", "kit", "prompt", "driver_brief"},
        )
        self.assertEqual(
            plan["frozen_public_inputs"]["task_definition"]["path"],
            RUNNER.L1_CONTRACT_PATH,
        )
        self.assertEqual(
            plan["readiness"]["blockers"],
            [
                "this plan does not authorize execution, provider access, or spend",
                "planned slots are not benchmark attempts or run evidence",
            ],
        )
        for run in plan["runs"]:
            base = f"runs/{run['run_id']}--{RUNNER.ATTEMPT_UUID_TOKEN}"
            self.assertEqual(
                run["planned_outputs"],
                {
                    "executor": {
                        key: (
                            f"{base}/{name}"
                            if key == "run_log"
                            else f"{base}/evidence/{name}"
                        )
                        for key, name in RUNNER.L1_OUTPUT_NAMES.items()
                    },
                    "downstream": {},
                },
            )
            self.assertTrue(
                run["planned_outputs"]["executor"]["baseline_editable_source_zip"].endswith(
                    ".zip"
                )
            )
            self.assertTrue(
                run["planned_outputs"]["executor"]["final_editable_source_zip"].endswith(
                    ".zip"
                )
            )

        rendered = _independent_canonical_json(envelope).decode("ascii")
        contract_rendered = L1_CONTRACT_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "answer_key",
            "reference_step",
            "placement_spec",
            "gated_dataset",
            '"spec"',
        ):
            self.assertNotIn(forbidden, rendered.casefold())
            self.assertNotIn(forbidden, contract_rendered.casefold())
        for field in ("reference_step", "spec"):
            value = registry_task.get(field)
            if isinstance(value, str):
                self.assertNotIn(value, rendered)

        fusion_kwargs = self._actual_kwargs("L1-ASSEMBLE")
        fusion_kwargs.update(
            {
                "cell_id": "test-l1-assemble-fusion-cell",
                "cohort_id": "test-l1-assemble-fusion-cohort",
                "driver_id": "fusion",
                "driver_version": "2703.1.11",
            }
        )
        fusion = RUNNER.build_plan(REPO, **fusion_kwargs)["plan"]
        self.assertEqual(
            fusion["frozen_public_inputs"]["kit"]["path"],
            RUNNER.DRIVERS["fusion"]["kit"],
        )
        self.assertEqual(
            fusion["frozen_public_inputs"]["driver_brief"]["path"],
            RUNNER.DRIVERS["fusion"]["brief"],
        )

    def test_l1_contract_and_envelope_fail_closed_on_drift_or_fabrication(self) -> None:
        envelope = self.fixture.build("L1-ASSEMBLE")
        raw = _independent_canonical_json(envelope, newline=True)
        self.assertEqual(RUNNER.verify_plan_envelope(raw), envelope)

        self.fixture.path("prompts/standard_prompt.md").write_bytes(b"drifted\n")
        with self.assertRaisesRegex(RUNNER.PlanError, "frozen SHA-256"):
            self.fixture.build("L1-ASSEMBLE")

        self.fixture._write_bytes("prompts/standard_prompt.md", b"Synthetic public prompt\n")
        self.fixture.write_l1_contract(unexpected=True)
        with self.assertRaisesRegex(RUNNER.PlanError, "unknown or missing fields"):
            self.fixture.build("L1-ASSEMBLE")

        self.fixture.write_l1_contract(change_request={"path": "request.md"})
        with self.assertRaisesRegex(RUNNER.PlanError, "must not define a change request"):
            self.fixture.build("L1-ASSEMBLE")

        self.fixture.write_l1_contract()
        forged_request = copy.deepcopy(envelope)
        forged_request["plan"]["task"]["change_request_id"] = "invented"
        forged_request["plan"]["task"]["change_request"] = {
            "path": "requests/invented.md",
            "sha256": "0" * 64,
        }
        forged_request["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(forged_request["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "must not invent"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(forged_request, newline=True)
            )

        forged_output = copy.deepcopy(envelope)
        forged_output["plan"]["runs"][0]["planned_outputs"]["executor"][
            "baseline_editable_source_zip"
        ] = "runs/other/source.py"
        forged_output["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(forged_output["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "output paths are inconsistent"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(forged_output, newline=True)
            )

    def test_current_l4_plan_is_blocked_and_excludes_private_metadata(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        task = registry["tasks"]["L4-ECO"]
        envelope = RUNNER.build_plan(REPO, **self._actual_kwargs("L4-ECO"))
        plan = envelope["plan"]

        self.assertEqual(plan["mode"], "plan-only")
        self.assertEqual(plan["execution_status"], "blocked-before-execution")
        self.assertEqual(plan["readiness"]["status"], "blocked")
        self.assertEqual(plan["readiness"]["registered_task_runs"], 0)
        self.assertIn(
            "immutable gated requested-change grading revision is missing",
            plan["readiness"]["blockers"],
        )
        self.assertEqual(plan["safety"]["model_calls_performed"], 0)
        self.assertEqual(plan["safety"]["provider_calls_performed"], 0)
        self.assertFalse(plan["safety"]["execution_authorized"])
        self.assertFalse(plan["safety"]["registry_mutated"])
        self.assertFalse(plan["safety"]["run_artifacts_created"])
        for run in plan["runs"]:
            self.assertEqual(run["status"], "planned")
            self.assertIsNone(run["outcome"])
            self.assertIsNone(run["driver_continuity_id"])
            self.assertIsNone(run["artifact_sha256"])
            self.assertIsNone(run["run_log_sha256"])
            self.assertEqual(run["model_calls_performed"], 0)

        rendered = _independent_canonical_json(envelope).decode("ascii")
        excluded_fields = (
            "answer_key_private_commit",
            "answer_key_step_sha256",
            "answer_key_spec_sha256",
            "private_build_evidence_sha256",
            "private_compiler_report_sha256",
            "private_self_grade_report_sha256",
            "private_snapshot_fingerprint",
            "reference_step",
            "spec",
            "gated_dataset_id",
            "gated_read_credential_env",
        )
        for field in excluded_fields:
            self.assertNotIn(f'"{field}"', rendered)
            value = task.get(field)
            if isinstance(value, str):
                self.assertNotIn(value, rendered)

    def test_active_task_states_and_readiness_blockers_follow_registry_contract(self) -> None:
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L4-ECO"]
        assert isinstance(task, dict)
        for status in ("defined_unmeasured", "measured"):
            with self.subTest(status=status):
                task["status"] = status
                task["answer_key_status"] = "ready"
                task["evidence_status"] = "complete"
                task["gated_distribution_revision"] = "c" * 40
                self.fixture.write_registry()
                blockers = self.fixture.build()["plan"]["readiness"]["blockers"]
                self.assertEqual(
                    blockers,
                    [
                        "this plan does not authorize execution, provider access, or spend",
                        "planned slots are not benchmark attempts or run evidence",
                    ],
                )

        task["status"] = "retired"
        self.fixture.write_registry()
        with self.assertRaisesRegex(RUNNER.PlanError, "supported active state"):
            self.fixture.build()

    def test_l4_readiness_rejects_malformed_and_accepts_immutable_revisions(self) -> None:
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L4-ECO"]
        assert isinstance(task, dict)
        task["answer_key_status"] = "ready"
        task["evidence_status"] = "complete"
        for revision in ("c" * 40, "d" * 64):
            with self.subTest(revision_length=len(revision)):
                task["gated_distribution_revision"] = revision
                self.fixture.write_registry()
                blockers = self.fixture.build()["plan"]["readiness"]["blockers"]
                self.assertNotIn(
                    "immutable gated requested-change grading revision is missing",
                    blockers,
                )
        task["gated_distribution_revision"] = "main"
        self.fixture.write_registry()
        blockers = self.fixture.build()["plan"]["readiness"]["blockers"]
        self.assertIn(
            "immutable gated requested-change grading revision is missing", blockers
        )

    def test_seed_minimum_and_normalized_aliases_fail_closed(self) -> None:
        cases = (
            (["01", "02"], "at least 3"),
            (["1", "01", "2"], "duplicate or normalized alias"),
            (["Alpha", "alpha", "Beta"], "duplicate or normalized alias"),
            (["01", "01", "02"], "duplicate or normalized alias"),
            (["a", "a.", "b"], "Windows-ambiguous"),
        )
        for seeds, message in cases:
            with self.subTest(seeds=seeds):
                with self.assertRaisesRegex(RUNNER.PlanError, message):
                    self.fixture.build(seeds=seeds)

    def test_both_publication_seed_bases_are_accepted(self) -> None:
        ordinal = self.fixture.build(
            seed_basis="independent-run-ordinal", seeds=["01", "02", "03"]
        )
        provider = self.fixture.build(
            seed_basis="provider-seed", seeds=["101", "102", "103"]
        )
        self.assertEqual(
            ordinal["plan"]["cohort"]["seed_basis"], "independent-run-ordinal"
        )
        self.assertEqual(provider["plan"]["cohort"]["seed_basis"], "provider-seed")

        for invalid in (
            ["seed-a", "102", "103"],
            [str(2**31), "102", "103"],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RUNNER.PlanError, "executor range"):
                    self.fixture.build(seed_basis="provider-seed", seeds=invalid)

    def test_frozen_hash_drift_fails_closed(self) -> None:
        self.fixture.path("prompts/standard_prompt.md").write_bytes(b"drifted prompt\n")
        with self.assertRaisesRegex(RUNNER.PlanError, "frozen SHA-256"):
            self.fixture.build()

    def test_missing_frozen_input_fails_closed(self) -> None:
        self.fixture.path(self.fixture.brief_path).unlink()
        with self.assertRaisesRegex(RUNNER.PlanError, "does not exist"):
            self.fixture.build()

    def test_registry_path_traversal_fails_closed(self) -> None:
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L2-RESOLVE"]
        assert isinstance(task, dict)
        task["task_definition"] = "../outside.yaml"
        task["task_definition_sha256"] = "0" * 64
        self.fixture.write_registry()
        with self.assertRaisesRegex(RUNNER.PlanError, "path traversal"):
            self.fixture.build("L2-RESOLVE")

    def test_registry_cannot_rename_a_frozen_public_input(self) -> None:
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L4-ECO"]
        assert isinstance(task, dict)
        original = self.fixture.path("tasks/m3_crete_l4_eco/task.yaml").read_bytes()
        for alternate in (
            "tasks/m3_crete_l4_eco/ANSWER.KEY.md",
            "tasks/m3_crete_l4_eco/renamed.yaml",
        ):
            with self.subTest(alternate=alternate):
                self.fixture._write_bytes(alternate, original)
                task["task_definition"] = alternate
                task["task_definition_sha256"] = self.fixture.text_hash(alternate)
                self.fixture.write_registry()
                with self.assertRaisesRegex(RUNNER.PlanError, "canonical public path"):
                    self.fixture.build()

    def test_symlinked_public_input_fails_closed(self) -> None:
        link = self.fixture.path("prompts/standard_prompt.md")
        target = self.fixture.path("prompts/real_prompt.md")
        link.rename(target)
        try:
            os.symlink(target.name, link)
        except (OSError, NotImplementedError):
            # Windows may require an unavailable privilege for real symlinks.
            # Keep the branch covered locally while Linux CI exercises a real one.
            link.write_bytes(target.read_bytes())
            original = RUNNER._is_link_like
            with mock.patch.object(
                RUNNER,
                "_is_link_like",
                side_effect=lambda path: path == link or original(path),
            ):
                with self.assertRaisesRegex(RUNNER.PlanError, "symlink or junction"):
                    self.fixture.build()
        else:
            with self.assertRaisesRegex(RUNNER.PlanError, "symlink or junction"):
                self.fixture.build()

    def _assert_malicious_kit_rejected(
        self, entries: list[tuple[str, bytes, int | None]], message: str
    ) -> None:
        self.fixture.write_kit(entries)
        self.fixture.refresh_kit_digest()
        with self.assertRaisesRegex(RUNNER.PlanError, message):
            self.fixture.build()

    def test_zip_path_traversal_entry_fails_closed(self) -> None:
        self._assert_malicious_kit_rejected(
            [
                (self.fixture.member_path, self.fixture.member_payload, None),
                ("../escape.txt", b"escape", None),
            ],
            "unsafe archive entry",
        )

    def test_zip_symbolic_link_entry_fails_closed(self) -> None:
        self._assert_malicious_kit_rejected(
            [
                (self.fixture.member_path, self.fixture.member_payload, None),
                ("kit/link", b"target", stat.S_IFLNK | 0o777),
            ],
            "symbolic-link archive entry",
        )

    def test_zip_casefold_duplicate_entry_fails_closed(self) -> None:
        self._assert_malicious_kit_rejected(
            [
                (self.fixture.member_path, self.fixture.member_payload, None),
                (self.fixture.member_path.upper(), b"different", None),
            ],
            "duplicate archive entries",
        )

    def test_boolean_l4_member_size_fails_closed(self) -> None:
        payload = b"x"
        self.fixture.write_kit([(self.fixture.member_path, payload, None)])
        self.fixture.refresh_kit_digest()
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L4-ECO"]
        assert isinstance(task, dict)
        task["added_source_sha256"] = hashlib.sha256(payload).hexdigest()
        task["added_source_bytes"] = True
        self.fixture.write_registry()
        with self.assertRaisesRegex(RUNNER.PlanError, "malformed frozen byte count"):
            self.fixture.build()

    def test_unsafe_public_identifiers_fail_closed(self) -> None:
        cases = (
            ({"cell_id": "../cell"}, "cell_id"),
            ({"cohort_id": "Uppercase"}, "cohort_id"),
            ({"prompt_variant": "nested/value"}, "prompt_variant"),
            ({"model_id": "https://provider.invalid/model"}, "model_id"),
            ({"model_name": "Model\nInjected"}, "model name"),
            ({"driver_version": "2.7.0;run"}, "driver version"),
            ({"seeds": ["01", "02", "../03"]}, "seed"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(RUNNER.PlanError, message):
                    self.fixture.build(**changes)

        with self.assertRaisesRegex(RUNNER.PlanError, "canonical byte-bound"):
            self.fixture.build(prompt_variant="alternate-safe-label")

    def test_source_revision_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(RUNNER.PlanError, "does not match"):
            self.fixture.build(source_revision="b" * 40)
        with self.assertRaisesRegex(RUNNER.PlanError, "full lowercase"):
            self.fixture.build(source_revision="A" * 40)

    def test_cadquery_driver_version_must_match_executor_and_l4_task_runtime(self) -> None:
        for task_id in ("L1-ASSEMBLE", "L2-RESOLVE", "L4-ECO"):
            with self.subTest(task_id=task_id):
                with self.assertRaisesRegex(RUNNER.PlanError, "executor runtime contract"):
                    self.fixture.build(task_id, driver_version="9.9")

    def test_existing_cell_requires_the_exact_frozen_cohort_identity(self) -> None:
        kwargs = self.fixture.kwargs("L4-ECO")
        tasks = self.fixture.registry["tasks"]
        assert isinstance(tasks, dict)
        task = tasks["L4-ECO"]
        assert isinstance(task, dict)
        driver = RUNNER.DRIVERS["cadquery"]
        run = {
            "run_id": "synthetic-l4-cohort-00",
            "cell_id": kwargs["cell_id"],
            "cell": kwargs["cell_label"],
            "task": "L4-ECO",
            "scoring_version": "v0.12",
            "track": "frontier",
            "cohort_id": kwargs["cohort_id"],
            "seed": "00",
            "model": {"id": kwargs["model_id"], "name": kwargs["model_name"]},
            "driver": {"tool": driver["tool"], "version": kwargs["driver_version"]},
            "kit": driver["kit"],
            "kit_sha256": task["allowed_kits"][driver["kit"]],
            "kit_version": driver["kit_version"],
            "prompt": task["prompt"],
            "prompt_sha256": task["prompt_sha256"],
            "prompt_variant": kwargs["prompt_variant"],
            "driver_brief": driver["brief"],
            "driver_brief_sha256": task["allowed_driver_briefs"][driver["brief"]],
            "task_definition": task["task_definition"],
            "task_definition_sha256": task["task_definition_sha256"],
            "eco_request": task["eco_request"],
            "eco_request_sha256": task["eco_request_sha256"],
            "eco_request_id": task["eco_request_id"],
        }
        for key in (
            "cadclaw_commit",
            "cadclaw_version",
            "cadquery_version",
            "cadquery_ocp_version",
            "invariant_gate_version",
            "invariant_gate_implementation_sha256",
            "invariant_gate_requirements_sha256",
            "requested_change_grade_method",
            "gated_distribution_revision",
            "answer_key_step_sha256",
            "answer_key_spec_sha256",
        ):
            run[key] = task[key]
        self.fixture.registry["runs"] = [copy.deepcopy(run)]
        self.fixture.write_registry()
        self.assertEqual(
            self.fixture.build()["plan"]["cohort"]["existing_registered_attempts_for_cell"],
            1,
        )

        mutations = (
            (("cell",), "Different display"),
            (("model", "name"), "Different model"),
            (("kit_version",), "v9.9"),
            (("prompt_variant",), "different-prompt"),
            (("track",), "local"),
            (("cadclaw_version",), "9.9"),
            (("answer_key_step_sha256",), "0" * 64),
            (("gated_distribution_revision",), "c" * 40),
        )
        for path, value in mutations:
            with self.subTest(path=path):
                changed = copy.deepcopy(run)
                cursor = changed
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                self.fixture.registry["runs"] = [changed]
                self.fixture.write_registry()
                with self.assertRaisesRegex(RUNNER.PlanError, "different frozen cohort"):
                    self.fixture.build()

    def test_planned_outputs_cannot_alias_registered_evidence_paths(self) -> None:
        for registered in (
            "runs/synthetic-l4-cohort-01/run_log.json",
            "RUNS/SYNTHETIC-L4-COHORT-01./RUN_LOG.JSON",
        ):
            with self.subTest(registered=registered):
                self.fixture.registry["runs"] = [
                    {
                        "run_id": f"unrelated-{hashlib.sha256(registered.encode()).hexdigest()[:8]}",
                        "cell_id": "unrelated-cell",
                        "seed": "99",
                        "run_log": registered,
                    }
                ]
                self.fixture.write_registry()
                with self.assertRaisesRegex(
                    RUNNER.PlanError, "output path collides|Windows-ambiguous"
                ):
                    self.fixture.build()

        self.fixture.registry["runs"] = [
            {
                "run_id": "unrelated-traversal",
                "cell_id": "unrelated-cell",
                "seed": "99",
                "run_log": "runs/other/../synthetic-l4-cohort-01/run_log.json",
            }
        ]
        self.fixture.write_registry()
        with self.assertRaisesRegex(RUNNER.PlanError, "path traversal"):
            self.fixture.build()

    def test_ast_has_no_execution_provider_network_or_environment_edge(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(RUNNER_PATH))
        imported_roots: set[str] = set()
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
                    imported_roots.add(alias.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden_roots = {
            "openai",
            "anthropic",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "subprocess",
            "asyncio",
            "multiprocessing",
            "os",
        }
        self.assertTrue(forbidden_roots.isdisjoint(imported_roots), imported_modules)
        self.assertFalse(
            any(
                module.endswith(("marb_local_harness", "run_batch"))
                for module in imported_modules
            ),
            imported_modules,
        )
        self.assertNotIn("MARB_LOCAL_ENDPOINT", source)
        self.assertNotIn("api-key", source.casefold())
        self.assertNotIn("base-url", source.casefold())

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                RUNNER._parser().parse_args(["run"])

    def test_runtime_plan_path_does_not_call_network_process_env_or_live_harness(self) -> None:
        before = {
            path.relative_to(self.fixture.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.fixture.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        live_modules = {
            "harness.marb_local_harness",
            "harness.run_batch",
            "marb_local_harness",
            "run_batch",
        }
        loaded_before = live_modules.intersection(sys.modules)

        def forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("plan-only runner crossed an execution boundary")

        with (
            mock.patch.object(subprocess, "run", side_effect=forbidden),
            mock.patch.object(subprocess, "Popen", side_effect=forbidden),
            mock.patch.object(socket, "create_connection", side_effect=forbidden),
            mock.patch.object(urllib.request, "urlopen", side_effect=forbidden),
            mock.patch.object(os, "getenv", side_effect=forbidden),
            mock.patch.object(importlib, "import_module", side_effect=forbidden),
        ):
            envelope = self.fixture.build()

        self.assertEqual(envelope["plan"]["safety"]["model_calls_performed"], 0)
        self.assertEqual(live_modules.intersection(sys.modules), loaded_before)
        after = {
            path.relative_to(self.fixture.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.fixture.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        self.assertEqual(after, before)

    def test_cli_contains_final_verification_errors_without_a_traceback(self) -> None:
        malformed = {"schema": RUNNER.PLAN_SCHEMA, "plan_sha256": "0" * 64, "plan": {}}
        argv = [
            "plan",
            "--task", "L4-ECO",
            "--source-revision", "a" * 40,
            "--cell-id", "cell",
            "--cell-label", "Cell",
            "--cohort-id", "cohort",
            "--model-id", "model",
            "--model-name", "Model",
            "--driver", "cadquery",
            "--driver-version", "2.7.0",
            "--prompt-variant", "frozen-core",
            "--seed-basis", "independent-run-ordinal",
            "--seed", "01", "--seed", "02", "--seed", "03",
        ]
        stderr = io.StringIO()
        with mock.patch.object(RUNNER, "build_plan", return_value=malformed):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(RUNNER.main(argv), 2)
        self.assertIn("cohort plan rejected", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_canonical_envelope_and_independent_digest_verify(self) -> None:
        envelope = self.fixture.build()
        raw = _independent_canonical_json(envelope, newline=True)
        independently_computed = hashlib.sha256(
            _independent_canonical_json(envelope["plan"])
        ).hexdigest()
        self.assertEqual(envelope["plan_sha256"], independently_computed)
        self.assertEqual(
            RUNNER.verify_plan_envelope(raw, expected_sha256=independently_computed),
            envelope,
        )
        with self.assertRaisesRegex(RUNNER.PlanError, "independently approved"):
            RUNNER.verify_plan_envelope(raw, expected_sha256="0" * 64)

    def test_envelope_tampering_and_noncanonical_encoding_fail_closed(self) -> None:
        envelope = self.fixture.build()

        stale_digest = copy.deepcopy(envelope)
        stale_digest["plan"]["runs"][0]["outcome"] = "graded"
        with self.assertRaisesRegex(RUNNER.PlanError, "payload digest"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(stale_digest, newline=True)
            )

        forged = copy.deepcopy(stale_digest)
        forged["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(forged["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "fabricated execution evidence"):
            RUNNER.verify_plan_envelope(_independent_canonical_json(forged, newline=True))

        unknown_field = copy.deepcopy(envelope)
        unknown_field["plan"]["runs"][0]["score"] = 100
        unknown_field["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(unknown_field["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "unknown or missing fields"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(unknown_field, newline=True)
            )

        path_mutations = (
            (("task_definition", "path"), "tasks/m3_crete_l4_eco/ANSWER.KEY.md"),
            (("prompt", "path"), "prompts/alternate.md"),
            (("connector_metadata", "path"), "tasks/m3_crete/other.yaml"),
            (("invariant_gate_implementation", "path"), "grader/other.py"),
            (("invariant_gate_requirements", "path"), "other-requirements.txt"),
            (("added_source", "member_path"), "kit/other.step"),
            (("added_source", "archive_path"), "kits/other.zip"),
        )
        for path, replacement in path_mutations:
            with self.subTest(path=path):
                forged_path = copy.deepcopy(envelope)
                forged_path["plan"]["frozen_public_inputs"][path[0]][path[1]] = replacement
                forged_path["plan_sha256"] = hashlib.sha256(
                    _independent_canonical_json(forged_path["plan"])
                ).hexdigest()
                with self.assertRaisesRegex(RUNNER.PlanError, "canonical|inconsistent"):
                    RUNNER.verify_plan_envelope(
                        _independent_canonical_json(forged_path, newline=True)
                    )

        wrong_hash_mode = copy.deepcopy(envelope)
        wrong_hash_mode["plan"]["frozen_public_inputs"]["kit"]["hash_mode"] = "utf8-lf"
        wrong_hash_mode["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(wrong_hash_mode["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "hash mode"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(wrong_hash_mode, newline=True)
            )

        contradictory_driver = copy.deepcopy(envelope)
        contradictory_driver["plan"]["cohort"]["driver"]["version"] = "9.9"
        contradictory_driver["plan_sha256"] = hashlib.sha256(
            _independent_canonical_json(contradictory_driver["plan"])
        ).hexdigest()
        with self.assertRaisesRegex(RUNNER.PlanError, "contradicts"):
            RUNNER.verify_plan_envelope(
                _independent_canonical_json(contradictory_driver, newline=True)
            )

        pretty = (json.dumps(envelope, indent=2, sort_keys=True) + "\n").encode("ascii")
        with self.assertRaisesRegex(RUNNER.PlanError, "not canonical JSON"):
            RUNNER.verify_plan_envelope(pretty)


if __name__ == "__main__":
    unittest.main()
