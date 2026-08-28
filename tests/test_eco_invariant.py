"""Synthetic regressions for the public L4-ECO invariant gate."""
from __future__ import annotations

import unittest
import math
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from grader.eco_invariant import (
    EcoInvariantError,
    InvariantTolerances,
    _attest_cadclaw_runtime,
    _validated_new_report_path,
    evaluate_snapshots,
    run_gate,
    serialize_report,
)


@dataclass(frozen=True)
class Part:
    index: int
    bbox_mm: tuple[float, float, float, float, float, float]
    center_mm: tuple[float, float, float]
    signature_mm: tuple[float, float, float]
    bbox_volume_mm3: float


@dataclass(frozen=True)
class Snapshot:
    parts: tuple[Part, ...]

    @property
    def part_count(self) -> int:
        return len(self.parts)


def part(index: int, center: tuple[float, float, float],
         dims: tuple[float, float, float]) -> Part:
    half = tuple(value / 2.0 for value in dims)
    bbox = (
        center[0] - half[0], center[1] - half[1], center[2] - half[2],
        center[0] + half[0], center[1] + half[1], center[2] + half[2],
    )
    return Part(index, bbox, center, tuple(sorted(dims)), dims[0] * dims[1] * dims[2])


BASELINE = Snapshot((
    part(0, (0.0, 0.0, 0.0), (10.0, 20.0, 30.0)),
    part(1, (100.0, 0.0, 0.0), (10.0, 20.0, 30.0)),
    part(2, (50.0, 50.0, 0.0), (5.0, 6.0, 7.0)),
))
ADDED = part(3, (75.0, 0.0, 100.0), (20.0, 1000.0, 40.0))


class TestEcoInvariant(unittest.TestCase):
    def test_exact_single_rail_addition_passes(self):
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (ADDED,)))
        self.assertEqual(report["public_gate_status"], "pass")
        self.assertEqual(report["counts"]["matched_invariant_shapes"], 3)
        self.assertNotIn("publication_eligible", report)
        self.assertNotIn("parts", report)

    def test_reordered_identical_parts_pass_deterministically(self):
        changed = Snapshot((BASELINE.parts[1], ADDED, BASELINE.parts[0], BASELINE.parts[2]))
        report = evaluate_snapshots(BASELINE, changed)
        self.assertEqual(report["public_gate_status"], "pass")

    def test_moved_existing_part_fails(self):
        moved = part(1, (100.051, 0.0, 0.0), (10.0, 20.0, 30.0))
        report = evaluate_snapshots(
            BASELINE, Snapshot((BASELINE.parts[0], moved, BASELINE.parts[2], ADDED))
        )
        self.assertEqual(report["public_gate_status"], "fail")
        self.assertEqual(report["counts"]["missing_baseline_shapes"], 1)

    def test_exact_bbox_coordinate_tolerance_passes(self):
        shifted = part(1, (100.05, 0.0, 0.0), (10.0, 20.0, 30.0))
        report = evaluate_snapshots(
            BASELINE, Snapshot((BASELINE.parts[0], shifted, BASELINE.parts[2], ADDED))
        )
        self.assertEqual(report["public_gate_status"], "pass")

    def test_bbox_coordinate_over_tolerance_fails(self):
        shifted = part(1, (100.0501, 0.0, 0.0), (10.0, 20.0, 30.0))
        report = evaluate_snapshots(
            BASELINE, Snapshot((BASELINE.parts[0], shifted, BASELINE.parts[2], ADDED))
        )
        self.assertEqual(report["public_gate_status"], "fail")

    def test_resized_existing_part_fails(self):
        resized = part(2, (50.0, 50.0, 0.0), (5.0, 6.0, 7.051))
        report = evaluate_snapshots(
            BASELINE, Snapshot((BASELINE.parts[0], BASELINE.parts[1], resized, ADDED))
        )
        self.assertEqual(report["public_gate_status"], "fail")

    def test_changed_cadclaw_signature_fails(self):
        original = BASELINE.parts[2]
        changed_signature = Part(
            original.index, original.bbox_mm, original.center_mm,
            (5.0, 6.0, 7.1), original.bbox_volume_mm3,
        )
        report = evaluate_snapshots(
            BASELINE,
            Snapshot((BASELINE.parts[0], BASELINE.parts[1], changed_signature, ADDED)),
        )
        self.assertEqual(report["public_gate_status"], "fail")

    def test_missing_existing_part_fails(self):
        report = evaluate_snapshots(BASELINE, Snapshot((BASELINE.parts[0], BASELINE.parts[1], ADDED)))
        self.assertEqual(report["public_gate_status"], "fail")

    def test_no_addition_fails(self):
        report = evaluate_snapshots(BASELINE, BASELINE)
        self.assertEqual(report["public_gate_status"], "fail")

    def test_duplicate_addition_fails(self):
        second = part(4, (80.0, 0.0, 100.0), (20.0, 1000.0, 40.0))
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (ADDED, second)))
        self.assertEqual(report["public_gate_status"], "fail")

    def test_wrong_added_source_shape_fails(self):
        wrong = part(3, (75.0, 0.0, 100.0), (20.0, 800.0, 40.0))
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (wrong,)))
        self.assertEqual(report["public_gate_status"], "fail")

    def test_nearby_but_wrong_stock_signature_fails(self):
        wrong = part(3, (75.0, 0.0, 100.0), (19.6, 999.6, 39.6))
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (wrong,)))
        self.assertEqual(report["public_gate_status"], "fail")

    def test_invalid_tolerances_fail_closed(self):
        for value in (-0.01, math.nan, math.inf):
            with self.assertRaisesRegex(ValueError, "finite and non-negative"):
                InvariantTolerances(center_mm=value)

    def test_nonfrozen_finite_tolerances_fail_closed(self):
        with self.assertRaisesRegex(EcoInvariantError, "exact frozen tolerances"):
            evaluate_snapshots(
                BASELINE,
                Snapshot(BASELINE.parts + (ADDED,)),
                limits=InvariantTolerances(
                    center_mm=1e300,
                    axis_bbox_mm=1e300,
                    bbox_volume_relative=1e300,
                    bbox_volume_absolute_mm3=1e300,
                    requested_axis_bbox_mm=1e300,
                ),
            )

    def test_wrong_added_axis_orientation_fails(self):
        wrong = part(3, (75.0, 0.0, 100.0), (1000.0, 20.0, 40.0))
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (wrong,)))
        self.assertEqual(report["public_gate_status"], "fail")
        statuses = {item["id"]: item["status"] for item in report["checks"]}
        self.assertEqual(statuses["added-stock-signature"], "pass")
        self.assertEqual(statuses["added-axis-orientation"], "fail")

    def test_duplicate_part_index_is_rejected(self):
        malformed = Snapshot((BASELINE.parts[0], BASELINE.parts[0]))
        with self.assertRaisesRegex(EcoInvariantError, "duplicate part index"):
            evaluate_snapshots(malformed, Snapshot(malformed.parts + (ADDED,)))

    def test_nonfinite_geometry_is_rejected(self):
        invalid = Part(
            9, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
            (math.nan, 0.0, 0.0), (1.0, 1.0, 1.0), 1.0,
        )
        with self.assertRaisesRegex(EcoInvariantError, "part center"):
            evaluate_snapshots(Snapshot((invalid,)), Snapshot((invalid, ADDED)))

    def test_over_limit_snapshot_is_rejected(self):
        many = tuple(part(index, (float(index), 0.0, 0.0), (1.0, 1.0, 1.0)) for index in range(257))
        with self.assertRaisesRegex(EcoInvariantError, "bounded part limit"):
            evaluate_snapshots(Snapshot(many), Snapshot(many + (ADDED,)))

    def test_declared_count_mismatch_is_rejected(self):
        class BadSnapshot:
            parts = BASELINE.parts
            part_count = 99

        with self.assertRaisesRegex(EcoInvariantError, "part count"):
            evaluate_snapshots(BadSnapshot(), Snapshot(BASELINE.parts + (ADDED,)))

    def test_aggregate_report_does_not_disclose_geometry(self):
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (ADDED,)))
        rendered = str(report)
        self.assertNotIn("75.0", rendered)
        self.assertNotIn("1000.0", rendered)
        self.assertEqual(report["private_requested_placement_check"], "required_separately")

    def test_serialized_report_is_deterministic(self):
        report = evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (ADDED,)))
        first = serialize_report(report)
        second = serialize_report(evaluate_snapshots(BASELINE, Snapshot(BASELINE.parts + (ADDED,))))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

    def test_path_validation_rejects_missing_empty_same_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.step"
            empty = root / "empty.step"
            empty.write_bytes(b"")
            full = root / "full.step"
            full.write_bytes(b"STEP")
            with self.assertRaisesRegex(EcoInvariantError, "unavailable"):
                run_gate(missing, full)
            with self.assertRaisesRegex(EcoInvariantError, "nonempty"):
                run_gate(empty, full)
            with self.assertRaisesRegex(EcoInvariantError, "must differ"):
                run_gate(full, full)
            with mock.patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(EcoInvariantError, "symbolic link"):
                    run_gate(full, root / "other.step")

    def test_run_gate_adds_only_raw_input_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.step"
            changed = root / "changed.step"
            baseline.write_bytes(b"baseline")
            changed.write_bytes(b"changed")
            roundtrip = types.ModuleType("cadclaw.roundtrip")
            roundtrip.snapshot_geometry = lambda path: (
                BASELINE if Path(path).name == "baseline.step"
                else Snapshot(BASELINE.parts + (ADDED,))
            )
            package = types.ModuleType("cadclaw")
            package.roundtrip = roundtrip
            with mock.patch.dict(
                sys.modules,
                {"cadclaw": package, "cadclaw.roundtrip": roundtrip},
            ), mock.patch(
                "grader.eco_invariant._attest_cadclaw_runtime",
                return_value={"cadclaw_version": "0.10.0"},
            ), mock.patch(
                "grader.eco_invariant._gate_source_attestation",
                return_value={"implementation_sha256": "1" * 64},
            ):
                report = run_gate(baseline, changed)
        self.assertEqual(report["public_gate_status"], "pass")
        self.assertEqual(len(report["input_artifacts"]["baseline_sha256"]), 64)
        self.assertEqual(len(report["input_artifacts"]["changed_sha256"]), 64)
        self.assertNotIn("baseline.step", str(report))
        self.assertEqual(report["runtime_attestation"]["cadclaw_version"], "0.10.0")

    def test_run_gate_rejects_input_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.step"
            changed = root / "changed.step"
            baseline.write_bytes(b"baseline")
            changed.write_bytes(b"changed")
            calls = 0

            def snapshot(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    changed.write_bytes(b"changed-after-snapshot")
                    return Snapshot(BASELINE.parts + (ADDED,))
                return BASELINE

            roundtrip = types.ModuleType("cadclaw.roundtrip")
            roundtrip.snapshot_geometry = snapshot
            package = types.ModuleType("cadclaw")
            package.roundtrip = roundtrip
            with mock.patch.dict(
                sys.modules,
                {"cadclaw": package, "cadclaw.roundtrip": roundtrip},
            ), mock.patch(
                "grader.eco_invariant._attest_cadclaw_runtime",
                return_value={"cadclaw_version": "0.10.0"},
            ):
                with self.assertRaisesRegex(EcoInvariantError, "changed while"):
                    run_gate(baseline, changed)

    def test_runtime_attestation_accepts_exact_vcs_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / "cadclaw" / "roundtrip.py"
            module.parent.mkdir()
            module.write_text("# audited test module\n", encoding="utf-8")
            distribution = mock.Mock()
            distribution.version = "0.10.0"
            distribution.files = [Path("cadclaw/roundtrip.py")]
            distribution.locate_file.side_effect = lambda item: root / item
            distribution.read_text.return_value = (
                '{"url":"https://example.invalid/CADCLAW.git",'
                '"vcs_info":{"vcs":"git","commit_id":'
                '"60fc271f68c8a794a4741f856b2dd4c9878416a6"}}'
            )
            with mock.patch(
                "grader.eco_invariant.importlib.metadata.distribution",
                return_value=distribution,
            ), mock.patch(
                "grader.eco_invariant.importlib.metadata.version",
                side_effect=lambda name: {
                    "cadquery": "2.7.0",
                    "cadquery-ocp": "7.8.1.1.post1",
                }[name],
            ):
                report = _attest_cadclaw_runtime(
                    types.SimpleNamespace(__file__=str(module))
                )
        self.assertEqual(report["cadclaw_provenance"], "vcs-direct-url")

    def test_runtime_attestation_rejects_wrong_distribution_version(self):
        distribution = mock.Mock()
        distribution.version = "0.9.0"
        distribution.read_text.return_value = "{}"
        with mock.patch(
            "grader.eco_invariant.importlib.metadata.distribution",
            return_value=distribution,
        ), mock.patch(
            "grader.eco_invariant.importlib.metadata.version",
            return_value="2.7.0",
        ):
            with self.assertRaisesRegex(EcoInvariantError, "installed CADCLAW version"):
                _attest_cadclaw_runtime(types.SimpleNamespace())

    def test_report_path_must_be_new_and_cannot_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.step"
            changed = root / "changed.step"
            baseline.write_bytes(b"baseline")
            changed.write_bytes(b"changed")
            existing = root / "existing.json"
            existing.write_text("historical", encoding="utf-8")
            with self.assertRaisesRegex(EcoInvariantError, "must be new"):
                _validated_new_report_path(existing, baseline, changed)
            with self.assertRaisesRegex(EcoInvariantError, "differ"):
                _validated_new_report_path(baseline, baseline, changed)
            target = _validated_new_report_path(root / "new.json", baseline, changed)
            self.assertEqual(target, root / "new.json")


if __name__ == "__main__":
    unittest.main()
