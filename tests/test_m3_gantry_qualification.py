"""Preflight and claim-boundary checks for assisted gantry qualification.

No native geometry is generated or solved. Synthetic byte fixtures exercise
frozen-input handling before native analysis. They are not M3 task inputs.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import qualify_m3_gantry as qualification


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding="utf-8")


def _temporary_directory():
    base = (qualification.ROOT / "runs").resolve()
    base.relative_to(qualification.ROOT.resolve())
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="m3-gantry-qualification-test-", dir=base)


def _complete_report():
    cases = []
    for weight in ("force_only", "with_stock_weight"):
        for ordinal in range(7):
            cases.append({"id": weight + "-" + str(ordinal), "verification": {
                "status": "expected_mechanism_refused" if ordinal == 1 else "analytical_equations_verified"}})
    return {"status": "diagnostic_complete", "analysis_admitted": False,
            "leaderboard_eligible": False, "physical_acceptance": "not_evaluated",
            "legacy_scores_changed": False, "strength_assessment": "not_qualified",
            "service_limit_assessment": "not_qualified", "whole_machine_coverage": False,
            "cases": cases, "errors": []}


class TestQualificationOutcomeClaims(unittest.TestCase):
    def test_complete_fourteen_scenarios_and_causal_refusal_can_be_accepted(self):
        qualification._accepted(_complete_report(), "diagnostic_complete")
        refused = _complete_report()
        refused.update(status="not_evaluable", cases=[], errors=["m3.gantry.stock_splice_does_not_close"])
        qualification._accepted(refused, "not_evaluable")

    def test_forged_physical_strength_service_or_whole_machine_claims_are_refused(self):
        for key, value in (("analysis_admitted", True), ("leaderboard_eligible", True),
                           ("physical_acceptance", "accepted"), ("legacy_scores_changed", True),
                           ("strength_assessment", "pass"), ("service_limit_assessment", "pass"),
                           ("whole_machine_coverage", True)):
            report = _complete_report()
            report[key] = value
            with self.subTest(key=key), self.assertRaises(qualification.ControlError):
                qualification._accepted(report, "diagnostic_complete")

    def test_missing_duplicate_and_incorrect_sensitivity_coverage_are_refused(self):
        for change in ("missing", "duplicate_id", "wrong_status"):
            report = _complete_report()
            if change == "missing":
                report["cases"].pop()
            elif change == "duplicate_id":
                report["cases"][1]["id"] = report["cases"][0]["id"]
            else:
                report["cases"][1]["verification"]["status"] = "analytical_equations_verified"
            with self.subTest(change=change), self.assertRaises(qualification.ControlError):
                qualification._accepted(report, "diagnostic_complete")

    def test_refusal_requires_a_diagnostic_and_expected_status(self):
        report = _complete_report()
        report.update(status="not_evaluable", cases=[], errors=[])
        with self.assertRaisesRegex(qualification.ControlError, "refusal_has_no_diagnostic"):
            qualification._accepted(report, "not_evaluable")
        with self.assertRaisesRegex(qualification.ControlError, "unexpected_outcome_or_claim"):
            qualification._accepted(_complete_report(), "not_evaluable")


class TestQualificationPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = _temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.step = self.root / "candidate.step"
        self.step.write_bytes(b"Synthetic byte fixture. Never parsed as geometry.")
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text("{}", encoding="utf-8")
        self.catalog_hash = _sha(self.catalog)
        self.kit = self.root / "catalog.zip"
        self.kit.write_bytes(b"Synthetic archive marker. Never opened as an archive.")
        packet_source = qualification.ROOT / "tasks/m3_crete/gantry_diagnostic.v0.1.json"
        self.packet_body = json.loads(packet_source.read_text(encoding="utf-8"))
        self.packet_body["catalog_contract_sha256"] = self.catalog_hash
        self.packet = self.root / "packet.json"
        self.bindings = self.root / "bindings.json"
        self.freeze_packet_and_binding()

    def freeze_packet_and_binding(self):
        _write(self.packet, self.packet_body)
        self.packet_hash = _sha(self.packet)
        self.binding_body = {"schema_version": "marb_m3_gantry_bindings.v0.1",
            "candidate_sha256": _sha(self.step), "packet_sha256": self.packet_hash,
            "beam_members": [{"id": "left", "source_id": "cbeam_4080", "solid_index": 0},
                             {"id": "right", "source_id": "cbeam_4080", "solid_index": 1}],
            "splice_insert": {"source_id": "vslot_2040", "solid_index": 2}}
        _write(self.bindings, self.binding_body)

    def run_preflight(self, output=None, **changes):
        return qualification.run_qualification(self.step, self.bindings, self.packet,
            changes.get("packet_hash", self.packet_hash), self.catalog,
            changes.get("catalog_hash", self.catalog_hash), self.kit,
            output or self.root / "qualification")

    def assert_preflight_refused(self, expected_code, **changes):
        with mock.patch.object(qualification.diagnostic, "analyze") as analyze, \
                mock.patch.object(qualification, "_solids") as native:
            report = self.run_preflight(**changes)
        analyze.assert_not_called()
        native.assert_not_called()
        self.assertEqual(report["status"], "qualification_rejected")
        self.assertIn(expected_code, " ".join(report["errors"]))
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["model_attempt"], False)
        self.assertEqual(report["physical_acceptance"], "not_evaluated")
        self.assertTrue((self.root / "qualification/qualification.json").is_file())
        return report

    def test_frozen_packet_and_catalog_hashes_are_required_before_analysis(self):
        self.assert_preflight_refused("frozen_input_hash_mismatch", packet_hash="f" * 64)
        with mock.patch.object(qualification.diagnostic, "analyze") as analyze:
            report = self.run_preflight(output=self.root / "catalog-refusal", catalog_hash="f" * 64)
        analyze.assert_not_called()
        self.assertIn("frozen_input_hash_mismatch", " ".join(report["errors"]))

    def test_only_prescribed_49n_packet_enters_operator_qualification(self):
        self.packet_body["case"]["force_n"] = 490.
        self.freeze_packet_and_binding()
        self.assert_preflight_refused("qualification_requires_prescribed_49n_packet")

    def test_packet_and_candidate_binding_authority_is_preserved(self):
        self.binding_body["candidate_sha256"] = "f" * 64
        _write(self.bindings, self.binding_body)
        self.assert_preflight_refused("candidate_binding_hash_mismatch")
        self.binding_body["candidate_sha256"] = _sha(self.step)
        self.binding_body["packet_sha256"] = "f" * 64
        _write(self.bindings, self.binding_body)
        with mock.patch.object(qualification.diagnostic, "analyze") as analyze:
            report = self.run_preflight(output=self.root / "binding-packet-refusal")
        analyze.assert_not_called()
        self.assertIn("binding_packet_hash_mismatch", " ".join(report["errors"]))

    def test_verified_snapshot_paths_and_bytes_reach_analysis(self):
        original_bytes = {path: path.read_bytes() for path in (self.step, self.bindings, self.packet, self.catalog, self.kit)}
        captured = []
        def stop_before_native(*args):
            captured.extend(args)
            raise qualification.ControlError("m3.gantry.qualification.test_stop_before_native")
        with mock.patch.object(qualification.diagnostic, "analyze", side_effect=stop_before_native), \
                mock.patch.object(qualification, "_solids") as native:
            report = self.run_preflight()
        native.assert_not_called()
        self.assertEqual(report["status"], "qualification_rejected")
        for copied, original in ((captured[0], self.step), (captured[1], self.bindings),
                                 (captured[2], self.packet), (captured[4], self.catalog), (captured[6], self.kit)):
            self.assertEqual(copied.parent, self.root / "qualification/frozen-inputs")
            self.assertNotEqual(copied, original)
            self.assertEqual(copied.read_bytes(), original_bytes[original])
            self.assertEqual(original.read_bytes(), original_bytes[original])

    def test_snapshot_identity_change_is_refused_before_native_analysis(self):
        original_read = Path.read_bytes
        def changed_read(path):
            return b"Changed bytes during snapshot." if path == self.step else original_read(path)
        with mock.patch.object(Path, "read_bytes", changed_read), \
                mock.patch.object(qualification.diagnostic, "analyze") as analyze, \
                mock.patch.object(qualification, "_solids") as native:
            report = self.run_preflight()
        analyze.assert_not_called()
        native.assert_not_called()
        self.assertIn("snapshot_identity_mismatch", " ".join(report["errors"]))
        self.assertEqual(self.step.read_bytes(), b"Synthetic byte fixture. Never parsed as geometry.")

    def test_existing_output_is_preserved_before_input_reads(self):
        target = self.root / "existing-output"
        target.mkdir()
        sentinel = target / "qualification.json"
        sentinel.write_text("preserved", encoding="utf-8")
        with mock.patch.object(qualification.boundary, "digest") as digest:
            with self.assertRaises(qualification.ControlError):
                self.run_preflight(output=target)
            digest.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserved")

    def test_public_repository_output_is_refused_before_input_reads(self):
        target = qualification.ROOT / "docs/m3-gantry-qualification-refused-test-output"
        self.assertFalse(target.exists())
        with mock.patch.object(qualification.boundary, "digest") as digest:
            with self.assertRaises(qualification.ControlError):
                self.run_preflight(output=target)
            digest.assert_not_called()
        self.assertFalse(target.exists())

    def test_unexpected_runtime_text_is_not_recorded(self):
        with mock.patch.object(qualification.diagnostic, "analyze", side_effect=OSError("sensitive-runtime-marker")):
            report = self.run_preflight()
        self.assertEqual(report["errors"], ["m3.gantry.qualification.runtime_or_io_failure"])
        self.assertNotIn("sensitive-runtime-marker", json.dumps(report))


class TestQualificationCli(unittest.TestCase):
    def test_cli_exception_diagnostic_never_echoes_supplied_runtime_text(self):
        args = ["--step", "candidate", "--bindings", "binding", "--packet", "packet",
                "--packet-sha256", "a" * 64, "--catalog-contract", "catalog",
                "--catalog-contract-sha256", "b" * 64, "--kit", "kit", "--out-dir", "output"]
        output = io.StringIO()
        with mock.patch.object(qualification, "run_qualification", side_effect=OSError("sensitive-marker")), redirect_stdout(output):
            self.assertEqual(qualification.main(args), 2)
        self.assertIn("output_or_runtime_refused", output.getvalue())
        self.assertNotIn("sensitive-marker", output.getvalue())

    def test_measurement_template_is_unfilled_and_does_not_authorize_physical_test(self):
        path = qualification.ROOT / "tasks/m3_crete/gantry_measurement_template.v0.1.json"
        template = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(template["status"], "draft_no_measurements")
        self.assertIs(template["physical_test_authorized"], False)
        self.assertEqual(template["physical_acceptance"], "not_evaluated")
        self.assertEqual(template["records"], [])
        self.assertIsNone(template["reviewed_physical_test_plan_sha256"])
        self.assertIsNone(template["reviewed_prediction_report_sha256"])
        self.assertTrue(all(value is None for value in template["hardware_identity"].values()))
        self.assertTrue(all(value is None for value in template["reviewed_test_conditions"].values()))


if __name__ == "__main__":
    unittest.main()
