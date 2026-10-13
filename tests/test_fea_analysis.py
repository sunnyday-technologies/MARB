"""Frozen-input and analytical regressions for unranked candidate beam FEA."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import importlib.metadata
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from grader import fea_analysis as analysis
from scripts.demo_fea_analysis import ControlError, ROOT, create_control, main as demo_main


def _contract() -> dict:
    return {"schema_version": "marb_fea_contract.v0.1", "task_id": "CONTROL", "revision": "r1",
            "coverage": "all_solids", "joint_assumption": "rigid",
            "sources": {"beam": {"file": "beam.step", "sha256": "a" * 64,
                "solid_index": 0,
                "material": {"E_pa": 69e9, "G_pa": 69e9 / 2.66, "nu": .33,
                             "rho_kg_m3": 2700., "yield_pa": 145e6},
                "section": {"A_m2": .0008, "Iy_m4": .04 * .02**3 / 12,
                            "Iz_m4": .02 * .04**3 / 12, "J_m4": 7.324e-8,
                            "Sy_m3": .04 * .02**3 / 12 / .01,
                            "Sz_m3": .02 * .04**3 / 12 / .02},
                "section_y": [0., 1., 0.],
                "stations": [{"node": "fixed", "point_mm": [0., 0., 0.]},
                             {"node": "tip", "point_mm": [1000., 0., 0.]}],
                "property_provenance": "Assumed analytical control properties."}},
            "supports": [{"node": "fixed", "restrained": [True] * 6}],
            "loads": [{"id": "tip-FY", "self_weight": False, "gravity": [0., -9.80665, 0.],
                       "point_loads": [{"node": "tip", "direction": "FY", "value": -49.}]}],
            "limits": {"deflection_mm": 3., "safety_factor": 1.65}}


def _bindings() -> dict:
    return {"schema_version": "marb_fea_bindings.v0.1", "candidate_sha256": "b" * 64,
            "contract_sha256": "c" * 64, "members": {"beam": {"solid_index": 0}}}


def _write_json(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, allow_nan=False), encoding="utf-8")


def _temporary_directory():
    # Resolve and verify the cleanup root before tempfile performs recursive
    # removal. Windows sandbox Temp can have unusable inherited permissions.
    base = (ROOT / "runs").resolve()
    base.relative_to(ROOT.resolve())
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="fea-test-", dir=base)


class TestFrozenFeaContract(unittest.TestCase):
    def test_inconsistent_isotropic_stiffness_is_rejected(self):
        contract = _contract()
        contract['sources']['beam']['material']['G_pa'] *= 2
        with self.assertRaisesRegex(analysis.AnalysisError, 'inconsistent isotropic'):
            analysis.validate_contract(contract)

    def test_rounded_isotropic_stiffness_is_allowed(self):
        contract = _contract()
        contract['sources']['beam']['material']['G_pa'] = 26e9
        analysis.validate_contract(contract)

    def test_explicit_contract_and_bindings_validate(self):
        analysis.validate_contract(_contract())
        analysis.validate_bindings(_bindings(), _contract(), "c" * 64)

    def test_unknown_fields_are_rejected(self):
        for area in ("root", "material", "section", "limits"):
            with self.subTest(area=area):
                contract = _contract()
                target = contract if area == "root" else (
                    contract["limits"] if area == "limits" else contract["sources"]["beam"][area])
                target["unexpected"] = 1
                with self.assertRaises(analysis.AnalysisError):
                    analysis.validate_contract(contract)

    def test_boolean_and_nonfinite_numbers_are_rejected(self):
        for value in (True, False, float("nan"), float("inf"), -float("inf")):
            for field in ("material", "section", "load", "station", "limit"):
                with self.subTest(value_type=type(value).__name__, field=field):
                    contract = _contract()
                    if field == "material":
                        contract["sources"]["beam"]["material"]["E_pa"] = value
                    elif field == "section":
                        contract["sources"]["beam"]["section"]["Iy_m4"] = value
                    elif field == "load":
                        contract["loads"][0]["point_loads"][0]["value"] = value
                    elif field == "station":
                        contract["sources"]["beam"]["stations"][0]["point_mm"][0] = value
                    else:
                        contract["limits"]["deflection_mm"] = value
                    with self.assertRaises(analysis.AnalysisError):
                        analysis.validate_contract(contract)

    def test_huge_integer_is_rejected_with_a_safe_diagnostic(self):
        contract = _contract()
        contract["sources"]["beam"]["material"]["E_pa"] = 10 ** 1000
        with self.assertRaisesRegex(analysis.AnalysisError, "numeric input must be finite"):
            analysis.validate_contract(contract)

    def test_support_flags_and_self_weight_require_literal_booleans(self):
        contract = _contract()
        contract["supports"][0]["restrained"][0] = 1
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_contract(contract)
        contract = _contract()
        contract["loads"][0]["self_weight"] = 0
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_contract(contract)

    def test_source_node_segment_and_case_caps_are_rejected_before_solver_work(self):
        contract = _contract()
        source = contract["sources"]["beam"]
        contract["sources"] = {f"beam{i}": copy.deepcopy(source) for i in range(129)}
        with self.assertRaisesRegex(analysis.AnalysisError, "source catalog size"):
            analysis.validate_contract(contract)

        contract = _contract()
        source = contract["sources"]["beam"]
        contract["sources"] = {}
        for index in range(3):
            member = copy.deepcopy(source)
            member["stations"] = [{"node": f"n{index}_{station}", "point_mm": [float(station), 0., 0.]}
                                  for station in range(43)]
            contract["sources"][f"beam{index}"] = member
        with self.assertRaisesRegex(analysis.AnalysisError, "node limit"):
            analysis.validate_contract(contract)

        contract = _contract()
        source = contract["sources"]["beam"]
        contract["sources"] = {}
        for index in range(5):
            member = copy.deepcopy(source)
            # Four times 63 plus five segments is 257. Shared nodes keep this
            # rejection independent of the global node count limit.
            member["stations"] = [{"node": f"n{station}", "point_mm": [float(station), 0., 0.]}
                                  for station in range(64 if index < 4 else 6)]
            contract["sources"][f"beam{index}"] = member
        with self.assertRaisesRegex(analysis.AnalysisError, "segment limit"):
            analysis.validate_contract(contract)

        contract = _contract()
        contract["loads"] = [{**copy.deepcopy(contract["loads"][0]), "id": f"case{i}"}
                             for i in range(17)]
        with self.assertRaisesRegex(analysis.AnalysisError, "load-case count"):
            analysis.validate_contract(contract)

    def test_duplicate_json_fields_are_rejected(self):
        with _temporary_directory() as temporary:
            path = Path(temporary) / "duplicate.json"
            for body in ('{"task_id":"a","task_id":"b"}', '{"sources":{"beam":{},"beam":{}}}'):
                path.write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(analysis.AnalysisError, "duplicate JSON field"):
                    analysis._load(path)

    def test_nonobject_or_invalid_json_is_rejected(self):
        with _temporary_directory() as temporary:
            path = Path(temporary) / "invalid.json"
            for body in ("[]", "null", "{", "42", '"string"'):
                path.write_text(body, encoding="utf-8")
                with self.assertRaises(analysis.AnalysisError):
                    analysis._load(path)

    def test_json_integer_exceeding_decoder_digit_limit_is_rejected(self):
        with _temporary_directory() as temporary:
            path = Path(temporary) / "huge-integer.json"
            path.write_text('{"number":' + "9" * 5000 + "}", encoding="utf-8")
            with self.assertRaisesRegex(analysis.AnalysisError, "invalid JSON input"):
                analysis._load(path)

    def test_contract_hash_is_exact_and_members_cannot_alias(self):
        bindings = _bindings()
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_bindings(bindings, _contract(), "d" * 64)
        contract = _contract()
        contract["sources"]["second"] = copy.deepcopy(contract["sources"]["beam"])
        bindings["members"]["second"] = {"solid_index": 0}
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_bindings(bindings, contract, "c" * 64)

    def test_boolean_solid_indices_and_unsafe_source_paths_are_rejected(self):
        for path in ("../beam.step", "C:/beam.step", "folder\\beam.step", "/beam.step"):
            contract = _contract()
            contract["sources"]["beam"]["file"] = path
            with self.assertRaises(analysis.AnalysisError):
                analysis.validate_contract(contract)
        contract = _contract()
        contract["sources"]["beam"]["solid_index"] = True
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_contract(contract)
        bindings = _bindings()
        bindings["members"]["beam"]["solid_index"] = True
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_bindings(bindings, _contract(), "c" * 64)

    def test_analysis_input_aliases_are_refused_before_analysis(self):
        with _temporary_directory() as temporary:
            root = Path(temporary)
            contract = _contract()
            paths = {"step": root / "candidate.step", "contract": root / "contract.json",
                     "bindings": root / "bindings.json", "source": root / "beam.step"}
            _write_json(paths["contract"], contract)
            _write_json(paths["bindings"], _bindings())
            paths["step"].write_bytes(b"candidate")
            paths["source"].write_bytes(b"source")
            for output in paths.values():
                original = output.read_bytes()
                with mock.patch.object(analysis, "analyze") as solver, redirect_stdout(io.StringIO()):
                    status = analysis.main(["--step", str(paths["step"]), "--contract", str(paths["contract"]),
                        "--contract-sha256", "c" * 64, "--bindings", str(paths["bindings"]),
                        "--catalog-root", str(root), "--out", str(output)])
                self.assertEqual(status, 2)
                solver.assert_not_called()
                self.assertEqual(output.read_bytes(), original)

    def test_analysis_existing_output_is_preserved(self):
        with _temporary_directory() as temporary:
            root = Path(temporary)
            output = root / "report.json"
            output.write_bytes(b"existing evidence")
            with mock.patch.object(analysis, "analyze", return_value={"status": "not_evaluable"}), redirect_stdout(io.StringIO()):
                status = analysis.main(["--step", str(root / "candidate.step"), "--contract", str(root / "contract.json"),
                    "--contract-sha256", "c" * 64, "--bindings", str(root / "bindings.json"),
                    "--catalog-root", str(root), "--out", str(output)])
            self.assertEqual(status, 2)
            self.assertEqual(output.read_bytes(), b"existing evidence")

    def test_analysis_hardlinked_output_preserves_input(self):
        with _temporary_directory() as temporary:
            root = Path(temporary)
            candidate, output = root / "candidate.step", root / "aliased-report.json"
            candidate.write_bytes(b"caller-owned input")
            try:
                output.hardlink_to(candidate)
            except OSError:
                self.skipTest("filesystem does not support hardlink creation")
            with mock.patch.object(analysis, "analyze", return_value={"status": "not_evaluable"}), redirect_stdout(io.StringIO()):
                status = analysis.main(["--step", str(candidate), "--contract", str(root / "contract.json"),
                    "--contract-sha256", "c" * 64, "--bindings", str(root / "bindings.json"),
                    "--catalog-root", str(root), "--out", str(output)])
            self.assertEqual(status, 2)
            self.assertTrue(candidate.is_file())
            self.assertEqual(candidate.read_bytes(), b"caller-owned input")
            self.assertEqual(output.read_bytes(), b"caller-owned input")

    def test_demo_refuses_existing_directory_before_writing(self):
        with _temporary_directory() as temporary:
            root = Path(temporary)
            marker = root / "preserve.txt"
            marker.write_bytes(b"caller-owned")
            with self.assertRaisesRegex(ControlError, "directory must be new"):
                create_control(root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(demo_main(["--out-dir", str(root)]), 2)
            self.assertEqual(list(root.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"caller-owned")


class TestFeaAnalyticalIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            versions = {name: importlib.metadata.version(name) for name in analysis.PINNED}
        except importlib.metadata.PackageNotFoundError:
            raise unittest.SkipTest("optional pinned FEA runtime is unavailable")
        if versions != analysis.PINNED:
            raise unittest.SkipTest("optional FEA runtime differs from the qualification pins")

    def setUp(self):
        self.temporary = _temporary_directory()
        self.addCleanup(self.temporary.cleanup)
        self.inputs = create_control(Path(self.temporary.name) / "control")

    def _analyze(self):
        paths = self.inputs
        return analysis.analyze(paths["step"], paths["contract"], paths["bindings"],
                                paths["catalog_root"], paths["contract_sha256"])

    def _rewrite_contract(self, contract):
        _write_json(self.inputs["contract"], contract)
        self.inputs["contract_sha256"] = analysis.digest(self.inputs["contract"])
        bindings = analysis._load(self.inputs["bindings"])
        bindings["contract_sha256"] = self.inputs["contract_sha256"]
        _write_json(self.inputs["bindings"], bindings)

    def test_translated_step_pass_matches_independent_cantilever_formula(self):
        report = self._analyze()
        self.assertEqual(report["status"], "simulated_pass", report["errors"])
        self.assertFalse(report["leaderboard_eligible"])
        self.assertFalse(report["legacy_scores_changed"])
        self.assertEqual(report["physical_acceptance"], "not_evaluated")
        case = report["cases"][0]
        expected = self.inputs["expected"]
        self.assertAlmostEqual(case["max_displacement_mm"], expected["tip_deflection_mm"], places=6)
        self.assertAlmostEqual(case["max_stress_utilization"], expected["stress_utilization"], places=8)
        self.assertAlmostEqual(case["node_displacements_mm"]["tip"]["DY"], -expected["tip_deflection_mm"], places=6)
        demand = next(iter(case["member_demands"].values()))
        self.assertAlmostEqual(demand["normal_stress_upper_bound_pa"], expected["max_bending_stress_pa"], places=3)
        self.assertEqual(case["equilibrium"]["status"], "pass")
        self.assertEqual(report["geometry"]["unused_candidate_solids"], 0)
        self.assertAlmostEqual(report["geometry"]["sources"]["beam"]["translation_mm"][0], 75.)
        self.assertAlmostEqual(report["geometry"]["sources"]["beam"]["translation_mm"][1], -125.)
        self.assertAlmostEqual(report["geometry"]["sources"]["beam"]["translation_mm"][2], 300.)

    def test_overload_fails_with_finite_demands(self):
        contract = analysis._load(self.inputs["contract"])
        contract["loads"][0]["point_loads"][0]["value"] = -490.
        self._rewrite_contract(contract)
        report = self._analyze()
        self.assertEqual(report["status"], "simulated_fail", report["errors"])
        self.assertEqual(report["cases"][0]["deflection_status"], "fail")
        self.assertEqual(report["cases"][0]["stress_status"], "fail")
        self.assertAlmostEqual(report["cases"][0]["max_displacement_mm"],
                               10 * self.inputs["expected"]["tip_deflection_mm"], places=6)
        json.dumps(report, allow_nan=False)

    def test_changed_source_hash_is_not_evaluable(self):
        source = self.inputs["catalog_root"] / "beam.step"
        source.write_bytes(source.read_bytes() + b"\n")
        self.assertEqual(self._analyze()["status"], "not_evaluable")

    def test_changed_candidate_hash_is_not_evaluable(self):
        candidate = self.inputs["step"]
        candidate.write_bytes(candidate.read_bytes() + b"\n")
        report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("candidate differs from the evaluator binding", report["errors"])

    def test_changed_contract_hash_is_not_evaluable(self):
        contract = self.inputs["contract"]
        contract.write_bytes(contract.read_bytes() + b"\n")
        report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("contract differs from the evaluator-pinned digest", report["errors"])

    def test_changed_bindings_contract_hash_is_not_evaluable(self):
        bindings = analysis._load(self.inputs["bindings"])
        bindings["contract_sha256"] = "0" * 64
        _write_json(self.inputs["bindings"], bindings)
        self.assertEqual(self._analyze()["status"], "not_evaluable")

    def test_invalid_claimed_section_is_not_evaluable(self):
        contract = analysis._load(self.inputs["contract"])
        contract["sources"]["beam"]["section"]["Iz_m4"] *= 2
        self._rewrite_contract(contract)
        self.assertEqual(self._analyze()["status"], "not_evaluable")

    def _backend_report(self):
        from grader import fea_backend, fea_step
        frame = fea_step.extract_candidate(analysis._load(self.inputs["contract"]),
            analysis._load(self.inputs["bindings"]), self.inputs["step"], self.inputs["catalog_root"])
        contract = analysis._load(self.inputs["contract"])
        # Geometry diagnostics are intentionally excluded from the SI solver contract.
        frame = {key: frame[key] for key in ("nodes", "members", "supports", "joint_assumption")}
        outcome = fea_backend.solve_cases(frame, contract["loads"], contract["limits"])
        self.assertEqual(outcome["status"], "pass")
        return outcome

    def _analyze_mocked_outcome(self, outcome):
        with mock.patch("grader.fea_backend.solve_cases", return_value=outcome):
            report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        json.dumps(report, allow_nan=False)
        return report

    def test_backend_pass_with_no_cases_is_not_evaluable(self):
        self._analyze_mocked_outcome({"status": "pass", "cases": []})

    def test_backend_missing_member_demands_is_not_evaluable(self):
        outcome = self._backend_report()
        del outcome["cases"][0]["member_demands"]
        self._analyze_mocked_outcome(outcome)

    def test_backend_pass_with_failed_case_is_not_evaluable(self):
        outcome = self._backend_report()
        outcome["cases"][0]["status"] = "fail"
        self._analyze_mocked_outcome(outcome)

    def test_backend_nested_nonfinite_demand_is_not_evaluable_and_serializable(self):
        outcome = self._backend_report()
        demand = next(iter(outcome["cases"][0]["member_demands"].values()))
        demand["bending_extrema_nm"]["Mz"][0] = float("nan")
        self._analyze_mocked_outcome(outcome)

    def test_backend_missing_member_extrema_are_not_evaluable(self):
        valid = self._backend_report()
        for field in ("axial_extrema_n", "bending_extrema_nm", "transverse_extrema_mm"):
            with self.subTest(field=field):
                outcome = copy.deepcopy(valid)
                del next(iter(outcome["cases"][0]["member_demands"].values()))[field]
                self._analyze_mocked_outcome(outcome)

    def test_backend_malformed_or_unordered_extrema_are_not_evaluable(self):
        valid = self._backend_report()
        for field, value in (
            ("axial_extrema_n", [2., 1.]),
            ("axial_extrema_n", [0.]),
            ("bending_extrema_nm", {"My": [0., 0.], "Mz": [2., 1.]}),
            ("transverse_extrema_mm", {"dy": [0., 0.], "dz": [0.]}),
        ):
            with self.subTest(field=field, value=value):
                outcome = copy.deepcopy(valid)
                next(iter(outcome["cases"][0]["member_demands"].values()))[field] = value
                self._analyze_mocked_outcome(outcome)

    def test_backend_self_consistent_stress_understatement_is_not_evaluable(self):
        outcome = self._backend_report()
        case = outcome["cases"][0]
        demand = next(iter(case["member_demands"].values()))
        # Keep the utilization and aggregate consistent with the false stress.
        # The frozen section and actual bending extrema must reject this claim.
        demand["normal_stress_upper_bound_pa"] /= 2.
        demand["stress_utilization"] /= 2.
        case["max_stress_utilization"] /= 2.
        report = self._analyze_mocked_outcome(outcome)
        self.assertIn("backend normal stress envelope contradicts member demands", report["errors"])

    def test_backend_displacement_envelope_must_cover_transverse_extrema(self):
        outcome = self._backend_report()
        demand = next(iter(outcome["cases"][0]["member_demands"].values()))
        demand["transverse_extrema_mm"]["dy"] = [-10., 0.]
        report = self._analyze_mocked_outcome(outcome)
        self.assertIn("backend displacement envelope omits transverse demands", report["errors"])

    def test_backend_missing_equilibrium_resultants_are_not_evaluable(self):
        valid = self._backend_report()
        for field in ("applied_force_n", "applied_moment_nm", "reaction_force_n", "reaction_moment_nm"):
            with self.subTest(field=field):
                outcome = copy.deepcopy(valid)
                del outcome["cases"][0]["equilibrium"][field]
                self._analyze_mocked_outcome(outcome)

    def test_backend_changed_applied_load_with_balanced_reaction_is_not_evaluable(self):
        valid = self._backend_report()
        for applied, reaction in (("applied_force_n", "reaction_force_n"),
                                  ("applied_moment_nm", "reaction_moment_nm")):
            with self.subTest(applied=applied):
                outcome = copy.deepcopy(valid)
                equilibrium = outcome["cases"][0]["equilibrium"]
                equilibrium[applied][0] += 1.
                equilibrium[reaction][0] -= 1.
                report = self._analyze_mocked_outcome(outcome)
                self.assertIn("backend applied resultants contradict the frozen loads", report["errors"])

    def test_backend_inflated_equilibrium_tolerance_is_not_evaluable(self):
        valid = self._backend_report()
        for field in ("force_tolerance_n", "moment_tolerance_nm"):
            with self.subTest(field=field):
                outcome = copy.deepcopy(valid)
                outcome["cases"][0]["equilibrium"][field] *= 100.
                report = self._analyze_mocked_outcome(outcome)
                self.assertIn("backend equilibrium tolerance contradicts the frozen loads", report["errors"])

    def test_backend_false_zero_equilibrium_residual_is_not_evaluable(self):
        valid = self._backend_report()
        for field in ("reaction_force_n", "reaction_moment_nm"):
            with self.subTest(field=field):
                outcome = copy.deepcopy(valid)
                outcome["cases"][0]["equilibrium"][field][0] += 1.
                report = self._analyze_mocked_outcome(outcome)
                self.assertIn("backend equilibrium residual contradicts applied and reaction resultants", report["errors"])

    def test_actual_self_weight_and_point_moment_equilibrium_passes(self):
        contract = analysis._load(self.inputs["contract"])
        prescribed = contract["loads"][0]
        prescribed["self_weight"] = True
        prescribed["gravity"] = [1.3, -9.81, 2.7]
        prescribed["point_loads"].append({"node": "tip", "direction": "MZ", "value": .2})
        self._rewrite_contract(contract)
        report = self._analyze()
        self.assertEqual(report["status"], "simulated_pass", report["errors"])
        equilibrium = report["cases"][0]["equilibrium"]
        # The real 1 m x 20 mm x 40 mm stock has 2.16 kg mass.
        expected_force = [2.16 * 1.3, -49. - 2.16 * 9.81, 2.16 * 2.7]
        for actual, expected in zip(equilibrium["applied_force_n"], expected_force):
            self.assertAlmostEqual(actual, expected, places=8)
        self.assertEqual(equilibrium["status"], "pass")
        json.dumps(report, allow_nan=False)

    def test_huge_numeric_contract_is_not_evaluable(self):
        contract = analysis._load(self.inputs["contract"])
        contract["sources"]["beam"]["material"]["E_pa"] = 10 ** 1000
        self._rewrite_contract(contract)
        report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("numeric input must be finite", report["errors"])

    def test_aggregate_catalog_size_cap_prevents_geometry_and_solver_work(self):
        source = self.inputs["catalog_root"] / "beam.step"
        second = self.inputs["catalog_root"] / "second.step"
        second.write_bytes(source.read_bytes())
        contract = analysis._load(self.inputs["contract"])
        contract["sources"]["second"] = copy.deepcopy(contract["sources"]["beam"])
        contract["sources"]["second"]["file"] = "second.step"
        self._rewrite_contract(contract)
        bindings = analysis._load(self.inputs["bindings"])
        bindings["members"]["second"] = {"solid_index": 1}
        _write_json(self.inputs["bindings"], bindings)
        original_stat = Path.stat

        def sized_catalog(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path in (source, second):
                fields = list(result)
                fields[6] = 34 * 1024 * 1024
                return os.stat_result(fields)
            return result

        with mock.patch.object(Path, "stat", sized_catalog), mock.patch("grader.fea_step.extract_candidate") as extract:
            report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("authored catalog exceeds aggregate input size limit", report["errors"])
        extract.assert_not_called()

    def test_json_decoder_digit_limit_returns_cli_two_without_traceback(self):
        self.inputs["contract"].write_text('{"number":' + "9" * 5000 + "}", encoding="utf-8")
        self.inputs["contract_sha256"] = analysis.digest(self.inputs["contract"])
        report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("invalid JSON input", report["errors"])
        output = self.inputs["output_dir"] / "invalid-report.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = analysis.main(["--step", str(self.inputs["step"]), "--contract", str(self.inputs["contract"]),
                "--contract-sha256", self.inputs["contract_sha256"], "--bindings", str(self.inputs["bindings"]),
                "--catalog-root", str(self.inputs["catalog_root"]), "--out", str(output)])
        self.assertEqual(status, 2)
        self.assertEqual(analysis._load(output)["status"], "not_evaluable")
        self.assertNotIn(str(self.inputs["output_dir"]), stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue())

    def test_candidate_changed_during_solve_is_not_evaluable(self):
        from grader import fea_backend
        original = fea_backend.solve_cases

        def mutate(*args, **kwargs):
            report = original(*args, **kwargs)
            candidate = self.inputs["step"]
            candidate.write_bytes(candidate.read_bytes() + b"\n")
            return report

        with mock.patch.object(fea_backend, "solve_cases", side_effect=mutate):
            report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("an input changed during analysis", report["errors"])

    def test_source_changed_during_solve_is_not_evaluable(self):
        from grader import fea_backend
        original = fea_backend.solve_cases

        def mutate(*args, **kwargs):
            report = original(*args, **kwargs)
            source = self.inputs["catalog_root"] / "beam.step"
            source.write_bytes(source.read_bytes() + b"\n")
            return report

        with mock.patch.object(fea_backend, "solve_cases", side_effect=mutate):
            report = self._analyze()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("an authored source changed during analysis", report["errors"])

    def test_analysis_does_not_change_legacy_registry_scores_or_publication(self):
        tracked = [ROOT / "benchmark.yaml", ROOT / "grader" / "marb_grade_all.py",
                   ROOT / "grader" / "run_registry.py", ROOT / "results" / "marb_runs.json",
                   ROOT / "results" / "marb_native_grades.json", ROOT / "publishing" / "index.html"]
        before = {path: analysis.digest(path) for path in tracked}
        self.assertEqual(self._analyze()["status"], "simulated_pass")
        self.assertEqual(before, {path: analysis.digest(path) for path in tracked})


if __name__ == "__main__":
    unittest.main()
