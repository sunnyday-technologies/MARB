"""Deterministic in-container probes for the MARB H2b runtime smoke.

This module only defines immutable probe and input bytes.  Importing it does
not read the environment or filesystem, create a provider, start a process, or
perform network access.  The host runner materializes these exact bytes in a
temporary workspace immediately before invoking the isolated engine.
"""
from __future__ import annotations

from dataclasses import dataclass


PROBE_SCHEMA = "marb_runtime_smoke_probe.v1"
NONZERO_EXIT_CODE = 23
STDOUT_OVERFLOW_BYTES = 1_048_577
WORKSPACE_OVERFLOW_ENTRIES = 4_096
TIMEOUT_SLEEP_SECONDS = 3_600
EXPECTED_RUNTIME = {
    "runtime_contract": "marb-v0.13-h2b",
    "runtime_contract_sha256": "2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80",
    "cadclaw_version": "0.10.0",
    "cadclaw_commit": "fad0dd552a49a0b32336f1845c2b82873ad6360a",
    "cadclaw_gate_spec_version": "0.13.0",
    "cadclaw_gate_registry_version": "harness-gates.v1",
    "cadclaw_source_manifest_sha256": "6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831",
    "cadclaw_calibration_sha256": "64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84",
    "cadquery_version": "2.7.0",
    "cadquery_ocp_version": "7.8.1.1.post1",
}
EXPECTED_PIN_BASIS = "marb_v0.13_calibrated_cadclaw_fad0dd55"
EXPECTED_ENVIRONMENT = {
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "TMPDIR": "/tmp",
    "TZ": "UTC",
    "XDG_CACHE_HOME": "/tmp/cache",
}


@dataclass(frozen=True)
class ProbeCase:
    """One fixed smoke case; ``source`` is exact ASCII with one final LF."""

    case_id: str
    source: bytes
    expected: str
    timeout_seconds: float
    oversized_host_input: bool = False


def _ascii_source(value: str) -> bytes:
    if "\r" in value or not value.endswith("\n"):
        raise AssertionError("smoke probe source must use LF and one final newline")
    return value.encode("ascii")


# This is also the executor's runtime preflight.  Keep it self-contained: it is
# copied into the isolated workspace and executed with Python ``-I -B``.
POSITIVE_PROVENANCE_AND_IMPORT_SOURCE = """\
import hashlib
import json
import os
from importlib.metadata import distribution, version
from pathlib import Path

import OCP
import cadclaw
import cadquery
import vtk
from cadclaw.gate_registry import HARNESS_GATE_REGISTRY
from cadclaw.gate_spec import GATE_SPEC_VERSION

expected_env = {
    "HOME", "TMPDIR", "XDG_CACHE_HOME", "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED", "TZ", "LANG", "LC_ALL", "PATH"
}
status = {}
for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
    if line.startswith(("CapEff:", "NoNewPrivs:", "Seccomp:")):
        key, value = line.split(":", 1)
        status[key] = value.strip()
root_options = None
for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
    fields = line.split()
    if len(fields) >= 4 and fields[1] == "/":
        root_options = fields[3].split(",")
runtime = json.loads(Path("/opt/marb/runtime.json").read_text(encoding="utf-8"))
contract_raw = Path("/opt/marb/runtime-contract.json").read_bytes()
calibration_raw = Path("/opt/marb/cadclaw-calibration.json").read_bytes()
contract = json.loads(contract_raw.decode("utf-8"))
calibration = json.loads(calibration_raw.decode("utf-8"))
provenance = json.loads(
    Path("/opt/marb/build-provenance.json").read_text(encoding="utf-8")
)
measured_runtime = {
    "runtime_contract": contract["contract_id"],
    "runtime_contract_sha256": hashlib.sha256(contract_raw).hexdigest(),
    "cadclaw_version": version("cadclaw"),
    "cadclaw_commit": contract["cadclaw"]["commit"],
    "cadclaw_gate_spec_version": GATE_SPEC_VERSION,
    "cadclaw_gate_registry_version": HARNESS_GATE_REGISTRY.version,
    "cadclaw_source_manifest_sha256": contract["cadclaw"]["package_source_manifest_sha256"],
    "cadclaw_calibration_sha256": hashlib.sha256(calibration_raw).hexdigest(),
    "cadquery_version": version("cadquery"),
    "cadquery_ocp_version": version("cadquery-ocp"),
}
assert contract["cadclaw"]["calibration_evidence_sha256"] == measured_runtime["cadclaw_calibration_sha256"]
assert runtime.get("cadclaw_pin_basis") == contract["pin_basis"]
assert all(runtime.get(key) == value for key, value in measured_runtime.items())
assert runtime.get("run_limiter_sha256") == hashlib.sha256(Path("/opt/marb/run_limited.py").read_bytes()).hexdigest()
assert calibration["classification"] == "compatible" and calibration["failed_checks"] == []
assert calibration["source_control"]["candidate_commit"] == measured_runtime["cadclaw_commit"]
candidate_manifest = calibration["source_manifests"]["source_to_wheel_inputs"]["candidate"]
manifest_bytes = b"".join(
    (item["sha256"] + "  " + item["path"] + chr(10)).encode("utf-8")
    for item in candidate_manifest["files"]
)
assert len(candidate_manifest["files"]) == candidate_manifest["file_count"] == 53
assert hashlib.sha256(manifest_bytes).hexdigest() == candidate_manifest["manifest_sha256"] == measured_runtime["cadclaw_source_manifest_sha256"]
package_roots = ("cadclaw/", "cadclaw_cli/", "cadclaw_mcp/", "cadharness/")
expected_package_files = {
    item["path"]: item["sha256"]
    for item in candidate_manifest["files"]
    if item["path"].startswith(package_roots) and item["path"].endswith(".py")
}
cadclaw_distribution = distribution("cadclaw")
installed_package_files = sorted(
    str(item).replace(chr(92), "/")
    for item in (cadclaw_distribution.files or [])
    if str(item).replace(chr(92), "/").startswith(package_roots)
    and str(item).replace(chr(92), "/").endswith(".py")
)
assert installed_package_files == sorted(expected_package_files)
assert all(
    hashlib.sha256(Path(cadclaw_distribution.locate_file(path)).read_bytes()).hexdigest()
    == digest
    for path, digest in expected_package_files.items()
)
assert provenance["schema"] == "marb_h2b_image_build_provenance.v3"
for key in (
    "runtime_contract", "runtime_contract_sha256", "cadclaw_commit",
    "cadclaw_gate_spec_version", "cadclaw_gate_registry_version",
    "cadclaw_source_manifest_sha256", "cadclaw_calibration_sha256",
):
    assert provenance[key] == measured_runtime[key]
assert provenance["cadclaw_pin_basis"] == contract["pin_basis"]
expected_native_provenance = {
    "native_deb_lock_sha256": "4b12f84d010651166dd4067ede60689215c174d1d2926d4b1d07befff05232f1",
    "native_deb_manifest_sha256": "0ad2f18d336e070c5cbaab7204e3cc76f1ec112e9d8fbd6d69a42902b27fa1e1",
    "native_bundle_verifier_sha256": "f067b00c69c5c341d5dcd98a0d941cdf8c1bf0dbec4edc7aa1ceeb23df319179",
    "native_deb_package_count": 39,
    "native_deb_total_bytes": 48570480,
}
assert all(provenance.get(key) == value for key, value in expected_native_provenance.items())
assert hashlib.sha256(Path("/opt/marb/native-debs.lock.json").read_bytes()).hexdigest() == expected_native_provenance["native_deb_lock_sha256"]
assert hashlib.sha256(Path("/opt/marb/native-debs.sha256").read_bytes()).hexdigest() == expected_native_provenance["native_deb_manifest_sha256"]
assert hashlib.sha256(Path("/opt/marb/verify_native_bundle.py").read_bytes()).hexdigest() == expected_native_provenance["native_bundle_verifier_sha256"]
runtime.update(measured_runtime)
probe = Path("/workspace/.marb-write-probe")
probe.write_text("ok", encoding="ascii")
probe.unlink()
tmp_probe = Path("/tmp/marb-write-probe")
tmp_probe.write_text("ok", encoding="ascii")
tmp_probe.unlink()
kit_read_only = False
try:
    Path("/workspace/kit/.marb-write-probe").write_text("no", encoding="ascii")
except OSError:
    kit_read_only = True
staged_inputs_read_only = False
try:
    Path("/marb-input/.marb-write-probe").write_text("no", encoding="ascii")
except OSError:
    staged_inputs_read_only = True
result = {
    **runtime,
    "uid": os.geteuid(),
    "gid": os.getegid(),
    "capabilities_zero": status.get("CapEff") == "0000000000000000",
    "no_new_privileges": status.get("NoNewPrivs") == "1",
    "seccomp_filtered": status.get("Seccomp") == "2",
    "root_read_only": isinstance(root_options, list) and "ro" in root_options,
    "network_interfaces": sorted(item.name for item in Path("/sys/class/net").iterdir()),
    "docker_socket_absent": not Path("/var/run/docker.sock").exists(),
    "environment_keys": sorted(os.environ),
    "cwd": os.getcwd(),
    "kit_read_only": kit_read_only,
    "staged_inputs_read_only": staged_inputs_read_only,
    "staged_input_root": "/marb-input",
}
assert result["uid"] == 65532 and result["gid"] == 65532
assert result["capabilities_zero"] and result["no_new_privileges"]
assert result["seccomp_filtered"] and result["root_read_only"]
assert result["network_interfaces"] == ["lo"] and result["docker_socket_absent"]
assert result["kit_read_only"]
assert result["staged_inputs_read_only"] and result["staged_input_root"] == "/marb-input"
assert set(result["environment_keys"]) == expected_env and result["cwd"] == "/workspace"
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
"""


EXPECTED_NONZERO_EXIT_SOURCE = f"raise SystemExit({NONZERO_EXIT_CODE})\n"

NETWORK_DENIAL_SOURCE = f'''\
import json
import socket
from pathlib import Path

interfaces = sorted(item.name for item in Path("/sys/class/net").iterdir())
denied = False
connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connection.settimeout(0.25)
try:
    connection.connect(("192.0.2.1", 9))
except OSError:
    denied = True
finally:
    connection.close()
result = {{
    "docker_socket_absent": not Path("/var/run/docker.sock").exists(),
    "network_interfaces": interfaces,
    "outbound_denied": denied,
    "schema": "{PROBE_SCHEMA}",
}}
assert result == {{
    "docker_socket_absent": True,
    "network_interfaces": ["lo"],
    "outbound_denied": True,
    "schema": "{PROBE_SCHEMA}",
}}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''

PROTECTED_WRITE_DENIAL_SOURCE = f'''\
import json
from pathlib import Path

def denied(path):
    try:
        path.write_bytes(b"x")
    except OSError:
        return True
    path.unlink()
    return False

protected = {{
    "host_workspace": denied(Path("/marb-host-workspace/.marb-smoke-write")),
    "kit": denied(Path("/workspace/kit/.marb-smoke-write")),
    "root": denied(Path("/.marb-smoke-write")),
    "staged_input": denied(Path("/marb-input/.marb-smoke-write")),
}}
workspace_probe = Path("/workspace/.marb-smoke-write")
workspace_probe.write_bytes(b"ok")
workspace_probe.unlink()
tmp_probe = Path("/tmp/.marb-smoke-write")
tmp_probe.write_bytes(b"ok")
tmp_probe.unlink()
export_probe = Path("/marb-export/.marb-smoke-write")
export_probe.write_bytes(b"ok")
export_probe.unlink()
result = {{
    "export_tmpfs_writable": True,
    "protected_denials": protected,
    "schema": "{PROBE_SCHEMA}",
    "tmp_writable": True,
    "workspace_writable": True,
}}
assert all(protected.values())
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''

EXACT_CLEAN_ENVIRONMENT_SOURCE = f'''\
import json
import os

expected = {{
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "TMPDIR": "/tmp",
    "TZ": "UTC",
    "XDG_CACHE_HOME": "/tmp/cache",
}}
assert dict(os.environ) == expected
result = {{
    "environment_keys": sorted(os.environ),
    "schema": "{PROBE_SCHEMA}",
}}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''

STDOUT_OVERFLOW_REJECTION_SOURCE = f'''\
import sys

written = sys.stdout.buffer.write(b"x" * {STDOUT_OVERFLOW_BYTES})
sys.stdout.buffer.flush()
assert written == {STDOUT_OVERFLOW_BYTES}
'''

WORKSPACE_INPUT_REJECTION_SOURCE = "raise AssertionError(\"pre-Docker rejection did not occur\")\n"

EXPORT_WORKSPACE_OVERFLOW_SOURCE = f'''\
from pathlib import Path
import time

for index in range({WORKSPACE_OVERFLOW_ENTRIES}):
    Path(f"overflow-{{index:04d}}").mkdir()
time.sleep({TIMEOUT_SLEEP_SECONDS})
raise AssertionError("workspace limiter did not reject overflow")
'''

TIMEOUT_SOURCE = f'''\
import time

time.sleep({TIMEOUT_SLEEP_SECONDS})
raise AssertionError("execution timeout did not terminate the probe")
'''


RUNTIME_SMOKE_INPUTS: tuple[tuple[str, bytes], ...] = (
    ("brief.md", b"MARB deterministic no-provider runtime smoke input\n"),
    ("docs/readme.txt", b"Tracked runtime smoke documentation sentinel\n"),
    ("kit/geometry.txt", b"Deterministic kit geometry sentinel\n"),
    ("license.txt", b"Synthetic non-secret runtime smoke license sentinel\n"),
    ("reference/reference.bin", b"MARB-RUNTIME-SMOKE-REFERENCE\x00\x01\n"),
)


REQUIRED_CASE_IDS = (
    "positive_provenance_and_import",
    "expected_nonzero_exit",
    "network_denial",
    "protected_write_denial",
    "exact_clean_environment",
    "stdout_overflow_rejection",
    "workspace_input_rejection_overflow",
    "export_workspace_overflow",
    "timeout",
)


CASES: tuple[ProbeCase, ...] = (
    ProbeCase(
        "positive_provenance_and_import",
        _ascii_source(POSITIVE_PROVENANCE_AND_IMPORT_SOURCE),
        "success_json",
        60.0,
    ),
    ProbeCase(
        "expected_nonzero_exit",
        _ascii_source(EXPECTED_NONZERO_EXIT_SOURCE),
        "nonzero_exit",
        15.0,
    ),
    ProbeCase(
        "network_denial",
        _ascii_source(NETWORK_DENIAL_SOURCE),
        "network_denial",
        15.0,
    ),
    ProbeCase(
        "protected_write_denial",
        _ascii_source(PROTECTED_WRITE_DENIAL_SOURCE),
        "protected_write_denial",
        15.0,
    ),
    ProbeCase(
        "exact_clean_environment",
        _ascii_source(EXACT_CLEAN_ENVIRONMENT_SOURCE),
        "exact_clean_environment",
        15.0,
    ),
    ProbeCase(
        "stdout_overflow_rejection",
        _ascii_source(STDOUT_OVERFLOW_REJECTION_SOURCE),
        "stdout_overflow_rejection",
        15.0,
    ),
    ProbeCase(
        "workspace_input_rejection_overflow",
        _ascii_source(WORKSPACE_INPUT_REJECTION_SOURCE),
        "pre_docker_rejection",
        15.0,
        oversized_host_input=True,
    ),
    ProbeCase(
        "export_workspace_overflow",
        _ascii_source(EXPORT_WORKSPACE_OVERFLOW_SOURCE),
        "export_workspace_rejection",
        30.0,
    ),
    ProbeCase(
        "timeout",
        _ascii_source(TIMEOUT_SOURCE),
        "timeout",
        1.0,
    ),
)

CASE_IDS = tuple(item.case_id for item in CASES)

if CASE_IDS != REQUIRED_CASE_IDS:
    raise AssertionError("runtime smoke contract does not match the required case order")


__all__ = [
    "CASES",
    "CASE_IDS",
    "EXPECTED_ENVIRONMENT",
    "EXPECTED_PIN_BASIS",
    "EXPECTED_RUNTIME",
    "NONZERO_EXIT_CODE",
    "POSITIVE_PROVENANCE_AND_IMPORT_SOURCE",
    "PROBE_SCHEMA",
    "ProbeCase",
    "REQUIRED_CASE_IDS",
    "RUNTIME_SMOKE_INPUTS",
    "STDOUT_OVERFLOW_BYTES",
]
