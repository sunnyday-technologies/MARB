"""Bounded STEP-to-beam extraction for unranked MARB development analysis.

The evaluator owns catalog geometry, material, section, node stations and
bindings. Candidate geometry supplies each member's verified rigid placement.
Only straight, axis-aligned, catalog-congruent stock is supported. No missing
member, node or connection is replaced with reference assembly coordinates.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from pathlib import Path

MAX_SOLIDS = 256
MAX_FILE_BYTES = 64 * 1024 * 1024
NODE_CLOSURE_MM = 0.01
GEOMETRY_MM = 1e-5
VOLUME_ABS_MM3 = 1e-4
VOLUME_REL = 1e-9
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")


class FeaGeometryError(ValueError):
    """A bounded diagnostic safe to include in a development report."""


def _fail(code: str) -> None:
    # Do not echo candidate text, paths or geometry runtime exceptions.
    raise FeaGeometryError("fea.geometry." + code)


def _sha256(path: Path) -> str:
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            _fail("file_unavailable_or_out_of_bounds")
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        _fail("file_unavailable_or_out_of_bounds")


def _verify_hash(path: Path, expected: object) -> str:
    if not isinstance(expected, str) or not _HEX.fullmatch(expected):
        _fail("invalid_file_hash")
    actual = _sha256(path)
    if actual != expected.lower():
        _fail("file_hash_mismatch")
    return actual


def _catalog_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("invalid_catalog_path")
    relative = Path(value)
    if relative.is_absolute() or relative.drive or any(p in {"..", "."} for p in value.split("/")):
        _fail("invalid_catalog_path")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / relative).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        _fail("catalog_path_outside_root_or_missing")
    if resolved.suffix.lower() not in {".step", ".stp"}:
        _fail("invalid_catalog_file_type")
    return resolved


def _solids(path: Path) -> list:
    try:
        import cadquery as cq
        solids = cq.importers.importStep(str(path)).solids().vals()
        if not 0 < len(solids) <= MAX_SOLIDS:
            _fail("solid_count_out_of_bounds")
        for solid in solids:
            if not solid.isValid() or not math.isfinite(solid.Volume()) or solid.Volume() <= 0:
                _fail("invalid_solid")
        # Preserve imported order. Bbox/centroid dedup would silently discard
        # candidate stock and make evaluator-authored indices unstable.
        return solids
    except FeaGeometryError:
        raise
    except Exception:
        _fail("step_load_failed")


def _vec(value: object) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _fail("invalid_vector")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        _fail("invalid_vector")
    return tuple(float(v) for v in value)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _matvec(rotation, vector) -> tuple:
    return tuple(_dot(row, vector) for row in rotation)


def _rotations() -> tuple:
    """The 24 proper orthogonal signed-permutation rotations of a cube."""
    out = []
    for permutation in itertools.permutations(range(3)):
        parity = (-1) ** sum(permutation[i] > permutation[j] for i in range(3) for j in range(i + 1, 3))
        for signs in itertools.product((-1, 1), repeat=3):
            if parity * math.prod(signs) != 1:
                continue
            rows = tuple(tuple(signs[i] if j == permutation[i] else 0 for j in range(3)) for i in range(3))
            out.append(rows)
    return tuple(out)


ROTATIONS = _rotations()


def _bounds(solid) -> tuple:
    bbox = solid.BoundingBox()
    low = (bbox.xmin, bbox.ymin, bbox.zmin)
    high = (bbox.xmax, bbox.ymax, bbox.zmax)
    if not all(math.isfinite(v) for v in low + high):
        _fail("invalid_bounding_box")
    return low, high


def _stock_geometry(solid) -> tuple:
    low, high = _bounds(solid)
    dims = tuple(b - a for a, b in zip(low, high))
    axis = max(range(3), key=lambda i: dims[i])
    transverse = [i for i in range(3) if i != axis]
    if min(dims) <= GEOMETRY_MM or dims[axis] < 2.0 * max(dims[i] for i in transverse):
        _fail("source_not_bounded_straight_stock")
    mass_center = _vec(solid.Center().toTuple())
    # A uniform prismatic section's bending centroid can differ from its bbox
    # midpoint. This is a centroidal beam line, not a measured shear center.
    # Use the axial bbox midpoint only to cut and reconstruct the full stock.
    center = tuple((low[i] + high[i]) / 2 if i == axis else mass_center[i] for i in range(3))
    return low, high, dims, axis, center


def _section_orientation(value: object, axis: int) -> tuple:
    section_y = _vec(value)
    if abs(_dot(section_y, section_y) - 1.0) > 1e-9 or abs(section_y[axis]) > 1e-9:
        _fail("invalid_section_orientation")
    if sum(abs(v) > 1e-9 for v in section_y) != 1:
        _fail("non_orthogonal_section_orientation_unsupported")
    return section_y


def _source_stations(source: dict, solid) -> tuple:
    low, high, dims, axis, center = _stock_geometry(solid)
    transverse = [i for i in range(3) if i != axis]
    stations = source.get("stations")
    if not isinstance(stations, list) or not 2 <= len(stations) <= MAX_SOLIDS:
        _fail("invalid_stations")
    points = [_vec(s.get("point_mm")) if isinstance(s, dict) else _vec(None) for s in stations]
    nodes = [s.get("node") for s in stations]
    if any(not isinstance(n, str) or not n for n in nodes) or len(set(nodes)) != len(nodes):
        _fail("invalid_station_nodes")
    if any(abs(p[i] - center[i]) > GEOMETRY_MM for p in points for i in transverse):
        _fail("stations_off_centroidal_stock_axis")
    if abs(points[0][axis] - low[axis]) > GEOMETRY_MM or abs(points[-1][axis] - high[axis]) > GEOMETRY_MM:
        _fail("stations_do_not_span_stock")
    if any(b[axis] - a[axis] <= GEOMETRY_MM for a, b in zip(points, points[1:])):
        _fail("stations_not_strictly_increasing")
    section_y = _section_orientation(source.get("section_y"), axis)
    _verify_prismatic_section(source, solid, section_y)
    return axis, points, section_y


def inspect_stock_section(solid, section_y: object) -> dict:
    """Inspect a bounded axis-aligned prism's centroidal bending properties.

    The temporary extrusion is a geometric comparison only. It is never
    substituted for a candidate, written to STEP or included in the model.
    No material strength or Saint-Venant torsion J is inferred from geometry.
    Centroidal principal bending axes are checked. Shear-center eccentricity,
    warping and flexural-torsional coupling are not inferred from this adapter.
    Metadata describes the supplied catalog solid, not assembly placement.
    """
    try:
        import cadquery as cq
        low, high, dims, axis, center = _stock_geometry(solid)
        section_y = _section_orientation(section_y, axis)
        beam_x = tuple(1.0 if i == axis else 0.0 for i in range(3))
        section_z = _cross(beam_x, section_y)
        plane = cq.Plane(origin=center, xDir=section_y, normal=beam_x)
        rebuilt = cq.Workplane(plane).add(solid).section().wires().toPending().extrude(dims[axis] / 2, both=True, combine=False).val()
        tolerance = max(VOLUME_ABS_MM3, solid.Volume() * VOLUME_REL)
        difference = solid.cut(rebuilt).Volume() + rebuilt.cut(solid).Volume()
        if not math.isfinite(difference) or difference > tolerance:
            _fail("source_not_prismatic_stock")
        area_mm2 = solid.Volume() / dims[axis]
        inertia = cq.Shape.matrixOfInertia(solid)
        axial_term = area_mm2 * dims[axis] ** 2 / 12.0
        iy_mm4 = _dot(section_y, _matvec(inertia, section_y)) / dims[axis] - axial_term
        iz_mm4 = _dot(section_z, _matvec(inertia, section_z)) / dims[axis] - axial_term
        product_mm4 = _dot(section_y, _matvec(inertia, section_z)) / dims[axis]
        if abs(product_mm4) > max(1e-5, min(iy_mm4, iz_mm4) * 1e-6):
            _fail("non_principal_section_axes_unsupported")
        extremes = tuple(max(abs(a - c), abs(b - c)) for a, b, c in zip(low, high, center))
        # For an asymmetric section use the farther extreme fiber on each
        # centroidal axis. These unsigned moduli conservatively bound either
        # sign of bending. Orthogonal section axes were required above.
        cy_mm = sum(abs(v) * d for v, d in zip(section_y, extremes))
        cz_mm = sum(abs(v) * d for v, d in zip(section_z, extremes))
        derived = {"A_m2": area_mm2 * 1e-6, "Iy_m4": iy_mm4 * 1e-12,
                   "Iz_m4": iz_mm4 * 1e-12, "Sy_m3": iy_mm4 / cz_mm * 1e-9,
                   "Sz_m3": iz_mm4 / cy_mm * 1e-9}
        if any(not math.isfinite(v) or v <= 0 for v in derived.values()):
            _fail("source_section_qualification_failed")
        return {"axis": axis, "length_mm": dims[axis], "centroid_mm": list(solid.Center().toTuple()),
                "stock_bounds_mm": {"low": list(low), "high": list(high)}, "section": derived,
                "section_y": list(section_y), "product_inertia_mm4": product_mm4,
                "principal_axes_verified": True, "prismatic_verified": True}
    except FeaGeometryError:
        raise
    except Exception:
        _fail("source_section_qualification_failed")


def _verify_prismatic_section(source: dict, solid, section_y: tuple) -> None:
    derived = inspect_stock_section(solid, section_y)["section"]
    section = source.get("section", {})
    if not isinstance(section, dict) or any(not isinstance(section.get(k), (int, float)) or isinstance(section.get(k), bool)
                                          or not math.isfinite(section[k]) or section[k] <= 0
                                          or not math.isclose(section[k], v, rel_tol=0.001, abs_tol=0)
                                          for k, v in derived.items()):
        _fail("section_constants_do_not_match_catalog")


def _rotation_deg(axis: int, section_y: tuple) -> float:
    # PyNite Member3D.T: +X/+Z stock starts with global +Y as local y;
    # +Y stock starts with global -X as local y. Positive rotation is
    # Rodrigues rotation about the positive member axis.
    base_y = (-1.0, 0.0, 0.0) if axis == 1 else (0.0, 1.0, 0.0)
    beam_x = tuple(1.0 if i == axis else 0.0 for i in range(3))
    return math.degrees(math.atan2(_dot(beam_x, _cross(base_y, section_y)), _dot(base_y, section_y)))


def _isotropic(section: dict) -> bool:
    try:
        return math.isclose(section["Iy_m4"], section["Iz_m4"], rel_tol=1e-9, abs_tol=0) and math.isclose(section["Sy_m3"], section["Sz_m3"], rel_tol=1e-9, abs_tol=0)
    except (KeyError, TypeError):
        _fail("invalid_section")


def _match(source: dict, source_solid, candidate_solid) -> dict:
    source_axis, stations, section_y = _source_stations(source, source_solid)
    low, high = _bounds(candidate_solid)
    dims = tuple(b - a for a, b in zip(low, high))
    candidate_axis = max(range(3), key=lambda i: dims[i])
    source_center = source_solid.Center().toTuple()
    candidate_center = candidate_solid.Center().toTuple()
    source_volume = source_solid.Volume()
    candidate_volume = candidate_solid.Volume()
    volume_tolerance = max(VOLUME_ABS_MM3, source_volume * VOLUME_REL)
    if abs(source_volume - candidate_volume) > volume_tolerance:
        _fail("catalog_geometry_mismatch")
    matches = []
    try:
        import cadquery as cq
        from OCP.gp import gp_Trsf
        source_low, source_high = _bounds(source_solid)
        source_dims = tuple(b - a for a, b in zip(source_low, source_high))
        for rotation in ROTATIONS:
            # Symmetric stock has an unavoidable end reversal ambiguity.
            # Canonicalize its physical direction to positive global axis.
            if rotation[candidate_axis][source_axis] != 1:
                continue
            rotated_dims = tuple(sum(abs(rotation[i][j]) * source_dims[j] for j in range(3)) for i in range(3))
            if any(abs(a - b) > GEOMETRY_MM for a, b in zip(rotated_dims, dims)):
                continue
            rotated_center = _matvec(rotation, source_center)
            translation = tuple(a - b for a, b in zip(candidate_center, rotated_center))
            transform = gp_Trsf()
            transform.SetValues(*(v for row, shift in zip(rotation, translation) for v in (*row, shift)))
            placed = source_solid.transformShape(cq.Matrix(transform))
            difference_volume = placed.cut(candidate_solid).Volume() + candidate_solid.cut(placed).Volume()
            if not math.isfinite(difference_volume) or difference_volume > volume_tolerance:
                continue
            transformed_stations = [tuple(a + b for a, b in zip(_matvec(rotation, p), translation)) for p in stations]
            transformed_y = _matvec(rotation, section_y)
            matches.append({"rotation_matrix": rotation, "translation_mm": translation,
                            "points_mm": transformed_stations, "section_y": transformed_y,
                            "rotation_deg": _rotation_deg(candidate_axis, transformed_y),
                            "symmetric_difference_mm3": difference_volume})
    except Exception:
        _fail("geometry_comparison_failed")
    if not matches:
        _fail("catalog_geometry_mismatch_or_unsupported_rotation")
    first = matches[0]
    isotropic = _isotropic(source.get("section", {}))
    for other in matches[1:]:
        if any(_distance(a, b) > GEOMETRY_MM for a, b in zip(first["points_mm"], other["points_mm"])):
            _fail("ambiguous_station_binding")
        if not isotropic and abs(abs(_dot(first["section_y"], other["section_y"])) - 1.0) > 1e-9:
            _fail("ambiguous_anisotropic_section_orientation")
    # Equivalent sign reversal or isotropic sections use the smallest roll.
    return min(matches, key=lambda item: (abs(item["rotation_deg"]), item["rotation_deg"], item["rotation_matrix"]))


def extract_candidate(contract: dict, bindings: dict, candidate_step: Path, catalog_root: Path) -> dict:
    """Verify catalog congruence and extract candidate-specific beam stations.

    Contract schema/numeric validation belongs to the calling orchestrator.
    File identity, catalog confinement, solid mapping and geometry constraints
    are independently rechecked here. Any unsupported condition raises a
    generic ValueError; the orchestrator reports it as not-evaluable.
    """
    try:
        return _extract_candidate(contract, bindings, candidate_step, catalog_root)
    except FeaGeometryError:
        raise
    except Exception:
        _fail("geometry_extraction_failed")


def _extract_candidate(contract: dict, bindings: dict, candidate_step: Path, catalog_root: Path) -> dict:
    if not isinstance(contract, dict) or not isinstance(bindings, dict):
        _fail("invalid_contract_or_bindings")
    if contract.get("schema_version") != "marb_fea_contract.v0.1" or bindings.get("schema_version") != "marb_fea_bindings.v0.1":
        _fail("unsupported_schema")
    sources = contract.get("sources")
    mapped = bindings.get("members")
    if not isinstance(sources, dict) or not sources or not isinstance(mapped, dict) or set(sources) != set(mapped):
        _fail("incomplete_member_bindings")
    if contract.get("coverage") != "all_solids":
        _fail("unsupported_coverage")
    candidate_path = Path(candidate_step)
    candidate_hash = _verify_hash(candidate_path, bindings.get("candidate_sha256"))
    candidate_solids = _solids(candidate_path)
    if len(candidate_solids) != len(sources):
        _fail("candidate_solid_coverage_mismatch")
    nodes_mm: dict[str, tuple] = {}
    members = []
    source_diagnostics = {}
    indices = set()
    source_cache: dict[Path, tuple[str, list]] = {}
    for source_id in sorted(sources):
        source, binding = sources[source_id], mapped[source_id]
        if not isinstance(source, dict) or not isinstance(binding, dict):
            _fail("invalid_member_binding")
        candidate_index = binding.get("solid_index")
        if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or not 0 <= candidate_index < len(candidate_solids):
            _fail("candidate_index_out_of_bounds")
        if candidate_index in indices:
            _fail("duplicate_candidate_mapping")
        indices.add(candidate_index)
        source_path = _catalog_path(Path(catalog_root), source.get("file"))
        # Recheck the hash for each instance, even when its shape is cached.
        source_hash = _verify_hash(source_path, source.get("sha256"))
        if source_path not in source_cache:
            source_cache[source_path] = (source_hash, _solids(source_path))
        cached_hash, source_solids = source_cache[source_path]
        if cached_hash != source_hash:
            _fail("source_changed_during_analysis")
        source_index = source.get("solid_index")
        if isinstance(source_index, bool) or not isinstance(source_index, int) or not 0 <= source_index < len(source_solids):
            _fail("source_index_out_of_bounds")
        match = _match(source, source_solids[source_index], candidate_solids[candidate_index])
        for station, point in zip(source["stations"], match["points_mm"]):
            node = station["node"]
            if node in nodes_mm and _distance(nodes_mm[node], point) > NODE_CLOSURE_MM:
                _fail("joint_stations_do_not_close")
            # Keep the first measured anchor. Closure tolerance does not grant
            # permission to bridge a physical gap or invent a connection.
            nodes_mm.setdefault(node, point)
        for index, (a, b) in enumerate(zip(source["stations"], source["stations"][1:])):
            members.append({"id": f"{source_id}:{index}", "i_node": a["node"], "j_node": b["node"],
                            "material": source["material"], "section": source["section"],
                            "rotation_deg": match["rotation_deg"]})
        source_diagnostics[source_id] = {"sha256": source_hash, "solid_index": candidate_index,
                                         "rotation_matrix": match["rotation_matrix"],
                                         "translation_mm": match["translation_mm"],
                                         "symmetric_difference_mm3": match["symmetric_difference_mm3"]}
    # Detect changes to any input while native geometry operations ran.
    if _sha256(candidate_path) != candidate_hash:
        _fail("candidate_changed_during_analysis")
    for source_path, (source_hash, _) in source_cache.items():
        if _sha256(source_path) != source_hash:
            _fail("source_changed_during_analysis")
    mapping_digest = hashlib.sha256(json.dumps(mapped, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"nodes": {node: [v / 1000.0 for v in point] for node, point in nodes_mm.items()},
            "members": members, "supports": contract["supports"], "loads": contract["loads"],
            "joint_assumption": contract["joint_assumption"],
            "geometry": {"candidate_sha256": candidate_hash, "sources": source_diagnostics,
                         "mapping_sha256": mapping_digest, "unused_candidate_solids": 0,
                         "node_closure_tolerance_mm": NODE_CLOSURE_MM,
                         "assumptions": ["The prismatic stock centroid line and principal bending axes are verified.",
                                         "Shear centers, warping and flexural-torsional coupling are not verified.",
                                         "Stock direction is canonicalized to the positive global axis.",
                                         "Shared station nodes assume the evaluator-declared connection model.",
                                         "All candidate solids are mapped. Physical connections are not verified."]}}
