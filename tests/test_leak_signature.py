"""Tests for the submission contamination check.

These build their own synthetic signature and submissions, so they run without
the gated marker table. Never point these at the real signature: the test file
is public and the markers are not.
"""
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_leak_signature.py"

# A synthetic task: two instances corrected structurally (large delta), one
# corrected slightly (numeric), mirroring the shape of a real signature.
SIGNATURE = {
    "schema_version": "marb_leak_signature.v0.1",
    "task": "synthetic",
    "markers": [
        {
            "instance_id": "beam_left",
            "superseded": {"translate_mm": [-900.0, 0.0, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "current": {"translate_mm": [0.0, 0.0, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "diverging_axes": "x",
            "max_delta_mm": 900.0,
        },
        {
            "instance_id": "beam_right",
            "superseded": {"translate_mm": [900.0, 0.0, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "current": {"translate_mm": [0.0, 0.0, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "diverging_axes": "x",
            "max_delta_mm": 900.0,
        },
        {
            "instance_id": "plate",
            "superseded": {"translate_mm": [0.0, 503.0, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "current": {"translate_mm": [0.0, 501.5, 0.0], "rotate_deg": [0.0, 0.0, 0.0]},
            "diverging_axes": "y",
            "max_delta_mm": 1.5,
        },
    ],
}

CORRECT = {"beam_left": [0.0, 0.0, 0.0], "beam_right": [0.0, 0.0, 0.0], "plate": [0.0, 501.5, 0.0]}
SUPERSEDED = {"beam_left": [-900.0, 0.0, 0.0], "beam_right": [900.0, 0.0, 0.0], "plate": [0.0, 503.0, 0.0]}


def _spec(poses):
    return {
        "schema_version": "assembly_spec.v0.1",
        "meta": {"project": "synthetic", "assembly_id": "t"},
        "outputs": {"step": "b/o.step", "views_dir": "b/v"},
        "instances": [
            {"id": k, "role": "part", "component_id": "c",
             "transform": {"translate_mm": v, "rotate_deg": [0.0, 0.0, 0.0]}}
            for k, v in poses.items()
        ],
    }


class TestLeakSignature(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.sig = self.dir / "sig.yaml"
        self.sig.write_text(yaml.safe_dump(SIGNATURE), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, poses):
        path = self.dir / "sub.yaml"
        path.write_text(yaml.safe_dump(_spec(poses)), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(path), "--signature", str(self.sig), "--json"],
            capture_output=True, text=True,
        )
        import json
        return json.loads(r.stdout), r.returncode

    # --- the two ends -------------------------------------------------

    def test_correct_answer_is_clean(self):
        res, code = self._run(dict(CORRECT))
        self.assertEqual(res["verdict"], "CLEAN")
        self.assertEqual(code, 0)

    def test_wholesale_copy_is_contaminated(self):
        res, code = self._run(dict(SUPERSEDED))
        self.assertEqual(res["verdict"], "CONTAMINATED")
        self.assertEqual(code, 1)
        self.assertEqual(len(res["hits"]), 3)
        self.assertEqual(res["structural_hits"], 2)

    # --- false positives are the thing that matters -------------------

    def test_slightly_wrong_honest_solve_is_clean(self):
        poses = {k: [v[0] + 5.0, v[1], v[2]] for k, v in CORRECT.items()}
        res, _ = self._run(poses)
        self.assertEqual(res["verdict"], "CLEAN")

    def test_badly_wrong_honest_solve_is_clean(self):
        """Being far off is failure, not contamination."""
        poses = {k: [v[0] + 250.0, v[1] - 80.0, v[2]] for k, v in CORRECT.items()}
        res, _ = self._run(poses)
        self.assertEqual(res["verdict"], "CLEAN")

    def test_landing_between_old_and_new_is_clean(self):
        """Halfway between the corrected and superseded plate value."""
        poses = dict(CORRECT)
        poses["plate"] = [0.0, 502.25, 0.0]
        res, _ = self._run(poses)
        self.assertEqual(res["verdict"], "CLEAN")

    # --- graduated response -------------------------------------------

    def test_one_structural_hit_is_review_not_contaminated(self):
        poses = dict(CORRECT)
        poses["beam_left"] = list(SUPERSEDED["beam_left"])
        res, code = self._run(poses)
        self.assertEqual(res["verdict"], "REVIEW")
        self.assertEqual(code, 0, "REVIEW must not fail the run; a human decides")

    def test_two_structural_hits_is_contaminated(self):
        poses = dict(CORRECT)
        poses["beam_left"] = list(SUPERSEDED["beam_left"])
        poses["beam_right"] = list(SUPERSEDED["beam_right"])
        res, code = self._run(poses)
        self.assertEqual(res["verdict"], "CONTAMINATED")
        self.assertEqual(code, 1)

    def test_only_the_small_numeric_hit_is_review(self):
        """A 1.5mm match alone is suggestive, not conclusive."""
        poses = dict(CORRECT)
        poses["plate"] = list(SUPERSEDED["plate"])
        res, _ = self._run(poses)
        self.assertEqual(res["verdict"], "REVIEW")
        self.assertFalse(res["hits"][0]["structural"])

    # --- robustness ----------------------------------------------------

    def test_float_noise_still_matches(self):
        """Formatting round-trips must not defeat the check."""
        poses = {k: [v[0] + 0.001, v[1], v[2]] for k, v in SUPERSEDED.items()}
        res, _ = self._run(poses)
        self.assertEqual(res["verdict"], "CONTAMINATED")

    def test_missing_instances_are_skipped_not_counted(self):
        res, _ = self._run({"beam_left": list(SUPERSEDED["beam_left"])})
        self.assertEqual(res["markers_applicable"], 1)
        self.assertEqual(res["markers_available"], 3)

    def test_missing_signature_is_a_setup_error(self):
        path = self.dir / "sub.yaml"
        path.write_text(yaml.safe_dump(_spec(dict(CORRECT))), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(path), "--signature", str(self.dir / "nope.yaml")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("gated", r.stderr)


if __name__ == "__main__":
    unittest.main()
