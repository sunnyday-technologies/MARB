"""Frozen-input and synthetic-geometry controls for the gantry diagnostic.

The geometry fixtures are simple analytical stock. They contain no authored M3
parts, gated reference placements, or benchmark attempts. Passing these tests
does not qualify a physical gantry or clear the packet's engineering gaps.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from grader import m3_gantry_diagnostic as diagnostic
from grader import gantry_bending as bending

try:
    import cadquery as cq
    HAS_NATIVE = all(importlib.metadata.version(name) == version
                     for name, version in diagnostic.boundary.PINNED.items())
except (ImportError, importlib.metadata.PackageNotFoundError):
    cq = None
    HAS_NATIVE = False

try:
    HAS_NUMPY = importlib.metadata.version("numpy") == bending.NUMPY_VERSION
except importlib.metadata.PackageNotFoundError:
    HAS_NUMPY = False


def _packet(catalog_hash="a" * 64):
    return {
        "schema_version": diagnostic.SCHEMA, "task_id": "M3-CRETE",
        "revision": "synthetic-control-r1", "status": "nominal_diagnostic",
        "catalog_contract_sha256": catalog_hash,
        "case": {"force_n": 49., "vertical_axis": 2, "load_position": "stock_splice",
                 "gravity_m_s2": 9.80665, "include_stock_self_weight": True,
                 "self_weight_scope": "two_main_stock_members_only",
                 "basis": "Synthetic prescribed-force control. No measured payload."},
        "nominal_material": {"E_pa": 68.9e9, "rho_kg_m3": 2700., "status": "assumed",
                             "basis": ["Synthetic control material, not approved M3 material."]},
        "support_model": {"kind": "vertical_end_restraints_free_rotation",
                          "normalized_vertical_stiffness": [10., 100.],
                          "status": "hypotheses_not_measured"},
        "splice_model": {"kind": "relative_rotation_sensitivity",
                         "normalized_rotation_stiffness": [0., .1, 1., 10.],
                         "rigid_limit": True, "insert_action": "geometry_witness_only",
                         "vertical_transfer": "shared_displacement_assumption",
                         "status": "hypotheses_not_measured"},
        "scope": {"beam_source_id": "cbeam_4080", "insert_source_id": "vslot_2040",
                  "projection": "uncoupled_centroidal_principal_axis_bending",
                  "geometry_closure_mm": .01, "partial_assembly": True},
        "evidence_gaps": {name: "unqualified" for name in diagnostic.GAPS},
    }


def _bindings(packet_hash="b" * 64, candidate_hash="c" * 64):
    return {"schema_version": "marb_m3_gantry_bindings.v0.1", "candidate_sha256": candidate_hash,
            "packet_sha256": packet_hash,
            "beam_members": [{"id": "left-stock", "source_id": "cbeam_4080", "solid_index": 0},
                             {"id": "right-stock", "source_id": "cbeam_4080", "solid_index": 1}],
            "splice_insert": {"source_id": "vslot_2040", "solid_index": 2}}


def _temporary_directory():
    base = (diagnostic.ROOT / "runs").resolve()
    base.relative_to(diagnostic.ROOT.resolve())
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="m3-gantry-test-", dir=base)


def _write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _analytical_geometry():
    return {"span_m": 2., "half_span_m": 1., "I_m4": 1e-8, "S_m3": 1e-6, "A_m2": .0012}


def _analytical_packet():
    packet = _packet()
    packet["case"].update(force_n=48., gravity_m_s2=10.)
    packet["nominal_material"].update(E_pa=200e9, rho_kg_m3=1000.)
    return packet


class TestNominalGantryPacket(unittest.TestCase):
    def test_valid_packet_and_checked_in_packet_keep_every_gap_unqualified(self):
        diagnostic.validate_packet(_packet())
        checked_in = diagnostic.boundary._load(diagnostic.ROOT / "tasks/m3_crete/gantry_diagnostic.v0.1.json")
        diagnostic.validate_packet(checked_in)
        self.assertEqual(checked_in["status"], "nominal_diagnostic")
        self.assertEqual(set(checked_in["evidence_gaps"].values()), {"unqualified"})

    def test_packet_rejects_readiness_material_support_and_composite_claims(self):
        changes = ((None, "status", "qualified"), ("nominal_material", "status", "qualified"),
                   ("support_model", "status", "measured"), ("splice_model", "status", "measured"),
                   ("splice_model", "insert_action", "perfect_composite"),
                   ("scope", "partial_assembly", False), ("scope", "projection", "physical_torsion_model"))
        for area, field, value in changes:
            packet = _packet()
            (packet if area is None else packet[area])[field] = value
            with self.subTest(area=area, field=field), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.validate_packet(packet)
        for name in diagnostic.GAPS:
            packet = _packet()
            packet["evidence_gaps"][name] = "qualified"
            with self.subTest(gap=name), self.assertRaisesRegex(diagnostic.boundary.AnalysisError, "cannot_clear"):
                diagnostic.validate_packet(packet)

    def test_unknown_yield_service_and_torsion_fields_are_refused(self):
        for area, field in ((None, "deflection_limit_mm"), ("nominal_material", "yield_pa"),
                            ("nominal_material", "J_m4"), ("scope", "service_limits")):
            packet = _packet()
            (packet if area is None else packet[area])[field] = 1.
            with self.subTest(field=field), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.validate_packet(packet)

    def test_force_gravity_modulus_density_and_axis_bounds_are_strict(self):
        for area, field, bad in (("case", "force_n", 0.), ("case", "force_n", 100001.),
                                ("case", "force_n", True), ("case", "gravity_m_s2", 101.),
                                ("nominal_material", "E_pa", 1.), ("nominal_material", "rho_kg_m3", 30001.),
                                ("case", "vertical_axis", True), ("case", "vertical_axis", 3),
                                ("case", "force_n", float("nan"))):
            packet = _packet()
            packet[area][field] = bad
            with self.subTest(field=field, kind=type(bad).__name__), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.validate_packet(packet)

    def test_stiffness_hypotheses_require_free_control_unique_finite_parameters(self):
        for area, field, value in (("splice_model", "normalized_rotation_stiffness", [.1, 1.]),
                                   ("splice_model", "normalized_rotation_stiffness", [0., -.1]),
                                   ("splice_model", "normalized_rotation_stiffness", [0., 1., 1.]),
                                   ("support_model", "normalized_vertical_stiffness", [0.]),
                                   ("support_model", "normalized_vertical_stiffness", [float("inf")]),
                                   ("support_model", "normalized_vertical_stiffness", [1.] * 9)):
            packet = _packet()
            packet[area][field] = value
            with self.subTest(area=area, value_type=type(value).__name__), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.validate_packet(packet)


class TestGantryBindings(unittest.TestCase):
    def test_valid_binding_and_swapped_member_order_validate(self):
        bindings = _bindings()
        diagnostic.validate_bindings(bindings, _packet(), "b" * 64)
        bindings["beam_members"].reverse()
        diagnostic.validate_bindings(bindings, _packet(), "b" * 64)

    def test_frozen_packet_hash_and_binding_shape_are_enforced(self):
        bindings = _bindings()
        with self.assertRaisesRegex(diagnostic.boundary.AnalysisError, "binding_packet_hash_mismatch"):
            diagnostic.validate_bindings(bindings, _packet(), "d" * 64)
        for count in (0, 1, 3):
            bindings = _bindings()
            bindings["beam_members"] = bindings["beam_members"][:count] if count < 2 else bindings["beam_members"] * 2
            with self.subTest(count=count), self.assertRaisesRegex(diagnostic.boundary.AnalysisError, "two_stock_members_required"):
                diagnostic.validate_bindings(bindings, _packet(), "b" * 64)

    def test_duplicate_indices_ids_wrong_sources_and_boolean_indices_are_refused(self):
        for change in ("duplicate_index", "insert_index", "duplicate_id", "beam_source", "insert_source", "boolean", "out_of_bounds"):
            bindings = _bindings()
            if change == "duplicate_index":
                bindings["beam_members"][1]["solid_index"] = 0
            elif change == "insert_index":
                bindings["splice_insert"]["solid_index"] = 1
            elif change == "duplicate_id":
                bindings["beam_members"][1]["id"] = "left-stock"
            elif change == "beam_source":
                bindings["beam_members"][0]["source_id"] = "vslot_2040"
            elif change == "insert_source":
                bindings["splice_insert"]["source_id"] = "cbeam_4080"
            else:
                bindings["beam_members"][0]["solid_index"] = True if change == "boolean" else 256
            with self.subTest(change=change), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.validate_bindings(bindings, _packet(), "b" * 64)


class TestIndependentGantryFormulae(unittest.TestCase):
    def test_center_point_plus_uniform_weight_has_known_displacement_moment_and_reaction(self):
        result = diagnostic.analytical_case(_analytical_geometry(), _analytical_packet(), "rigid", "rigid", True)
        self.assertAlmostEqual(result["midpoint_relative_to_support_chord_mm"], 5.25, places=12)
        self.assertAlmostEqual(result["max_bending_moment_nm"], 30., places=12)
        self.assertAlmostEqual(result["peak_bending_stress_pa"], 30e6, places=6)
        self.assertAlmostEqual(result["support_reaction_n"], 36., places=12)

    def test_finite_rotation_and_support_compliance_add_known_contributions(self):
        result = diagnostic.analytical_case(_analytical_geometry(), _analytical_packet(), 1., 10., True)
        self.assertAlmostEqual(result["midpoint_relative_to_support_chord_mm"], 20.25, places=12)
        self.assertAlmostEqual(result["end_support_displacement_mm"], 14.4, places=12)
        self.assertAlmostEqual(result["midpoint_absolute_mm"], 34.65, places=12)

    def test_force_only_and_free_splice_do_not_acquire_physical_acceptance(self):
        result = diagnostic.analytical_case(_analytical_geometry(), _analytical_packet(), "rigid", "rigid", False)
        self.assertAlmostEqual(result["midpoint_absolute_mm"], 4., places=12)
        self.assertEqual(diagnostic.analytical_case(_analytical_geometry(), _analytical_packet(), 0., "rigid", True),
                         {"status": "mechanism"})
        model = diagnostic.make_model(_analytical_geometry(), _analytical_packet(), "rigid", "rigid", True)
        self.assertEqual(set(model), {"nodes_m", "elements", "supports", "rotation_couplings", "point_loads", "point_moments"})
        self.assertTrue(all(set(e) == {"id", "i", "j", "E_pa", "I_m4", "S_m3", "distributed_load_n_m"}
                            for e in model["elements"]))
        self.assertNotIn("yield_pa", json.dumps(model))
        self.assertNotIn("J_m4", json.dumps(model))

    def test_small_deflection_review_never_qualifies_a_physical_linear_domain(self):
        outcome = {"status": "diagnostic_complete", "max_deflection_mm": 1.,
                   "element_end_rotations_rad": {"left": {"left": .001, "right": 0.}}}
        review = diagnostic.linear_review(outcome, 2.)
        self.assertEqual(review["status"], "within_review_triggers")
        self.assertIs(review["physical_domain_qualified"], False)
        outcome["element_end_rotations_rad"]["left"]["left"] = .1
        review = diagnostic.linear_review(outcome, 2.)
        self.assertEqual(review["status"], "review_required")
        self.assertIs(review["physical_domain_qualified"], False)
        self.assertIs(diagnostic.linear_review({"status": "not_evaluable"}, 2.)["physical_domain_qualified"], False)


@unittest.skipUnless(HAS_NUMPY, "pinned NumPy diagnostic runtime is unavailable")
class TestBackendEvidenceBoundary(unittest.TestCase):
    def control(self):
        geometry, packet = _analytical_geometry(), _analytical_packet()
        expected = diagnostic.analytical_case(geometry, packet, "rigid", "rigid", True)
        outcome = bending.solve_bending(diagnostic.make_model(geometry, packet, "rigid", "rigid", True))
        self.assertEqual(outcome["status"], "diagnostic_complete", outcome.get("errors"))
        return outcome, expected

    def test_real_calibrated_backend_evidence_is_accepted(self):
        outcome, expected = self.control()
        result = diagnostic.verify_scenario(outcome, expected)
        self.assertEqual(result["status"], "analytical_equations_verified")

    def test_changed_displacements_moments_stresses_and_coverage_are_refused(self):
        original, expected = self.control()
        for change in ("midpoint", "moment", "stress", "missing_element", "extra_element", "missing_node", "nonfinite",
                       "peak_displacement", "individual_reaction", "splice_jump", "spring_moment", "end_rotation"):
            outcome = deepcopy(original)
            if change == "midpoint":
                outcome["node_deflections_mm"][1] *= 2
            elif change == "moment":
                outcome["element_results"]["left"]["max_abs_bending_moment_nm"] *= 2
            elif change == "stress":
                outcome["element_results"]["right"]["peak_bending_stress_pa"] *= 2
            elif change == "missing_element":
                del outcome["element_results"]["left"]
            elif change == "extra_element":
                outcome["element_results"]["invented"] = outcome["element_results"]["left"]
            elif change == "missing_node":
                outcome["node_deflections_mm"].pop()
            elif change == "peak_displacement":
                outcome["max_deflection_mm"] *= 2
            elif change == "individual_reaction":
                outcome["support_reactions"][0]["force_n"] += 1.
            elif change == "splice_jump":
                outcome["rotation_jumps_rad"]["stock_splice"] += .001
            elif change == "spring_moment":
                outcome["spring_moments_nm"]["stock_splice"] += 1.
            elif change == "end_rotation":
                outcome["element_end_rotations_rad"]["left"]["left"] += .001
            else:
                outcome["node_deflections_mm"][1] = float("nan")
            with self.subTest(change=change), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.verify_scenario(outcome, expected)

    def test_forged_applied_load_reactions_residuals_and_tolerances_are_refused(self):
        original, expected = self.control()
        for key, value in (("applied_force_n", -1.), ("reaction_force_n", 1.),
                           ("force_residual_n", 1.), ("moment_residual_nm", 1.),
                           ("force_tolerance_n", 1e30), ("moment_tolerance_nm", 1e30),
                           ("verified", False)):
            outcome = deepcopy(original)
            outcome["equilibrium"][key] = value
            with self.subTest(key=key), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.verify_scenario(outcome, expected)

    def test_mechanism_refuses_fake_complete_solution(self):
        outcome, _ = self.control()
        with self.assertRaisesRegex(diagnostic.boundary.AnalysisError, "mechanism_not_refused"):
            diagnostic.verify_scenario(outcome, {"status": "mechanism"})

    def test_backend_cannot_claim_physical_or_leaderboard_acceptance(self):
        original, expected = self.control()
        for key, value in (("analysis_admitted", True), ("leaderboard_eligible", True),
                           ("physical_acceptance", "accepted"), ("legacy_scores_changed", True)):
            outcome = deepcopy(original)
            outcome[key] = value
            with self.subTest(key=key), self.assertRaises(diagnostic.boundary.AnalysisError):
                diagnostic.verify_scenario(outcome, expected)


@unittest.skipUnless(HAS_NATIVE, "pinned native stock runtime is unavailable")
class TestSyntheticGantryGeometry(unittest.TestCase):
    def test_cli_refuses_public_repository_output_before_reading_inputs(self):
        destination = diagnostic.ROOT / "docs" / "gantry-private-output-control"
        with redirect_stdout(io.StringIO()), mock.patch.object(diagnostic, "analyze") as analyze:
            code = diagnostic.main(["--step", "missing.step", "--bindings", "missing.json",
                                    "--packet", "missing-packet.json", "--packet-sha256", "a"*64,
                                    "--catalog-contract", "missing-catalog.json",
                                    "--catalog-contract-sha256", "b"*64, "--kit", "missing.zip",
                                    "--out-dir", str(destination)])
        self.assertEqual(code, 2)
        analyze.assert_not_called()
        self.assertFalse(destination.exists())

    @classmethod
    def setUpClass(cls):
        cls.source_temp = _temporary_directory()
        cls.source_root = Path(cls.source_temp.name)
        # Independent tube and insert prisms. They are analytical fixtures.
        cls.tube = cq.Workplane("XY").rect(40., 80.).rect(32., 72.).extrude(1000.).val()
        cls.insert = cq.Solid.makeBox(32., 20., 1000., cq.Vector(-16., -10., 0.))
        cls.source_bytes = {}
        for source_id, shape in (("cbeam_4080", cls.tube), ("vslot_2040", cls.insert), ("vslot_2080", cls.insert)):
            path = cls.source_root / (source_id + ".step")
            cq.exporters.export(shape, str(path))
            cls.source_bytes[source_id] = path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.source_temp.cleanup()

    def setUp(self):
        self.temp = _temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "synthetic-kit.zip"
        with zipfile.ZipFile(self.archive, "x") as stream:
            for source_id, data in self.source_bytes.items():
                stream.writestr("stock/" + source_id + ".step", data)
        self.catalog = {"schema_version": diagnostic.catalog_audit.SCHEMA, "task_id": "M3-CRETE",
            "revision": "synthetic-r1", "status": "draft_unqualified",
            "archives": {self.archive.name: _sha(self.archive)},
            "catalog": {sid: {"entry": "stock/" + sid + ".step", "sha256": hashlib.sha256(data).hexdigest(),
                "solid_count": 1, "role": "beam_stock", "section_y": [1., 0., 0.],
                "disposition": "Analytical synthetic stock. No qualified physical inputs."}
                for sid, data in self.source_bytes.items()},
            "requirements": {name: {"phase": phase, "status": "missing", "needed": "Independent physical evidence.", "basis": []}
                for name, phase in diagnostic.catalog_audit.REQUIREMENTS.items()},
            "historical_controls": {"payload_n": 49., "lateral_n": 100., "deflection_limit_mm": .5,
                                    "safety_factor": 1.65, "adopted_for_M3": False}}
        self.catalog_path = self.root / "catalog.json"
        _write_json(self.catalog_path, self.catalog)
        self.catalog_hash = _sha(self.catalog_path)
        self.packet = _packet(self.catalog_hash)
        self.packet_path = self.root / "packet.json"
        _write_json(self.packet_path, self.packet)
        self.packet_hash = _sha(self.packet_path)
        self.candidate = self.root / "candidate.step"
        self.bindings_path = self.root / "bindings.json"
        self.shapes = [self.tube.rotate((0, 0, 0), (0, 1, 0), 90).translate((100., 200., 300.)),
                       self.tube.rotate((0, 0, 0), (0, 1, 0), 90).translate((1100., 200., 300.)),
                       self.insert.rotate((0, 0, 0), (0, 1, 0), 90).translate((600., 200., 300.))]
        self.export_candidate()

    def export_candidate(self):
        cq.exporters.export(cq.Compound.makeCompound(self.shapes), str(self.candidate))
        self.bindings = _bindings(self.packet_hash, _sha(self.candidate))
        _write_json(self.bindings_path, self.bindings)

    def analyze(self, **changed):
        return diagnostic.analyze(changed.get("candidate", self.candidate), self.bindings_path,
            self.packet_path, changed.get("packet_hash", self.packet_hash), self.catalog_path,
            changed.get("catalog_hash", self.catalog_hash), self.archive, self.root)

    def assert_rejected(self, report, diagnostic_code=None):
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["whole_machine_coverage"], False)
        self.assertTrue(report["errors"])
        if diagnostic_code:
            self.assertIn(diagnostic_code, " ".join(report["errors"]))

    def test_real_native_geometry_and_all_fourteen_scenarios_remain_unranked(self):
        report = self.analyze()
        self.assertEqual(report["status"], "diagnostic_complete", report["errors"])
        self.assertEqual(len(report["cases"]), 14)
        self.assertEqual(sum(case["verification"]["status"] == "expected_mechanism_refused" for case in report["cases"]), 2)
        self.assertEqual(sum(case["verification"]["status"] == "analytical_equations_verified" for case in report["cases"]), 12)
        self.assertEqual(set(report["evidence_gaps"].values()), {"unqualified"})
        self.assertFalse(report["analysis_admitted"])
        self.assertFalse(report["leaderboard_eligible"])
        self.assertFalse(report["legacy_scores_changed"])
        self.assertEqual(report["physical_acceptance"], "not_evaluated")
        self.assertEqual(report["strength_assessment"], "not_qualified")
        self.assertEqual(report["service_limit_assessment"], "not_qualified")
        for actual, expected in zip(report["geometry"]["stock_centroid_stations_mm"],
                                    [[100., 200., 300.], [1100., 200., 300.], [2100., 200., 300.]]):
            for measured, reference in zip(actual, expected):
                self.assertAlmostEqual(measured, reference, places=8)
        self.assertFalse(report["geometry"]["insert_adds_composite_stiffness"])

    def test_additional_solid_is_counted_as_omitted_not_whole_machine_coverage(self):
        self.shapes.append(cq.Solid.makeBox(10., 10., 10., cq.Vector(4000., 0., 0.)))
        self.export_candidate()
        report = self.analyze()
        self.assertEqual(report["status"], "diagnostic_complete", report["errors"])
        self.assertEqual(report["geometry"]["unused_candidate_solids"], 1)
        self.assertEqual(report["geometry"]["candidate_solid_count"], 4)
        self.assertFalse(report["whole_machine_coverage"])

    def test_splice_gap_is_rejected(self):
        self.shapes[1] = self.shapes[1].translate((1., 0., 0.))
        self.export_candidate()
        self.assert_rejected(self.analyze(), "stock_splice_does_not_close")

    def test_missing_insert_and_missing_beam_are_rejected(self):
        for omitted in (2, 1):
            with self.subTest(omitted=omitted):
                original = self.shapes
                self.shapes = [shape for index, shape in enumerate(original) if index != omitted]
                self.export_candidate()
                self.assert_rejected(self.analyze(), "solid_index_out_of_bounds")
                self.shapes = original

    def test_insert_outside_splice_or_transverse_stock_contact_is_rejected(self):
        original = self.shapes[2]
        for offset, code in (((-1000., 0., 0.), "insert_does_not_span_stock_splice"),
                             ((0., 0., 1.), "insert_stock_interference")):
            self.shapes[2] = original.translate(offset)
            self.export_candidate()
            with self.subTest(code=code):
                self.assert_rejected(self.analyze(), code)

    def test_changed_beam_shape_is_rejected_by_authored_congruence(self):
        shortened = cq.Workplane("XY").rect(40., 80.).rect(32., 72.).extrude(999.).val()
        self.shapes[0] = shortened.rotate((0, 0, 0), (0, 1, 0), 90).translate((100., 200., 300.))
        self.export_candidate()
        self.assert_rejected(self.analyze(), "catalog_geometry_mismatch")

    def test_one_weak_axis_roll_is_rejected_as_unequal_bending_scope(self):
        self.shapes[1] = self.shapes[1].rotate((1100., 200., 300.), (2100., 200., 300.), 90)
        self.export_candidate()
        self.assert_rejected(self.analyze(), "unequal_bending_axes")

    def test_frozen_packet_catalog_and_candidate_binding_hashes_are_enforced(self):
        self.assert_rejected(self.analyze(packet_hash="e" * 64), "frozen_input_hash_mismatch")
        self.assert_rejected(self.analyze(catalog_hash="e" * 64), "frozen_input_hash_mismatch")
        self.bindings["candidate_sha256"] = "e" * 64
        _write_json(self.bindings_path, self.bindings)
        self.assert_rejected(self.analyze(), "candidate_binding_hash_mismatch")

    def test_packet_catalog_authority_and_binding_packet_hash_are_enforced(self):
        self.packet["catalog_contract_sha256"] = "e" * 64
        _write_json(self.packet_path, self.packet)
        self.packet_hash = _sha(self.packet_path)
        self.bindings["packet_sha256"] = self.packet_hash
        _write_json(self.bindings_path, self.bindings)
        self.assert_rejected(self.analyze(), "packet_catalog_identity_mismatch")
        self.packet["catalog_contract_sha256"] = self.catalog_hash
        _write_json(self.packet_path, self.packet)
        self.packet_hash = _sha(self.packet_path)
        self.assert_rejected(self.analyze(), "binding_packet_hash_mismatch")

    def test_altered_packet_and_runtime_mismatch_fail_closed(self):
        self.packet["case"]["force_n"] = 490.
        _write_json(self.packet_path, self.packet)
        self.assert_rejected(self.analyze(), "frozen_input_hash_mismatch")
        _write_json(self.packet_path, _packet(self.catalog_hash))
        with mock.patch.object(diagnostic.importlib.metadata, "version", return_value="different"):
            self.assert_rejected(self.analyze(), "runtime_version_mismatch")

    def test_input_mutation_after_geometry_extraction_is_detected(self):
        original = diagnostic.extract_gantry
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.bindings_path.write_text("{}", encoding="utf-8")
            return result
        with mock.patch.object(diagnostic, "extract_gantry", side_effect=mutate):
            self.assert_rejected(self.analyze(), "input_changed_during_diagnostic")


class TestGantryCliEvidencePreservation(unittest.TestCase):
    def arguments(self, target):
        return ["--step", "missing-step", "--bindings", "missing-bindings", "--packet", "missing-packet",
                "--packet-sha256", "a" * 64, "--catalog-contract", "missing-catalog",
                "--catalog-contract-sha256", "b" * 64, "--kit", "missing-kit", "--out-dir", str(target)]

    def test_existing_output_preserves_evidence_and_refuses_before_analysis(self):
        with _temporary_directory() as directory:
            target = Path(directory)
            sentinel = target / "diagnostic.json"
            sentinel.write_text("preserved", encoding="utf-8")
            with mock.patch.object(diagnostic, "analyze") as analyze, redirect_stdout(io.StringIO()):
                self.assertEqual(diagnostic.main(self.arguments(target)), 2)
                analyze.assert_not_called()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserved")

    def test_cli_writes_a_rejected_input_report_without_exposing_runtime_text(self):
        with _temporary_directory() as directory:
            target = Path(directory) / "new-output"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(diagnostic.main(self.arguments(target)), 2)
            report = json.loads((target / "diagnostic.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "not_evaluable")
            self.assertNotIn("missing-step", json.dumps(report))
            self.assertNotIn("missing-step", output.getvalue())

    def test_output_write_failure_has_a_bounded_diagnostic(self):
        with _temporary_directory() as directory:
            target = Path(directory) / "new-output"
            output = io.StringIO()
            with mock.patch.object(diagnostic, "analyze", return_value={"status": "diagnostic_complete"}), \
                    mock.patch.object(Path, "open", side_effect=OSError("sensitive-output-path")), redirect_stdout(output):
                self.assertEqual(diagnostic.main(self.arguments(target)), 2)
            self.assertIn("output_write_failed", output.getvalue())
            self.assertNotIn("sensitive-output-path", output.getvalue())


if __name__ == "__main__":
    unittest.main()
