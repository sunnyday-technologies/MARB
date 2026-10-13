"""Run an unranked analytical STEP control for optional MARB beam analysis.

This generates simple rectangular beam stock only. It is not M3-CRETE geometry,
a benchmark submission, a leaderboard score, or a physical validation result.
Generated STEP and report artifacts remain in a caller-selected new directory.
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

from grader.fea_analysis import PINNED, analyze, digest


class ControlError(ValueError):
    """Safe control diagnostic that does not disclose supplied paths."""


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _new_output_directory(output_dir: Path) -> Path:
    target = output_dir.resolve()
    if output_dir.exists() or output_dir.is_symlink() or target.exists():
        raise ControlError("output directory must be new")
    try:
        relative = target.relative_to(ROOT)
    except ValueError:
        relative = None
    if relative is not None and (not relative.parts or relative.parts[0] != "runs"):
        raise ControlError("repository controls must use the ignored runs directory")
    if not target.parent.is_dir():
        raise ControlError("output parent directory must already exist")
    return target


def _positive(value: float, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ControlError(label + " must be a finite positive number")
    return float(value)


def create_control(output_dir: Path, load_n: float = 49.0,
                   deflection_mm: float = 3.0) -> dict:
    """Write a frozen evaluator control and an actual translated STEP candidate.

    The Euler-Bernoulli cantilever expectations are independent of the solver.
    FY bends about local z. Local y is the 40 mm dimension and local z is 20 mm.
    No reference assembly or benchmark source geometry is read or generated.
    """
    target = _new_output_directory(Path(output_dir))
    load_n = _positive(load_n, "load")
    deflection_mm = _positive(deflection_mm, "deflection limit")
    try:
        versions = {name: importlib.metadata.version(name) for name in PINNED}
    except importlib.metadata.PackageNotFoundError as error:
        raise ControlError("pinned control dependencies are unavailable") from error
    if versions != PINNED:
        raise ControlError("control dependencies differ from the pinned runtime")
    import cadquery as cq

    # SI properties of the 20 mm by 40 mm rectangular cross section.
    length_m, depth_y_m, width_z_m = 1.0, 0.04, 0.02
    young_pa, poisson, density, yield_pa = 69e9, 0.33, 2700.0, 145e6
    iy = depth_y_m * width_z_m ** 3 / 12
    iz = width_z_m * depth_y_m ** 3 / 12
    # Saint-Venant rectangular approximation. Torsion strength is not checked.
    ratio = width_z_m / depth_y_m
    j = depth_y_m * width_z_m ** 3 / 3 * (1 - 0.63 * ratio + 0.052 * ratio ** 5)
    section = {"A_m2": depth_y_m * width_z_m, "Iy_m4": iy, "Iz_m4": iz,
               "J_m4": j, "Sy_m3": iy / (width_z_m / 2),
               "Sz_m3": iz / (depth_y_m / 2)}
    material = {"E_pa": young_pa, "G_pa": young_pa / (2 * (1 + poisson)),
                "nu": poisson, "rho_kg_m3": density, "yield_pa": yield_pa}
    safety_factor = 1.65
    expected = {"tip_deflection_mm": load_n * length_m ** 3 / (3 * young_pa * iz) * 1000,
                "max_bending_stress_pa": load_n * length_m / section["Sz_m3"],
                "stress_utilization": load_n * length_m / section["Sz_m3"] * safety_factor / yield_pa}
    stock = cq.Solid.makeBox(1000., 40., 20., cq.Vector(0., -20., -10.))
    translation_mm = [75., -125., 300.]
    candidate = stock.translate(tuple(translation_mm))

    # Check and exclusively create the output before any artifact is written.
    try:
        target.mkdir()
    except OSError as error:
        raise ControlError("output directory cannot be exclusively created") from error
    catalog = target / "catalog"
    catalog.mkdir()
    source_step, candidate_step = catalog / "beam.step", target / "candidate.step"
    cq.exporters.export(stock, str(source_step))
    cq.exporters.export(candidate, str(candidate_step))
    contract = {
        "schema_version": "marb_fea_contract.v0.1", "task_id": "ANALYTICAL-CANTILEVER",
        "revision": "r1", "coverage": "all_solids", "joint_assumption": "rigid",
        "sources": {"beam": {
            "file": "beam.step", "sha256": digest(source_step), "solid_index": 0,
            "material": material, "section": section, "section_y": [0., 1., 0.],
            "stations": [{"node": "fixed", "point_mm": [0., 0., 0.]},
                         {"node": "tip", "point_mm": [1000., 0., 0.]}],
            "property_provenance": "Analytical rectangular control. Assumed isotropic material: E 69 GPa, nu 0.33, density 2700 kg/m3, yield 145 MPa. A, Iy, Iz, Sy and Sz use exact rectangle formulae. J uses the Saint-Venant rectangular approximation. No certified stock or physical validation.",
        }},
        "supports": [{"node": "fixed", "restrained": [True] * 6}],
        "loads": [{"id": "tip-FY", "self_weight": False, "gravity": [0., -9.80665, 0.],
                   "point_loads": [{"node": "tip", "direction": "FY", "value": -load_n}]}],
        "limits": {"deflection_mm": deflection_mm, "safety_factor": safety_factor},
    }
    contract_path = target / "contract.json"
    _write_json(contract_path, contract)
    contract_hash = digest(contract_path)
    bindings = {"schema_version": "marb_fea_bindings.v0.1",
                "candidate_sha256": digest(candidate_step), "contract_sha256": contract_hash,
                "members": {"beam": {"solid_index": 0}}}
    bindings_path = target / "bindings.json"
    _write_json(bindings_path, bindings)
    metadata = {"control": "analytical_rectangular_cantilever", "leaderboard_eligible": False,
                "publication_status": "unranked_development_analysis", "physical_validation": False,
                "candidate_translation_mm": translation_mm,
                "independent_formulae": {"deflection": "F*L^3/(3*E*Iz)",
                                         "bending_stress": "F*L/Sz"},
                "analytic_expected": expected, "runtime_versions": versions,
                "hashes": {"source_sha256": digest(source_step),
                           "candidate_sha256": digest(candidate_step),
                           "contract_sha256": contract_hash,
                           "bindings_sha256": digest(bindings_path)}}
    _write_json(target / "control.json", metadata)
    return {"step": candidate_step, "contract": contract_path, "bindings": bindings_path,
            "catalog_root": catalog, "contract_sha256": contract_hash,
            "output_dir": target, "expected": expected}


def run_control(output_dir: Path, load_n: float = 49.0,
                deflection_mm: float = 3.0) -> dict:
    inputs = create_control(output_dir, load_n, deflection_mm)
    report = analyze(inputs["step"], inputs["contract"], inputs["bindings"],
                     inputs["catalog_root"], inputs["contract_sha256"])
    _write_json(inputs["output_dir"] / "report.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="New directory with an existing parent. Repository outputs must use runs/.")
    parser.add_argument("--load-n", type=float, default=49.0)
    parser.add_argument("--deflection-mm", type=float, default=3.0)
    args = parser.parse_args(argv)
    try:
        report = run_control(args.out_dir, args.load_n, args.deflection_mm)
    except ControlError as error:
        print("Refused:", str(error))
        return 2
    except Exception as error:
        print("Control could not be completed:", type(error).__name__)
        return 2
    print("Unranked analytical control:", report["status"])
    return {"simulated_pass": 0, "simulated_fail": 1, "not_evaluable": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
