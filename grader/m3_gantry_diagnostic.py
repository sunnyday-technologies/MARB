"""Candidate-derived, unranked planar gantry sensitivity diagnostics.

The evaluator freezes the packet and exact stock catalog. Actual candidate
geometry supplies three stock placements. Insert engagement, support stiffness,
material identity and open-section torsion remain unqualified engineering inputs.
This command never issues a readiness pass or changes a MARB score.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grader import fea_analysis as boundary
from grader import m3_structural_audit as catalog_audit
from grader.fea_step import (FeaGeometryError, GEOMETRY_MM, VOLUME_ABS_MM3,
                             VOLUME_REL, _solids, _match, inspect_stock_section)
from scripts.demo_fea_analysis import ControlError, _new_output_directory

SCHEMA = "marb_m3_gantry_packet.v0.1"
REPORT_SCHEMA = "marb_m3_gantry_diagnostic.v0.1"
GAPS = {"material_identity", "component_masses", "carriage_restraints", "splice_transfer",
        "load_attachment_offsets", "shear_center_and_torsion", "service_limits", "physical_comparison"}
OMISSIONS = ["Unmeasured material properties, insert self-weight and all nonbeam component masses.",
             "Splice engagement, strength, slip and composite insert action.",
             "Carriage plate, wheel, support and full-machine load transfer.",
             "Shear-center eccentricity, torsion, warping and out-of-plane response.",
             "Shear deformation, buckling, geometric nonlinearity, fatigue and dynamics.",
             "Approved service limits, ultimate capacity and physical validation."]


def _fail(code):
    raise boundary.AnalysisError("m3.gantry." + code)


def validate_packet(packet: dict) -> None:
    boundary._fields(packet, {"schema_version", "task_id", "revision", "status",
                             "catalog_contract_sha256", "case", "nominal_material",
                             "support_model", "splice_model", "scope", "evidence_gaps"}, "gantry packet")
    if (packet["schema_version"] != SCHEMA or packet["task_id"] != "M3-CRETE"
            or packet["status"] != "nominal_diagnostic"):
        _fail("unsupported_packet_or_claim")
    boundary._identifier(packet["revision"])
    boundary._hash(packet["catalog_contract_sha256"])
    case = packet["case"]
    boundary._fields(case, {"force_n", "vertical_axis", "load_position", "gravity_m_s2",
                           "include_stock_self_weight", "self_weight_scope", "basis"}, "gantry case")
    if not 0 < boundary._number(case["force_n"]) <= 1e5:
        _fail("force_out_of_bounds")
    if type(case["vertical_axis"]) is not int or case["vertical_axis"] not in (0, 1, 2):
        _fail("invalid_vertical_axis")
    if (case["load_position"] != "stock_splice"
            or case["self_weight_scope"] != "two_main_stock_members_only"
            or type(case["include_stock_self_weight"]) is not bool
            or not 0 < boundary._number(case["gravity_m_s2"]) <= 100):
        _fail("unsupported_case")
    catalog_audit._text(case["basis"])
    material = packet["nominal_material"]
    boundary._fields(material, {"E_pa", "rho_kg_m3", "status", "basis"}, "nominal material")
    if material["status"] != "assumed":
        _fail("material_not_qualified_by_packet")
    if not 1e6 <= boundary._number(material["E_pa"]) <= 1e12:
        _fail("nominal_modulus_out_of_bounds")
    if not 1 <= boundary._number(material["rho_kg_m3"]) <= 30000:
        _fail("nominal_density_out_of_bounds")
    if not isinstance(material["basis"], list) or not 1 <= len(material["basis"]) <= 8:
        _fail("missing_material_basis")
    for text in material["basis"]:
        catalog_audit._text(text, 300)
    support = packet["support_model"]
    boundary._fields(support, {"kind", "normalized_vertical_stiffness", "status"}, "support hypotheses")
    if (support["kind"] != "vertical_end_restraints_free_rotation"
            or support["status"] != "hypotheses_not_measured"):
        _fail("support_hypotheses_required")
    splice = packet["splice_model"]
    boundary._fields(splice, {"kind", "normalized_rotation_stiffness", "rigid_limit",
                             "insert_action", "vertical_transfer", "status"}, "splice hypotheses")
    if (splice["kind"] != "relative_rotation_sensitivity" or splice["rigid_limit"] is not True
            or splice["insert_action"] != "geometry_witness_only"
            or splice["vertical_transfer"] != "shared_displacement_assumption"
            or splice["status"] != "hypotheses_not_measured"):
        _fail("splice_hypotheses_required")
    for values, zero in ((support["normalized_vertical_stiffness"], False),
                         (splice["normalized_rotation_stiffness"], True)):
        if not isinstance(values, list) or not 1 <= len(values) <= 8:
            _fail("invalid_stiffness_hypotheses")
        checked = [boundary._number(x) for x in values]
        if (len(set(checked)) != len(checked)
                or any(x < (0 if zero else 1e-6) or x > 1e6 for x in checked)):
            _fail("invalid_stiffness_hypotheses")
        if zero and 0.0 not in checked:
            _fail("free_splice_control_required")
    scope = packet["scope"]
    boundary._fields(scope, {"beam_source_id", "insert_source_id", "projection",
                            "geometry_closure_mm", "partial_assembly"}, "diagnostic scope")
    for key in ("beam_source_id", "insert_source_id"):
        boundary._identifier(scope[key])
    if (scope["projection"] != "uncoupled_centroidal_principal_axis_bending"
            or boundary._number(scope["geometry_closure_mm"]) != 0.01
            or scope["partial_assembly"] is not True):
        _fail("unsupported_scope")
    boundary._fields(packet["evidence_gaps"], GAPS, "engineering evidence gaps")
    if any(state != "unqualified" for state in packet["evidence_gaps"].values()):
        _fail("packet_cannot_clear_engineering_gaps")


def validate_bindings(bindings, packet, packet_hash):
    boundary._fields(bindings, {"schema_version", "candidate_sha256", "packet_sha256",
                               "beam_members", "splice_insert"}, "gantry bindings")
    if bindings["schema_version"] != "marb_m3_gantry_bindings.v0.1":
        _fail("unsupported_bindings")
    for key in ("candidate_sha256", "packet_sha256"):
        boundary._hash(bindings[key])
    if bindings["packet_sha256"] != packet_hash:
        _fail("binding_packet_hash_mismatch")
    beams = bindings["beam_members"]
    if not isinstance(beams, list) or len(beams) != 2:
        _fail("two_stock_members_required")
    indices, ids = set(), set()
    for member in beams:
        boundary._fields(member, {"id", "source_id", "solid_index"}, "stock binding")
        boundary._identifier(member["id"])
        if member["id"] in ids or member["source_id"] != packet["scope"]["beam_source_id"]:
            _fail("invalid_stock_identity")
        ids.add(member["id"])
        _binding_index(member["solid_index"], indices)
    insert = bindings["splice_insert"]
    boundary._fields(insert, {"source_id", "solid_index"}, "insert binding")
    if insert["source_id"] != packet["scope"]["insert_source_id"]:
        _fail("invalid_insert_identity")
    _binding_index(insert["solid_index"], indices)


def _binding_index(value, seen):
    if type(value) is not int or not 0 <= value < 256 or value in seen:
        _fail("invalid_or_duplicate_solid_index")
    seen.add(value)


def _source_descriptor(solid, source):
    geometry = inspect_stock_section(solid, source["section_y"])
    points = [list(geometry["centroid_mm"]) for _ in range(2)]
    for point, end in zip(points, ("low", "high")):
        point[geometry["axis"]] = geometry["stock_bounds_mm"][end][geometry["axis"]]
    descriptor = {"section": geometry["section"], "section_y": geometry["section_y"],
                  "stations": [{"node": "a", "point_mm": points[0]}, {"node": "b", "point_mm": points[1]}]}
    return geometry, descriptor


def extract_gantry(candidate, packet, bindings, catalog, contents, workspace):
    """Verify selected stock. No reference frame or connection stiffness is read."""
    solids = _solids(candidate)
    if max(m["solid_index"] for m in bindings["beam_members"] + [bindings["splice_insert"]]) >= len(solids):
        _fail("candidate_solid_index_out_of_bounds")
    selected = {}
    with tempfile.TemporaryDirectory(prefix="gantry-stock-", dir=workspace) as temp:
        for sid in {packet["scope"]["beam_source_id"], packet["scope"]["insert_source_id"]}:
            if sid not in catalog["catalog"]:
                _fail("source_not_in_frozen_catalog")
            source = catalog["catalog"][sid]
            if source["role"] != "beam_stock" or source["solid_count"] != 1:
                _fail("selected_source_not_qualified_stock")
            path = Path(temp) / (sid + ".step")
            path.write_bytes(contents[sid])
            stock = _solids(path)
            if len(stock) != 1:
                _fail("stock_source_solid_count_mismatch")
            geometry, descriptor = _source_descriptor(stock[0], source)
            selected[sid] = (stock[0], geometry, descriptor)
        beams = []
        for member in bindings["beam_members"]:
            source, geometry, descriptor = selected[member["source_id"]]
            shape = solids[member["solid_index"]]
            placement = _match(descriptor, source, shape)
            points = placement["points_mm"]
            delta = [points[1][i]-points[0][i] for i in range(3)]
            axis = max(range(3), key=lambda i: abs(delta[i]))
            if axis == packet["case"]["vertical_axis"]:
                _fail("stock_axis_parallel_to_vertical_load")
            section_y = placement["section_y"]
            force_parallel_to_y = abs(section_y[packet["case"]["vertical_axis"]]) > 0.5
            key = "z" if force_parallel_to_y else "y"
            beams.append({"id": member["id"], "source_id": member["source_id"], "shape": shape,
                          "axis": axis, "points_mm": points, "placement": placement,
                          "geometry": geometry, "I_m4": geometry["section"]["I"+key+"_m4"],
                          "S_m3": geometry["section"]["S"+key+"_m3"]})
        if beams[0]["axis"] != beams[1]["axis"]:
            _fail("stock_axes_do_not_align")
        axis = beams[0]["axis"]
        beams.sort(key=lambda member: member["points_mm"][0][axis])
        gap = math.dist(beams[0]["points_mm"][1], beams[1]["points_mm"][0])
        if gap > packet["scope"]["geometry_closure_mm"]:
            _fail("stock_splice_does_not_close")
        for key in ("I_m4", "S_m3"):
            if not math.isclose(beams[0][key], beams[1][key], rel_tol=1e-9):
                _fail("unequal_bending_axes_outside_diagnostic_scope")
        lengths = [math.dist(*member["points_mm"]) / 1000 for member in beams]
        if not math.isclose(lengths[0], lengths[1], rel_tol=1e-9):
            _fail("unequal_stock_lengths_outside_diagnostic_scope")
        witness = bindings["splice_insert"]
        source, geometry, descriptor = selected[witness["source_id"]]
        insert_shape = solids[witness["solid_index"]]
        insert_placement = _match(descriptor, source, insert_shape)
        insert_points = insert_placement["points_mm"]
        insert_axis = max(range(3), key=lambda i: abs(insert_points[1][i]-insert_points[0][i]))
        join = beams[0]["points_mm"][1][axis]
        if (insert_axis != axis or not insert_points[0][axis] + GEOMETRY_MM < join < insert_points[1][axis] - GEOMETRY_MM):
            _fail("insert_does_not_span_stock_splice")
        insert_box = insert_shape.BoundingBox()
        low, high = (insert_box.xmin, insert_box.ymin, insert_box.zmin), (insert_box.xmax, insert_box.ymax, insert_box.zmax)
        contacts = []
        for beam in beams:
            box = beam["shape"].BoundingBox()
            stock_low, stock_high = (box.xmin, box.ymin, box.zmin), (box.xmax, box.ymax, box.zmax)
            if any(low[i] < stock_low[i]-GEOMETRY_MM or high[i] > stock_high[i]+GEOMETRY_MM for i in range(3) if i != axis):
                _fail("insert_outside_stock_section_envelope")
            distance = beam["shape"].distance(insert_shape)
            interference = beam["shape"].intersect(insert_shape).Volume()
            tolerance = max(VOLUME_ABS_MM3, min(beam["shape"].Volume(), insert_shape.Volume())*VOLUME_REL)
            if not math.isfinite(distance) or distance > packet["scope"]["geometry_closure_mm"]:
                _fail("insert_contact_geometry_not_established")
            if not math.isfinite(interference) or interference > tolerance:
                _fail("insert_stock_interference")
            contacts.append({"distance_mm": distance, "interference_mm3": interference})
    return {"axis": axis, "span_m": sum(lengths), "half_span_m": lengths[0],
            "I_m4": beams[0]["I_m4"], "S_m3": beams[0]["S_m3"],
            "A_m2": beams[0]["geometry"]["section"]["A_m2"],
            "stock_centroid_stations_mm": [beams[0]["points_mm"][0], beams[0]["points_mm"][1], beams[1]["points_mm"][1]],
            "stock_splice_closure_mm": gap, "insert_contact_geometry": contacts,
            "selected_candidate_solids": 3, "unused_candidate_solids": len(solids)-3,
            "candidate_solid_count": len(solids), "whole_machine_coverage": False,
            "insert_adds_composite_stiffness": False, "insert_weight_included": False,
            "placements": [{"id": beam["id"], "source_id": beam["source_id"], "placement": beam["placement"]} for beam in beams]}


def make_model(geometry, packet, rotational, vertical, self_weight):
    length, inertia = geometry["span_m"], geometry["I_m4"]
    modulus = packet["nominal_material"]["E_pa"]
    ei = modulus * inertia
    rotation_stiffness = "rigid" if rotational == "rigid" else rotational*ei/length
    vertical_stiffness = "fixed" if vertical == "rigid" else {"stiffness_n_m": vertical*ei/length**3}
    distributed = (-packet["nominal_material"]["rho_kg_m3"]*geometry["A_m2"]*packet["case"]["gravity_m_s2"] if self_weight else 0.)
    elements = [{"id": name, "i": i, "j": i+1, "E_pa": modulus,
                 "I_m4": inertia, "S_m3": geometry["S_m3"], "distributed_load_n_m": distributed}
                for i, name in enumerate(("left", "right"))]
    return {"nodes_m": [0., geometry["half_span_m"], length], "elements": elements,
            "supports": [{"node": node, "vertical": vertical_stiffness, "rotation": "free"} for node in (0, 2)],
            "rotation_couplings": [{"id": "stock_splice", "left_element": "left", "right_element": "right",
                                    "stiffness_nm_rad": rotation_stiffness}],
            "point_loads": [{"node": 1, "force_n": -packet["case"]["force_n"]}], "point_moments": []}


def analytical_case(geometry, packet, rotational, vertical, self_weight):
    length = geometry["span_m"]
    ei = packet["nominal_material"]["E_pa"]*geometry["I_m4"]
    point = packet["case"]["force_n"]
    q = packet["nominal_material"]["rho_kg_m3"]*geometry["A_m2"]*packet["case"]["gravity_m_s2"] if self_weight else 0.
    if rotational == 0:
        return {"status": "mechanism"}
    moment = point*length/4 + q*length**2/8
    rotation_jump = 0. if rotational == "rigid" else moment/(rotational*ei/length)
    end_rotation = point*length**2/(16*ei) + q*length**3/(24*ei) + rotation_jump/2
    delta = point*length**3/(48*ei) + 5*q*length**4/(384*ei)
    if rotational != "rigid":
        delta += moment*length/(4*(rotational*ei/length))
    support_displacement = 0. if vertical == "rigid" else (point+q*length)/2/(vertical*ei/length**3)
    return {"status": "finite", "midpoint_relative_to_support_chord_mm": delta*1000,
            "midpoint_absolute_mm": (delta+support_displacement)*1000,
            "end_support_displacement_mm": support_displacement*1000,
            "max_bending_moment_nm": moment, "peak_bending_stress_pa": moment/geometry["S_m3"],
            "support_reaction_n": (point+q*length)/2,
            "splice_rotation_jump_rad": rotation_jump, "endpoint_abs_rotation_rad": end_rotation,
            "applied_force_n": -point-q*length,
            "applied_moment_nm": -point*length/2-q*length**2/2}


def verify_scenario(outcome, expected):
    if (outcome.get("analysis_admitted") is not False or outcome.get("leaderboard_eligible") is not False
            or outcome.get("physical_acceptance") != "not_evaluated"
            or outcome.get("legacy_scores_changed") is not False
            or outcome.get("capacity_acceptance") != "not_evaluated"):
        _fail("backend_unsupported_acceptance_claim")
    if expected["status"] == "mechanism":
        if (outcome.get("status") != "not_evaluable"
                or outcome.get("errors") != ["gantry.bending.mechanism_or_ill_conditioned_stiffness"]):
            _fail("free_splice_mechanism_not_refused")
        return {"status": "expected_mechanism_refused"}
    if outcome.get("status") != "diagnostic_complete":
        _fail("positive_stiffness_scenario_not_evaluable")
    displacement = outcome["node_deflections_mm"]
    if not isinstance(displacement, list) or len(displacement) != 3:
        _fail("backend_displacement_coverage")
    signed = [boundary._number(x) for x in displacement]
    actual = {"midpoint_absolute_mm": -signed[1],
              "midpoint_relative_to_support_chord_mm": -(signed[1]-(signed[0]+signed[2])/2),
              "end_support_displacement_mm": -(signed[0]+signed[2])/2}
    elements = outcome["element_results"]
    if not isinstance(elements, dict) or set(elements) != {"left", "right"}:
        _fail("backend_element_coverage")
    for key in ("max_bending_moment_nm", "peak_bending_stress_pa"):
        for element in elements.values():
            backend_key = "max_abs_bending_moment_nm" if key == "max_bending_moment_nm" else key
            value = boundary._number(element[backend_key])
            if not math.isclose(value, expected[key], rel_tol=1e-6, abs_tol=1e-8):
                _fail("backend_analytical_bending_disagreement")
    for key, value in actual.items():
        if not math.isclose(value, expected[key], rel_tol=1e-6, abs_tol=1e-8):
            _fail("backend_analytical_displacement_disagreement")
    if not math.isclose(boundary._number(outcome["max_deflection_mm"]), expected["midpoint_absolute_mm"], rel_tol=1e-6, abs_tol=1e-8):
        _fail("backend_peak_displacement_disagrees")
    reactions = outcome["support_reactions"]
    if not isinstance(reactions, list) or len(reactions) != 2 or {x["node"] for x in reactions} != {0, 2}:
        _fail("backend_support_reaction_coverage")
    for reaction in reactions:
        if (not math.isclose(boundary._number(reaction["force_n"]), expected["support_reaction_n"], rel_tol=1e-6, abs_tol=1e-8)
                or abs(boundary._number(reaction["moment_nm"])) > 1e-8):
            _fail("backend_support_reaction_disagrees")
    jump = boundary._number(outcome["rotation_jumps_rad"]["stock_splice"])
    spring_moment = boundary._number(outcome["spring_moments_nm"]["stock_splice"])
    if (not math.isclose(jump, expected["splice_rotation_jump_rad"], rel_tol=1e-6, abs_tol=1e-10)
            or not math.isclose(spring_moment, expected["max_bending_moment_nm"], rel_tol=1e-6, abs_tol=1e-8)):
        _fail("backend_splice_rotation_or_moment_disagrees")
    rotations = outcome["element_end_rotations_rad"]
    wanted = {"left": {"left": -expected["endpoint_abs_rotation_rad"], "right": -jump/2},
              "right": {"left": jump/2, "right": expected["endpoint_abs_rotation_rad"]}}
    for member, ends in wanted.items():
        for end, value in ends.items():
            if not math.isclose(boundary._number(rotations[member][end]), value, rel_tol=1e-6, abs_tol=1e-10):
                _fail("backend_member_rotation_disagrees")
    equilibrium = outcome["equilibrium"]
    if equilibrium["verified"] is not True:
        _fail("backend_equilibrium_failed")
    for name, unit in (("force", "n"), ("moment", "nm")):
        applied = boundary._number(equilibrium["applied_"+name+"_"+unit])
        reaction = boundary._number(equilibrium["reaction_"+name+"_"+unit])
        trusted = expected["applied_"+name+"_"+unit]
        tolerance = 1e-7*max(1., abs(trusted))
        if (not math.isclose(applied, trusted, rel_tol=1e-9, abs_tol=1e-9)
                or abs(applied+reaction) > tolerance):
            _fail("backend_equilibrium_resultants_disagree")
        claimed_residual = boundary._number(equilibrium[name+"_residual_"+unit])
        claimed_tolerance = boundary._number(equilibrium[name+"_tolerance_"+unit], True)
        if (not math.isclose(claimed_residual, applied+reaction, rel_tol=1e-9, abs_tol=1e-9)
                or claimed_tolerance > tolerance*(1+1e-9)):
            _fail("backend_equilibrium_evidence_disagrees")
    return {"status": "analytical_equations_verified", **actual}


def linear_review(outcome, span):
    """Declared review triggers. These do not validate a physical linear domain."""
    if outcome.get("status") != "diagnostic_complete":
        return {"status": "not_evaluable", "physical_domain_qualified": False}
    rotations = outcome["element_end_rotations_rad"]
    peak_rotation = max(abs(boundary._number(value)) for pair in rotations.values() for value in pair.values())
    deflection_ratio = boundary._number(outcome["max_deflection_mm"])/1000/span
    return {"status": "review_required" if peak_rotation > .05 or deflection_ratio > .01 else "within_review_triggers",
            "peak_abs_rotation_rad": peak_rotation, "deflection_span_ratio": deflection_ratio,
            "review_triggers": {"rotation_rad": .05, "deflection_span_ratio": .01},
            "physical_domain_qualified": False}


def analyze(candidate, bindings_path, packet_path, expected_packet_hash,
            catalog_path, expected_catalog_hash, archive, workspace):
    report = {"schema_version": REPORT_SCHEMA, "status": "not_evaluable", "errors": [], "cases": [],
              "publication_status": "unranked_nominal_engineering_diagnostic",
              "analysis_admitted": False, "leaderboard_eligible": False,
              "physical_acceptance": "not_evaluated", "legacy_scores_changed": False,
              "service_limit_assessment": "not_qualified", "strength_assessment": "not_qualified",
              "whole_machine_coverage": False, "not_checked": list(OMISSIONS)}
    report["model_assumptions"] = ["Shared splice vertical displacement assumes complete shear transfer.",
                                   "Rotational splice and grounded vertical stiffness are unmeasured hypotheses.",
                                   "Only the main stock members contribute nominal self-weight.",
                                   "The planar model omits physical shear-center and torsion behavior."]
    try:
        for value in (expected_packet_hash, expected_catalog_hash):
            boundary._hash(value)
        paths = {"candidate": (candidate, boundary.MAX_STEP_BYTES), "bindings": (bindings_path, boundary.MAX_JSON_BYTES),
                 "packet": (packet_path, boundary.MAX_JSON_BYTES), "catalog_contract": (catalog_path, boundary.MAX_JSON_BYTES),
                 "kit": (archive, catalog_audit.MAX_ARCHIVE_BYTES)}
        hashes = {name: boundary.digest(path, limit) for name, (path, limit) in paths.items()}
        if hashes["packet"] != expected_packet_hash or hashes["catalog_contract"] != expected_catalog_hash:
            _fail("frozen_input_hash_mismatch")
        packet, catalog = boundary._load(packet_path), boundary._load(catalog_path)
        validate_packet(packet)
        catalog_audit.validate_draft(catalog)
        if packet["catalog_contract_sha256"] != expected_catalog_hash:
            _fail("packet_catalog_identity_mismatch")
        bindings = boundary._load(bindings_path)
        validate_bindings(bindings, packet, expected_packet_hash)
        if bindings["candidate_sha256"] != hashes["candidate"]:
            _fail("candidate_binding_hash_mismatch")
        versions = {name: importlib.metadata.version(name) for name in boundary.PINNED}
        if versions != boundary.PINNED:
            _fail("runtime_version_mismatch")
        source_hash = hashlib.sha256()
        for name in ("m3_gantry_diagnostic.py", "gantry_bending.py", "fea_step.py", "fea_analysis.py", "m3_structural_audit.py"):
            source_hash.update(name.encode("ascii"))
            source_hash.update(Path(__file__).with_name(name).read_bytes())
        report["provenance"] = {"input_sha256": hashes, "implementation_sha256": source_hash.hexdigest(),
                                 "runtime_versions": versions, "task_revision": packet["revision"]}
        report["evidence_gaps"] = packet["evidence_gaps"]
        contents = catalog_audit._catalog_bytes(archive, catalog)
        geometry = extract_gantry(candidate, packet, bindings, catalog, contents, workspace)
        report["geometry"] = geometry
        from grader.gantry_bending import solve_bending
        settings = [("rigid", "rigid")]
        settings.extend((value, "rigid") for value in packet["splice_model"]["normalized_rotation_stiffness"])
        settings.extend(("rigid", value) for value in packet["support_model"]["normalized_vertical_stiffness"])
        for self_weight in ([False, True] if packet["case"]["include_stock_self_weight"] else [False]):
            for ordinal, (rotational, vertical) in enumerate(settings):
                model = make_model(geometry, packet, rotational, vertical, self_weight)
                expected = analytical_case(geometry, packet, rotational, vertical, self_weight)
                outcome = solve_bending(model)
                verification = verify_scenario(outcome, expected)
                report["cases"].append({"id": ("with_stock_weight" if self_weight else "force_only")+f"-{ordinal}",
                                        "splice_hypothesis": rotational, "support_hypothesis": vertical,
                                        "include_stock_self_weight": self_weight, "model": model,
                                        "analytical_expectation": expected, "verification": verification,
                                        "linear_assumption_review": linear_review(outcome, geometry["span_m"]),
                                        "stiffness_basis": {"rotation_scale": "k_theta = normalized * E * I / span",
                                                            "vertical_scale": "k_vertical = normalized * E * I / span^3",
                                                            "hardware_properties_measured": False},
                                        "backend": outcome})
        if any(boundary.digest(path, limit) != hashes[name] for name, (path, limit) in paths.items()):
            _fail("input_changed_during_diagnostic")
        report["status"] = "diagnostic_complete"
    except (boundary.AnalysisError, FeaGeometryError) as error:
        report["errors"].append(str(error))
    except Exception:
        report["errors"].append("m3.gantry.runtime_or_io_failure")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("step", "bindings", "packet", "catalog-contract", "kit", "out-dir"):
        parser.add_argument("--"+name, required=True, type=Path)
    for name in ("packet-sha256", "catalog-contract-sha256"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args(argv)
    try:
        args.out_dir = _new_output_directory(args.out_dir)
        args.out_dir.mkdir(parents=True, exist_ok=False)
    except (OSError, RuntimeError, ControlError):
        print("m3.gantry.output_directory_refused")
        return 2
    report = analyze(args.step, args.bindings, args.packet, args.packet_sha256,
                     args.catalog_contract, args.catalog_contract_sha256, args.kit, args.out_dir)
    try:
        with (args.out_dir/"diagnostic.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError):
        print("m3.gantry.output_write_failed")
        return 2
    print(report["status"])
    return 0 if report["status"] == "diagnostic_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
