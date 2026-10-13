"""Verify centroidal bending equations with exact authored M3 stock profiles.

These twelve local controls use assumed material values and ideal cantilever
fixtures. They ignore shear-center eccentricity. Centroid loading does not
prove torsion-free physical response for an open section. These controls do
not qualify M3 materials, torsion, connections or a machine.
No replacement M3 geometry, model attempt, or leaderboard score is produced.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grader import fea_analysis as analysis
from grader import m3_structural_audit as audit
from grader.fea_step import _solids, inspect_stock_section
from scripts.demo_fea_analysis import ControlError, _new_output_directory

PROFILES = {"cbeam_4080", "vslot_2040", "vslot_2080"}
YOUNG_PA = 69e9
POISSON = .33
YIELD_PA = 145e6
SAFETY_FACTOR = 1.65
PLACEHOLDER_J_M4 = 1e-9
RELATIVE_TOLERANCE = 1e-6
MATERIAL = {"E_pa": YOUNG_PA, "G_pa": YOUNG_PA / (2 * (1 + POISSON)),
            "nu": POISSON, "rho_kg_m3": 2700., "yield_pa": YIELD_PA}
NOT_QUALIFIED = ["M3 whole-machine structural coverage", "supplier material strength",
                 "torsion J, shear center, warping and flexural-torsional coupling",
                 "connections, fixtures, inserts and composite action",
                 "self-weight, payload, travel and operating loads",
                 "service limits, ultimate capacity and physical performance",
                 "blind assignment and public submission runtime"]


def _fail(code: str) -> None:
    raise ControlError("m3.stock." + code)


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _positive(value) -> float:
    try:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            _fail("invalid_analytical_input")
        return float(value)
    except (OverflowError, ValueError):
        _fail("invalid_analytical_input")


def analytical_expectation(geometry: dict, direction: str, load_n: float) -> dict:
    """Independent Euler-Bernoulli cantilever expectations for stock along Z.

    A transverse force parallel to local y bends about local z, and vice versa.
    Forces act through the centroid in an uncoupled beam idealization. There is
    no applied axial force or torque in that idealization. Shear-center
    eccentricity is ignored, so this is not a physical torsion qualification.
    The section moduli conservatively use the farther extreme fiber.
    """
    if geometry.get("axis") != 2 or direction not in {"FX", "FY"}:
        _fail("unsupported_control_axis")
    load_n = _positive(load_n)
    length = _positive(geometry.get("length_mm")) / 1000
    section_y = geometry.get("section_y")
    if (not isinstance(section_y, list) or len(section_y) != 3
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in section_y)
            or abs(section_y[2]) > 1e-9
            or sum(abs(x) > 1e-9 for x in section_y) != 1
            or abs(sum(x*x for x in section_y) - 1) > 1e-9):
        _fail("unsupported_control_section_axis")
    force_axis = 0 if direction == "FX" else 1
    bending_axis = "z" if abs(section_y[force_axis]) > .5 else "y"
    section = geometry.get("section", {})
    inertia = _positive(section.get("I" + bending_axis + "_m4"))
    modulus = _positive(section.get("S" + bending_axis + "_m3"))
    displacement = load_n * length**3 / (3 * YOUNG_PA * inertia) * 1000
    stress = load_n * length / modulus
    return {"tip_deflection_mm": _positive(displacement),
            "max_bending_stress_pa": _positive(stress),
            "stress_utilization": stress * SAFETY_FACTOR / YIELD_PA,
            "bending_axis": bending_axis,
            "formulae": {"deflection": "P*L^3/(3*E*I)", "stress": "P*L/S"}}


def verify_report(report: dict, expected: dict, expected_status: str) -> dict:
    """Check analytical agreement and scope on the already verified raw report."""
    if (report.get("status") != "simulated_" + expected_status
            or report.get("leaderboard_eligible") is not False
            or report.get("physical_acceptance") != "not_evaluated"
            or report.get("legacy_scores_changed") is not False):
        _fail("unexpected_result_or_scope")
    cases = report.get("cases")
    backend = report.get("backend", {})
    if (not isinstance(cases, list) or len(cases) != 1
            or backend.get("status") != expected_status
            or cases[0].get("status") != expected_status
            or cases[0].get("equilibrium", {}).get("status") != "pass"):
        _fail("missing_case_or_equilibrium")
    case = cases[0]
    nodes = case.get("node_displacements_mm", {})
    demands = case.get("member_demands", {})
    if set(nodes) != {"fixed", "tip"} or set(demands) != {"beam:0"}:
        _fail("unexpected_result_coverage")
    observed = {"tip_deflection_mm": _positive(nodes["tip"].get("resultant")),
                "max_deflection_mm": _positive(case.get("max_displacement_mm")),
                "max_bending_stress_pa": _positive(demands["beam:0"].get("normal_stress_upper_bound_pa"))}
    errors = {key: abs(observed[key] / expected[key] - 1)
              for key in ("tip_deflection_mm", "max_bending_stress_pa")}
    errors["max_deflection_mm"] = abs(observed["max_deflection_mm"] / expected["tip_deflection_mm"] - 1)
    if any(value > RELATIVE_TOLERANCE for value in errors.values()):
        _fail("analytical_agreement_failed")
    return {"expected_status": expected_status, "actual_status": report["status"],
            "observed": observed, "relative_errors": errors,
            "equilibrium": case["equilibrium"], "bending_equations_verified": True}


def _control(target: Path, source_id: str, source: dict, source_bytes: bytes,
             stock, geometry: dict, direction: str, load_n: float, ordinal: int) -> dict:
    import cadquery as cq
    fixture = target / (source_id + "-" + direction.lower() + "-" + str(int(load_n)) + "n")
    fixture.mkdir()
    catalog = fixture / "catalog"
    catalog.mkdir()
    source_path = catalog / "stock.step"
    with source_path.open("xb") as stream:
        stream.write(source_bytes)
    if analysis.digest(source_path) != source["sha256"]:
        _fail("copied_source_hash_mismatch")
    # Only place the imported authored BRep. No new stock profile is generated.
    translation = (71. + ordinal * 3., -113. - ordinal * 5., 307. + ordinal * 7.)
    candidate = fixture / "candidate.step"
    cq.exporters.export(stock.translate(translation), str(candidate))
    expected = analytical_expectation(geometry, direction, load_n)
    baseline = analytical_expectation(geometry, direction, 49.)
    if baseline["stress_utilization"] >= 1:
        _fail("baseline_strength_already_exceeded")
    section = dict(geometry["section"], J_m4=PLACEHOLDER_J_M4)
    points = [list(geometry["centroid_mm"]) for _ in range(2)]
    points[0][2] = geometry["stock_bounds_mm"]["low"][2]
    points[1][2] = geometry["stock_bounds_mm"]["high"][2]
    contract = {
        "schema_version": "marb_fea_contract.v0.1", "task_id": "M3-STOCK-BENDING-CONTROL",
        "revision": "r1", "coverage": "all_solids", "joint_assumption": "rigid",
        "sources": {"beam": {"file": "stock.step", "sha256": source["sha256"], "solid_index": 0,
            "material": dict(MATERIAL), "section": section, "section_y": geometry["section_y"],
            "stations": [{"node": node, "point_mm": point}
                         for node, point in zip(("fixed", "tip"), points)],
            "property_provenance": "Exact authored stock BRep bending properties. Assumed verification material E 69 GPa, nu 0.33, rho 2700 kg/m3, yield 145 MPa. J 1e-9 m4 is a placeholder, not qualified. Pure centroidal principal-axis bending with no applied torque. No supplier, connection, shear-center, warping or physical validation."}},
        "supports": [{"node": "fixed", "restrained": [True] * 6}],
        "loads": [{"id": "tip-" + direction, "self_weight": False, "gravity": [0., 0., 0.],
                   "point_loads": [{"node": "tip", "direction": direction, "value": -load_n}]}],
        "limits": {"deflection_mm": baseline["tip_deflection_mm"] * 1.25,
                   "safety_factor": SAFETY_FACTOR},
    }
    contract_path = fixture / "contract.json"
    _write_json(contract_path, contract)
    contract_hash = analysis.digest(contract_path)
    bindings = {"schema_version": "marb_fea_bindings.v0.1",
                "candidate_sha256": analysis.digest(candidate), "contract_sha256": contract_hash,
                "members": {"beam": {"solid_index": 0}}}
    bindings_path = fixture / "bindings.json"
    _write_json(bindings_path, bindings)
    expected_status = "pass" if load_n == 49. else "fail"
    _write_json(fixture / "control.json", {"profile_id": source_id, "direction": direction,
        "load_n": load_n, "expected": expected, "expected_status": expected_status,
        "candidate_translation_mm": list(translation), "stock_geometry": geometry,
        "assumed_material": MATERIAL, "placeholder_J_m4": PLACEHOLDER_J_M4,
        "torsion_constant_qualified": False, "shear_center_qualified": False,
        "shear_center_eccentricity_modeled": False,
        "torsion_scope": "Uncoupled beam idealization only. Centroid loading does not prove torsion-free physical response.",
        "M3_analysis_admitted": False, "leaderboard_eligible": False,
        "physical_acceptance": "not_evaluated", "not_qualified": list(NOT_QUALIFIED),
        "hashes": {"source_sha256": source["sha256"], "candidate_sha256": analysis.digest(candidate),
                   "contract_sha256": contract_hash, "bindings_sha256": analysis.digest(bindings_path)}})
    report = analysis.analyze(candidate, contract_path, bindings_path, catalog, contract_hash)
    _write_json(fixture / "report.json", report)
    result = {"control_id": fixture.name, "profile_id": source_id, "direction": direction,
              "load_n": load_n, "expected": expected, "status": "verification_failed"}
    try:
        result.update(verify_report(report, expected, expected_status))
        result["status"] = "bending_equations_verified"
    except ControlError as error:
        result["error"] = str(error)
    _write_json(fixture / "verification.json", result)
    return result


def run_controls(contract_path: Path, expected_contract_hash: str,
                 archive: Path, output_dir: Path) -> dict:
    target = _new_output_directory(Path(output_dir))
    target.mkdir()
    summary = {"schema_version": "marb_m3_stock_qualification.v0.1",
               "status": "qualification_rejected", "scope": "authored_stock_centroidal_bending_equations",
               "analysis_admitted": False, "leaderboard_eligible": False,
               "physical_acceptance": "not_evaluated", "legacy_scores_changed": False,
               "material_qualified": False, "torsion_constant_qualified": False,
               "relative_error_tolerance": RELATIVE_TOLERANCE,
               "not_qualified": list(NOT_QUALIFIED), "controls": [], "errors": []}
    try:
        analysis._hash(expected_contract_hash)
        if analysis.digest(contract_path, analysis.MAX_JSON_BYTES) != expected_contract_hash:
            _fail("draft_contract_hash_mismatch")
        draft = analysis._load(contract_path)
        audit.validate_draft(draft)
        versions = {name: importlib.metadata.version(name) for name in analysis.PINNED}
        if versions != analysis.PINNED:
            _fail("runtime_version_mismatch")
        archive_hash = analysis.digest(archive, audit.MAX_ARCHIVE_BYTES)
        contents = audit._catalog_bytes(archive, draft)
        profiles = {sid for sid, source in draft["catalog"].items() if source["role"] == "beam_stock"}
        if profiles != PROFILES:
            _fail("unexpected_authored_stock_profiles")
        with (target / "draft-contract.json").open("xb") as stream:
            stream.write(contract_path.read_bytes())
        if analysis.digest(target / "draft-contract.json") != expected_contract_hash:
            _fail("copied_draft_hash_mismatch")
        summary.update(draft_contract_sha256=expected_contract_hash, archive_sha256=archive_hash,
                       runtime_versions=versions)
        source_dir = target / "authored-sources"
        source_dir.mkdir()
        ordinal = 0
        for source_id in sorted(PROFILES):
            source = draft["catalog"][source_id]
            path = source_dir / (source_id + ".step")
            with path.open("xb") as stream:
                stream.write(contents[source_id])
            solids = _solids(path)
            if len(solids) != source["solid_count"] or len(solids) != 1:
                _fail("stock_source_solid_count_mismatch")
            geometry = inspect_stock_section(solids[0], source["section_y"])
            for direction in ("FX", "FY"):
                for load_n in (49., 490.):
                    ordinal += 1
                    summary["controls"].append(_control(target, source_id, source, contents[source_id],
                        solids[0], geometry, direction, load_n, ordinal))
        if (analysis.digest(contract_path, analysis.MAX_JSON_BYTES) != expected_contract_hash
                or analysis.digest(archive, audit.MAX_ARCHIVE_BYTES) != archive_hash):
            _fail("input_changed_during_qualification")
        verified = sum(row["status"] == "bending_equations_verified" for row in summary["controls"])
        summary.update(verified_controls=verified, expected_controls=12,
                       status="bending_equations_verified" if verified == 12 else "verification_failed")
    except (ControlError, analysis.AnalysisError) as error:
        summary["errors"].append(str(error))
    except Exception:
        summary["errors"].append("m3.stock.runtime_or_io_failure")
    _write_json(target / "qualification.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = run_controls(args.contract, args.contract_sha256, args.kit, args.out_dir)
    except ControlError:
        print("m3.stock.output_directory_refused")
        return 2
    except Exception:
        print("m3.stock.runtime_or_output_failure")
        return 2
    print(summary["status"])
    return 0 if summary["status"] == "bending_equations_verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
