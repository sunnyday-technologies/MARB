"""Analytical and fail-closed regressions for the optional MARB beam backend."""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
import json
import math
import subprocess
import sys
import unittest
from unittest import mock
import warnings

from grader import fea_backend


def cantilever():
    width, height = .02, .04
    iy, iz = height * width**3 / 12., width * height**3 / 12.
    return {
        "joint_assumption": "rigid",
        "nodes": {"fixed": [0., 0., 0.], "tip": [1., 0., 0.]},
        "members": [{"id": "beam", "i_node": "fixed", "j_node": "tip",
                     "rotation_deg": 0.,
                     "material": {"E_pa": 70e9, "G_pa": 70e9 / 2.6, "nu": .3,
                                  "rho_kg_m3": 2700., "yield_pa": 250e6},
                     "section": {"A_m2": width * height, "Iy_m4": iy,
                                 "Iz_m4": iz, "J_m4": iy + iz,
                                 "Sy_m3": iy / (width / 2.), "Sz_m3": iz / (height / 2.)}}],
        "supports": [{"node": "fixed", "restrained": [True] * 6}],
    }


def load_case(value=-10., *, self_weight=False, direction="FY"):
    return {"id": "tip_load", "gravity": [0., -9.81, 0.], "self_weight": self_weight,
            "point_loads": [{"node": "tip", "direction": direction, "value": value}]}


LIMITS = {"deflection_mm": 1., "safety_factor": 2.}
try:
    HAS_RUNTIME = importlib.metadata.version("PyNiteFEA") == fea_backend.PINNED_PYNITE_VERSION
except importlib.metadata.PackageNotFoundError:
    HAS_RUNTIME = False


class TestBackendInputBoundary(unittest.TestCase):
    def test_legacy_import_does_not_import_solver(self):
        script = "import sys; import grader.fea_backend; assert 'Pynite' not in sys.modules; assert 'numpy' not in sys.modules"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unrestrained_component_is_not_evaluable(self):
        frame = cantilever()
        frame["supports"] = []
        with mock.patch.object(fea_backend, "_load_runtime") as runtime:
            report = fea_backend.solve_cases(frame, [load_case()], LIMITS)
        self.assertEqual(report["status"], "not-evaluable")
        self.assertIn("no support", report["errors"][0])
        runtime.assert_not_called()

    def test_missing_member_node_is_not_evaluable(self):
        frame = cantilever()
        frame["members"][0]["j_node"] = "absent"
        report = fea_backend.solve_cases(frame, [load_case()], LIMITS)
        self.assertEqual(report["status"], "not-evaluable")
        self.assertIn("missing node", report["errors"][0])

    def test_implicit_joint_is_rejected(self):
        frame = cantilever()
        frame["nodes"]["interior"] = [.5, 0., 0.]
        report = fea_backend.solve_cases(frame, [load_case()], LIMITS)
        self.assertEqual(report["status"], "not-evaluable")
        self.assertIn("explicit beam segments", report["errors"][0])

    def test_distinct_coincident_node_ids_are_rejected(self):
        frame = cantilever()
        frame["nodes"]["tip_duplicate"] = list(frame["nodes"]["tip"])
        report = fea_backend.solve_cases(frame, [load_case()], LIMITS)
        self.assertEqual(report["status"], "not-evaluable")
        self.assertIn("coincident coordinates", report["errors"][0])

    def test_version_mismatch_is_not_evaluable(self):
        with mock.patch.object(importlib.metadata, "version", return_value="2.5.0"):
            report = fea_backend.solve_cases(cantilever(), [load_case()], LIMITS)
        self.assertEqual(report["status"], "not-evaluable")
        self.assertIn("pinned 2.4.1", report["errors"][0])

    def test_nonfinite_and_unknown_fields_rejected(self):
        for mutate in (
            lambda frame, case, limits: frame["nodes"]["tip"].__setitem__(0, math.nan),
            lambda frame, case, limits: frame["members"][0]["material"].__setitem__("E_pa", math.inf),
            lambda frame, case, limits: case["point_loads"][0].__setitem__("value", math.nan),
            lambda frame, case, limits: case.__setitem__("nonlinear", True),
            lambda frame, case, limits: limits.__setitem__("safety_factor", .9),
        ):
            with self.subTest(mutate=mutate):
                frame, case, limits = cantilever(), load_case(), dict(LIMITS)
                mutate(frame, case, limits)
                report = fea_backend.solve_cases(frame, [case], limits)
                self.assertEqual(report["status"], "not-evaluable")


@unittest.skipUnless(HAS_RUNTIME, "optional PyNiteFEA 2.4.1 runtime is not installed")
class TestAnalyticalBeam(unittest.TestCase):
    def solve(self, frame=None, case=None, limits=None):
        return fea_backend.solve_cases(frame or cantilever(), [case or load_case()], limits or LIMITS)

    def test_cantilever_force_deflection_stress_and_equilibrium(self):
        frame = cantilever()
        report = self.solve(frame)
        self.assertEqual(report["status"], "pass", report)
        case = report["cases"][0]
        material, section = frame["members"][0]["material"], frame["members"][0]["section"]
        expected_mm = 10. / (3. * material["E_pa"] * section["Iz_m4"]) * 1000.
        self.assertAlmostEqual(case["max_displacement_mm"], expected_mm, places=9)
        self.assertAlmostEqual(case["node_displacements_mm"]["tip"]["DY"], -expected_mm, places=9)
        demand = case["member_demands"]["beam"]
        expected_stress = 10. / section["Sz_m3"]
        self.assertAlmostEqual(demand["normal_stress_upper_bound_pa"], expected_stress, places=5)
        self.assertAlmostEqual(demand["stress_utilization"], expected_stress / (250e6 / 2.), places=10)
        self.assertEqual(case["equilibrium"]["status"], "pass")
        self.assertAlmostEqual(case["equilibrium"]["reaction_force_n"][1], 10.)
        self.assertAlmostEqual(case["equilibrium"]["reaction_moment_nm"][2], 10.)
        json.dumps(report, allow_nan=False)

    def test_excess_deflection_fails(self):
        report = self.solve(case=load_case(-1000.))
        self.assertEqual(report["status"], "fail", report)
        self.assertEqual(report["cases"][0]["deflection_status"], "fail")

    def test_excess_stress_fails_independently(self):
        report = self.solve(case=load_case(-1000.), limits={"deflection_mm": 100., "safety_factor": 2.})
        self.assertEqual(report["status"], "fail", report)
        self.assertEqual(report["cases"][0]["deflection_status"], "pass")
        self.assertEqual(report["cases"][0]["stress_status"], "fail")

    def test_self_weight_uses_mass_density_times_gravity(self):
        frame = cantilever()
        case = load_case(0., self_weight=True)
        report = self.solve(frame, case, {"deflection_mm": 2., "safety_factor": 2.})
        self.assertEqual(report["status"], "pass", report)
        result = report["cases"][0]
        material, section = frame["members"][0]["material"], frame["members"][0]["section"]
        weight = material["rho_kg_m3"] * section["A_m2"] * 9.81
        expected_mm = weight / (8 * material["E_pa"] * section["Iz_m4"]) * 1000.
        self.assertAlmostEqual(result["max_displacement_mm"], expected_mm, places=9)
        self.assertAlmostEqual(result["equilibrium"]["reaction_force_n"][1], weight, places=8)
        self.assertAlmostEqual(result["equilibrium"]["reaction_moment_nm"][2], weight / 2., places=8)

    def test_arbitrary_gravity_and_point_moment_equilibrium(self):
        frame = cantilever()
        frame["nodes"]["fixed"] = [.2, .3, .4]
        frame["nodes"]["tip"] = [1.2, .3, .4]
        frame["members"][0]["rotation_deg"] = 30.
        case = load_case(.01, self_weight=True, direction="MZ")
        case["gravity"] = [1.3, -9.81, 2.7]
        report = self.solve(frame, case, {"deflection_mm": 2., "safety_factor": 2.})
        self.assertEqual(report["status"], "pass", report)
        eq = report["cases"][0]["equilibrium"]
        self.assertLess(math.hypot(*eq["force_residual_n"]), 1e-8)
        self.assertLess(math.hypot(*eq["moment_residual_nm"]), 1e-8)

    def test_axial_load_counts_absolute_nodal_displacement(self):
        frame = cantilever()
        report = self.solve(frame, load_case(100., direction="FX"))
        self.assertEqual(report["status"], "pass", report)
        expected_mm = 100. / (70e9 * frame["members"][0]["section"]["A_m2"]) * 1000.
        self.assertAlmostEqual(report["cases"][0]["max_displacement_mm"], expected_mm, places=10)

    def test_ninety_degree_section_rotation_changes_anisotropic_bending(self):
        frame = cantilever()
        frame["members"][0]["rotation_deg"] = 90.
        report = self.solve(frame, limits={"deflection_mm": 2., "safety_factor": 2.})
        self.assertEqual(report["status"], "pass", report)
        section = frame["members"][0]["section"]
        expected_mm = 10. / (3 * 70e9 * section["Iy_m4"]) * 1000.
        expected_stress = 10. / section["Sy_m3"]
        self.assertAlmostEqual(report["cases"][0]["max_displacement_mm"], expected_mm, places=9)
        self.assertAlmostEqual(report["cases"][0]["member_demands"]["beam"]["normal_stress_upper_bound_pa"],
                               expected_stress, places=5)

    def test_pinned_translation_without_rotation_is_singular(self):
        frame = cantilever()
        frame["supports"][0]["restrained"] = [True, True, True, False, False, False]
        report = self.solve(frame)
        self.assertEqual(report["status"], "not-evaluable", report)

    def test_nonfinite_member_demand_is_not_evaluable(self):
        from Pynite.PhysMember import PhysMember
        with mock.patch.object(PhysMember, "max_moment", return_value=math.nan):
            report = self.solve()
        self.assertEqual(report["status"], "not-evaluable", report)
        self.assertIn("finite", report["cases"][0]["errors"][0])

    def test_failed_equilibrium_is_not_evaluable(self):
        original = fea_backend._build_model

        def build(*args):
            model = original(*args)
            analyze = model.analyze_linear

            def corrupt(*args, **kwargs):
                analyze(*args, **kwargs)
                model.nodes["fixed"].RxnFY["prescribed"] += 1.

            model.analyze_linear = corrupt
            return model

        with mock.patch.object(fea_backend, "_build_model", side_effect=build):
            report = self.solve()
        self.assertEqual(report["status"], "not-evaluable", report)
        self.assertIn("equilibrium", report["cases"][0]["errors"][0])

    def test_warning_is_not_evaluable(self):
        original = fea_backend._extract

        def warn(*args):
            warnings.warn("synthetic solver warning", RuntimeWarning)
            return original(*args)

        with mock.patch.object(fea_backend, "_extract", side_effect=warn):
            report = self.solve()
        self.assertEqual(report["status"], "not-evaluable", report)
        self.assertEqual(report["cases"][0]["warnings"], ["RuntimeWarning"])

    def test_independent_cases_and_input_preservation(self):
        frame, cases, limits = cantilever(), [load_case(), load_case(-1000.)], dict(LIMITS)
        cases[1]["id"] = "overload"
        before = deepcopy((frame, cases, limits))
        report = fea_backend.solve_cases(frame, cases, limits)
        self.assertEqual([case["status"] for case in report["cases"]], ["pass", "fail"], report)
        self.assertEqual((frame, cases, limits), before)
        self.assertEqual(report["status"], "fail")


if __name__ == "__main__":
    unittest.main()
