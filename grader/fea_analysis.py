"""Experimental candidate-specific FEA. Legacy MARB grading is unchanged."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grader.fea_step import FeaGeometryError

VERSION = 'marb_fea_analysis.v0.1.1'
SCHEMA = 'marb_fea_analysis_report.v0.1'
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_STEP_BYTES = 64 * 1024 * 1024
ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
PINNED = {'cadquery': '2.7.0', 'cadquery-ocp': '7.8.1.1.post1',
          'PyniteFEA': '2.4.1', 'numpy': '2.4.4', 'scipy': '1.17.1'}
NOT_CHECKED = [
    'Legacy MARB assembly score and whole-machine inventory.',
    'Material and section-property certification.',
    'Connection, fastener, contact, shear and torsion strength.',
    'Shear-center eccentricity, warping and flexural-torsional coupling.',
    'Transverse shear deformation, buckling, geometric nonlinearity, fatigue and dynamic loads.',
    'Local stresses in plates, holes, spacers and inserts.',
    'Ultimate load capacity, physical validation and certification.',
]


class AnalysisError(ValueError):
    """A safe diagnostic that does not echo supplied paths or input values."""


def digest(path: Path, maximum: int = MAX_STEP_BYTES) -> str:
    if not path.is_file() or path.stat().st_size > maximum:
        raise AnalysisError('input file is missing or exceeds its size limit')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError('duplicate JSON field')
        result[key] = value
    return result


def _load(path: Path) -> dict:
    digest(path, MAX_JSON_BYTES)
    try:
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except AnalysisError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise AnalysisError('invalid JSON input') from error
    if not isinstance(data, dict):
        raise AnalysisError('JSON input must be an object')
    return data


def _fields(data: Any, names: set[str], label: str) -> None:
    if not isinstance(data, dict) or set(data) != names:
        raise AnalysisError('invalid ' + label + ' fields')


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise AnalysisError('invalid identifier')


def _hash(value: Any) -> None:
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise AnalysisError('invalid input digest')


def _number(value: Any, positive: bool = False) -> float:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise AnalysisError('numeric input must be finite')
    if positive and value <= 0:
        raise AnalysisError('numeric input must be positive')
    return float(value)


def _vector(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise AnalysisError('invalid vector')
    return [_number(x) for x in value]


def validate_contract(contract: dict) -> None:
    _fields(contract, {'schema_version', 'task_id', 'revision', 'coverage',
                      'sources', 'supports', 'joint_assumption', 'loads', 'limits'}, 'contract')
    if contract['schema_version'] != 'marb_fea_contract.v0.1':
        raise AnalysisError('unsupported contract version')
    _identifier(contract['task_id'])
    _identifier(contract['revision'])
    if contract['coverage'] != 'all_solids' or contract['joint_assumption'] != 'rigid':
        raise AnalysisError('unsupported coverage or joint idealization')
    sources = contract['sources']
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 128:
        raise AnalysisError('invalid source catalog size')
    nodes = set()
    segments = 0
    for sid, source in sources.items():
        _identifier(sid)
        _fields(source, {'file', 'sha256', 'solid_index', 'material', 'section',
                         'section_y', 'stations', 'property_provenance'}, 'source')
        filename = source['file']
        if (not isinstance(filename, str) or filename.startswith('/') or '\\' in filename or ':' in filename
                or Path(filename).is_absolute() or '..' in Path(filename).parts
                or Path(filename).suffix.lower() not in ('.step', '.stp')):
            raise AnalysisError('source path must stay within the authored catalog')
        _hash(source['sha256'])
        if type(source['solid_index']) is not int or not 0 <= source['solid_index'] < 256:
            raise AnalysisError('invalid source solid index')
        provenance = source['property_provenance']
        if not isinstance(provenance, str) or not 1 <= len(provenance.strip()) <= 500:
            raise AnalysisError('material and section provenance is required')
        mat = source['material']
        _fields(mat, {'E_pa', 'G_pa', 'nu', 'rho_kg_m3', 'yield_pa'}, 'material')
        for key in ('E_pa', 'G_pa', 'rho_kg_m3', 'yield_pa'):
            _number(mat[key], True)
        if not -1 < _number(mat['nu']) < 0.5:
            raise AnalysisError('invalid isotropic Poisson ratio')
        expected_g = mat['E_pa'] / (2.0 * (1.0 + mat['nu']))
        # Rounded engineering data are allowed. This checks the declared
        # isotropic stiffness model, not supplier certification.
        if not math.isclose(mat['G_pa'], expected_g, rel_tol=0.01):
            raise AnalysisError('inconsistent isotropic E, G and Poisson ratio')
        section = source['section']
        _fields(section, {'A_m2', 'Iy_m4', 'Iz_m4', 'J_m4', 'Sy_m3', 'Sz_m3'}, 'section')
        for value in section.values():
            _number(value, True)
        axis = _vector(source['section_y'])
        if abs(sum(x*x for x in axis) - 1) > 1e-8:
            raise AnalysisError('section axis must be a unit vector')
        stations = source['stations']
        if not isinstance(stations, list) or not 2 <= len(stations) <= 64:
            raise AnalysisError('invalid source station count')
        segments += len(stations) - 1
        if segments > 256:
            raise AnalysisError('analysis exceeds member segment limit')
        source_nodes = set()
        for station in stations:
            _fields(station, {'node', 'point_mm'}, 'station')
            _identifier(station['node'])
            if station['node'] in source_nodes:
                raise AnalysisError('duplicate source station node')
            source_nodes.add(station['node'])
            nodes.add(station['node'])
            _vector(station['point_mm'])
    if len(nodes) > 128:
        raise AnalysisError('analysis exceeds node limit')
    supports = contract['supports']
    if not isinstance(supports, list) or not supports:
        raise AnalysisError('explicit supports are required')
    supported = set()
    for support in supports:
        _fields(support, {'node', 'restrained'}, 'support')
        if support['node'] not in nodes or support['node'] in supported:
            raise AnalysisError('unknown or duplicate support node')
        supported.add(support['node'])
        flags = support['restrained']
        if (not isinstance(flags, list) or len(flags) != 6
                or any(type(x) is not bool for x in flags) or not any(flags)):
            raise AnalysisError('support needs six explicit restraint flags')
    loads = contract['loads']
    if not isinstance(loads, list) or not 1 <= len(loads) <= 16:
        raise AnalysisError('invalid prescribed load-case count')
    case_ids = set()
    for case in loads:
        _fields(case, {'id', 'self_weight', 'gravity', 'point_loads'}, 'load case')
        _identifier(case['id'])
        if case['id'] in case_ids:
            raise AnalysisError('duplicate load-case identifier')
        case_ids.add(case['id'])
        if type(case['self_weight']) is not bool:
            raise AnalysisError('self-weight flag must be boolean')
        gravity = _vector(case['gravity'])
        point_loads = case['point_loads']
        if not isinstance(point_loads, list) or len(point_loads) > 256:
            raise AnalysisError('invalid point-load count')
        nonzero = case['self_weight'] and any(gravity)
        for load in point_loads:
            _fields(load, {'node', 'direction', 'value'}, 'point load')
            if load['node'] not in nodes or load['direction'] not in ('FX', 'FY', 'FZ', 'MX', 'MY', 'MZ'):
                raise AnalysisError('invalid load attachment or direction')
            nonzero = nonzero or bool(_number(load['value']))
            _number(load['value'])
        if not nonzero:
            raise AnalysisError('each load case must apply a nonzero load')
    _fields(contract['limits'], {'deflection_mm', 'safety_factor'}, 'limits')
    _number(contract['limits']['deflection_mm'], True)
    if _number(contract['limits']['safety_factor']) < 1:
        raise AnalysisError('safety factor must be at least one')


def validate_bindings(bindings: dict, contract: dict, contract_hash: str) -> None:
    _fields(bindings, {'schema_version', 'candidate_sha256', 'contract_sha256', 'members'}, 'bindings')
    if bindings['schema_version'] != 'marb_fea_bindings.v0.1':
        raise AnalysisError('unsupported binding version')
    _hash(bindings['candidate_sha256'])
    _hash(bindings['contract_sha256'])
    if bindings['contract_sha256'] != contract_hash:
        raise AnalysisError('binding references a different frozen contract')
    mappings = bindings['members']
    if not isinstance(mappings, dict) or set(mappings) != set(contract['sources']):
        raise AnalysisError('binding must cover every contract source')
    seen = set()
    for value in mappings.values():
        _fields(value, {'solid_index'}, 'candidate member binding')
        index = value['solid_index']
        if type(index) is not int or not 0 <= index < 256 or index in seen:
            raise AnalysisError('invalid or duplicate candidate solid index')
        seen.add(index)


def _implementation_hash() -> str:
    value = hashlib.sha256()
    for name in ('fea_analysis.py', 'fea_step.py', 'fea_backend.py'):
        value.update(name.encode('ascii'))
        value.update(Path(__file__).with_name(name).read_bytes())
    return value.hexdigest()


def _verify_outcome(outcome: Any, frame: dict, contract: dict) -> str:
    """Independently refuse incomplete or contradictory solver reports."""
    def extrema(value: Any) -> list[float]:
        if not isinstance(value, list) or len(value) != 2:
            raise AnalysisError('backend demand extrema are missing or invalid')
        pair = [_number(x) for x in value]
        if pair[0] > pair[1]:
            raise AnalysisError('backend demand extrema are not ordered')
        return pair

    def cross(a, b):
        return [a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2],
                a[0]*b[1] - a[1]*b[0]]

    def add(a, b):
        return [a[i] + b[i] for i in range(3)]

    def load_totals(load_case: dict):
        """Compute applied resultants and residual scales from frozen inputs."""
        force_total, moment_total = [0., 0., 0.], [0., 0., 0.]
        force_scale = moment_scale = 0.

        def accumulate(force, moment, position):
            nonlocal force_total, moment_total, force_scale, moment_scale
            lever = cross(position, force)
            force_total = add(force_total, force)
            moment_total = add(moment_total, add(moment, lever))
            force_scale += math.hypot(*force)
            moment_scale += math.hypot(*moment) + math.hypot(*lever)

        directions = ('FX', 'FY', 'FZ', 'MX', 'MY', 'MZ')
        for load in load_case['point_loads']:
            index = directions.index(load['direction'])
            force, moment = [0., 0., 0.], [0., 0., 0.]
            (force if index < 3 else moment)[index % 3] = _number(load['value'])
            accumulate(force, moment, _vector(frame['nodes'][load['node']]))
        if load_case['self_weight']:
            gravity = _vector(load_case['gravity'])
            for member in frame['members']:
                a = _vector(frame['nodes'][member['i_node']])
                b = _vector(frame['nodes'][member['j_node']])
                length = math.hypot(*(b[i] - a[i] for i in range(3)))
                mass = _number(member['material']['rho_kg_m3'] * member['section']['A_m2'] * length)
                force = [_number(mass * g) for g in gravity]
                midpoint = [(a[i] + b[i]) / 2 for i in range(3)]
                accumulate(force, [0., 0., 0.], midpoint)
        return (_vector(force_total), _vector(moment_total),
                _number(1e-7 * max(1., force_scale), True),
                _number(1e-7 * max(1., moment_scale), True))

    def finite_tree(value: Any, depth: int = 0) -> None:
        if depth > 20:
            raise AnalysisError('backend result nesting exceeds limit')
        if type(value) in (int, float):
            _number(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise AnalysisError('backend result has an invalid field')
                finite_tree(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                finite_tree(child, depth + 1)
        elif value is not None and type(value) not in (str, bool):
            raise AnalysisError('backend result is not a JSON value')
    finite_tree(outcome)
    if not isinstance(outcome, dict) or outcome.get('status') not in ('pass', 'fail', 'not-evaluable'):
        raise AnalysisError('backend returned an unsupported status')
    # Setup and solver failures can legitimately contain no member demands.
    if outcome['status'] == 'not-evaluable':
        return 'not_evaluable'
    cases = outcome.get('cases')
    if not isinstance(cases, list) or [x.get('id') for x in cases if isinstance(x, dict)] != [x['id'] for x in contract['loads']]:
        raise AnalysisError('backend load-case coverage is incomplete')
    members = {x['id']: x for x in frame['members']}
    for case, prescribed in zip(cases, contract['loads']):
        if case.get('status') not in ('pass', 'fail') or case.get('errors') or case.get('warnings'):
            raise AnalysisError('backend returned contradictory case evidence')
        demands = case.get('member_demands')
        nodes = case.get('node_displacements_mm')
        if not isinstance(demands, dict) or set(demands) != set(members) or not isinstance(nodes, dict) or set(nodes) != set(frame['nodes']):
            raise AnalysisError('backend result coverage is incomplete')
        displacements = []
        utilizations = []
        for row in nodes.values():
            if not isinstance(row, dict):
                raise AnalysisError('backend nodal result is invalid')
            xyz = [_number(row.get(dof)) for dof in ('DX', 'DY', 'DZ')]
            resultant = _number(row.get('resultant'))
            if resultant < 0 or not math.isclose(resultant, math.hypot(*xyz), rel_tol=1e-9, abs_tol=1e-9):
                raise AnalysisError('backend displacement summary is inconsistent')
            displacements.append(resultant)
        for mid, row in demands.items():
            if not isinstance(row, dict):
                raise AnalysisError('backend member result is invalid')
            axial = extrema(row.get('axial_extrema_n'))
            bending = row.get('bending_extrema_nm')
            transverse = row.get('transverse_extrema_mm')
            if not isinstance(bending, dict) or set(bending) != {'My', 'Mz'} or not isinstance(transverse, dict) or set(transverse) != {'dy', 'dz'}:
                raise AnalysisError('backend bending or transverse extrema are missing or invalid')
            my, mz = extrema(bending['My']), extrema(bending['Mz'])
            dy, dz = extrema(transverse['dy']), extrema(transverse['dz'])
            displacement = _number(row.get('displacement_envelope_mm'))
            stress = _number(row.get('normal_stress_upper_bound_pa'))
            allowable = _number(row.get('allowable_normal_stress_pa'), True)
            utilization = _number(row.get('stress_utilization'))
            section = members[mid]['section']
            expected_stress = _number(max(abs(x) for x in axial) / section['A_m2']
                + max(abs(x) for x in my) / section['Sy_m3']
                + max(abs(x) for x in mz) / section['Sz_m3'])
            if not math.isclose(stress, expected_stress, rel_tol=1e-9, abs_tol=1e-6):
                raise AnalysisError('backend normal stress envelope contradicts member demands')
            transverse_bound = _number(math.hypot(max(abs(x) for x in dy), max(abs(x) for x in dz)))
            if displacement < transverse_bound and not math.isclose(displacement, transverse_bound, rel_tol=1e-9, abs_tol=1e-9):
                raise AnalysisError('backend displacement envelope omits transverse demands')
            expected_allowable = members[mid]['material']['yield_pa'] / contract['limits']['safety_factor']
            if min(displacement, stress, utilization) < 0 or not math.isclose(allowable, expected_allowable, rel_tol=1e-9) or not math.isclose(utilization, stress / allowable, rel_tol=1e-9, abs_tol=1e-12):
                raise AnalysisError('backend stress summary is inconsistent')
            displacements.append(displacement)
            utilizations.append(utilization)
        max_displacement = _number(case.get('max_displacement_mm'))
        max_utilization = _number(case.get('max_stress_utilization'))
        if not math.isclose(max_displacement, max(displacements), rel_tol=1e-9, abs_tol=1e-9) or not math.isclose(max_utilization, max(utilizations), rel_tol=1e-9, abs_tol=1e-12):
            raise AnalysisError('backend aggregate demands are inconsistent')
        deflection_status = 'pass' if max_displacement <= contract['limits']['deflection_mm'] else 'fail'
        stress_status = 'pass' if max_utilization <= 1 else 'fail'
        expected_status = 'pass' if deflection_status == stress_status == 'pass' else 'fail'
        if case.get('deflection_status') != deflection_status or case.get('stress_status') != stress_status or case['status'] != expected_status:
            raise AnalysisError('backend classification contradicts the frozen limits')
        equilibrium = case.get('equilibrium')
        if not isinstance(equilibrium, dict) or equilibrium.get('status') != 'pass':
            raise AnalysisError('backend equilibrium evidence is missing')
        trusted_force, trusted_moment, force_tolerance, moment_tolerance = load_totals(prescribed)
        for applied_key, reaction_key, residual_key, tolerance_key, trusted, trusted_tolerance in (
            ('applied_force_n', 'reaction_force_n', 'force_residual_n', 'force_tolerance_n', trusted_force, force_tolerance),
            ('applied_moment_nm', 'reaction_moment_nm', 'moment_residual_nm', 'moment_tolerance_nm', trusted_moment, moment_tolerance),
        ):
            applied = _vector(equilibrium.get(applied_key))
            reaction = _vector(equilibrium.get(reaction_key))
            residual = _vector(equilibrium.get(residual_key))
            reported_tolerance = _number(equilibrium.get(tolerance_key), True)
            if not all(math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9) for actual, expected in zip(applied, trusted)):
                raise AnalysisError('backend applied resultants contradict the frozen loads')
            if not math.isclose(reported_tolerance, trusted_tolerance, rel_tol=1e-9, abs_tol=0):
                raise AnalysisError('backend equilibrium tolerance contradicts the frozen loads')
            expected_residual = [_number(x) for x in add(trusted, reaction)]
            if not all(math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9) for actual, expected in zip(residual, expected_residual)):
                raise AnalysisError('backend equilibrium residual contradicts applied and reaction resultants')
            if math.hypot(*expected_residual) > trusted_tolerance:
                raise AnalysisError('backend equilibrium residual exceeds tolerance')
    aggregate = 'fail' if any(x['status'] == 'fail' for x in cases) else 'pass'
    if outcome['status'] != aggregate:
        raise AnalysisError('backend aggregate status contradicts its cases')
    return 'simulated_' + aggregate


def analyze(candidate_step: Path, contract_path: Path, bindings_path: Path,
            catalog_root: Path, expected_contract_sha256: str) -> dict:
    report = {'schema_version': SCHEMA, 'analysis_version': VERSION,
              'generated_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'not_evaluable', 'publication_status': 'unranked_development_analysis',
              'leaderboard_eligible': False, 'physical_acceptance': 'not_evaluated',
              'legacy_scores_changed': False, 'provenance': {}, 'cases': [],
              'errors': [], 'assumptions': ['Evaluator-authored binding and frozen properties.',
                'Complete beam subassembly; orthogonal authored-stock placements.',
                'Static linear elasticity; rigid joints and ideal supports.'],
              'not_checked': list(NOT_CHECKED)}
    try:
        _hash(expected_contract_sha256)
        hashes = {'candidate_sha256': digest(candidate_step),
                  'contract_sha256': digest(contract_path, MAX_JSON_BYTES),
                  'bindings_sha256': digest(bindings_path, MAX_JSON_BYTES)}
        report['provenance'].update(hashes)
        if hashes['contract_sha256'] != expected_contract_sha256:
            raise AnalysisError('contract differs from the evaluator-pinned digest')
        contract = _load(contract_path)
        validate_contract(contract)
        bindings = _load(bindings_path)
        validate_bindings(bindings, contract, hashes['contract_sha256'])
        if bindings['candidate_sha256'] != hashes['candidate_sha256']:
            raise AnalysisError('candidate differs from the evaluator binding')
        report['task_id'] = contract['task_id']
        report['task_revision'] = contract['revision']
        versions = {name: importlib.metadata.version(name) for name in PINNED}
        report['provenance']['runtime_versions'] = versions
        if versions != PINNED:
            raise AnalysisError('runtime differs from the pinned qualification dependencies')
        report['provenance']['implementation_sha256'] = _implementation_hash()
        catalog = catalog_root.resolve()
        unique_sources = set()
        for source in contract['sources'].values():
            source_path = (catalog / source['file']).resolve()
            if not source_path.is_relative_to(catalog):
                raise AnalysisError('authored source escaped the catalog root')
            unique_sources.add(source_path)
        if sum(path.stat().st_size for path in unique_sources) > MAX_STEP_BYTES:
            raise AnalysisError('authored catalog exceeds aggregate input size limit')
        from grader.fea_step import extract_candidate
        from grader.fea_backend import solve_cases
        frame = extract_candidate(contract, bindings, candidate_step, catalog_root)
        report['geometry'] = frame.get('geometry', {})
        solver_frame = {key: frame[key] for key in ('nodes', 'members', 'supports', 'joint_assumption')}
        outcome = solve_cases(solver_frame, contract['loads'], contract['limits'])
        verified_status = _verify_outcome(outcome, solver_frame, contract)
        report['cases'] = outcome.get('cases', [])
        report['backend'] = outcome
        report['status'] = verified_status
        # Repeat identity checks after the solve. A changing file cannot acquire a pass.
        for key, path, limit in (('candidate_sha256', candidate_step, MAX_STEP_BYTES),
                                 ('contract_sha256', contract_path, MAX_JSON_BYTES),
                                 ('bindings_sha256', bindings_path, MAX_JSON_BYTES)):
            if digest(path, limit) != hashes[key]:
                raise AnalysisError('an input changed during analysis')
        for sid, source in contract['sources'].items():
            if digest(catalog_root / source['file']) != source['sha256']:
                raise AnalysisError('an authored source changed during analysis')
    except AnalysisError as error:
        report['status'] = 'not_evaluable'
        report['errors'].append(str(error))
    except FeaGeometryError as error:
        report['status'] = 'not_evaluable'
        report['errors'].append(str(error))
    except Exception as error:
        # Do not echo exception messages from geometry/solver libraries. They may
        # contain supplied paths, file content or environment details.
        report['status'] = 'not_evaluable'
        report['errors'].append('analysis could not be completed: ' + type(error).__name__)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', type=Path, required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--catalog-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = [args.step, args.contract, args.bindings]
    try:
        contract = _load(args.contract)
        validate_contract(contract)
        inputs.extend(args.catalog_root / source['file'] for source in contract['sources'].values())
    except Exception:
        pass  # analyze() emits the fail-closed diagnostic report.
    if args.out.resolve() in {path.resolve() for path in inputs}:
        print('Refused: output aliases an input.')
        return 2
    report = analyze(args.step, args.contract, args.bindings, args.catalog_root, args.contract_sha256)
    try:
        body = json.dumps(report, indent=2, allow_nan=False) + '\n'
        # Exclusive creation preserves previous analyses and caller-owned artifacts.
        with args.out.open('x', encoding='utf-8') as stream:
            stream.write(body)
    except (OSError, ValueError):
        print('Refused: output exists, is invalid, or cannot be written.')
        return 2
    print('FEA analysis:', report['status'])
    return {'simulated_pass': 0, 'simulated_fail': 1, 'not_evaluable': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
