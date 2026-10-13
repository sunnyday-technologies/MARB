"""Optional, independent static linear beam analysis for MARB.

This module accepts an explicit, SI-unit beam frame. It does not infer joints,
materials, supports, or loads from STEP geometry. It does not change MARB scores.
PyNite and NumPy imports are lazy so legacy grading needs neither dependency.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.metadata
import io
import math
import numbers
import warnings


PINNED_PYNITE_VERSION = "2.4.1"
NOT_CHECKED = [
    "shear and torsion strength",
    "buckling and geometric or material nonlinearity",
    "fastener, joint, and contact strength",
    "fatigue and dynamics",
    "physical validation",
]
_DOFS = ("DX", "DY", "DZ", "RX", "RY", "RZ")
_LOAD_DIRECTIONS = ("FX", "FY", "FZ", "MX", "MY", "MZ")


class _NotEvaluable(ValueError):
    """An input, solver, or demand coverage condition prevents a valid check."""


def _finite(value, label: str, *, positive=False, nonnegative=False) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise _NotEvaluable(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise _NotEvaluable(f"{label} must be finite")
    if positive and value <= 0:
        raise _NotEvaluable(f"{label} must be positive")
    if nonnegative and value < 0:
        raise _NotEvaluable(f"{label} must be nonnegative")
    return value


def _mapping(value, label: str, required: set[str], optional=()) -> dict:
    if not isinstance(value, dict):
        raise _NotEvaluable(f"{label} must be an object")
    if not required <= value.keys():
        raise _NotEvaluable(f"{label} is missing required fields")
    if set(value) - required - set(optional):
        raise _NotEvaluable(f"{label} contains unsupported fields")
    return value


def _identifier(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _NotEvaluable(f"{label} must be a nonempty string")
    return value


def _vector(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise _NotEvaluable(f"{label} must contain three SI-unit components")
    return tuple(_finite(v, label) for v in value)


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0])


def _add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def _validate(frame, cases, limits):
    _mapping(frame, "frame", {"nodes", "members", "supports", "joint_assumption"})
    if frame["joint_assumption"] != "rigid":
        raise _NotEvaluable("only the explicit rigid joint assumption is supported")
    _mapping(limits, "limits", {"deflection_mm", "safety_factor"})
    _finite(limits["deflection_mm"], "deflection limit", positive=True)
    if _finite(limits["safety_factor"], "safety factor", positive=True) < 1:
        raise _NotEvaluable("safety factor must be at least one")
    if not isinstance(frame["nodes"], dict) or not frame["nodes"]:
        raise _NotEvaluable("frame must contain nodes")
    nodes = {}
    for name, point in frame["nodes"].items():
        nodes[_identifier(name, "node id")] = _vector(point, "node coordinates")
    node_items = list(nodes.items())
    for index, (_, a) in enumerate(node_items):
        for _, b in node_items[index + 1:]:
            if math.hypot(*(b[i] - a[i] for i in range(3))) <= 1e-9:
                raise _NotEvaluable("distinct node ids have coincident coordinates; share one explicit node id")
    if not isinstance(frame["members"], list) or not frame["members"]:
        raise _NotEvaluable("frame must contain beam members")
    graph = {name: set() for name in nodes}
    member_ids = set()
    for member in frame["members"]:
        _mapping(member, "member", {"id", "i_node", "j_node", "material", "section",
                                    "rotation_deg"})
        mid = _identifier(member["id"], "member id")
        if mid in member_ids:
            raise _NotEvaluable("member ids must be unique")
        member_ids.add(mid)
        a, b = member["i_node"], member["j_node"]
        if a not in nodes or b not in nodes:
            raise _NotEvaluable("member references a missing node")
        delta = tuple(nodes[b][i] - nodes[a][i] for i in range(3))
        length = math.hypot(*delta)
        if not math.isfinite(length) or length <= 1e-9:
            raise _NotEvaluable("member length must be finite and exceed 1e-9 m")
        graph[a].add(b)
        graph[b].add(a)
        # PyNite splits physical members at any collinear node. Require explicit
        # segments to prevent the solver from inventing an undeclared joint.
        for name, point in nodes.items():
            if name in (a, b):
                continue
            relative = tuple(point[i] - nodes[a][i] for i in range(3))
            along = sum(relative[i] * delta[i] for i in range(3)) / length**2
            distance = math.hypot(*_cross(relative, delta)) / length
            if 0 < along < 1 and distance <= 1e-9 * max(1., length):
                raise _NotEvaluable("an interior node requires explicit beam segments")
        material = _mapping(member["material"], "material",
                            {"E_pa", "G_pa", "nu", "rho_kg_m3", "yield_pa"})
        for key in ("E_pa", "G_pa", "yield_pa"):
            _finite(material[key], key, positive=True)
        _finite(material["rho_kg_m3"], "density", nonnegative=True)
        nu = _finite(material["nu"], "Poisson ratio")
        if not -1 < nu < .5:
            raise _NotEvaluable("Poisson ratio must lie between -1 and 0.5")
        section = _mapping(member["section"], "section",
                           {"A_m2", "Iy_m4", "Iz_m4", "J_m4", "Sy_m3", "Sz_m3"})
        for key, value in section.items():
            _finite(value, key, positive=True)
        _finite(member["rotation_deg"], "section rotation")
    if not isinstance(frame["supports"], list):
        raise _NotEvaluable("supports must be a list")
    restrained_nodes = set()
    support_nodes = set()
    for support in frame["supports"]:
        _mapping(support, "support", {"node", "restrained"})
        name = support["node"]
        if name not in nodes or name in support_nodes:
            raise _NotEvaluable("support references a missing or duplicate node")
        support_nodes.add(name)
        restraints = support["restrained"]
        if (not isinstance(restraints, (tuple, list)) or len(restraints) != 6
                or any(type(v) is not bool for v in restraints)):
            raise _NotEvaluable("support restraints must be six booleans in DX DY DZ RX RY RZ order")
        if any(restraints):
            restrained_nodes.add(name)
    remaining = set(nodes)
    while remaining:
        pending = [next(iter(remaining))]
        component = set()
        while pending:
            name = pending.pop()
            if name not in component:
                component.add(name)
                pending.extend(graph[name] - component)
        remaining -= component
        if not component & restrained_nodes:
            raise _NotEvaluable("a frame component has no support restraints")
    if not isinstance(cases, list) or not cases:
        raise _NotEvaluable("at least one prescribed load case is required")
    case_ids = set()
    for case in cases:
        _mapping(case, "case", {"id", "self_weight", "gravity", "point_loads"})
        cid = _identifier(case["id"], "case id")
        if cid in case_ids:
            raise _NotEvaluable("case ids must be unique")
        case_ids.add(cid)
        if type(case["self_weight"]) is not bool:
            raise _NotEvaluable("self_weight must be a boolean")
        _vector(case["gravity"], "gravity")
        if not isinstance(case["point_loads"], list):
            raise _NotEvaluable("point_loads must be a list")
        for load in case["point_loads"]:
            _mapping(load, "point load", {"node", "direction", "value"})
            if load["node"] not in nodes:
                raise _NotEvaluable("point load references a missing node")
            if load["direction"] not in _LOAD_DIRECTIONS:
                raise _NotEvaluable("point loads require a global FX FY FZ MX MY MZ direction")
            _finite(load["value"], "point load")


def _load_runtime():
    version = importlib.metadata.version("PyNiteFEA")
    if version != PINNED_PYNITE_VERSION:
        raise _NotEvaluable("PyNiteFEA version does not match the pinned 2.4.1 backend")
    from Pynite import FEModel3D
    import numpy as np
    return FEModel3D, np, version


def _build_model(model_type, frame, case):
    model = model_type()
    for name, point in frame["nodes"].items():
        model.add_node(name, *point)
    for index, member in enumerate(frame["members"]):
        material, section = member["material"], member["section"]
        mat_name, sec_name = f"material_{index}", f"section_{index}"
        # Explicit distributed gravity loads below handle density in kg/m3.
        # Never pass a mass density to PyNite's weight-density convenience API.
        model.add_material(mat_name, material["E_pa"], material["G_pa"],
                           material["nu"], 0., fy=material["yield_pa"])
        model.add_section(sec_name, section["A_m2"], section["Iy_m4"],
                          section["Iz_m4"], section["J_m4"])
        model.add_member(member["id"], member["i_node"], member["j_node"],
                         mat_name, sec_name, rotation=member["rotation_deg"])
    for support in frame["supports"]:
        model.def_support(support["node"], *support["restrained"])
    for load in case["point_loads"]:
        model.add_node_load(load["node"], load["direction"], load["value"], case="prescribed")
    if case["self_weight"]:
        for member in frame["members"]:
            mass_per_length = member["material"]["rho_kg_m3"] * member["section"]["A_m2"]
            for direction, gravity in zip(_LOAD_DIRECTIONS[:3], case["gravity"]):
                intensity = _finite(mass_per_length * gravity, "gravity load intensity")
                if intensity:
                    model.add_member_dist_load(member["id"], direction, intensity, intensity,
                                               case="prescribed")
    model.add_load_combo("prescribed", {"prescribed": 1.})
    return model


def _check_stiffness(model, np):
    """Reject rigid modes that PyNite's diagonal stability check can miss."""
    stiffness = np.asarray(model.K("prescribed", check_stability=True, sparse=False), dtype=float)
    free = [node.ID * 6 + i for node in model.nodes.values()
            for i, dof in enumerate(_DOFS) if not getattr(node, "support_" + dof)]
    if not free:
        return
    reduced = stiffness[np.ix_(free, free)]
    if not np.isfinite(reduced).all():
        raise _NotEvaluable("stiffness contains nonfinite values")
    diagonal = np.diag(reduced)
    if (diagonal <= 0).any():
        raise _NotEvaluable("frame stiffness is singular or nonpositive")
    scales = np.sqrt(diagonal)
    normalized = reduced / scales[:, None] / scales[None, :]
    eigenvalues = np.linalg.eigvalsh(normalized)
    if (not np.isfinite(eigenvalues).all()
            or eigenvalues[0] <= eigenvalues[-1] * 1e-12):
        raise _NotEvaluable("frame stiffness is singular or numerically ill-conditioned")


def _transverse_extrema(member, direction, np):
    """Find first-order elastic-curve extrema instead of PyNite's 100 samples.

    Version 2.4.1 exposes beam segments after a demand query. For each segment,
    displacement is a polynomial through degree five. Its derivative roots and
    endpoints cover the transverse displacement extrema exactly to roundoff.
    """
    member.deflection(direction, 0., "prescribed")
    segments = member.SegmentsZ if direction == "dy" else member.SegmentsY
    if not segments:
        raise _NotEvaluable("member has no transverse result segments")
    values = []
    for segment in segments:
        length = _finite(segment.Length(), "result segment length", positive=True)
        ei = _finite(segment.EI, "result segment EI", positive=True)
        sign = 1. if direction == "dy" else -1.
        # Coefficients are scaled to t=x/L in [0, 1] for root conditioning.
        coefficients = [
            _finite(segment.delta1, "segment displacement"),
            sign * _finite(segment.theta1, "segment slope") * length,
            -sign * _finite(segment.M1, "segment moment") * length**2 / (2 * ei),
            _finite(segment.V1, "segment shear") * length**3 / (6 * ei),
            _finite(segment.w1, "segment load") * length**4 / (24 * ei),
            (_finite(segment.w2, "segment load") - segment.w1) * length**4 / (120 * ei),
        ]
        polynomial = np.polynomial.Polynomial(coefficients)
        roots = polynomial.deriv().roots()
        if not np.isfinite(roots).all():
            raise _NotEvaluable("transverse extrema roots are nonfinite")
        positions = [0., 1.]
        positions.extend(float(root.real) for root in roots
                         if abs(root.imag) <= 1e-8 and 0 < root.real < 1)
        for position in positions:
            value = _finite(segment.deflection(position * length), "transverse displacement")
            fitted = _finite(polynomial(position), "transverse displacement polynomial")
            if not math.isclose(value, fitted, rel_tol=1e-8, abs_tol=1e-12):
                raise _NotEvaluable("pinned transverse displacement formulation does not match solver")
            values.append(value)
    return min(values), max(values)


def _axial_extrema(member, np):
    member.deflection("dx", 0., "prescribed")
    if not member.SegmentsZ:
        raise _NotEvaluable("member has no axial result segments")
    values = []
    for segment in member.SegmentsZ:
        length = _finite(segment.Length(), "result segment length", positive=True)
        ea = _finite(segment.EA, "result segment EA", positive=True)
        coefficients = [
            _finite(segment.delta_x1, "axial displacement"),
            -_finite(segment.P1, "segment axial force") * length / ea,
            -_finite(segment.p1, "segment axial load") * length**2 / (2 * ea),
            -(_finite(segment.p2, "segment axial load") - segment.p1) * length**2 / (6 * ea),
        ]
        polynomial = np.polynomial.Polynomial(coefficients)
        roots = polynomial.deriv().roots()
        if not np.isfinite(roots).all():
            raise _NotEvaluable("axial extrema roots are nonfinite")
        positions = [0., 1.]
        positions.extend(float(root.real) for root in roots
                         if abs(root.imag) <= 1e-8 and 0 < root.real < 1)
        values.extend(_finite(segment.axial_deflection(p * length), "axial displacement")
                      for p in positions)
    return min(values), max(values)


def _equilibrium(model, frame, case):
    applied_force = (0., 0., 0.)
    applied_moment = (0., 0., 0.)
    force_scale = moment_scale = 0.

    def accumulate(force, moment, position):
        nonlocal applied_force, applied_moment, force_scale, moment_scale
        lever = _cross(position, force)
        applied_force = _add(applied_force, force)
        applied_moment = _add(applied_moment, _add(moment, lever))
        force_scale += math.hypot(*force)
        moment_scale += math.hypot(*moment) + math.hypot(*lever)

    for load in case["point_loads"]:
        index = _LOAD_DIRECTIONS.index(load["direction"])
        force, moment = [0., 0., 0.], [0., 0., 0.]
        (force if index < 3 else moment)[index % 3] = load["value"]
        accumulate(force, moment, frame["nodes"][load["node"]])
    if case["self_weight"]:
        for member in frame["members"]:
            a, b = frame["nodes"][member["i_node"]], frame["nodes"][member["j_node"]]
            length = math.hypot(*(b[i] - a[i] for i in range(3)))
            mass = member["material"]["rho_kg_m3"] * member["section"]["A_m2"] * length
            force = tuple(mass * g for g in case["gravity"])
            midpoint = tuple((a[i] + b[i]) / 2 for i in range(3))
            accumulate(force, (0., 0., 0.), midpoint)
    reaction_force, reaction_moment = (0., 0., 0.), (0., 0., 0.)
    for name, node in model.nodes.items():
        force = tuple(_finite(getattr(node, "Rxn" + direction)["prescribed"], "reaction")
                      for direction in ("FX", "FY", "FZ"))
        moment = tuple(_finite(getattr(node, "Rxn" + direction)["prescribed"], "reaction")
                       for direction in ("MX", "MY", "MZ"))
        reaction_force = _add(reaction_force, force)
        reaction_moment = _add(reaction_moment, _add(moment, _cross(frame["nodes"][name], force)))
    force_residual, moment_residual = _add(applied_force, reaction_force), _add(applied_moment, reaction_moment)
    force_tolerance = 1e-7 * max(1., force_scale)
    moment_tolerance = 1e-7 * max(1., moment_scale)
    passed = (math.hypot(*force_residual) <= force_tolerance
              and math.hypot(*moment_residual) <= moment_tolerance)
    for value in (*applied_force, *applied_moment, *reaction_force, *reaction_moment,
                  *force_residual, *moment_residual, force_tolerance, moment_tolerance):
        _finite(value, "equilibrium quantity")
    return {"status": "pass" if passed else "fail",
            "applied_force_n": list(applied_force), "applied_moment_nm": list(applied_moment),
            "reaction_force_n": list(reaction_force), "reaction_moment_nm": list(reaction_moment),
            "force_residual_n": list(force_residual), "moment_residual_nm": list(moment_residual),
            "force_tolerance_n": force_tolerance, "moment_tolerance_nm": moment_tolerance}


def _extract(model, frame, case, limits, np):
    node_rows = {}
    max_displacement = 0.
    for name, node in model.nodes.items():
        all_dofs = {dof: _finite(getattr(node, dof)["prescribed"], "nodal result") for dof in _DOFS}
        translation = {dof: all_dofs[dof] * 1000 for dof in _DOFS[:3]}
        translation["resultant"] = _finite(math.hypot(*translation.values()), "nodal displacement")
        node_rows[name] = translation
        max_displacement = max(max_displacement, translation["resultant"])
    demands = {}
    max_utilization = 0.
    for authored in frame["members"]:
        member = model.members[authored["id"]]
        if not member.sub_members:
            raise _NotEvaluable("member result coverage is missing")
        for segment in member.sub_members.values():
            if segment.active.get("prescribed") is not True:
                raise _NotEvaluable("member is inactive or has missing demand coverage")
        axial = [_finite(member.min_axial("prescribed"), "axial demand"),
                 _finite(member.max_axial("prescribed"), "axial demand")]
        moments = {direction: [_finite(member.min_moment(direction, "prescribed"), "bending demand"),
                               _finite(member.max_moment(direction, "prescribed"), "bending demand")]
                   for direction in ("My", "Mz")}
        transverse = {direction: [min(pair[0] for pair in pairs), max(pair[1] for pair in pairs)]
                      for direction in ("dy", "dz")
                      for pairs in [[_transverse_extrema(segment, direction, np)
                                     for segment in member.sub_members.values()]]}
        # Include the API extrema as an additional finite-coverage check. The
        # analytic segment extrema above determine the displacement envelope.
        for direction in ("dx", "dy", "dz"):
            _finite(member.min_deflection(direction, "prescribed"), "member displacement demand")
            _finite(member.max_deflection(direction, "prescribed"), "member displacement demand")
        axial_deflection_pairs = [_axial_extrema(segment, np) for segment in member.sub_members.values()]
        dx_abs = max(abs(v) for pair in axial_deflection_pairs for v in pair)
        envelope_mm = _finite(1000 * math.hypot(dx_abs,
                             max(abs(v) for v in transverse["dy"]),
                             max(abs(v) for v in transverse["dz"])), "displacement envelope")
        section, material = authored["section"], authored["material"]
        stress = _finite(max(abs(v) for v in axial) / section["A_m2"]
                         + max(abs(v) for v in moments["My"]) / section["Sy_m3"]
                         + max(abs(v) for v in moments["Mz"]) / section["Sz_m3"], "stress envelope")
        allowable = _finite(material["yield_pa"] / limits["safety_factor"], "allowable normal stress", positive=True)
        utilization = _finite(stress / allowable, "stress utilization")
        demands[authored["id"]] = {
            "axial_extrema_n": axial, "bending_extrema_nm": moments,
            "transverse_extrema_mm": {d: [v * 1000 for v in values] for d, values in transverse.items()},
            "displacement_envelope_mm": envelope_mm,
            "normal_stress_upper_bound_pa": stress, "allowable_normal_stress_pa": allowable,
            "stress_utilization": utilization,
        }
        max_displacement = max(max_displacement, envelope_mm)
        max_utilization = max(max_utilization, utilization)
    equilibrium = _equilibrium(model, frame, case)
    if equilibrium["status"] != "pass":
        raise _NotEvaluable("global force or moment equilibrium check failed")
    deflection_status = "pass" if max_displacement <= limits["deflection_mm"] else "fail"
    stress_status = "pass" if max_utilization <= 1. else "fail"
    return {"id": case["id"], "status": "pass" if deflection_status == stress_status == "pass" else "fail",
            "deflection_status": deflection_status, "stress_status": stress_status,
            "max_displacement_mm": max_displacement, "max_stress_utilization": max_utilization,
            "node_displacements_mm": node_rows, "member_demands": demands,
            "equilibrium": equilibrium, "errors": [], "warnings": []}


def solve_cases(frame: dict, cases: list[dict], limits: dict) -> dict:
    """Evaluate prescribed static linear cases without an ultimate-load claim.

    ``pass`` means the displacement and conservative axial+bending normal-stress
    envelopes meet the supplied limits under the explicit beam assumptions.
    Missing coverage, warnings, unsupported inputs, unstable frames, nonfinite
    results, and runtime version mismatches return ``not-evaluable``.
    """
    report = {
        "status": "not-evaluable", "analysis": "static-linear-prescribed-loads",
        "backend": {"name": "PyNiteFEA", "required_version": PINNED_PYNITE_VERSION},
        "assumptions": ["rigid beam joints", "linear elastic beam members",
                        "small displacements", "explicit SI materials, sections, supports, and loads",
                        "conservative independent axial and biaxial bending extrema envelope"],
        "not_checked": list(NOT_CHECKED), "cases": [], "errors": [],
    }
    try:
        _validate(frame, cases, limits)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            model_type, np, version = _load_runtime()
        if caught:
            raise _NotEvaluable("backend import emitted warnings")
        report["backend"]["version"] = version
    except _NotEvaluable as error:
        report["errors"] = [str(error)]
        return report
    except Exception as error:
        report["errors"] = [f"backend unavailable or invalid input ({type(error).__name__})"]
        return report
    for case in cases:
        output = io.StringIO()
        caught = []
        try:
            with warnings.catch_warnings(record=True) as caught, redirect_stdout(output), redirect_stderr(output):
                warnings.simplefilter("always")
                model = _build_model(model_type, frame, case)
                model.analyze_linear(log=False, check_stability=True, check_statics=False, sparse=False)
                if model.solution != "Linear":
                    raise _NotEvaluable("solver did not produce a static linear solution")
                _check_stiffness(model, np)
                result = _extract(model, frame, case, limits, np)
            if caught or output.getvalue().strip():
                raise _NotEvaluable("solver emitted warnings or diagnostics")
        except Exception as error:
            reason = str(error) if isinstance(error, _NotEvaluable) else f"solver failed ({type(error).__name__})"
            result = {"id": case["id"], "status": "not-evaluable", "errors": [reason],
                      "warnings": [type(w.message).__name__ for w in caught]}
        report["cases"].append(result)
    statuses = {case["status"] for case in report["cases"]}
    report["status"] = "not-evaluable" if "not-evaluable" in statuses else "fail" if "fail" in statuses else "pass"
    return report
