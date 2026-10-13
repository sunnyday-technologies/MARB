"""Draft-contract and coverage checks using synthetic catalog fixtures only.

No gated assembly, authored M3 source, placement key or benchmark attempt is
included. Audit completion deliberately remains distinct from FEA admission.
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
import warnings
import zipfile

from grader import m3_structural_audit as audit

try:
    import cadquery as cq
except ImportError:
    cq = None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _temporary_directory():
    base = (audit.ROOT / "runs").resolve()
    base.relative_to(audit.ROOT.resolve())
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="m3-audit-test-", dir=base)


def _draft() -> dict:
    return {
        "schema_version": audit.SCHEMA,
        "task_id": "M3-CRETE",
        "revision": "control-r1",
        "status": "draft_unqualified",
        "archives": {"control.zip": "a" * 64},
        "catalog": {
            "stock": {"entry": "parts/stock.step", "sha256": "b" * 64,
                      "solid_count": 1, "role": "beam_stock", "section_y": [0, 1, 0],
                      "disposition": "Centroidal stock geometry control. No approved material or joint model."},
            "roller": {"entry": "parts/roller.step", "sha256": "c" * 64,
                       "solid_count": 2, "role": "rolling_contact", "section_y": None,
                       "disposition": "Two solids per occurrence. Contact load transfer remains missing."},
        },
        "requirements": {
            name: {"phase": phase, "status": "missing", "needed": "Independent engineering evidence.", "basis": []}
            for name, phase in audit.REQUIREMENTS.items()
        },
        "historical_controls": {"payload_n": 49, "lateral_n": 100,
                                "deflection_limit_mm": 0.5, "safety_factor": 1.65,
                                "adopted_for_M3": False},
    }


def _inventory(reference_sha256="d" * 64, stock=3, roller=4) -> dict:
    return {"schema_version": "marb_m3_private_inventory.v0.1", "reference_sha256": reference_sha256,
            "instances": {"stock": stock, "roller": roller}}


class TestM3StructuralDraft(unittest.TestCase):
    def test_explicit_draft_validates_without_numerical_admission(self):
        audit.validate_draft(_draft())

    def test_checked_in_m3_contract_remains_a_gap_record_under_the_strict_schema(self):
        path = audit.ROOT / "tasks" / "m3_crete" / "structural_contract.v0.1.json"
        contract = audit.boundary._load(path)
        audit.validate_draft(contract)
        self.assertEqual(contract["status"], "draft_unqualified")
        self.assertIs(contract["historical_controls"]["adopted_for_M3"], False)

    def test_status_cannot_assert_qualification_or_admission(self):
        for status in ("qualified", "validated", "analysis_admitted", "published", True, None):
            with self.subTest(status=status):
                contract = _draft()
                contract["status"] = status
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "unsupported_draft_or_status"):
                    audit.validate_draft(contract)

    def test_every_requirement_rejects_qualified_status(self):
        for name in audit.REQUIREMENTS:
            with self.subTest(requirement=name):
                contract = _draft()
                contract["requirements"][name]["status"] = "qualified"
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "draft_cannot_claim_qualification"):
                    audit.validate_draft(contract)

    def test_requirements_cannot_change_phase_or_omit_known_gaps(self):
        contract = _draft()
        contract["requirements"]["material_properties"]["phase"] = "physical"
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "draft_cannot_claim_qualification"):
            audit.validate_draft(contract)
        del contract["requirements"]["connections"]
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "qualification requirements"):
            audit.validate_draft(contract)

    def test_undeclared_material_or_section_claims_are_rejected(self):
        for key in ("material", "section", "material_certified", "load_capacity_n"):
            with self.subTest(key=key):
                contract = _draft()
                contract["catalog"]["stock"][key] = {"claim": "approved"}
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "catalog source fields"):
                    audit.validate_draft(contract)

    def test_historical_loads_cannot_be_adopted_by_the_draft(self):
        for value in (True, 1, "false", None):
            with self.subTest(value=value):
                contract = _draft()
                contract["historical_controls"]["adopted_for_M3"] = value
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "historical_controls_not_task_approval"):
                    audit.validate_draft(contract)

    def test_historical_controls_require_finite_positive_numbers(self):
        for value in (True, 0, -1, float("inf"), float("nan"), 10**1000):
            with self.subTest(value_type=type(value).__name__):
                contract = _draft()
                contract["historical_controls"]["payload_n"] = value
                with self.assertRaises(audit.boundary.AnalysisError):
                    audit.validate_draft(contract)

    def test_unknown_role_and_nonbeam_section_are_rejected(self):
        contract = _draft()
        contract["catalog"]["roller"]["role"] = "ignore_nonstructural"
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "unknown_structural_role"):
            audit.validate_draft(contract)
        contract = _draft()
        contract["catalog"]["roller"]["section_y"] = [0, 1, 0]
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "nonbeam_cannot_declare_beam_section"):
            audit.validate_draft(contract)

    def test_beam_stock_requires_one_solid_and_an_orthogonal_unit_axis(self):
        contract = _draft()
        contract["catalog"]["stock"]["solid_count"] = 2
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "beam_stock_requires_one_solid"):
            audit.validate_draft(contract)
        for vector in ([0, 0, 0], [0, 2, 0], [0, 2**-0.5, 2**-0.5], [0, True, 0]):
            with self.subTest(vector=vector):
                contract = _draft()
                contract["catalog"]["stock"]["section_y"] = vector
                with self.assertRaises(audit.boundary.AnalysisError):
                    audit.validate_draft(contract)

    def test_catalog_counts_are_literal_bounded_integers(self):
        for value in (True, 0, 17, 1.0, "1"):
            with self.subTest(value=value):
                contract = _draft()
                contract["catalog"]["stock"]["solid_count"] = value
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "invalid_source_solid_count"):
                    audit.validate_draft(contract)

    def test_duplicate_catalog_entry_is_rejected(self):
        contract = _draft()
        contract["catalog"]["roller"]["entry"] = contract["catalog"]["stock"]["entry"]
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "duplicate_catalog_entry"):
            audit.validate_draft(contract)

    def test_catalog_entries_and_archive_names_cannot_escape_catalog(self):
        for entry in ("../stock.step", "/stock.step", "parts//stock.step", "parts/./stock.step",
                      "parts\\stock.step", "C:/stock.step", "parts/stock.txt"):
            with self.subTest(entry=entry):
                contract = _draft()
                contract["catalog"]["stock"]["entry"] = entry
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "invalid_catalog_entry"):
                    audit.validate_draft(contract)
        for name in ("../control.zip", "C:control.zip", "a\\control.zip", "control.step"):
            with self.subTest(name=name):
                contract = _draft()
                contract["archives"] = {name: "a" * 64}
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "invalid_archive_name"):
                    audit.validate_draft(contract)

    def test_catalog_hashes_require_exact_lowercase_sha256(self):
        for value in ("g" * 64, "b" * 63, "B" * 64, None):
            with self.subTest(value=value):
                contract = _draft()
                contract["catalog"]["stock"]["sha256"] = value
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "invalid input digest"):
                    audit.validate_draft(contract)


class TestM3CatalogAndPrivateInventory(unittest.TestCase):
    def setUp(self):
        self.temp = _temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "control.zip"
        self.contract = _draft()
        self.entries = [("parts/stock.step", b"synthetic-stock-content"),
                        ("parts/roller.step", b"synthetic-roller-content")]
        self.write_archive(self.entries)
        for source, (_, content) in zip(self.contract["catalog"].values(), self.entries):
            source["sha256"] = hashlib.sha256(content).hexdigest()

    def write_archive(self, entries):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as stream:
                for name, content in entries:
                    stream.writestr(name, content)
        self.contract["archives"] = {"control.zip": _sha(self.archive)}

    def test_exact_frozen_step_catalog_and_nonstep_readme_are_accepted(self):
        self.write_archive(self.entries + [("README.txt", b"Unranked fixture.")])
        result = audit._catalog_bytes(self.archive, self.contract)
        self.assertEqual(result, {"stock": self.entries[0][1], "roller": self.entries[1][1]})

    def test_archive_hash_is_independently_frozen(self):
        self.contract["archives"]["control.zip"] = "0" * 64
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "archive_not_frozen"):
            audit._catalog_bytes(self.archive, self.contract)

    def test_changed_authored_source_cannot_be_hidden_by_refreezing_archive(self):
        self.write_archive([(self.entries[0][0], b"changed-content"), self.entries[1]])
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "authored_source_hash_mismatch"):
            audit._catalog_bytes(self.archive, self.contract)

    def test_missing_or_extra_step_entry_is_rejected(self):
        for entries in (self.entries[:1], self.entries + [("parts/extra.STEP", b"extra")]):
            with self.subTest(entries=len(entries)):
                self.write_archive(entries)
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "archive_step_coverage_mismatch"):
                    audit._catalog_bytes(self.archive, self.contract)

    def test_duplicate_archive_name_is_rejected_before_reading_content(self):
        self.write_archive(self.entries + [self.entries[0]])
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "archive_entries_invalid"):
            audit._catalog_bytes(self.archive, self.contract)

    def test_archive_entry_count_and_empty_catalog_content_are_bounded(self):
        self.write_archive(self.entries + [(f"note-{i}.txt", b"x") for i in range(audit.MAX_ENTRIES)])
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "archive_entries_invalid"):
            audit._catalog_bytes(self.archive, self.contract)
        self.write_archive([(self.entries[0][0], b""), self.entries[1]])
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "catalog_bytes_out_of_bounds"):
            audit._catalog_bytes(self.archive, self.contract)

    def test_malformed_frozen_archive_has_a_safe_error(self):
        self.archive.write_bytes(b"not a zip, fixture-only marker")
        self.contract["archives"] = {"control.zip": _sha(self.archive)}
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "archive_read_failed"):
            audit._catalog_bytes(self.archive, self.contract)

    def test_inventory_counts_occurrences_separately_from_step_solids(self):
        summary = audit.validate_inventory(_inventory(), self.contract, "d" * 64)
        self.assertEqual(summary["instances"], 7)
        self.assertEqual(summary["expected_solids"], 11)
        self.assertEqual(summary["beam_instances"], 3)
        self.assertEqual(summary["nonbeam_instances"], 4)
        self.assertEqual(summary["role_instances"], {"beam_stock": 3, "rolling_contact": 4})
        self.assertIs(summary["part_identity_and_placement_verified"], False)

    def test_inventory_requires_every_source_and_rejects_extras(self):
        for counts in ({"stock": 3}, {"stock": 3, "roller": 4, "extra": 1}):
            with self.subTest(counts=counts):
                inventory = _inventory()
                inventory["instances"] = counts
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "inventory_must_account_for_every_source"):
                    audit.validate_inventory(inventory, self.contract, "d" * 64)

    def test_inventory_reference_digest_cannot_be_substituted(self):
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "inventory_reference_hash_mismatch"):
            audit.validate_inventory(_inventory(), self.contract, "e" * 64)

    def test_inventory_counts_and_total_solid_budget_are_bounded(self):
        for value in (True, -1, 257, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "invalid_instance_count"):
                    audit.validate_inventory(_inventory(stock=value), self.contract, "d" * 64)
        for stock, roller in ((0, 0), (129, 64), (200, 100)):
            with self.subTest(stock=stock, roller=roller):
                with self.assertRaisesRegex(audit.boundary.AnalysisError, "inventory_out_of_bounds"):
                    audit.validate_inventory(_inventory(stock=stock, roller=roller), self.contract, "d" * 64)

    def test_private_inventory_cannot_include_placement_fields(self):
        inventory = _inventory()
        inventory["positions"] = [[1, 2, 3]]
        with self.assertRaisesRegex(audit.boundary.AnalysisError, "private inventory fields"):
            audit.validate_inventory(inventory, self.contract, "d" * 64)


@unittest.skipIf(cq is None, "CadQuery runtime unavailable")
class TestM3AuditIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = _temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.contract = _draft()
        self.contract_path = self.root / "contract.json"
        self.archive = self.root / "control.zip"
        self.inventory_path = self.root / "inventory.json"
        self.reference = self.root / "reference.step"
        self.stock = cq.Workplane("XY").box(200, 10, 20).val()
        # Two solids constitute one occurrence of this nonbeam control source.
        compact = cq.Workplane("XY").box(5, 6, 7).val()
        self.roller = cq.Compound.makeCompound([compact, compact.translate((0, 15, 0))])
        with zipfile.ZipFile(self.archive, "w") as stream:
            for source_id, shape in (("stock", self.stock), ("roller", self.roller)):
                path = self.root / (source_id + ".step")
                cq.exporters.export(shape, str(path))
                source = self.contract["catalog"][source_id]
                source["sha256"] = _sha(path)
                stream.writestr(source["entry"], path.read_bytes())
        self.contract["archives"] = {"control.zip": _sha(self.archive)}
        _write_json(self.contract_path, self.contract)
        assembly = cq.Compound.makeCompound([self.stock, self.roller.translate((0, 40, 30)),
                                            self.roller.translate((0, 80, 60))])
        cq.exporters.export(assembly, str(self.reference))
        _write_json(self.inventory_path, _inventory(_sha(self.reference), stock=1, roller=2))
        self.version_mock = mock.patch.object(audit.boundary.importlib.metadata, "version",
                                             side_effect=lambda name: audit.boundary.PINNED[name])
        self.version_mock.start()
        self.addCleanup(self.version_mock.stop)

    def run_audit(self, *, private=False, **overrides):
        kwargs = {"contract_path": self.contract_path, "expected_contract_hash": _sha(self.contract_path),
                  "archive": self.archive, "workspace": self.workspace}
        if private:
            kwargs.update(inventory_path=self.inventory_path, expected_inventory_hash=_sha(self.inventory_path),
                          reference=self.reference, expected_reference_hash=_sha(self.reference))
        kwargs.update(overrides)
        return audit.audit(**kwargs)

    def assert_rejected(self, report, error):
        self.assertEqual(report["status"], "audit_rejected")
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["leaderboard_eligible"], False)
        self.assertEqual(report["errors"], [error])

    def cli(self, out, extra=()):
        return ["--contract", str(self.contract_path), "--contract-sha256", _sha(self.contract_path),
                "--kit", str(self.archive), "--out-dir", str(out), *extra]

    def test_actual_stock_inspector_and_nonbeam_multisolid_catalog_complete_only_audit(self):
        with mock.patch.object(audit, "inspect_stock_section", wraps=audit.inspect_stock_section) as inspector:
            report = self.run_audit(private=True)
        self.assertEqual(inspector.call_count, 1)
        self.assertEqual(report["status"], "audit_complete_admission_blocked")
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["leaderboard_eligible"], False)
        self.assertIs(report["whole_machine_structural_coverage"], False)
        self.assertIs(report["legacy_scores_changed"], False)
        self.assertEqual(report["physical_acceptance"], "not_evaluated")
        self.assertEqual(report["catalog_summary"], {"files": 2, "solids": 3, "beam_profiles": 1})
        self.assertEqual(report["private_inventory"]["instances"], 3)
        self.assertEqual(report["private_inventory"]["expected_solids"], 5)
        self.assertEqual(report["private_inventory"]["actual_solids"], 5)
        self.assertIs(report["private_inventory"]["part_identity_and_placement_verified"], False)
        self.assertIs(report["sources"]["stock"]["beam_geometry_verified"], True)
        self.assertIs(report["sources"]["roller"]["beam_geometry_verified"], False)
        for source in report["sources"].values():
            self.assertIs(source["numerical_model_present"], False)
        geometry = report["sources"]["stock"]["geometry"]
        self.assertAlmostEqual(geometry["section"]["A_m2"], 200e-6)
        self.assertAlmostEqual(geometry["section"]["Iy_m4"], 10 * 20**3 / 12 * 1e-12)
        self.assertEqual(set(report["blockers"]["analysis"]),
                         {name for name, phase in audit.REQUIREMENTS.items() if phase == "analysis"})
        # The only positions in this report describe catalog stock geometry.
        # No reference assembly node, transform or placement is serialized.
        private = json.dumps(report["private_inventory"])
        for forbidden in ("positions", "centroid", "translation", "rotation", "nodes", "members"):
            self.assertNotIn(forbidden, private)
        for forbidden in ("reference_positions", "reference_nodes", "reference_transforms"):
            self.assertNotIn(forbidden, json.dumps(report))

    def test_catalog_only_audit_does_not_claim_private_inventory_verification(self):
        report = self.run_audit()
        self.assertEqual(report["status"], "audit_complete_admission_blocked")
        self.assertNotIn("private_inventory", report)
        self.assertIs(report["analysis_admitted"], False)

    def test_contract_runtime_and_declared_solid_count_fail_closed(self):
        self.assert_rejected(self.run_audit(expected_contract_hash="0" * 64), "m3.audit.contract_hash_mismatch")
        with mock.patch.object(audit.boundary.importlib.metadata, "version", return_value="999.0"):
            self.assert_rejected(self.run_audit(), "m3.audit.runtime_version_mismatch")
        self.contract["catalog"]["roller"]["solid_count"] = 1
        _write_json(self.contract_path, self.contract)
        self.assert_rejected(self.run_audit(), "m3.audit.source_solid_count_mismatch")

    def test_geometry_failure_has_bounded_diagnostic(self):
        self.contract["catalog"]["stock"]["section_y"] = [1, 0, 0]
        _write_json(self.contract_path, self.contract)
        self.assert_rejected(self.run_audit(), "fea.geometry.invalid_section_orientation")

    def test_private_inventory_requires_all_four_frozen_inputs(self):
        inputs = {"inventory_path": self.inventory_path, "expected_inventory_hash": _sha(self.inventory_path),
                  "reference": self.reference, "expected_reference_hash": _sha(self.reference)}
        for missing in inputs:
            with self.subTest(missing=missing):
                incomplete = dict(inputs)
                incomplete.pop(missing)
                self.assert_rejected(self.run_audit(**incomplete),
                                     "m3.audit.private_inventory_requires_all_frozen_inputs")

    def test_private_inventory_and_reference_hashes_are_independently_checked(self):
        self.assert_rejected(self.run_audit(private=True, expected_inventory_hash="0" * 64),
                             "m3.audit.inventory_hash_mismatch")
        bad_reference_hash = "e" * 64
        _write_json(self.inventory_path, _inventory(bad_reference_hash, stock=1, roller=2))
        self.assert_rejected(self.run_audit(private=True, expected_reference_hash=bad_reference_hash),
                             "m3.audit.reference_hash_mismatch")

    def test_private_inventory_solid_total_is_compared_with_actual_step(self):
        _write_json(self.inventory_path, _inventory(_sha(self.reference), stock=1, roller=1))
        self.assert_rejected(self.run_audit(private=True), "m3.audit.reference_inventory_solid_count_mismatch")

    def test_reference_and_inventory_changes_during_inspection_are_rejected(self):
        original_solids = audit._solids
        original_reference = self.reference.read_bytes()
        original_inventory = self.inventory_path.read_bytes()
        for target, error in ((self.reference, "m3.audit.reference_changed_during_audit"),
                              (self.inventory_path, "m3.audit.inventory_changed_during_audit")):
            with self.subTest(target=target.name):
                self.reference.write_bytes(original_reference)
                self.inventory_path.write_bytes(original_inventory)

                def mutate_after_reference_read(path):
                    solids = original_solids(path)
                    if path == self.reference:
                        target.write_bytes(target.read_bytes() + b"\n")
                    return solids

                with mock.patch.object(audit, "_solids", side_effect=mutate_after_reference_read):
                    self.assert_rejected(self.run_audit(private=True), error)

    def test_contract_or_archive_changed_after_catalog_read_is_rejected(self):
        original_solids = audit._solids
        original_contract = self.contract_path.read_bytes()
        original_archive = self.archive.read_bytes()
        for target in (self.contract_path, self.archive):
            with self.subTest(target=target.name):
                self.contract_path.write_bytes(original_contract)
                self.archive.write_bytes(original_archive)
                mutated = False

                def mutate_after_catalog_read(path):
                    nonlocal mutated
                    solids = original_solids(path)
                    if not mutated:
                        target.write_bytes(target.read_bytes() + b"\n")
                        mutated = True
                    return solids

                with mock.patch.object(audit, "_solids", side_effect=mutate_after_catalog_read):
                    self.assert_rejected(self.run_audit(), "m3.audit.input_changed_during_audit")

    def test_runtime_exception_does_not_echo_paths_or_native_exception_text(self):
        with mock.patch.object(audit, "_solids", side_effect=RuntimeError("private fixture path: sensitive-marker")):
            report = self.run_audit()
        self.assert_rejected(report, "m3.audit.runtime_or_io_failure")
        self.assertNotIn("sensitive-marker", json.dumps(report))
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_cli_existing_output_directory_preserves_input_and_previous_evidence(self):
        output = self.root / "existing-output"
        output.mkdir()
        sentinel = output / "coverage.json"
        sentinel.write_bytes(b"previous-evidence")
        before = {path: path.read_bytes() for path in (self.contract_path, self.archive, sentinel)}
        stream = io.StringIO()
        with redirect_stdout(stream), mock.patch.object(audit, "audit") as run:
            status = audit.main(self.cli(output))
        self.assertEqual(status, 2)
        run.assert_not_called()
        self.assertEqual(stream.getvalue().strip(), "m3.audit.output_directory_unavailable")
        for path, value in before.items():
            self.assertEqual(path.read_bytes(), value)

    def test_cli_output_aliasing_input_file_is_rejected_before_any_write(self):
        before = self.contract_path.read_bytes()
        with redirect_stdout(io.StringIO()), mock.patch.object(audit, "audit") as run:
            status = audit.main(self.cli(self.contract_path))
        self.assertEqual(status, 2)
        run.assert_not_called()
        self.assertEqual(self.contract_path.read_bytes(), before)

    def test_cli_success_means_completed_audit_with_admission_still_blocked(self):
        output = self.root / "new-output"
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = audit.main(self.cli(output))
        self.assertEqual(status, 0)
        self.assertEqual(stream.getvalue().strip(), "audit_complete_admission_blocked")
        report = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["leaderboard_eligible"], False)
        self.assertEqual(report["physical_acceptance"], "not_evaluated")

    def test_cli_report_write_failure_has_a_bounded_diagnostic(self):
        output = self.root / "write-failed-output"
        stream = io.StringIO()
        arguments = self.cli(output)
        with redirect_stdout(stream), mock.patch.object(audit, "audit", return_value={}), \
                mock.patch.object(Path, "open", side_effect=PermissionError("private-path-marker")):
            status = audit.main(arguments)
        self.assertEqual(status, 2)
        self.assertEqual(stream.getvalue().strip(), "m3.audit.output_write_failed")
        self.assertNotIn("private-path-marker", stream.getvalue())

    def test_cli_failure_preserves_inputs_and_writes_safe_rejected_report(self):
        output = self.root / "rejected-output"
        before = self.contract_path.read_bytes()
        self.contract["status"] = "qualified"
        _write_json(self.contract_path, self.contract)
        supplied = self.contract_path.read_bytes()
        with redirect_stdout(io.StringIO()):
            status = audit.main(self.cli(output))
        self.assertEqual(status, 2)
        report = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assert_rejected(report, "m3.audit.unsupported_draft_or_status")
        self.assertEqual(self.contract_path.read_bytes(), supplied)
        self.assertNotEqual(before, supplied)
        self.assertNotIn(str(self.root), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
