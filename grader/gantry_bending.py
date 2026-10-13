"""Bounded planar Euler-Bernoulli diagnostic with explicit splice rotations.

This independent model has vertical displacement and bending rotation only.
It does not establish a physical M3 load path, material strength, torsion,
connection capacity or benchmark admission. Positive vertical loads point up.
"""
from __future__ import annotations

import importlib.metadata
import math
import re
import warnings

SCHEMA = "marb_gantry_bending_diagnostic.v0.1"
NUMPY_VERSION = "2.4.4"
MAX_NODES = 32
MAX_ELEMENTS = 32
MAX_COUPLINGS = 64
MAX_CONDITION = 1e12
RESIDUAL_RELATIVE = 1e-7
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


class BendingDiagnosticError(ValueError):
    """A static diagnostic that never includes supplied text or coordinates."""


def _fail(code):
    raise BendingDiagnosticError("gantry.bending." + code)


def _fields(value, names, code):
    if not isinstance(value, dict) or set(value) != set(names):
        _fail(code)


def _number(value, *, positive=False, nonnegative=False):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            _fail("invalid_number")
    except (OverflowError, ValueError):
        _fail("invalid_number")
    if (positive and value <= 0) or (nonnegative and value < 0):
        _fail("invalid_number")
    return float(value)


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("invalid_identifier")


def _node(value, count):
    if type(value) is not int or not 0 <= value < count:
        _fail("invalid_node")


def _validate(model):
    _fields(model, {"nodes_m", "elements", "supports", "rotation_couplings", "point_loads", "point_moments"},
            "invalid_model_fields")
    nodes = model["nodes_m"]
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= MAX_NODES:
        _fail("invalid_nodes")
    positions = [_number(value) for value in nodes]
    if any(not math.isfinite(b - a) or b <= a for a, b in zip(positions, positions[1:])):
        _fail("nodes_must_be_strictly_increasing")
    elements = model["elements"]
    if not isinstance(elements, list) or not 1 <= len(elements) <= MAX_ELEMENTS or len(elements) != len(nodes) - 1:
        _fail("elements_must_cover_one_chain")
    by_id, by_pair = {}, {}
    incidence = [[] for _ in nodes]
    for index, element in enumerate(elements):
        _fields(element, {"id", "i", "j", "E_pa", "I_m4", "S_m3", "distributed_load_n_m"},
                "invalid_element_fields")
        _identifier(element["id"])
        if element["id"] in by_id:
            _fail("duplicate_element_id")
        i, j = element["i"], element["j"]
        _node(i, len(nodes))
        _node(j, len(nodes))
        if j != i + 1 or (i, j) in by_pair:
            _fail("elements_must_cover_one_chain")
        for key in ("E_pa", "I_m4", "S_m3"):
            _number(element[key], positive=True)
        _number(element["distributed_load_n_m"])
        by_id[element["id"]] = index
        by_pair[i, j] = index
        incidence[i].append((index, 0))
        incidence[j].append((index, 1))
    if set(by_pair) != {(i, i + 1) for i in range(len(nodes) - 1)}:
        _fail("elements_must_cover_one_chain")
    couplings = model["rotation_couplings"]
    if not isinstance(couplings, list) or len(couplings) > MAX_COUPLINGS:
        _fail("invalid_rotation_couplings")
    coupling_ids, coupled_nodes = set(), {}
    for coupling in couplings:
        _fields(coupling, {"id", "left_element", "right_element", "stiffness_nm_rad"},
                "invalid_rotation_coupling_fields")
        _identifier(coupling["id"])
        if coupling["id"] in coupling_ids:
            _fail("duplicate_coupling_id")
        coupling_ids.add(coupling["id"])
        if (not isinstance(coupling["left_element"], str) or not isinstance(coupling["right_element"], str)
                or coupling["left_element"] not in by_id or coupling["right_element"] not in by_id):
            _fail("unknown_coupling_element")
        left = elements[by_id[coupling["left_element"]]]
        right = elements[by_id[coupling["right_element"]]]
        node = left["j"]
        if node != right["i"] or node in coupled_nodes:
            _fail("invalid_or_duplicate_interior_coupling")
        stiffness = coupling["stiffness_nm_rad"]
        if stiffness != "rigid":
            _number(stiffness, nonnegative=True)
        coupled_nodes[node] = coupling
    if set(coupled_nodes) != set(range(1, len(nodes) - 1)):
        _fail("every_interior_rotation_pair_requires_a_coupling")
    supports = model["supports"]
    if not isinstance(supports, list) or len(supports) > MAX_NODES:
        _fail("invalid_supports")
    supported = set()
    for support in supports:
        _fields(support, {"node", "vertical", "rotation"}, "invalid_support_fields")
        node = support["node"]
        _node(node, len(nodes))
        if node in supported:
            _fail("duplicate_support")
        supported.add(node)
        vertical = support["vertical"]
        if vertical != "fixed":
            _fields(vertical, {"stiffness_n_m"}, "invalid_vertical_support")
            _number(vertical["stiffness_n_m"], positive=True)
        rotation = support["rotation"]
        if rotation not in ("fixed", "free"):
            _fields(rotation, {"stiffness_nm_rad"}, "invalid_rotation_support")
            _number(rotation["stiffness_nm_rad"], positive=True)
        if rotation != "free" and len(incidence[node]) != 1:
            _fail("grounded_rotation_requires_single_incident_element")
    for name, field in (("point_loads", "force_n"), ("point_moments", "moment_nm")):
        values = model[name]
        if not isinstance(values, list) or len(values) > 64:
            _fail("point_load_count_out_of_bounds")
        for load in values:
            _fields(load, {"node", field}, "invalid_point_load_fields")
            _node(load["node"], len(nodes))
            _number(load[field])
            if (name == "point_moments" and len(incidence[load["node"]]) != 1
                    and coupled_nodes[load["node"]]["stiffness_nm_rad"] != "rigid"):
                _fail("interior_point_moment_requires_rigid_rotation")
    return positions, by_id, incidence


def _end_rotation(node_count, element_index, end):
    return node_count + 2 * element_index + end


def _stationary_points(np, coefficients):
    derivative = np.array([i * value for i, value in enumerate(coefficients)][1:], dtype=float)
    if not np.isfinite(derivative).all():
        _fail("nonfinite_result")
    candidates = [0.0, 1.0]
    if np.any(derivative):
        for root in np.polynomial.polynomial.polyroots(derivative):
            if abs(float(root.imag)) <= 1e-9 * (1 + abs(float(root.real))):
                x = float(root.real)
                if -1e-10 <= x <= 1 + 1e-10:
                    candidates.append(min(1.0, max(0.0, x)))
    return sorted(set(candidates))


def _solve(np, model, nodes, by_id, incidence):
    elements = model["elements"]
    n = len(nodes)
    size = n + 2 * len(elements)
    total = np.zeros((size, size))
    force = np.zeros(size)
    parent = list(range(size))
    local = []

    def representative(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, element in enumerate(elements):
        i, j = element["i"], element["j"]
        length = nodes[j] - nodes[i]
        ei = float(element["E_pa"]) * float(element["I_m4"])
        stiffness = ei / length**3 * np.array([
            [12, 6 * length, -12, 6 * length],
            [6 * length, 4 * length**2, -6 * length, 2 * length**2],
            [-12, -6 * length, 12, -6 * length],
            [6 * length, 2 * length**2, -6 * length, 4 * length**2],
        ], dtype=float)
        q = float(element["distributed_load_n_m"])
        consistent = np.array([q * length / 2, q * length**2 / 12,
                               q * length / 2, -q * length**2 / 12])
        dofs = [i, _end_rotation(n, index, 0), j, _end_rotation(n, index, 1)]
        total[np.ix_(dofs, dofs)] += stiffness
        force[dofs] += consistent
        local.append((length, ei, stiffness, consistent, dofs))
    for coupling in model["rotation_couplings"]:
        left = _end_rotation(n, by_id[coupling["left_element"]], 1)
        right = _end_rotation(n, by_id[coupling["right_element"]], 0)
        if coupling["stiffness_nm_rad"] == "rigid":
            parent[representative(right)] = representative(left)
        else:
            k = float(coupling["stiffness_nm_rad"])
            total[np.ix_([left, right], [left, right])] += k * np.array([[1, -1], [-1, 1]])
    fixed = set()
    for support in model["supports"]:
        node = support["node"]
        if support["vertical"] == "fixed":
            fixed.add(node)
        else:
            total[node, node] += float(support["vertical"]["stiffness_n_m"])
        rotation = support["rotation"]
        if rotation != "free":
            element_index, end = incidence[node][0]
            dof = _end_rotation(n, element_index, end)
            if rotation == "fixed":
                fixed.add(dof)
            else:
                total[dof, dof] += float(rotation["stiffness_nm_rad"])
    for load in model["point_loads"]:
        force[load["node"]] += float(load["force_n"])
    for moment in model["point_moments"]:
        element_index, end = incidence[moment["node"]][0]
        force[_end_rotation(n, element_index, end)] += float(moment["moment_nm"])
    if not np.isfinite(total).all() or not np.isfinite(force).all():
        _fail("nonfinite_stiffness_or_load")
    representatives = [representative(index) for index in range(size)]
    unique = sorted(set(representatives))
    reduced_index = {dof: index for index, dof in enumerate(unique)}
    mapping = np.zeros((size, len(unique)))
    for index, dof in enumerate(representatives):
        mapping[index, reduced_index[dof]] = 1.0
    reduced = mapping.T @ total @ mapping
    rhs = mapping.T @ force
    constrained = {reduced_index[representative(index)] for index in fixed}
    free = [index for index in range(len(unique)) if index not in constrained]
    displacement_reduced = np.zeros(len(unique))
    condition = 1.0
    if free:
        active = reduced[np.ix_(free, free)]
        condition = float(np.linalg.cond(active))
        if (not math.isfinite(condition) or condition > MAX_CONDITION
                or float(np.linalg.eigvalsh(active)[0]) <= 0):
            _fail("mechanism_or_ill_conditioned_stiffness")
        displacement_reduced[free] = np.linalg.solve(active, rhs[free])
    displacement = mapping @ displacement_reduced
    if not np.isfinite(displacement).all():
        _fail("nonfinite_result")
    algebraic = reduced @ displacement_reduced - rhs
    if free and np.max(np.abs(algebraic[free])) > RESIDUAL_RELATIVE * max(1.0, float(np.sum(np.abs(rhs)))):
        _fail("free_dof_equilibrium_failed")
    residual = total @ displacement - force
    results, rotations = {}, {}
    maximum_deflection = maximum_stress = 0.0
    for index, element in enumerate(elements):
        length, ei, stiffness, consistent, dofs = local[index]
        values = displacement[dofs]
        ends = stiffness @ values - consistent
        if not np.isfinite(ends).all():
            _fail("nonfinite_result")
        vi, ti, vj, tj = [float(value) for value in values]
        q = float(element["distributed_load_n_m"])
        # Hermite interpolation plus the exact quartic particular solution
        # q*x^2*(x-L)^2/(24*EI). Interior extrema are not nodal samples.
        bubble = q * length**4 / (24 * ei)
        coefficients = [vi, ti * length,
                        3 * (vj - vi) - (2 * ti + tj) * length + bubble,
                        -2 * (vj - vi) + (ti + tj) * length - 2 * bubble, bubble]
        stations = _stationary_points(np, coefficients)
        deflections = [(x, float(np.polynomial.polynomial.polyval(x, coefficients))) for x in stations]
        low = min(deflections, key=lambda value: value[1])
        high = max(deflections, key=lambda value: value[1])
        peak = max(deflections, key=lambda value: abs(value[1]))
        # Internal sagging moment is -M_left + V_left*x + q*x^2/2.
        moment_coefficients = [-float(ends[1]), float(ends[0]) * length, q * length**2 / 2]
        moment_stations = _stationary_points(np, moment_coefficients)
        moments = [(x, float(np.polynomial.polynomial.polyval(x, moment_coefficients))) for x in moment_stations]
        peak_moment = max(moments, key=lambda value: abs(value[1]))
        stress = abs(peak_moment[1]) / float(element["S_m3"])
        if not all(math.isfinite(value) for value in (low[1], high[1], peak[1], peak_moment[1], stress)):
            _fail("nonfinite_result")
        x0 = nodes[element["i"]]
        results[element["id"]] = {
            "end_forces": {"left": {"shear_n": float(ends[0]), "moment_nm": float(ends[1])},
                           "right": {"shear_n": float(ends[2]), "moment_nm": float(ends[3])}},
            "displacement_extrema": {"minimum_mm": low[1] * 1000, "maximum_mm": high[1] * 1000,
                                     "max_abs_mm": abs(peak[1]) * 1000,
                                     "minimum_x_m": x0 + low[0] * length,
                                     "maximum_x_m": x0 + high[0] * length,
                                     "max_abs_x_m": x0 + peak[0] * length,
                                     "stationary_x_m": [x0 + x * length for x in stations if 0 < x < 1]},
            "max_abs_bending_moment_nm": abs(peak_moment[1]),
            "peak_bending_moment_x_m": x0 + peak_moment[0] * length,
            "peak_bending_stress_pa": stress,
        }
        rotations[element["id"]] = {"left": ti, "right": tj}
        maximum_deflection = max(maximum_deflection, abs(peak[1]) * 1000)
        maximum_stress = max(maximum_stress, stress)
    jumps, spring_moments = {}, {}
    for coupling in model["rotation_couplings"]:
        jump = rotations[coupling["right_element"]]["left"] - rotations[coupling["left_element"]]["right"]
        jumps[coupling["id"]] = jump
        if coupling["stiffness_nm_rad"] == "rigid":
            spring_moments[coupling["id"]] = results[coupling["left_element"]]["end_forces"]["right"]["moment_nm"]
        else:
            spring_moments[coupling["id"]] = float(coupling["stiffness_nm_rad"]) * jump
    reactions = []
    for support in model["supports"]:
        node = support["node"]
        vertical = support["vertical"]
        reaction = float(residual[node]) if vertical == "fixed" else -float(vertical["stiffness_n_m"]) * float(displacement[node])
        moment = 0.0
        rotation = support["rotation"]
        if rotation != "free":
            element_index, end = incidence[node][0]
            dof = _end_rotation(n, element_index, end)
            moment = float(residual[dof]) if rotation == "fixed" else -float(rotation["stiffness_nm_rad"]) * float(displacement[dof])
        reactions.append({"node": node, "force_n": reaction, "moment_nm": moment})
    # Independently integrate input loads. Do not reuse the assembled FE rhs.
    origin = nodes[0]
    force_terms = [float(load["force_n"]) for load in model["point_loads"]]
    moment_terms = [float(load["force_n"]) * (nodes[load["node"]] - origin) for load in model["point_loads"]]
    moment_terms.extend(float(load["moment_nm"]) for load in model["point_moments"])
    for element in elements:
        length = nodes[element["j"]] - nodes[element["i"]]
        distributed = float(element["distributed_load_n_m"]) * length
        force_terms.append(distributed)
        moment_terms.append(distributed * ((nodes[element["i"]] + nodes[element["j"]]) / 2 - origin))
    applied_force = math.fsum(force_terms)
    applied_moment = math.fsum(moment_terms)
    reaction_force = math.fsum(reaction["force_n"] for reaction in reactions)
    reaction_moment = math.fsum(reaction["force_n"] * (nodes[reaction["node"]] - origin) + reaction["moment_nm"] for reaction in reactions)
    force_residual = applied_force + reaction_force
    moment_residual = applied_moment + reaction_moment
    force_tolerance = RESIDUAL_RELATIVE * max(1.0, math.fsum(abs(value) for value in force_terms))
    moment_tolerance = RESIDUAL_RELATIVE * max(1.0, math.fsum(abs(value) for value in moment_terms))
    if (not all(math.isfinite(value) for value in (force_residual, moment_residual, force_tolerance, moment_tolerance))
            or abs(force_residual) > force_tolerance or abs(moment_residual) > moment_tolerance):
        _fail("global_equilibrium_failed")
    return {
        "node_deflections_mm": [float(value) * 1000 for value in displacement[:n]],
        "element_results": results, "element_end_rotations_rad": rotations,
        "rotation_jumps_rad": jumps, "spring_moments_nm": spring_moments,
        "max_deflection_mm": maximum_deflection, "peak_bending_stress_pa": maximum_stress,
        "support_reactions": reactions, "stiffness_condition_number": condition,
        "equilibrium": {"origin_x_m": origin, "applied_force_n": applied_force,
                        "applied_moment_nm": applied_moment, "reaction_force_n": reaction_force,
                        "reaction_moment_nm": reaction_moment, "force_residual_n": force_residual,
                        "moment_residual_nm": moment_residual, "force_tolerance_n": force_tolerance,
                        "moment_tolerance_nm": moment_tolerance, "verified": True},
    }


def solve_bending(model) -> dict:
    """Solve one explicitly declared chain. Any unsupported state fails closed.

    Rigid splice rotations are merged exactly. Finite splice stiffness couples
    separate member-end rotations through k*(theta_right-theta_left)^2/2.
    A zero-stiffness splice under pinned end supports is a mechanism.
    """
    report = {"schema_version": SCHEMA, "status": "not_evaluable", "errors": [],
              "analysis_admitted": False, "leaderboard_eligible": False,
              "physical_acceptance": "not_evaluated", "legacy_scores_changed": False,
              "scope": "planar_centroidal_linear_elastic_bending_diagnostic",
              "capacity_acceptance": "not_evaluated",
              "not_checked": ["Axial behavior, shear deformation, torsion and warping.",
                              "Physical connection stiffness, slip and strength.",
                              "Material yield, buckling, geometric nonlinearity and ultimate capacity.",
                              "Full machine load paths, dynamics and physical validation."]}
    try:
        nodes, by_id, incidence = _validate(model)
        if importlib.metadata.version("numpy") != NUMPY_VERSION:
            _fail("runtime_version_mismatch")
        import numpy as np
        # Treat numerical warnings as a failed diagnostic, never a usable solve.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = _solve(np, model, nodes, by_id, incidence)

        def finite_result(value):
            if isinstance(value, dict):
                return all(finite_result(item) for item in value.values())
            if isinstance(value, list):
                return all(finite_result(item) for item in value)
            return not isinstance(value, float) or math.isfinite(value)

        if not finite_result(result):
            _fail("nonfinite_result")
        report.update(result, status="diagnostic_complete", runtime_versions={"numpy": NUMPY_VERSION})
    except BendingDiagnosticError as error:
        report["errors"] = [str(error)]
    except (ImportError, importlib.metadata.PackageNotFoundError):
        report["errors"] = ["gantry.bending.runtime_unavailable"]
    except Exception:
        report["errors"] = ["gantry.bending.numerical_or_runtime_failure"]
    return report
