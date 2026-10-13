"""Independent analytical and scope checks for authored-stock qualification."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import qualify_m3_stock as control


def _geometry():
    # A 20 mm by 40 mm analytical rectangular section. Stock runs along Z.
    # The 40 mm dimension follows section_y. These fixtures create no geometry.
    return {"axis": 2, "length_mm": 1000., "section_y": [1., 0., 0.],
            "section": {"A_m2": .0008, "Iy_m4": .04 * .02**3 / 12,
                        "Iz_m4": .02 * .04**3 / 12,
                        "Sy_m3": .04 * .02**2 / 6, "Sz_m3": .02 * .04**2 / 6}}


def _report(expected, status="pass"):
    delta = expected["tip_deflection_mm"]
    stress = expected["max_bending_stress_pa"]
    return {"status": "simulated_" + status, "leaderboard_eligible": False,
            "physical_acceptance": "not_evaluated", "legacy_scores_changed": False,
            "backend": {"status": status}, "cases": [{"status": status,
                "equilibrium": {"status": "pass"}, "max_displacement_mm": delta,
                "node_displacements_mm": {"fixed": {"resultant": 0.}, "tip": {"resultant": delta}},
                "member_demands": {"beam:0": {"normal_stress_upper_bound_pa": stress}}}]}


class TestStockFormulae(unittest.TestCase):
    def test_axes_match_independent_rectangular_cantilever_values(self):
        x = control.analytical_expectation(_geometry(), "FX", 49.)
        y = control.analytical_expectation(_geometry(), "FY", 49.)
        self.assertAlmostEqual(x["tip_deflection_mm"], 2.2192028985507246, places=12)
        self.assertAlmostEqual(x["max_bending_stress_pa"], 9_187_500., places=6)
        self.assertAlmostEqual(y["tip_deflection_mm"], 8.876811594202898, places=11)
        self.assertAlmostEqual(y["max_bending_stress_pa"], 18_375_000., places=6)
        self.assertEqual((x["bending_axis"], y["bending_axis"]), ("z", "y"))

    def test_section_roll_exchanges_global_bending_responses(self):
        rolled = _geometry()
        rolled["section_y"] = [0., -1., 0.]
        self.assertEqual(control.analytical_expectation(rolled, "FX", 49.)["bending_axis"], "y")
        self.assertEqual(control.analytical_expectation(rolled, "FY", 49.)["bending_axis"], "z")

    def test_overload_scales_linearly_without_approving_physical_strength(self):
        low = control.analytical_expectation(_geometry(), "FX", 49.)
        high = control.analytical_expectation(_geometry(), "FX", 490.)
        self.assertAlmostEqual(high["tip_deflection_mm"] / low["tip_deflection_mm"], 10.)
        self.assertAlmostEqual(high["max_bending_stress_pa"] / low["max_bending_stress_pa"], 10.)
        self.assertIn("supplier material strength", control.NOT_QUALIFIED)
        self.assertIn("connections, fixtures, inserts and composite action", control.NOT_QUALIFIED)

    def test_invalid_or_out_of_domain_inputs_are_refused(self):
        for value in (0., -1., True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(control.ControlError):
                control.analytical_expectation(_geometry(), "FX", value)
        for field, value in (("axis", 0), ("section_y", [2**-.5, 2**-.5, 0.]), ("length_mm", 0.)):
            geometry = _geometry()
            geometry[field] = value
            with self.subTest(field=field), self.assertRaises(control.ControlError):
                control.analytical_expectation(geometry, "FX", 49.)


class TestStockVerification(unittest.TestCase):
    def test_expected_pass_and_fail_are_verification_successes(self):
        for load, status in ((49., "pass"), (490., "fail")):
            expected = control.analytical_expectation(_geometry(), "FX", load)
            with self.subTest(status=status):
                result = control.verify_report(_report(expected, status), expected, status)
                self.assertTrue(result["bending_equations_verified"])
                self.assertEqual(result["relative_errors"]["tip_deflection_mm"], 0.)

    def test_wrong_axis_numerics_and_false_admission_are_refused(self):
        expected = control.analytical_expectation(_geometry(), "FX", 49.)
        report = _report(expected)
        report["cases"][0]["member_demands"]["beam:0"]["normal_stress_upper_bound_pa"] *= 2
        with self.assertRaisesRegex(control.ControlError, "analytical_agreement_failed"):
            control.verify_report(report, expected, "pass")
        for key, value in (("leaderboard_eligible", True), ("physical_acceptance", "accepted"),
                           ("legacy_scores_changed", True), ("status", "simulated_fail")):
            report = _report(expected)
            report[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(control.ControlError, "unexpected_result_or_scope"):
                control.verify_report(report, expected, "pass")

    def test_missing_equilibrium_or_extra_members_are_refused(self):
        expected = control.analytical_expectation(_geometry(), "FX", 49.)
        for defect in ("equilibrium", "member"):
            report = _report(expected)
            if defect == "equilibrium":
                report["cases"][0]["equilibrium"]["status"] = "fail"
            else:
                report["cases"][0]["member_demands"]["extra"] = {}
            with self.subTest(defect=defect), self.assertRaises(control.ControlError):
                control.verify_report(report, expected, "pass")

    def test_cli_failure_diagnostic_does_not_echo_runtime_exception(self):
        output = io.StringIO()
        with mock.patch.object(control, "run_controls", side_effect=OSError("sensitive path")), redirect_stdout(output):
            code = control.main(["--contract", "x", "--contract-sha256", "a" * 64,
                                 "--kit", "y", "--out-dir", "z"])
        self.assertEqual(code, 2)
        self.assertNotIn("sensitive path", output.getvalue())

    def test_existing_output_is_preserved_without_reading_inputs(self):
        base = (control.ROOT / "runs").resolve()
        base.relative_to(control.ROOT.resolve())
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="m3-stock-test-", dir=base) as directory:
            existing = Path(directory)
            sentinel = existing / "preserved.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with mock.patch.object(control.analysis, "digest") as digest:
                with self.assertRaises(control.ControlError):
                    control.run_controls(Path("missing"), "a" * 64, Path("missing"), existing)
                digest.assert_not_called()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
