"""Audit an authored M3 catalog and private inventory. Never admit an FEA task.

This local operator tool verifies identities and reports coverage gaps. A draft
coverage contract is deliberately different from the numerical beam contract.
Counts and profile geometry cannot establish load paths or connection capacity.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grader import fea_analysis as boundary
from grader.fea_step import FeaGeometryError, _solids, inspect_stock_section

SCHEMA = "marb_m3_structural_draft.v0.1"
REPORT_SCHEMA = "marb_m3_coverage_report.v0.1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 128
ROLES = {"beam_stock", "load_transfer_plate", "load_transfer_spacer",
         "rolling_contact", "equipment_mass", "drive_component", "drive_belt"}
REQUIREMENTS = {
    "material_properties": "analysis", "torsion_and_shear_center": "analysis",
    "connections": "analysis", "supports": "analysis", "mass_and_payload": "analysis",
    "travel_loads": "analysis", "service_limits": "analysis",
    "candidate_extraction": "analysis", "independent_controls": "analysis",
    "blind_assignment": "publication", "immutable_isolated_runtime": "publication",
    "physical_comparison": "physical",
}


def _fail(code: str) -> None:
    raise boundary.AnalysisError("m3.audit." + code)


def _text(value, maximum=1000):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        _fail("invalid_text")


def _entry_name(value):
    _text(value, 200)
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or ":" in value
            or any(p in {"", ".", ".."} for p in value.split("/"))
            or path.suffix.lower() not in {".step", ".stp"}):
        _fail("invalid_catalog_entry")


def validate_draft(contract: dict) -> None:
    boundary._fields(contract, {"schema_version", "task_id", "revision", "status",
                               "archives", "catalog", "requirements", "historical_controls"}, "M3 draft")
    if contract["schema_version"] != SCHEMA or contract["status"] != "draft_unqualified":
        _fail("unsupported_draft_or_status")
    if contract["task_id"] != "M3-CRETE":
        _fail("unsupported_task")
    boundary._identifier(contract["revision"])
    archives = contract["archives"]
    if not isinstance(archives, dict) or not 1 <= len(archives) <= 8:
        _fail("invalid_archive_catalog")
    for name, value in archives.items():
        _text(name, 100)
        if Path(name).name != name or "\\" in name or ":" in name or not name.endswith(".zip"):
            _fail("invalid_archive_name")
        boundary._hash(value)
    catalog = contract["catalog"]
    if not isinstance(catalog, dict) or not 1 <= len(catalog) <= MAX_ENTRIES:
        _fail("invalid_catalog")
    entries = set()
    for source_id, source in catalog.items():
        boundary._identifier(source_id)
        boundary._fields(source, {"entry", "sha256", "solid_count", "role", "section_y", "disposition"}, "catalog source")
        _entry_name(source["entry"])
        if source["entry"] in entries:
            _fail("duplicate_catalog_entry")
        entries.add(source["entry"])
        boundary._hash(source["sha256"])
        if type(source["solid_count"]) is not int or not 1 <= source["solid_count"] <= 16:
            _fail("invalid_source_solid_count")
        if source["role"] not in ROLES:
            _fail("unknown_structural_role")
        _text(source["disposition"])
        if source["role"] == "beam_stock":
            if source["solid_count"] != 1:
                _fail("beam_stock_requires_one_solid")
            vector = boundary._vector(source["section_y"])
            if sum(abs(x) > 1e-9 for x in vector) != 1 or abs(sum(x*x for x in vector)-1) > 1e-9:
                _fail("invalid_section_axis")
        elif source["section_y"] is not None:
            _fail("nonbeam_cannot_declare_beam_section")
    requirements = contract["requirements"]
    boundary._fields(requirements, set(REQUIREMENTS), "qualification requirements")
    for name, requirement in requirements.items():
        boundary._fields(requirement, {"phase", "status", "needed", "basis"}, "qualification requirement")
        # This schema records gaps. It cannot be used to assert qualification.
        if requirement["phase"] != REQUIREMENTS[name] or requirement["status"] not in {"missing", "provisional"}:
            _fail("draft_cannot_claim_qualification")
        _text(requirement["needed"])
        if not isinstance(requirement["basis"], list) or len(requirement["basis"]) > 8:
            _fail("invalid_requirement_basis")
        for basis in requirement["basis"]:
            _text(basis, 300)
    history = contract["historical_controls"]
    boundary._fields(history, {"payload_n", "lateral_n", "deflection_limit_mm", "safety_factor", "adopted_for_M3"}, "historical controls")
    for name in ("payload_n", "lateral_n", "deflection_limit_mm", "safety_factor"):
        boundary._number(history[name], True)
    if history["adopted_for_M3"] is not False:
        _fail("historical_controls_not_task_approval")


def validate_inventory(inventory: dict, contract: dict, expected_reference_hash: str) -> dict:
    boundary._hash(expected_reference_hash)
    boundary._fields(inventory, {"schema_version", "reference_sha256", "instances"}, "private inventory")
    if inventory["schema_version"] != "marb_m3_private_inventory.v0.1":
        _fail("unsupported_inventory")
    if inventory["reference_sha256"] != expected_reference_hash:
        _fail("inventory_reference_hash_mismatch")
    counts = inventory["instances"]
    if not isinstance(counts, dict) or set(counts) != set(contract["catalog"]):
        _fail("inventory_must_account_for_every_source")
    roles = Counter()
    instances = solids = 0
    for source_id, count in counts.items():
        if type(count) is not int or not 0 <= count <= 256:
            _fail("invalid_instance_count")
        source = contract["catalog"][source_id]
        roles[source["role"]] += count
        instances += count
        solids += count * source["solid_count"]
    if not 1 <= instances <= 256 or solids > 256:
        _fail("inventory_out_of_bounds")
    return {"instances": instances, "expected_solids": solids,
            "role_instances": dict(sorted(roles.items())),
            "beam_instances": roles["beam_stock"],
            "nonbeam_instances": instances - roles["beam_stock"],
            "part_identity_and_placement_verified": False}


def _catalog_bytes(archive: Path, contract: dict) -> dict:
    if boundary.digest(archive, MAX_ARCHIVE_BYTES) not in contract["archives"].values():
        _fail("archive_not_frozen")
    try:
        with zipfile.ZipFile(archive) as stream:
            infos = stream.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_ENTRIES or len(set(names)) != len(names):
                _fail("archive_entries_invalid")
            expected = {s["entry"] for s in contract["catalog"].values()}
            actual = {n for n in names if PurePosixPath(n).suffix.lower() in {".step", ".stp"}}
            if actual != expected:
                _fail("archive_step_coverage_mismatch")
            result = {}
            total = 0
            for source_id, source in contract["catalog"].items():
                info = stream.getinfo(source["entry"])
                total += info.file_size
                if not 0 < info.file_size <= boundary.MAX_STEP_BYTES or total > MAX_CATALOG_BYTES:
                    _fail("catalog_bytes_out_of_bounds")
                content = stream.read(info)
                if hashlib.sha256(content).hexdigest() != source["sha256"]:
                    _fail("authored_source_hash_mismatch")
                result[source_id] = content
            return result
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        _fail("archive_read_failed")


def audit(contract_path: Path, expected_contract_hash: str, archive: Path,
          workspace: Path, *, inventory_path: Path | None = None,
          expected_inventory_hash: str | None = None, reference: Path | None = None,
          expected_reference_hash: str | None = None) -> dict:
    """Return internal coverage evidence without reference coordinates or a solve."""
    report = {"schema_version": REPORT_SCHEMA, "status": "audit_rejected",
              "analysis_admitted": False, "leaderboard_eligible": False,
              "physical_acceptance": "not_evaluated", "legacy_scores_changed": False,
              "scope": "authored_catalog_and_declared_private_inventory",
              "whole_machine_structural_coverage": False, "errors": []}
    try:
        boundary._hash(expected_contract_hash)
        if boundary.digest(contract_path, boundary.MAX_JSON_BYTES) != expected_contract_hash:
            _fail("contract_hash_mismatch")
        contract = boundary._load(contract_path)
        validate_draft(contract)
        versions = {name: importlib.metadata.version(name) for name in boundary.PINNED}
        if versions != boundary.PINNED:
            _fail("runtime_version_mismatch")
        implementation = hashlib.sha256()
        for filename in ('m3_structural_audit.py', 'fea_step.py', 'fea_analysis.py'):
            implementation.update(filename.encode('ascii'))
            implementation.update(Path(__file__).with_name(filename).read_bytes())
        report.update(contract_sha256=expected_contract_hash, task_id=contract["task_id"],
                      implementation_sha256=implementation.hexdigest(),
                      revision=contract["revision"], runtime_versions=versions)
        initial_archive_hash = boundary.digest(archive, MAX_ARCHIVE_BYTES)
        catalog = _catalog_bytes(archive, contract)
        sources = {}
        # Read only hash-verified catalog bytes. Never extract archive paths.
        with tempfile.TemporaryDirectory(prefix="m3-stock-", dir=workspace) as temp:
            for source_id, content in catalog.items():
                path = Path(temp) / (source_id + ".step")
                path.write_bytes(content)
                solids = _solids(path)
                source = contract["catalog"][source_id]
                if len(solids) != source["solid_count"]:
                    _fail("source_solid_count_mismatch")
                result = {"sha256": source["sha256"], "solid_count": len(solids),
                          "role": source["role"], "numerical_model_present": False}
                if source["role"] == "beam_stock":
                    result["geometry"] = inspect_stock_section(solids[0], source["section_y"])
                    result["beam_geometry_verified"] = True
                else:
                    result["beam_geometry_verified"] = False
                sources[source_id] = result
        report["sources"] = sources
        report["catalog_summary"] = {"files": len(sources),
                                     "solids": sum(s["solid_count"] for s in sources.values()),
                                     "beam_profiles": sum(s["beam_geometry_verified"] for s in sources.values())}
        optional = (inventory_path, expected_inventory_hash, reference, expected_reference_hash)
        if any(v is not None for v in optional):
            if not all(v is not None for v in optional):
                _fail("private_inventory_requires_all_frozen_inputs")
            boundary._hash(expected_inventory_hash)
            if boundary.digest(inventory_path, boundary.MAX_JSON_BYTES) != expected_inventory_hash:
                _fail("inventory_hash_mismatch")
            summary = validate_inventory(boundary._load(inventory_path), contract, expected_reference_hash)
            if boundary.digest(reference) != expected_reference_hash:
                _fail("reference_hash_mismatch")
            # Independently count STEP solids. Never serialize their positions.
            actual_solids = len(_solids(reference))
            if actual_solids != summary["expected_solids"]:
                _fail("reference_inventory_solid_count_mismatch")
            if boundary.digest(reference) != expected_reference_hash:
                _fail("reference_changed_during_audit")
            if boundary.digest(inventory_path, boundary.MAX_JSON_BYTES) != expected_inventory_hash:
                _fail("inventory_changed_during_audit")
            summary.update(actual_solids=actual_solids, reference_sha256=expected_reference_hash,
                           inventory_sha256=expected_inventory_hash)
            report["private_inventory"] = summary
        if (boundary.digest(contract_path, boundary.MAX_JSON_BYTES) != expected_contract_hash
                or boundary.digest(archive, MAX_ARCHIVE_BYTES) != initial_archive_hash):
            _fail("input_changed_during_audit")
        report.update(status="audit_complete_admission_blocked", archive_sha256=initial_archive_hash,
                      blockers={phase: [name for name, scope in REQUIREMENTS.items() if scope == phase]
                                for phase in ("analysis", "publication", "physical")})
    except (boundary.AnalysisError, FeaGeometryError) as error:
        report["errors"] = [str(error)]
    except Exception:
        report["errors"] = ["m3.audit.runtime_or_io_failure"]
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inventory-sha256")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args(argv)
    try:
        # Exclusive directory creation preserves inputs and prior evidence.
        args.out_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("m3.audit.output_directory_unavailable")
        return 2
    report = audit(args.contract, args.contract_sha256, args.kit, args.out_dir,
                   inventory_path=args.inventory, expected_inventory_hash=args.inventory_sha256,
                   reference=args.reference, expected_reference_hash=args.reference_sha256)
    try:
        with (args.out_dir / "coverage.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError):
        print("m3.audit.output_write_failed")
        return 2
    print(report["status"])
    # Audit completion never means structural admission. Exit 0 means audit only.
    return 0 if report["status"] == "audit_complete_admission_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
