"""Independent analytical controls for the optional planar bending diagnostic.

All geometry and properties here are synthetic. Passing these controls does
not establish a physical M3 connection model, strength or load capacity.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
import json
import math
import subprocess
import sys
import unittest
from unittest import mock

from grader import gantry_bending as bending

try:
    HAS_RUNTIME = importlib.metadata.version("numpy") == bending.NUMPY_VERSION
except importlib.metadata.PackageNotFoundError:
    HAS_RUNTIME = False

E = 200e9
I = 1e-8
S = 1e-6
EI = E * I
L = 2.0


def split_beam(*, payload=49.0, distributed=0.0, splice="rigid", vertical="fixed"):
    return {
        "nodes_m": [0.0, L / 2, L],
        "elements": [{"id": name, "i": index, "j": index + 1,
                      "E_pa": E, "I_m4": I, "S_m3": S, "distributed_load_n_m": -distributed}
                     for index, name in enumerate(("left", "right"))],
        "supports": [{"node": node, "vertical": deepcopy(vertical), "rotation": "free"} for node in (0, 2)],
        "rotation_couplings": [{"id": "splice", "left_element": "left", "right_element": "right",
                                "stiffness_nm_rad": splice}],
        "point_loads": [{"node": 1, "force_n": -payload}],
        "point_moments": [],
    }


def one_beam(*, distributed=0.0, cantilever=False, payload=0.0, moment=0.0):
    return {
        "nodes_m": [0.0, L],
        "elements": [{"id": "beam", "i": 0, "j": 1, "E_pa": E, "I_m4": I, "S_m3": S,
                      "distributed_load_n_m": -distributed}],
        "supports": ([{"node": 0, "vertical": "fixed", "rotation": "fixed"}] if cantilever else
                     [{"node": node, "vertical": "fixed", "rotation": "free"} for node in (0, 1)]),
        "rotation_couplings": [],
        "point_loads": [{"node": 1, "force_n": -payload}],
        "point_moments": [{"node": 1, "moment_nm": moment}],
    }


class TestBendingInputBoundary(unittest.TestCase):
    def rejected_before_runtime(self, model):
        with mock.patch.object(bending.importlib.metadata, "version") as runtime:
            report = bending.solve_bending(model)
        runtime.assert_not_called()
        self.assertEqual(report["status"], "not_evaluable")
        self.assertTrue(report["errors"])
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["leaderboard_eligible"], False)
        self.assertNotIn("node_deflections_mm", report)
        return report

    def test_module_import_does_not_import_numpy(self):
        code = "import sys; import grader.gantry_bending; assert 'numpy' not in sys.modules"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_model_element_support_and_coupling_fields_are_refused(self):
        for area in ("model", "element", "support", "coupling", "point_load", "point_moment"):
            with self.subTest(area=area):
                model = split_beam()
                model["point_moments"] = [{"node": 0, "moment_nm": 0.0}]
                target = {"model": model, "element": model["elements"][0],
                          "support": model["supports"][0], "coupling": model["rotation_couplings"][0],
                          "point_load": model["point_loads"][0], "point_moment": model["point_moments"][0]}[area]
                target["unsupported_private_field"] = "sensitive-fixture-marker"
                report = self.rejected_before_runtime(model)
                self.assertNotIn("sensitive-fixture-marker", json.dumps(report))

    def test_unsorted_duplicate_boolean_and_nonfinite_nodes_are_refused(self):
        for nodes in ([0, 2, 1], [0, 1, 1], [0, True, 2], [0, math.inf, 2], [0, math.nan, 2], [0, 10**1000, 2]):
            with self.subTest(nodes_type=[type(value).__name__ for value in nodes]):
                model = split_beam()
                model["nodes_m"] = nodes
                self.rejected_before_runtime(model)

    def test_model_size_is_bounded_before_runtime(self):
        model = split_beam()
        model["nodes_m"] = list(range(33))
        self.rejected_before_runtime(model)
        model = split_beam()
        model["rotation_couplings"] *= 65
        self.rejected_before_runtime(model)
        model = split_beam()
        model["point_loads"] *= 65
        self.rejected_before_runtime(model)

    def test_missing_reversed_duplicate_or_disconnected_elements_are_refused(self):
        for change in ("missing", "reversed", "duplicate", "disconnected", "unknown_node"):
            with self.subTest(change=change):
                model = split_beam()
                if change == "missing":
                    model["elements"].pop()
                elif change == "reversed":
                    model["elements"][0].update(i=1, j=0)
                elif change == "duplicate":
                    model["elements"][1].update(i=0, j=1)
                elif change == "disconnected":
                    model["elements"][0].update(i=0, j=2)
                else:
                    model["elements"][0]["j"] = 3
                self.rejected_before_runtime(model)

    def test_nonpositive_boolean_and_nonfinite_section_properties_are_refused(self):
        for key in ("E_pa", "I_m4", "S_m3"):
            for value in (0, -1, True, math.inf, math.nan, 10**1000):
                with self.subTest(key=key, value_type=type(value).__name__):
                    model = split_beam()
                    model["elements"][0][key] = value
                    self.rejected_before_runtime(model)

    def test_duplicate_ids_and_missing_or_invalid_rotation_couplings_are_refused(self):
        for change in ("duplicate_element_id", "missing", "duplicate", "unknown", "reversed", "negative", "boolean", "infinite"):
            with self.subTest(change=change):
                model = split_beam()
                coupling = model["rotation_couplings"][0]
                if change == "duplicate_element_id":
                    model["elements"][1]["id"] = "left"
                elif change == "missing":
                    model["rotation_couplings"] = []
                elif change == "duplicate":
                    model["rotation_couplings"].append(deepcopy(coupling))
                elif change == "unknown":
                    coupling["right_element"] = "absent"
                elif change == "reversed":
                    coupling.update(left_element="right", right_element="left")
                else:
                    coupling["stiffness_nm_rad"] = {"negative": -1, "boolean": True, "infinite": math.inf}[change]
                self.rejected_before_runtime(model)

    def test_grounded_rotation_at_a_split_node_is_ambiguous_and_refused(self):
        model = split_beam()
        model["supports"].append({"node": 1, "vertical": "fixed", "rotation": "fixed"})
        self.rejected_before_runtime(model)

    def test_support_springs_require_finite_positive_stiffness(self):
        for field, stiffness_name in (("vertical", "stiffness_n_m"), ("rotation", "stiffness_nm_rad")):
            for value in (0, -1, True, math.inf, math.nan):
                with self.subTest(field=field, value_type=type(value).__name__):
                    model = split_beam()
                    model["supports"][0][field] = {stiffness_name: value}
                    self.rejected_before_runtime(model)

    def test_nonfinite_loads_and_moments_are_refused(self):
        for value in (True, math.inf, math.nan, 10**1000):
            with self.subTest(value_type=type(value).__name__):
                model = split_beam()
                model["point_loads"][0]["force_n"] = value
                self.rejected_before_runtime(model)
                model = split_beam()
                model["point_moments"] = [{"node": 0, "moment_nm": value}]
                self.rejected_before_runtime(model)

    def test_point_moment_at_a_finite_splice_requires_an_explicit_side_and_is_refused(self):
        model = split_beam(splice=1000)
        model["point_moments"] = [{"node": 1, "moment_nm": 10}]
        self.rejected_before_runtime(model)

    def test_runtime_version_mismatch_is_not_evaluable(self):
        with mock.patch.object(bending.importlib.metadata, "version", return_value="0.0"):
            report = bending.solve_bending(split_beam())
        self.assertEqual(report["status"], "not_evaluable")
        self.assertEqual(report["errors"], ["gantry.bending.runtime_version_mismatch"])


@unittest.skipUnless(HAS_RUNTIME, "Pinned NumPy runtime unavailable")
class TestIndependentBendingControls(unittest.TestCase):
    def completed(self, model):
        before = deepcopy(model)
        report = bending.solve_bending(model)
        self.assertEqual(model, before)
        self.assertEqual(report["status"], "diagnostic_complete", report["errors"])
        self.assertFalse(report["errors"])
        self.assertIs(report["analysis_admitted"], False)
        self.assertIs(report["leaderboard_eligible"], False)
        self.assertEqual(report["physical_acceptance"], "not_evaluated")
        self.assertEqual(report["capacity_acceptance"], "not_evaluated")
        self.assertNotIn("safety_factor", report)
        self.assertNotIn("pass", report)
        equilibrium = report["equilibrium"]
        self.assertIs(equilibrium["verified"], True)
        self.assertLessEqual(abs(equilibrium["force_residual_n"]), equilibrium["force_tolerance_n"])
        self.assertLessEqual(abs(equilibrium["moment_residual_nm"]), equilibrium["moment_tolerance_nm"])
        self.assertTrue(math.isfinite(report["stiffness_condition_number"]))
        return report

    def test_rigid_simply_supported_center_point_load_matches_closed_form(self):
        report = self.completed(split_beam())
        expected = 49 * L**3 / (48 * EI)
        self.assertAlmostEqual(report["node_deflections_mm"][1], -expected * 1000, places=9)
        self.assertAlmostEqual(report["max_deflection_mm"], expected * 1000, places=9)
        self.assertEqual(report["rotation_jumps_rad"]["splice"], 0)
        self.assertAlmostEqual(report["spring_moments_nm"]["splice"], 49 * L / 4, places=9)
        self.assertAlmostEqual(report["peak_bending_stress_pa"], 49 * L / 4 / S, places=6)
        for reaction in report["support_reactions"]:
            self.assertAlmostEqual(reaction["force_n"], 49 / 2, places=9)

    def test_single_element_uniform_load_finds_non_nodal_quartic_extremum(self):
        report = self.completed(one_beam(distributed=10))
        expected = 5 * 10 * L**4 / (384 * EI)
        self.assertEqual(report["node_deflections_mm"], [0.0, 0.0])
        extrema = report["element_results"]["beam"]["displacement_extrema"]
        self.assertAlmostEqual(extrema["minimum_mm"], -expected * 1000, places=9)
        self.assertAlmostEqual(extrema["minimum_x_m"], L / 2, places=9)
        self.assertAlmostEqual(report["element_results"]["beam"]["max_abs_bending_moment_nm"], 10 * L**2 / 8, places=9)
        self.assertAlmostEqual(report["peak_bending_stress_pa"], 10 * L**2 / 8 / S, places=6)

    def test_continuous_split_uniform_load_matches_closed_form(self):
        report = self.completed(split_beam(payload=0, distributed=10))
        self.assertAlmostEqual(report["node_deflections_mm"][1], -5 * 10 * L**4 / (384 * EI) * 1000, places=9)

    def test_finite_splice_adds_the_independent_series_rotation_compliance(self):
        stiffness = 10000
        report = self.completed(split_beam(splice=stiffness))
        expected = 49 * L**3 / (48 * EI) + 49 * L**2 / (16 * stiffness)
        self.assertAlmostEqual(report["node_deflections_mm"][1], -expected * 1000, places=9)
        self.assertAlmostEqual(report["rotation_jumps_rad"]["splice"], 49 * L / (4 * stiffness), places=12)
        self.assertAlmostEqual(report["spring_moments_nm"]["splice"], 49 * L / 4, places=9)
        self.assertNotEqual(report["element_end_rotations_rad"]["left"]["right"],
                            report["element_end_rotations_rad"]["right"]["left"])

    def test_piecewise_rigidity_preserves_each_declared_section(self):
        model = split_beam()
        model["elements"][0]["I_m4"] *= 2
        model["elements"][0]["S_m3"] *= 2
        report = self.completed(model)
        # Virtual work for the two half-spans gives this center displacement.
        expected = 49 * L**3 / 96 * (1 / (2 * EI) + 1 / EI)
        self.assertAlmostEqual(report["node_deflections_mm"][1], -expected * 1000, places=9)
        self.assertAlmostEqual(report["element_results"]["left"]["peak_bending_stress_pa"], 49 * L / 4 / (2 * S), places=6)
        self.assertAlmostEqual(report["element_results"]["right"]["peak_bending_stress_pa"], 49 * L / 4 / S, places=6)
        self.assertNotEqual(report["element_end_rotations_rad"]["left"]["right"], 0.0)
        self.assertEqual(report["element_end_rotations_rad"]["left"]["right"],
                         report["element_end_rotations_rad"]["right"]["left"])

    def test_multiple_loads_at_one_node_are_summed_without_exclusion(self):
        model = split_beam()
        model["point_loads"] = [{"node": 1, "force_n": -20}, {"node": 1, "force_n": -29}]
        report = self.completed(model)
        self.assertAlmostEqual(report["node_deflections_mm"][1], -49 * L**3 / (48 * EI) * 1000, places=9)
        self.assertEqual(report["equilibrium"]["applied_force_n"], -49)

    def test_payload_uniform_load_splice_and_support_compliance_superpose(self):
        rotational, vertical, q = 10000, 2000, 10
        report = self.completed(split_beam(distributed=q, splice=rotational, vertical={"stiffness_n_m": vertical}))
        reaction = (49 + q * L) / 2
        expected = (49 * L**3 / (48 * EI) + 5 * q * L**4 / (384 * EI)
                    + (49 * L**2 / 16 + q * L**3 / 32) / rotational + reaction / vertical)
        self.assertAlmostEqual(report["node_deflections_mm"][1], -expected * 1000, places=8)
        for index in (0, 2):
            self.assertAlmostEqual(report["node_deflections_mm"][index], -reaction / vertical * 1000, places=8)
        self.assertAlmostEqual(report["peak_bending_stress_pa"], (49 * L / 4 + q * L**2 / 8) / S, places=5)

    def test_zero_stiffness_splice_is_a_mechanism_not_a_zero_score(self):
        report = bending.solve_bending(split_beam(splice=0))
        self.assertEqual(report["status"], "not_evaluable")
        self.assertEqual(report["errors"], ["gantry.bending.mechanism_or_ill_conditioned_stiffness"])
        self.assertNotIn("node_deflections_mm", report)
        self.assertNotIn("peak_bending_stress_pa", report)

    def test_unrestrained_or_extremely_weak_supports_fail_closed(self):
        model = split_beam()
        model["supports"] = []
        report = bending.solve_bending(model)
        self.assertEqual(report["status"], "not_evaluable")
        model = split_beam(vertical={"stiffness_n_m": 1e-15})
        self.assertEqual(bending.solve_bending(model)["status"], "not_evaluable")

    def test_cantilever_point_load_and_endpoint_moment_match_closed_form(self):
        report = self.completed(one_beam(cantilever=True, payload=49))
        self.assertAlmostEqual(report["node_deflections_mm"][1], -49 * L**3 / (3 * EI) * 1000, places=8)
        self.assertAlmostEqual(report["element_end_rotations_rad"]["beam"]["right"], -49 * L**2 / (2 * EI), places=10)
        self.assertAlmostEqual(report["support_reactions"][0]["moment_nm"], 49 * L, places=9)
        report = self.completed(one_beam(cantilever=True, moment=10))
        self.assertAlmostEqual(report["node_deflections_mm"][1], 10 * L**2 / (2 * EI) * 1000, places=9)
        self.assertAlmostEqual(report["support_reactions"][0]["moment_nm"], -10, places=9)

    def test_grounded_rotation_spring_adds_cantilever_rotation_compliance(self):
        model = one_beam(cantilever=True, payload=49)
        model["supports"][0]["rotation"] = {"stiffness_nm_rad": 10000}
        report = self.completed(model)
        expected = 49 * L**3 / (3 * EI) + 49 * L**2 / 10000
        self.assertAlmostEqual(report["node_deflections_mm"][1], -expected * 1000, places=8)
        self.assertAlmostEqual(report["support_reactions"][0]["moment_nm"], 49 * L, places=8)

    def test_fully_fixed_uniform_load_uses_exact_particular_solution(self):
        model = one_beam(distributed=10)
        for support in model["supports"]:
            support["rotation"] = "fixed"
        report = self.completed(model)
        self.assertEqual(report["node_deflections_mm"], [0.0, 0.0])
        self.assertAlmostEqual(report["max_deflection_mm"], 10 * L**4 / (384 * EI) * 1000, places=10)
        self.assertAlmostEqual(report["element_results"]["beam"]["max_abs_bending_moment_nm"], 10 * L**2 / 12, places=10)

    def test_analytic_bending_is_unchanged_by_rigid_subdivision(self):
        model = split_beam()
        model["nodes_m"] = [0, 0.5, 1, 1.5, 2]
        model["elements"] = [{"id": f"beam{index}", "i": index, "j": index + 1,
                              "E_pa": E, "I_m4": I, "S_m3": S, "distributed_load_n_m": 0}
                             for index in range(4)]
        model["rotation_couplings"] = [{"id": f"joint{index}", "left_element": f"beam{index}",
                                        "right_element": f"beam{index+1}", "stiffness_nm_rad": "rigid"}
                                       for index in range(3)]
        model["supports"][1]["node"] = 4
        model["point_loads"][0]["node"] = 2
        report = self.completed(model)
        self.assertAlmostEqual(report["node_deflections_mm"][2], -49 * L**3 / (48 * EI) * 1000, places=8)
        self.assertEqual(set(report["element_results"]), {f"beam{index}" for index in range(4)})
        self.assertEqual(set(report["rotation_jumps_rad"].values()), {0.0})

    def test_internal_point_moment_on_an_exactly_rigid_joint_balances_globally(self):
        model = split_beam(payload=0)
        model["point_moments"] = [{"node": 1, "moment_nm": 10}]
        report = self.completed(model)
        self.assertAlmostEqual(report["support_reactions"][0]["force_n"], 5, places=9)
        self.assertAlmostEqual(report["support_reactions"][1]["force_n"], -5, places=9)
        self.assertAlmostEqual(report["equilibrium"]["applied_moment_nm"], 10, places=9)

    def test_overload_remains_linear_without_a_capacity_claim(self):
        baseline = self.completed(split_beam())
        overload = self.completed(split_beam(payload=490))
        self.assertAlmostEqual(overload["max_deflection_mm"] / baseline["max_deflection_mm"], 10, places=10)
        self.assertAlmostEqual(overload["peak_bending_stress_pa"] / baseline["peak_bending_stress_pa"], 10, places=10)
        self.assertEqual(overload["capacity_acceptance"], "not_evaluated")

    def test_coordinate_translation_preserves_the_solution_and_equilibrium(self):
        model = split_beam(distributed=10)
        model["nodes_m"] = [value + 123 for value in model["nodes_m"]]
        report = self.completed(model)
        self.assertEqual(report["equilibrium"]["origin_x_m"], 123)
        self.assertAlmostEqual(report["max_deflection_mm"], self.completed(split_beam(distributed=10))["max_deflection_mm"], places=10)

    def test_corrupted_solver_results_are_rejected_before_reporting_a_diagnostic(self):
        import numpy as np
        for corruption in ("zero", "nan"):
            with self.subTest(corruption=corruption):
                def broken_solve(matrix, rhs):
                    return np.zeros_like(rhs) if corruption == "zero" else np.full_like(rhs, np.nan)

                with mock.patch.object(np.linalg, "solve", side_effect=broken_solve):
                    report = bending.solve_bending(split_beam())
                self.assertEqual(report["status"], "not_evaluable")
                self.assertNotIn("node_deflections_mm", report)

    def test_numerical_warning_does_not_become_a_complete_diagnostic_or_echo_native_text(self):
        import numpy as np
        with mock.patch.object(np.linalg, "solve", side_effect=RuntimeWarning("private-fixture-marker")):
            report = bending.solve_bending(split_beam())
        self.assertEqual(report["status"], "not_evaluable")
        self.assertNotIn("private-fixture-marker", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
