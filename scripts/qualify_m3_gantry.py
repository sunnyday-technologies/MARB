"""Preserve assisted gantry diagnostics and adversarial stock controls.

This operator qualification uses supplied authored stock placements. It never
generates substitute parts, performs inference, admits a benchmark task or
records a physical measurement. Reports and bindings remain private evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grader import fea_analysis as boundary
from grader import m3_gantry_diagnostic as diagnostic
from grader.fea_step import _solids
from scripts.demo_fea_analysis import ControlError, _new_output_directory


def _write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def _fail(code):
    raise ControlError("m3.gantry.qualification."+code)


def _implementation_digest():
    fingerprint = hashlib.sha256()
    for path in (Path(__file__), ROOT/"grader/m3_gantry_diagnostic.py", ROOT/"grader/gantry_bending.py",
                 ROOT/"grader/fea_step.py", ROOT/"grader/fea_analysis.py", ROOT/"grader/m3_structural_audit.py",
                 ROOT/"scripts/demo_fea_analysis.py"):
        fingerprint.update(path.name.encode("ascii"))
        fingerprint.update(path.read_bytes())
    return fingerprint.hexdigest()


def _accepted(report, expected):
    if (report.get("status") != expected or report.get("analysis_admitted") is not False
            or report.get("leaderboard_eligible") is not False
            or report.get("physical_acceptance") != "not_evaluated"
            or report.get("legacy_scores_changed") is not False
            or report.get("whole_machine_coverage") is not False
            or report.get("strength_assessment") != "not_qualified"
            or report.get("service_limit_assessment") != "not_qualified"):
        _fail("unexpected_outcome_or_claim")
    if expected == "diagnostic_complete":
        expected_ids = {prefix+f"-{ordinal}" for prefix in ("force_only", "with_stock_weight") for ordinal in range(7)}
        if (len(report.get("cases", [])) != 14
                or {case.get("id") for case in report["cases"]} != expected_ids
                or sum(c["verification"]["status"] == "expected_mechanism_refused" for c in report["cases"]) != 2
                or sum(c["verification"]["status"] == "analytical_equations_verified" for c in report["cases"]) != 12):
            _fail("incomplete_sensitivity_coverage")
    elif not report.get("errors"):
        _fail("refusal_has_no_diagnostic")


def _case(output, name, step, bindings, packet, catalog, catalog_hash, kit):
    destination = output/name
    destination.mkdir()
    packet_path, binding_path = destination/"packet.json", destination/"bindings.json"
    _write(packet_path, packet)
    packet_hash = boundary.digest(packet_path, boundary.MAX_JSON_BYTES)
    frozen_binding = json.loads(json.dumps(bindings))
    frozen_binding["candidate_sha256"] = boundary.digest(step)
    frozen_binding["packet_sha256"] = packet_hash
    _write(binding_path, frozen_binding)
    report = diagnostic.analyze(step, binding_path, packet_path, packet_hash,
                                catalog, catalog_hash, kit, destination)
    _write(destination/"diagnostic.json", report)
    return report


def run_qualification(step, bindings_path, packet_path, expected_packet_hash,
                      catalog_path, expected_catalog_hash, kit, output_dir):
    target = _new_output_directory(Path(output_dir))
    target.mkdir()
    summary = {"schema_version": "marb_m3_gantry_qualification.v0.1",
               "status": "qualification_rejected", "controls": [], "errors": [],
               "scope": "assisted_candidate_geometry_and_nominal_planar_equations",
               "model_attempt": False, "analysis_admitted": False,
               "leaderboard_eligible": False, "physical_acceptance": "not_evaluated",
               "legacy_scores_changed": False}
    try:
        implementation_hash = _implementation_digest()
        summary["implementation_sha256"] = implementation_hash
        for value in (expected_packet_hash, expected_catalog_hash):
            boundary._hash(value)
        originals = {"candidate": (Path(step), boundary.MAX_STEP_BYTES),
                     "bindings": (Path(bindings_path), boundary.MAX_JSON_BYTES),
                     "packet": (Path(packet_path), boundary.MAX_JSON_BYTES),
                     "catalog": (Path(catalog_path), boundary.MAX_JSON_BYTES),
                     "kit": (Path(kit), diagnostic.catalog_audit.MAX_ARCHIVE_BYTES)}
        hashes = {}
        summary["input_sha256"] = hashes
        for key, (path, limit) in originals.items():
            hashes[key] = boundary.digest(path, limit)
        if hashes["packet"] != expected_packet_hash or hashes["catalog"] != expected_catalog_hash:
            _fail("frozen_input_hash_mismatch")
        packet, bindings = boundary._load(Path(packet_path)), boundary._load(Path(bindings_path))
        diagnostic.validate_packet(packet)
        diagnostic.validate_bindings(bindings, packet, expected_packet_hash)
        if packet["case"]["force_n"] != 49.0:
            _fail("qualification_requires_prescribed_49n_packet")
        if bindings["candidate_sha256"] != hashes["candidate"]:
            _fail("candidate_binding_hash_mismatch")
        # Copy fixed filenames, never supplied names. Each snapshot is checked
        # against the preflight identity before it reaches native geometry.
        snapshot = target/"frozen-inputs"
        snapshot.mkdir()
        paths = {}
        for key, (path, limit) in originals.items():
            destination = snapshot/(key+path.suffix.lower())
            with destination.open("xb") as stream:
                stream.write(path.read_bytes())
            if boundary.digest(destination, limit) != hashes[key]:
                _fail("snapshot_identity_mismatch")
            paths[key] = destination
        original_report = diagnostic.analyze(paths["candidate"], paths["bindings"], paths["packet"], expected_packet_hash,
                                              paths["catalog"], expected_catalog_hash, paths["kit"], target)
        _write(target/"supplied-candidate-diagnostic.json", original_report)
        _accepted(original_report, "diagnostic_complete")
        summary["controls"].append({"id": "supplied_candidate", "status": original_report["status"],
                                    "expected": "diagnostic_complete"})
        import cadquery as cq
        source_solids = _solids(paths["candidate"])
        beam_bindings = bindings["beam_members"]
        selected = [source_solids[member["solid_index"]] for member in beam_bindings]
        selected.append(source_solids[bindings["splice_insert"]["solid_index"]])
        local_bindings = json.loads(json.dumps(bindings))
        for index, member in enumerate(local_bindings["beam_members"]):
            member["solid_index"] = index
        local_bindings["splice_insert"]["solid_index"] = 2
        geometry = original_report["geometry"]
        axis = geometry["axis"]
        axis_a, axis_b = geometry["stock_centroid_stations_mm"][0], geometry["stock_centroid_stations_mm"][-1]
        translation = [0., 0., 0.]
        translation[axis] = 5.0
        insert_move = [0., 0., 0.]
        insert_move[axis] = geometry["span_m"]*1000
        controls = [
            ("stock_subset", list(selected), 49., "diagnostic_complete"),
            ("weak_axis", [shape.rotate(axis_a, axis_b, 90) for shape in selected], 49., "diagnostic_complete"),
            ("stock_gap", [selected[0], selected[1].translate(translation), selected[2]], 49., "not_evaluable"),
            ("missing_insert", list(selected[:2]), 49., "not_evaluable"),
            ("misplaced_insert", [selected[0], selected[1], selected[2].translate(insert_move)], 49., "not_evaluable"),
            ("altered_stock", [selected[0].scale(1.01), selected[1], selected[2]], 49., "not_evaluable"),
            ("overload_490n", list(selected), 490., "diagnostic_complete"),
        ]
        outcomes = {}
        expected_refusals = {"stock_gap": "m3.gantry.stock_splice_does_not_close",
                             "missing_insert": "m3.gantry.candidate_solid_index_out_of_bounds",
                             "misplaced_insert": "m3.gantry.insert_does_not_span_stock_splice",
                             "altered_stock": "fea.geometry.catalog_geometry_mismatch"}
        for name, shapes, force, expected in controls:
            candidate = target/(name+".step")
            cq.exporters.export(cq.Compound.makeCompound(shapes), str(candidate))
            fixture_packet = json.loads(json.dumps(packet))
            fixture_packet["revision"] = "control-"+name.replace("_", "-")
            fixture_packet["case"]["force_n"] = force
            report = _case(target, name, candidate, local_bindings, fixture_packet,
                           paths["catalog"], expected_catalog_hash, paths["kit"])
            _accepted(report, expected)
            if name in expected_refusals and report["errors"] != [expected_refusals[name]]:
                _fail("unexpected_refusal_cause")
            outcomes[name] = report
            summary["controls"].append({"id": name, "status": report["status"], "expected": expected,
                                        "errors": report["errors"]})
        def finite_case(report):
            return next(case for case in report["cases"] if case["id"] == "force_only-0")
        baseline = finite_case(outcomes["stock_subset"])["verification"]["midpoint_absolute_mm"]
        weak = finite_case(outcomes["weak_axis"])["verification"]["midpoint_absolute_mm"]
        overload = finite_case(outcomes["overload_490n"])["verification"]["midpoint_absolute_mm"]
        expected_weak_ratio = outcomes["stock_subset"]["geometry"]["I_m4"]/outcomes["weak_axis"]["geometry"]["I_m4"]
        if (not math.isclose(weak/baseline, expected_weak_ratio, rel_tol=1e-6)
                or not math.isclose(overload/baseline, 10., rel_tol=1e-6)):
            _fail("axis_or_load_scaling_failed")
        if not any(case["linear_assumption_review"]["status"] == "review_required" for case in outcomes["overload_490n"]["cases"]):
            _fail("large_overload_review_not_triggered")
        if any(boundary.digest(path, limit) != hashes[key] for key, (path, limit) in originals.items()):
            _fail("operator_input_changed_during_qualification")
        if _implementation_digest() != implementation_hash:
            _fail("implementation_changed_during_qualification")
        summary.update(status="qualification_complete", input_sha256=hashes,
                       implementation_sha256=implementation_hash,
                       weak_axis_displacement_ratio=weak/baseline, overload_displacement_ratio=overload/baseline,
                       supplied_candidate_solids=geometry["candidate_solid_count"],
                       selected_candidate_solids=geometry["selected_candidate_solids"],
                       unused_candidate_solids=geometry["unused_candidate_solids"],
                       references_contain_private_placements=True)
    except (ControlError, boundary.AnalysisError) as error:
        summary["errors"].append(str(error))
    except Exception:
        summary["errors"].append("m3.gantry.qualification.runtime_or_io_failure")
    _write(target/"qualification.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("step", "bindings", "packet", "catalog-contract", "kit", "out-dir"):
        parser.add_argument("--"+name, required=True, type=Path)
    for name in ("packet-sha256", "catalog-contract-sha256"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_qualification(args.step, args.bindings, args.packet, args.packet_sha256,
                                   args.catalog_contract, args.catalog_contract_sha256, args.kit, args.out_dir)
    except Exception:
        print("m3.gantry.qualification.output_or_runtime_refused")
        return 2
    print(result["status"])
    return 0 if result["status"] == "qualification_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
