"""Static regressions for the offline H2b OCI runtime recipe.

These tests inspect tracked source and may invoke a local POSIX shell for a
syntax-only quoting probe. They never invoke Docker, a provider, or a model,
and they do not claim that a real image has been built or qualified.
"""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from harness import (
    cohort_executor,
    cohort_runner,
    isolated_container,
    runtime_smoke_probes,
)
from harness.container import verify_native_bundle as native_bundle
from scripts import canonical_manifest
from scripts.calibrate_h2b_cadclaw import (
    CANDIDATE_COMMIT,
    FROZEN_COMMIT,
    NIST_TEST_RUNNER,
    REVISION_PROBE,
    SYNTHETIC_GENERATOR,
    CalibrationError,
    _LocalGit,
    _archive_commit,
    _canonical_json,
    _new_output_path,
    _normalize_report,
    _require_expected_commits,
    _safe_environment,
    _snapshot_aggregate,
)


REPO = Path(__file__).resolve().parents[1]
CONTAINER = REPO / "harness" / "container"
DOCKERFILE = CONTAINER / "Dockerfile"
LOCK = CONTAINER / "requirements.lock"
NATIVE_LOCK = CONTAINER / "native-debs.lock.json"
NATIVE_VERIFIER = CONTAINER / "verify_native_bundle.py"
LIMITER = CONTAINER / "run_limited.py"
NOTES = CONTAINER / "README.md"
NATIVE_LEDGER = CONTAINER / "NATIVE_RUNTIME_REPAIR_LEDGER.md"
HARNESS_NOTES = REPO / "harness" / "README.md"
EXECUTOR_NOTES = REPO / "harness" / "H2B_EXECUTOR.md"
CHANGELOG = REPO / "CHANGELOG.md"
GITIGNORE = REPO / ".gitignore"
DOCKERIGNORE = CONTAINER / ".dockerignore"
BUILD_CONTEXT_DOCKERIGNORE = CONTAINER / "build-context.dockerignore"
GITATTRIBUTES = REPO / ".gitattributes"
MANIFEST_TOOL = REPO / "scripts" / "canonical_manifest.py"
RUNTIME_SMOKE_PROBES = REPO / "harness" / "runtime_smoke_probes.py"

R4_NATIVE_MANIFEST_SHA256 = (
    "0ad2f18d336e070c5cbaab7204e3cc76f1ec112e9d8fbd6d69a42902b27fa1e1"
)
R4_BUILD_CONTEXT_MANIFEST_SHA256 = (
    "b06fe7d32efbf26eb8e9de51cd24e79001e43e2fd81c8c688a8a71e1eaabc2bb"
)
R4_INSERTION_CONTEXT_MANIFEST_SHA256 = (
    "60f20631283c508e7f00e5e32e3740b78b4a3bab176c0aba64c6c753c190ca5f"
)
R4_WHEEL_PAYLOAD_BYTES = 297_642_677
R4_CONTEXT_PAYLOAD_BYTES = 346_343_497
R4_CONTEXT_TOTAL_BYTES = 346_354_716
R4_EFFECTIVE_DOCKERIGNORE_BYTES = 287
R4_NATIVE_VERIFIER_BYTES = 18_161
R4_CONTEXT_STATIC_DIGESTS = {
    ".dockerignore": "21d3c82490a8f2e2309193d31dc868c274ddb121539c82d5bc778c5095b0eb01",
    "Dockerfile": "ecde1d618fc1f673d3fb29c798eb8cebc9d837a1596284c4529f4148c4ac432f",
    "cadclaw-calibration.fad0dd55.json": "64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84",
    "native-debs.lock.json": "4b12f84d010651166dd4067ede60689215c174d1d2926d4b1d07befff05232f1",
    "native-debs.sha256": R4_NATIVE_MANIFEST_SHA256,
    "requirements.lock": "8512d48e20dc30458485f0d2ca252e2be3af9e444486d922671a79628dc0b308",
    "run_limited.py": "659c8fd6e9070f8591a868f337cb6be7375c87910bb01ac09974653045408c1b",
    "runtime-contract.v0.13.json": "2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80",
    "verify_native_bundle.py": "37c8f9a3014fca98dfad646b4dbe569ee4921a5afaa47c800a8a93e4570336cb",
    "wheelhouse.sha256": "63211cd2d2df66b9005fcff1fdf618b8c14bb12a6a5aaa183106c03abd221eca",
}
R4_CONTEXT_STATIC_BYTES = {
    "Dockerfile": 16_420,
    "cadclaw-calibration.fad0dd55.json": 52_011,
    "native-debs.lock.json": 20_636,
    "requirements.lock": 1_185,
    "run_limited.py": 9_445,
    "runtime-contract.v0.13.json": 1_839,
}
R6_BUILD_CONTEXT_MANIFEST_SHA256 = (
    "fd52aeee64309e26891542454bc02e4aad8ece49b01d3b6da44297ca4192ecb2"
)
R6_INSERTION_CONTEXT_MANIFEST_SHA256 = (
    "a51894764d526bc0c243323144533a031ac089d55b7efad15ac693f83df7fdb1"
)
R6_CONTEXT_PAYLOAD_BYTES = 346_360_029
R6_CONTEXT_TOTAL_BYTES = 346_371_248
R6_CONTEXT_STATIC_DIGESTS = {
    **R4_CONTEXT_STATIC_DIGESTS,
    "verify_native_bundle.py": "f067b00c69c5c341d5dcd98a0d941cdf8c1bf0dbec4edc7aa1ceeb23df319179",
}
R6_CONTEXT_STATIC_BYTES = {
    **R4_CONTEXT_STATIC_BYTES,
    "verify_native_bundle.py": 34_693,
}
CURRENT_RUN_LIMITER_SHA256 = (
    "f621fc46b49f53c21ed2ea44d21b65f6e8bb16bff4a61afcfb471c061e708188"
)
CURRENT_RUN_LIMITER_BYTES = 9_925
R7_BUILD_CONTEXT_MANIFEST_SHA256 = (
    "1a04532ee9d8be923e4857eaa0cfb64be53dc004c28d897add60c5fc4b2d9ded"
)
R7_CONTEXT_PAYLOAD_BYTES = 346_360_509
R7_CONTEXT_TOTAL_BYTES = 346_371_728
R7_CONTEXT_STATIC_DIGESTS = {
    **R6_CONTEXT_STATIC_DIGESTS,
    "run_limited.py": CURRENT_RUN_LIMITER_SHA256,
}
R7_CONTEXT_STATIC_BYTES = {
    **R6_CONTEXT_STATIC_BYTES,
    "run_limited.py": CURRENT_RUN_LIMITER_BYTES,
}
R4_WHEEL_MANIFEST = b"""f349ba8f4b75cb25c99c5c2d84e997e485204d2902a9597802b0371f09331fb8  wheelhouse/aiohappyeyeballs-2.6.1-py3-none-any.whl
3a807cabd5115fb55af198b98178997a5e0e57dead43eb74a93d9c07d6d4a7dc  wheelhouse/aiohttp-3.13.5-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
053243f8b92b990551949e63930a839ff0cf0b0ebbe0597b0f3fb19e1a0fe82e  wheelhouse/aiosignal-1.4.0-py3-none-any.whl
1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53  wheelhouse/annotated_types-0.7.0-py3-none-any.whl
c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309  wheelhouse/attrs-26.1.0-py3-none-any.whl
aafba01d51a0524d1be61de219f704992649b7daeee07a79446210e8be4359da  wheelhouse/cadclaw-0.10.0-py3-none-any.whl
13bdffe3a1c9a7b29d86d43313021173b1620f45223929d2631cf8d4c5dbe2c0  wheelhouse/cadquery-2.7.0-py3-none-any.whl
0ff754db099da9a4285268cecf7740fef782567621480b007697c00e47f8fbde  wheelhouse/cadquery_ocp-7.8.1.1.post1-cp311-cp311-manylinux_2_31_x86_64.whl
5086799a46d10ba884b72fd02c21be09dae52cbc189272354a5d424791b55f37  wheelhouse/casadi-3.7.2-cp311-none-manylinux2014_x86_64.whl
51e79c1f7470158e838808d4a996fa9bac72c498e93d8ebe5119bc1e6becb0db  wheelhouse/contourpy-1.3.3-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
85cef7cff222d8644161529808465972e51340599459b8ac3ccbac5a854e0d30  wheelhouse/cycler-0.12.1-py3-none-any.whl
7f75a4f2924ebdda0f5b2779ff2135ba92de2596c95a8fa9b1d9ebcabea1be41  wheelhouse/ezdxf-1.4.4-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
d76ac49f929aecaf82d83250b8347e099d7aecba0f4726c1d9b6df3b8bb5fe18  wheelhouse/fonttools-4.63.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
2552f44204b744fba866e573be4c1f9048d6a324dfe14475103fd51613eb1d1f  wheelhouse/frozenlist-1.8.0-cp311-cp311-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl
048adeaf8c2d788c40fee287673ccaa74c24ffd8dcf09ffa555a2fbb59f10ac8  wheelhouse/idna-3.15-py3-none-any.whl
2517e24d7315eb51c10664cdb865195df38ab74456c677df67bb47f12d088a27  wheelhouse/kiwisolver-1.5.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
8e436d155fa8a3399dc62683f8f5d0e2e50d25d0144a73edd73f82eec8f4abfb  wheelhouse/matplotlib-3.10.9-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
6e35b35f818b01f691643c6c611bc0902f2e92b46c18fffa77ae1e7c46e912e4  wheelhouse/more_itertools-11.0.2-py3-none-any.whl
454e29e186285d2ebe65be34629fa0e8605202c60fbc7c4c650ccd41870896ef  wheelhouse/msgpack-1.1.2-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
439cbebd499f92e9aa6793016a8acaa161dfa749ae86d20960189f5398a19144  wheelhouse/multidict-6.7.1-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
fd0c473c43558908d97cc06e4d68e8f69202f167db46f7b4e4058893e7dbdf60  wheelhouse/multimethod-1.12-py3-none-any.whl
4dc7b2bc9b4c524037ce42aa0d743004e40d02c1c5c2493e5eda4d66328b0fde  wheelhouse/nlopt-2.10.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
df3775294accfdd75f32c74ae39fcba920c9a378a2fc18a12b6820aa8c1fb502  wheelhouse/numpy-2.4.4-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e  wheelhouse/packaging-26.2-py3-none-any.whl
ec7e136df29172e5030dd07e037d55f676bdb29d15bfa09b80da29d07d3b9303  wheelhouse/path-17.1.1-py3-none-any.whl
e74473c875d78b8e9d5da2a70f7099549f9eb37ded4e2f6a463e60125bccd176  wheelhouse/pillow-12.2.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
5aaa2b923c1944ac8febd6609cb373540a5563e7cbcb0fd770f75dace2eb817b  wheelhouse/propcache-0.5.2-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
45a282cde31d808236fd7ea9d919b128653c8b38b393d1c4ab335c62924d9aba  wheelhouse/pydantic-2.13.4-py3-none-any.whl
f9fa868638bf362d3d138ea55829cefb3d5f4b0d7f142234382a15e2485dbec4  wheelhouse/pydantic_core-2.46.4-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
850ba148bd908d7e2411587e247a1e4f0327839c40e2e5e6d05a007ecc69911d  wheelhouse/pyparsing-3.3.2-py3-none-any.whl
a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427  wheelhouse/python_dateutil-2.9.0.post0-py2.py3-none-any.whl
b8bb0864c5a28024fac8a632c443c87c5aa6f215c0b126c449ae1a150412f31d  wheelhouse/pyyaml-6.0.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
ea8cc6828217ebfda5c159dce969e832efd865a09d6ad1fc993f5bf5e1a627ee  wheelhouse/runtype-0.5.3-py3-none-any.whl
4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274  wheelhouse/six-1.17.0-py2.py3-none-any.whl
cbe3a8986119455669753ec8744b125a64cf04645eddca47a40c56479df78de7  wheelhouse/trame-3.13.1-py3-none-any.whl
829a59bc5e2bf9ae2adc3a254ce9b0183c0312c656b3f5b0c88be35671703c9c  wheelhouse/trame_client-3.12.2-py3-none-any.whl
85d13ac87e7e4db853278afc98d19d14972e1fc75394aac9800221be0829e5cd  wheelhouse/trame_common-1.2.3-py3-none-any.whl
897a6c0ebcc72d95a461bde28d2c2e37c4bc4922013ad07df3a65e64d4884672  wheelhouse/trame_components-2.5.0-py3-none-any.whl
7d7871085c0d0f59a1389d2721a5ece3c55c9e1e1a3db8f4cb512111eb71bd71  wheelhouse/trame_server-3.12.1-py3-none-any.whl
31c8220f59dcc3b5f2fcfe6de8b9796e8bdb7db5dcf790ee01df83d44e79a413  wheelhouse/trame_vtk-2.11.8-py3-none-any.whl
b40ecfa30a675df4ce40d5161b9b6c040cfe5445599933c478a3359afbee91d3  wheelhouse/trame_vuetify-3.2.2-py3-none-any.whl
f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548  wheelhouse/typing_extensions-4.15.0-py3-none-any.whl
4ed1cacbdc298c220f1bd249ed5287caa16f34d44ef4e9c3d0cbad5b521545e7  wheelhouse/typing_inspection-0.4.2-py3-none-any.whl
f8edc04e0f8b6719cfc769e575a777267d667f447d1948c62fa97fb756cd75bb  wheelhouse/vtk-9.3.1-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
89f23bad3b3522dcb78be84907487f6cf742c6b4526a666fd3e4013f5f705015  wheelhouse/wslink-2.5.6-py3-none-any.whl
99c8a9ed30f4164bc4c14b37a90208836cbf50d4ce2a57c71d0f52c7fb4f7598  wheelhouse/yarl-1.23.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
"""

LF_PINNED_INPUTS = (
    "harness/cohort_runner.py",
    "harness/cohort_executor.py",
    "harness/provider_transport.py",
    "harness/isolated_container.py",
    "harness/runtime_smoke_probes.py",
    "harness/runtime_smoke_runner.py",
    "harness/container/run_limited.py",
    "harness/container/Dockerfile",
    "harness/container/.dockerignore",
    "harness/container/requirements.lock",
    "harness/container/native-debs.lock.json",
    "harness/container/verify_native_bundle.py",
    "harness/container/runtime-contract.v0.12.json",
    "harness/container/runtime-contract.v0.13.json",
    "harness/container/cadclaw-calibration.fad0dd55.json",
    "scripts/calibrate_h2b_cadclaw.py",
    "scripts/canonical_manifest.py",
)


def _r4_native_entries() -> list[canonical_manifest.ManifestEntry]:
    lock = json.loads(NATIVE_LOCK.read_text(encoding="utf-8"))
    return [
        canonical_manifest.ManifestEntry(
            f"native-debs/{Path(item['filename']).name}",
            item["sha256"],
            item["size"],
        )
        for item in lock["packages"]
    ]


def _r4_wheel_entries() -> list[canonical_manifest.ManifestEntry]:
    return list(canonical_manifest.parse_manifest_bytes(R4_WHEEL_MANIFEST))


def _context_entries(
    static_digests: dict[str, str],
) -> list[canonical_manifest.ManifestEntry]:
    entries = [
        canonical_manifest.ManifestEntry(path, digest)
        for path, digest in static_digests.items()
    ]
    entries.extend(_r4_wheel_entries())
    entries.extend(_r4_native_entries())
    return entries


def _r4_context_entries() -> list[canonical_manifest.ManifestEntry]:
    return _context_entries(R4_CONTEXT_STATIC_DIGESTS)


def _r6_context_entries() -> list[canonical_manifest.ManifestEntry]:
    return _context_entries(R6_CONTEXT_STATIC_DIGESTS)


def _r7_context_entries() -> list[canonical_manifest.ManifestEntry]:
    return _context_entries(R7_CONTEXT_STATIC_DIGESTS)


def _serialize_in_order(entries: list[canonical_manifest.ManifestEntry]) -> bytes:
    return b"".join(
        entry.sha256.encode("ascii")
        + b"  "
        + entry.path.encode("ascii")
        + b"\n"
        for entry in entries
    )


class CanonicalManifestTests(unittest.TestCase):
    def _run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = canonical_manifest.main(list(arguments))
        return result, stdout.getvalue(), stderr.getvalue()

    def _minimal_build_context(self, root: Path) -> None:
        wheelhouse = root / "wheelhouse"
        native_debs = root / "native-debs"
        wheelhouse.mkdir()
        native_debs.mkdir()
        wheel = wheelhouse / "sample-1.0-py3-none-any.whl"
        native = native_debs / "sample_1_amd64.deb"
        wheel.write_bytes(b"wheel")
        native.write_bytes(b"native")

        wheel_document = canonical_manifest.build_manifest(
            [
                canonical_manifest.ManifestEntry(
                    "wheelhouse/sample-1.0-py3-none-any.whl",
                    hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    wheel.stat().st_size,
                )
            ]
        )
        native_document = canonical_manifest.build_manifest(
            [
                canonical_manifest.ManifestEntry(
                    "native-debs/sample_1_amd64.deb",
                    hashlib.sha256(native.read_bytes()).hexdigest(),
                    native.stat().st_size,
                )
            ]
        )
        (root / "wheelhouse.sha256").write_bytes(wheel_document.raw)
        (root / "native-debs.sha256").write_bytes(native_document.raw)
        lock = {
            "packages": [
                {
                    "filename": "pool/sample_1_amd64.deb",
                    "sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
                    "size": native.stat().st_size,
                }
            ]
        }
        (root / "native-debs.lock.json").write_text(
            json.dumps(lock) + "\n", encoding="ascii", newline="\n"
        )
        for name in (
            "Dockerfile",
            ".dockerignore",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
            "cadclaw-calibration.fad0dd55.json",
            "verify_native_bundle.py",
        ):
            (root / name).write_bytes((name + "\n").encode("ascii"))

    def test_exact_historical_r4_native_and_context_vectors_without_payload_bodies(self) -> None:
        native = canonical_manifest.build_manifest(_r4_native_entries())
        self.assertEqual(len(native.entries), 39)
        self.assertEqual(len(native.raw), 4_351)
        self.assertEqual(native.sha256, R4_NATIVE_MANIFEST_SHA256)
        self.assertEqual(native.payload_bytes, 48_570_480)
        self.assertEqual(
            canonical_manifest.build_manifest(
                canonical_manifest.parse_manifest_bytes(native.raw)
            ).raw,
            native.raw,
        )

        wheel_entries = _r4_wheel_entries()
        self.assertEqual(len(wheel_entries), 46)
        self.assertEqual(hashlib.sha256(R4_WHEEL_MANIFEST).hexdigest(), R4_CONTEXT_STATIC_DIGESTS["wheelhouse.sha256"])
        context = canonical_manifest.build_manifest(_r4_context_entries())
        self.assertEqual(len(context.entries), 95)
        self.assertEqual(len(context.raw), 11_219)
        self.assertEqual(context.sha256, R4_BUILD_CONTEXT_MANIFEST_SHA256)
        self.assertEqual(
            canonical_manifest.build_manifest(
                canonical_manifest.parse_manifest_bytes(context.raw)
            ).raw,
            context.raw,
        )

        tracked = (
            "Dockerfile",
            "cadclaw-calibration.fad0dd55.json",
            "native-debs.lock.json",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
        )
        for path in tracked:
            with self.subTest(path=path):
                if path != "run_limited.py":
                    self.assertEqual(
                        hashlib.sha256((CONTAINER / path).read_bytes()).hexdigest(),
                        R4_CONTEXT_STATIC_DIGESTS[path],
                    )
        reconstructed_payload = (
            sum(R4_CONTEXT_STATIC_BYTES[path] for path in tracked)
            + R4_NATIVE_VERIFIER_BYTES
            + R4_EFFECTIVE_DOCKERIGNORE_BYTES
            + len(R4_WHEEL_MANIFEST)
            + len(native.raw)
            + R4_WHEEL_PAYLOAD_BYTES
            + native.payload_bytes
        )
        self.assertEqual(reconstructed_payload, R4_CONTEXT_PAYLOAD_BYTES)
        self.assertEqual(
            reconstructed_payload + len(context.raw), R4_CONTEXT_TOTAL_BYTES
        )

    def test_exact_historical_r6_context_vector_without_payload_bodies(self) -> None:
        context = canonical_manifest.build_manifest(_r6_context_entries())
        self.assertEqual(len(context.entries), 95)
        self.assertEqual(len(context.raw), 11_219)
        self.assertEqual(context.sha256, R6_BUILD_CONTEXT_MANIFEST_SHA256)

        tracked = (
            "Dockerfile",
            "cadclaw-calibration.fad0dd55.json",
            "native-debs.lock.json",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
            "verify_native_bundle.py",
        )
        for path in tracked:
            with self.subTest(path=path):
                if path != "run_limited.py":
                    self.assertEqual(
                        hashlib.sha256((CONTAINER / path).read_bytes()).hexdigest(),
                        R6_CONTEXT_STATIC_DIGESTS[path],
                    )
        native = canonical_manifest.build_manifest(_r4_native_entries())
        reconstructed_payload = (
            sum(R6_CONTEXT_STATIC_BYTES[path] for path in tracked)
            + R4_EFFECTIVE_DOCKERIGNORE_BYTES
            + len(R4_WHEEL_MANIFEST)
            + len(native.raw)
            + R4_WHEEL_PAYLOAD_BYTES
            + native.payload_bytes
        )
        self.assertEqual(reconstructed_payload, R6_CONTEXT_PAYLOAD_BYTES)
        self.assertEqual(
            reconstructed_payload + len(context.raw), R6_CONTEXT_TOTAL_BYTES
        )

    def test_current_run_limiter_vector_is_distinct_from_historical_r4_r6(self) -> None:
        raw = LIMITER.read_bytes()
        self.assertEqual(len(raw), CURRENT_RUN_LIMITER_BYTES)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CURRENT_RUN_LIMITER_SHA256)
        self.assertNotEqual(
            CURRENT_RUN_LIMITER_SHA256,
            R6_CONTEXT_STATIC_DIGESTS["run_limited.py"],
        )
        self.assertEqual(R4_CONTEXT_STATIC_BYTES["run_limited.py"], 9_445)
        self.assertEqual(R6_CONTEXT_STATIC_BYTES["run_limited.py"], 9_445)

    def test_exact_current_r7_context_vector_without_payload_bodies(self) -> None:
        effective_dockerignore = BUILD_CONTEXT_DOCKERIGNORE.read_bytes()
        self.assertEqual(len(effective_dockerignore), R4_EFFECTIVE_DOCKERIGNORE_BYTES)
        self.assertNotIn(b"\r", effective_dockerignore)
        self.assertEqual(
            hashlib.sha256(effective_dockerignore).hexdigest(),
            R7_CONTEXT_STATIC_DIGESTS[".dockerignore"],
        )
        self.assertNotEqual(effective_dockerignore, DOCKERIGNORE.read_bytes())

        context = canonical_manifest.build_manifest(_r7_context_entries())
        self.assertEqual(len(context.entries), 95)
        self.assertEqual(len(context.raw), 11_219)
        self.assertEqual(context.sha256, R7_BUILD_CONTEXT_MANIFEST_SHA256)

        tracked = (
            "Dockerfile",
            "cadclaw-calibration.fad0dd55.json",
            "native-debs.lock.json",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
            "verify_native_bundle.py",
        )
        for path in tracked:
            with self.subTest(path=path):
                self.assertEqual(
                    hashlib.sha256((CONTAINER / path).read_bytes()).hexdigest(),
                    R7_CONTEXT_STATIC_DIGESTS[path],
                )
        native = canonical_manifest.build_manifest(_r4_native_entries())
        reconstructed_payload = (
            sum(R7_CONTEXT_STATIC_BYTES[path] for path in tracked)
            + R4_EFFECTIVE_DOCKERIGNORE_BYTES
            + len(R4_WHEEL_MANIFEST)
            + len(native.raw)
            + R4_WHEEL_PAYLOAD_BYTES
            + native.payload_bytes
        )
        self.assertEqual(reconstructed_payload, R7_CONTEXT_PAYLOAD_BYTES)
        self.assertEqual(
            reconstructed_payload + len(context.raw), R7_CONTEXT_TOTAL_BYTES
        )

    def test_r4_hash_first_native_variant_is_rejected(self) -> None:
        entries = _r4_native_entries()
        hash_first = _serialize_in_order(
            sorted(entries, key=lambda item: (item.sha256, item.path))
        )
        self.assertNotEqual(hashlib.sha256(hash_first).hexdigest(), R4_NATIVE_MANIFEST_SHA256)
        with self.assertRaisesRegex(canonical_manifest.ManifestError, "ordinal UTF-8"):
            canonical_manifest.parse_manifest_bytes(hash_first)

    def test_r4_insertion_order_context_variant_is_rejected(self) -> None:
        by_path = {entry.path: entry for entry in _r4_context_entries()}
        insertion_paths = [
            "Dockerfile",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
            "cadclaw-calibration.fad0dd55.json",
            "wheelhouse.sha256",
            "native-debs.lock.json",
            "verify_native_bundle.py",
            ".dockerignore",
            *(entry.path for entry in _r4_wheel_entries()),
            "native-debs.sha256",
            *(entry.path for entry in canonical_manifest.canonicalize_entries(_r4_native_entries())),
        ]
        insertion = _serialize_in_order([by_path[path] for path in insertion_paths])
        self.assertEqual(
            hashlib.sha256(insertion).hexdigest(),
            R4_INSERTION_CONTEXT_MANIFEST_SHA256,
        )
        with self.assertRaisesRegex(canonical_manifest.ManifestError, "ordinal UTF-8"):
            canonical_manifest.parse_manifest_bytes(insertion)

    def test_r6_insertion_order_context_variant_is_rejected(self) -> None:
        by_path = {entry.path: entry for entry in _r6_context_entries()}
        insertion_paths = [
            "Dockerfile",
            "requirements.lock",
            "run_limited.py",
            "runtime-contract.v0.13.json",
            "cadclaw-calibration.fad0dd55.json",
            "wheelhouse.sha256",
            "native-debs.lock.json",
            "verify_native_bundle.py",
            ".dockerignore",
            *(entry.path for entry in _r4_wheel_entries()),
            "native-debs.sha256",
            *(
                entry.path
                for entry in canonical_manifest.canonicalize_entries(
                    _r4_native_entries()
                )
            ),
        ]
        insertion = _serialize_in_order([by_path[path] for path in insertion_paths])
        self.assertEqual(
            hashlib.sha256(insertion).hexdigest(),
            R6_INSERTION_CONTEXT_MANIFEST_SHA256,
        )
        with self.assertRaisesRegex(canonical_manifest.ManifestError, "ordinal UTF-8"):
            canonical_manifest.parse_manifest_bytes(insertion)

    def test_planning_is_creation_order_independent_and_utf8_byte_sorted(self) -> None:
        expected = canonical_manifest.build_manifest(
            [
                canonical_manifest.ManifestEntry("z.whl", "1" * 64),
                canonical_manifest.ManifestEntry("B.whl", "2" * 64),
                canonical_manifest.ManifestEntry("a.whl", "3" * 64),
            ]
        )
        for seed in range(10):
            entries = list(expected.entries)
            random.Random(seed).shuffle(entries)
            self.assertEqual(canonical_manifest.build_manifest(entries).raw, expected.raw)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            names = ["z-1.whl", "B-1.whl", "a-1.whl"]
            random.Random(7).shuffle(names)
            for name in names:
                (wheelhouse / name).write_bytes(name.encode("ascii"))
            document = canonical_manifest.collect_profile(root, "wheelhouse")
            self.assertEqual(
                [entry.path for entry in document.entries],
                ["wheelhouse/B-1.whl", "wheelhouse/a-1.whl", "wheelhouse/z-1.whl"],
            )

    def test_wheelhouse_profile_requires_parent_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "sample.whl").write_bytes(b"sample")

            document = canonical_manifest.collect_profile(root, "wheelhouse")
            self.assertEqual(
                [entry.path for entry in document.entries],
                ["wheelhouse/sample.whl"],
            )

            with self.assertRaisesRegex(
                canonical_manifest.ManifestError,
                "path component wheelhouse is unavailable",
            ):
                canonical_manifest.collect_profile(wheelhouse, "wheelhouse")

            code, stdout, stderr = self._run_cli(
                "preview", "wheelhouse", "--root", str(wheelhouse)
            )
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                "ERROR: path component wheelhouse is unavailable\n",
            )

    def test_strict_byte_format_and_portable_path_aliases_fail_closed(self) -> None:
        valid = b"a" * 64 + b"  file.whl\n"
        self.assertEqual(canonical_manifest.parse_manifest_bytes(valid)[0].path, "file.whl")
        malformed = (
            b"A" * 64 + b"  file.whl\n",
            b"a" * 64 + b" file.whl\n",
            b"a" * 64 + b"  file.whl\r\n",
            b"a" * 64 + b"  file.whl",
            valid + b"\n",
        )
        for raw in malformed:
            with self.subTest(raw_sha256=hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(canonical_manifest.ManifestError):
                    canonical_manifest.parse_manifest_bytes(raw)

        unsafe = (
            "/absolute",
            "C:/drive",
            "//unc/share",
            "dir\\file",
            "file:stream",
            ".",
            "a/../b",
            "a//b",
            "line\nfeed",
            "nonascii-\N{LATIN SMALL LETTER E WITH ACUTE}",
            "trailing.",
            "AUX.txt",
            "less<than",
            "greater>than",
            'double"quote',
            "pipe|name",
            "question?mark",
            "star*name",
        )
        for path in unsafe:
            with self.subTest(path=repr(path)):
                with self.assertRaises(canonical_manifest.ManifestError):
                    canonical_manifest.normalize_relative_posix_path(path)

        with self.assertRaisesRegex(canonical_manifest.ManifestError, "duplicate"):
            canonical_manifest.build_manifest(
                [
                    canonical_manifest.ManifestEntry("same", "1" * 64),
                    canonical_manifest.ManifestEntry("same", "2" * 64),
                ]
            )
        with self.assertRaisesRegex(canonical_manifest.ManifestError, "case-colliding"):
            canonical_manifest.build_manifest(
                [
                    canonical_manifest.ManifestEntry("File", "1" * 64),
                    canonical_manifest.ManifestEntry("file", "2" * 64),
                ]
            )

    def test_profiles_reject_nested_symlink_and_hardlink_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "nested").mkdir()
            with self.assertRaisesRegex(canonical_manifest.ManifestError, "non-file"):
                canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "unsafe name.whl").write_bytes(b"payload")
            with self.assertRaisesRegex(canonical_manifest.ManifestError, "unexpected"):
                canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            first = wheelhouse / "first.whl"
            first.write_bytes(b"payload")
            os.link(first, wheelhouse / "second.whl")
            with self.assertRaises(canonical_manifest.ManifestError):
                canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            target = root / "outside.whl"
            target.write_bytes(b"payload")
            link = wheelhouse / "linked.whl"
            try:
                link.symlink_to(target)
            except OSError:
                marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                metadata = mock.Mock(st_file_attributes=marker)
                self.assertTrue(canonical_manifest._is_reparse_point(metadata))
            else:
                with self.assertRaises(canonical_manifest.ManifestError):
                    canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_parent = root / "real-parent"
            context = real_parent / "context"
            (context / "wheelhouse").mkdir(parents=True)
            (context / "wheelhouse" / "sample.whl").write_bytes(b"payload")
            linked_parent = root / "linked-parent"
            link_kind = "symlink"
            try:
                linked_parent.symlink_to(real_parent, target_is_directory=True)
            except OSError:
                link_kind = "none"
                if os.name == "nt":
                    completed = subprocess.run(
                        [
                            "cmd.exe",
                            "/d",
                            "/c",
                            "mklink",
                            "/J",
                            str(linked_parent),
                            str(real_parent),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    if completed.returncode == 0:
                        link_kind = "junction"
                if link_kind == "none":
                    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    metadata = mock.Mock(
                        st_file_attributes=marker,
                        st_mode=stat.S_IFDIR,
                    )
                    fake_path = mock.Mock()
                    fake_path.lstat.return_value = metadata
                    fake_path.is_symlink.return_value = False
                    with self.assertRaises(canonical_manifest.ManifestError):
                        canonical_manifest._require_directory(
                            fake_path, "reparse test directory"
                        )
            if link_kind != "none":
                try:
                    with self.assertRaisesRegex(
                        canonical_manifest.ManifestError, "every ancestor"
                    ):
                        canonical_manifest.collect_profile(
                            linked_parent / "context", "wheelhouse"
                        )
                finally:
                    if link_kind == "junction" and linked_parent.exists():
                        os.rmdir(linked_parent)

    @unittest.skipIf(os.name == "nt", "Windows does not permit byte-path names")
    def test_profile_rejects_an_undecodable_filesystem_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            raw_name = os.fsencode(wheelhouse) + b"/invalid-\xff.whl"
            descriptor = os.open(raw_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with self.assertRaisesRegex(
                canonical_manifest.ManifestError, "unsafe entry name"
            ):
                canonical_manifest.collect_profile(root, "wheelhouse")

    def test_profiles_detect_file_mutation_and_inventory_races(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "one.whl").write_bytes(b"one")
            original = canonical_manifest._hash_regular_file

            def mutate(path, label):
                result = original(path, label)
                path.write_bytes(b"changed")
                return result

            with mock.patch.object(canonical_manifest, "_hash_regular_file", side_effect=mutate):
                with self.assertRaisesRegex(canonical_manifest.ManifestError, "changed"):
                    canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "one.whl").write_bytes(b"one")
            original = canonical_manifest._hash_regular_file

            def add_file(path, label):
                result = original(path, label)
                (wheelhouse / "two.whl").write_bytes(b"two")
                return result

            with mock.patch.object(canonical_manifest, "_hash_regular_file", side_effect=add_file):
                with self.assertRaisesRegex(canonical_manifest.ManifestError, "changed"):
                    canonical_manifest.collect_profile(root, "wheelhouse")

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            ancestor = base / "staging"
            root = ancestor / "context"
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir(parents=True)
            (wheelhouse / "one.whl").write_bytes(b"one")
            displaced = base / "staging-displaced"
            original = canonical_manifest._hash_regular_file

            def swap_ancestor(path, label):
                result = original(path, label)
                ancestor.replace(displaced)
                replacement = ancestor / "context" / "wheelhouse"
                replacement.mkdir(parents=True)
                (replacement / "one.whl").write_bytes(b"one")
                return result

            with mock.patch.object(
                canonical_manifest, "_hash_regular_file", side_effect=swap_ancestor
            ):
                with self.assertRaisesRegex(canonical_manifest.ManifestError, "changed"):
                    canonical_manifest.collect_profile(root, "wheelhouse")

    def test_preview_write_and_readback_share_exact_bytes_without_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "sample.whl").write_bytes(b"sample")
            output = root / "wheelhouse.sha256"

            code, stdout, stderr = self._run_cli(
                "preview", "wheelhouse", "--root", str(root)
            )
            self.assertEqual((code, stderr), (0, ""))
            preview = json.loads(stdout)
            self.assertFalse(output.exists())

            code, stdout, stderr = self._run_cli(
                "write",
                "wheelhouse",
                "--root",
                str(root),
                "--expected-sha256",
                preview["manifest_sha256"],
            )
            self.assertEqual((code, stderr), (0, ""))
            written = json.loads(stdout)
            self.assertEqual(written["manifest_sha256"], preview["manifest_sha256"])
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), preview["manifest_sha256"])

            code, stdout, stderr = self._run_cli(
                "readback", "wheelhouse", "--root", str(root)
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["manifest_sha256"], preview["manifest_sha256"])
            original = output.read_bytes()
            code, _stdout, stderr = self._run_cli(
                "write",
                "wheelhouse",
                "--root",
                str(root),
                "--expected-sha256",
                preview["manifest_sha256"],
            )
            self.assertEqual(code, 2)
            self.assertIn("already exists", stderr)
            self.assertEqual(output.read_bytes(), original)

    def test_failed_write_readback_cleans_only_its_new_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "sample.whl").write_bytes(b"sample")
            preview = canonical_manifest.collect_profile(root, "wheelhouse")
            with mock.patch.object(
                canonical_manifest,
                "_readback",
                side_effect=canonical_manifest.ManifestError("forced readback failure"),
            ):
                code, _stdout, stderr = self._run_cli(
                    "write",
                    "wheelhouse",
                    "--root",
                    str(root),
                    "--expected-sha256",
                    preview.sha256,
                )
            self.assertEqual(code, 2)
            self.assertIn("forced readback failure", stderr)
            self.assertFalse((root / "wheelhouse.sha256").exists())

    def test_post_link_replacement_is_never_marked_owned_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "sample.whl").write_bytes(b"sample")
            preview = canonical_manifest.collect_profile(root, "wheelhouse")
            output = root / "wheelhouse.sha256"
            owned = root / "original-published-manifest"
            foreign = b"foreign replacement bytes\n"
            original_require = canonical_manifest._require_regular_file
            swapped = False

            def replace_before_validation(path, label):
                nonlocal swapped
                if label == "wheelhouse manifest output" and not swapped:
                    swapped = True
                    path.replace(owned)
                    path.write_bytes(foreign)
                return original_require(path, label)

            with mock.patch.object(
                canonical_manifest,
                "_require_regular_file",
                side_effect=replace_before_validation,
            ):
                code, _stdout, stderr = self._run_cli(
                    "write",
                    "wheelhouse",
                    "--root",
                    str(root),
                    "--expected-sha256",
                    preview.sha256,
                )
            self.assertTrue(swapped)
            self.assertEqual(code, 2)
            self.assertIn("replaced during publication", stderr)
            self.assertEqual(output.read_bytes(), foreign)

    def test_destination_created_during_link_race_is_not_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "sample.whl").write_bytes(b"sample")
            preview = canonical_manifest.collect_profile(root, "wheelhouse")
            output = root / "wheelhouse.sha256"
            foreign = b"concurrent destination bytes\n"
            original_link = os.link

            def create_destination_then_link(source, destination):
                Path(destination).write_bytes(foreign)
                return original_link(source, destination)

            with mock.patch.object(os, "link", side_effect=create_destination_then_link):
                code, _stdout, _stderr = self._run_cli(
                    "write",
                    "wheelhouse",
                    "--root",
                    str(root),
                    "--expected-sha256",
                    preview.sha256,
                )
            self.assertEqual(code, 2)
            self.assertEqual(output.read_bytes(), foreign)

    def test_write_readback_remains_bound_to_reviewed_preview_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            payload = wheelhouse / "sample.whl"
            payload.write_bytes(b"sample")
            preview = canonical_manifest.collect_profile(root, "wheelhouse")
            output = root / "wheelhouse.sha256"
            original_readback = canonical_manifest._readback

            def mutate_coherently(root_path, profile, **bindings):
                payload.write_bytes(b"changed")
                replacement = canonical_manifest.collect_profile(root_path, profile)
                output.write_bytes(replacement.raw)
                return original_readback(root_path, profile, **bindings)

            with mock.patch.object(
                canonical_manifest, "_readback", side_effect=mutate_coherently
            ):
                code, _stdout, stderr = self._run_cli(
                    "write",
                    "wheelhouse",
                    "--root",
                    str(root),
                    "--expected-sha256",
                    preview.sha256,
                )
            self.assertEqual(code, 2)
            self.assertIn("reviewed preview", stderr)
            self.assertFalse(output.exists())

    def test_coherent_foreign_readback_replacement_survives_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            payload = wheelhouse / "sample.whl"
            payload.write_bytes(b"sample")
            preview = canonical_manifest.collect_profile(root, "wheelhouse")
            output = root / "wheelhouse.sha256"
            owned = root / "original-published-manifest"
            original_readback = canonical_manifest._readback
            foreign_raw = b""

            def replace_coherently(root_path, profile, **bindings):
                nonlocal foreign_raw
                payload.write_bytes(b"changed")
                replacement = canonical_manifest.collect_profile(root_path, profile)
                foreign_raw = replacement.raw
                output.replace(owned)
                output.write_bytes(foreign_raw)
                return original_readback(root_path, profile, **bindings)

            with mock.patch.object(
                canonical_manifest, "_readback", side_effect=replace_coherently
            ):
                code, _stdout, stderr = self._run_cli(
                    "write",
                    "wheelhouse",
                    "--root",
                    str(root),
                    "--expected-sha256",
                    preview.sha256,
                )
            self.assertEqual(code, 2)
            self.assertIn("replaced before readback", stderr)
            self.assertNotEqual(hashlib.sha256(foreign_raw).hexdigest(), preview.sha256)
            self.assertEqual(output.read_bytes(), foreign_raw)

    def test_build_context_reconciles_child_manifests_and_native_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._minimal_build_context(root)
            document = canonical_manifest.collect_profile(root, "build-context")
            self.assertEqual(len(document.entries), 12)
            wheel_manifest = root / "wheelhouse.sha256"
            wheel_raw = wheel_manifest.read_bytes()
            wheel_manifest.write_bytes(b"0" * 64 + b"  wheelhouse/sample-1.0-py3-none-any.whl\n")
            with self.assertRaisesRegex(canonical_manifest.ManifestError, "does not match"):
                canonical_manifest.collect_profile(root, "build-context")
            wheel_manifest.write_bytes(wheel_raw)

            native_manifest = root / "native-debs.sha256"
            native_raw = native_manifest.read_bytes()
            native_manifest.write_bytes(
                b"0" * 64 + b"  native-debs/sample_1_amd64.deb\n"
            )
            with self.assertRaisesRegex(canonical_manifest.ManifestError, "does not match"):
                canonical_manifest.collect_profile(root, "build-context")
            native_manifest.write_bytes(native_raw)

            lock_path = root / "native-debs.lock.json"
            lock = json.loads(lock_path.read_text(encoding="ascii"))
            lock["packages"][0]["sha256"] = "0" * 64
            lock_path.write_text(json.dumps(lock) + "\n", encoding="ascii", newline="\n")
            with self.assertRaisesRegex(canonical_manifest.ManifestError, "lock does not match"):
                canonical_manifest.collect_profile(root, "build-context")


class ContainerRecipeTests(unittest.TestCase):
    def test_shared_runtime_smoke_probe_is_exactly_bound_and_strong(self) -> None:
        raw = RUNTIME_SMOKE_PROBES.read_bytes()
        normalized = (
            raw.decode("utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode("utf-8")
        )
        self.assertEqual(
            hashlib.sha256(normalized).hexdigest(),
            cohort_executor.RUNTIME_SMOKE_PROBES_SHA256,
        )
        self.assertIs(
            cohort_executor.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE,
            runtime_smoke_probes.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE,
        )
        for required in (
            "import OCP",
            "import cadclaw",
            "import cadquery",
            "import vtk",
            "/opt/marb/build-context.sha256",
            "/opt/marb/runtime-wheelhouse.sha256",
            "/opt/marb/runtime-requirements.lock",
            "context_dockerignore_sha256",
            "build_context_manifest_sha256",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runtime_smoke_probes.POSITIVE_PROVENANCE_AND_IMPORT_SOURCE)

    def test_generated_runtime_preflight_source_compiles(self) -> None:
        docker_identity = {
            "path": "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
            "sha256": "0" * 64,
        }
        container = {
            "image": "ghcr.io/sunnyday-technologies/marb-worker@sha256:" + "1" * 64,
            "docker_executable": docker_identity["path"],
        }
        with (
            mock.patch.object(
                cohort_executor,
                "_verify_host_docker_executable",
                return_value=docker_identity,
            ),
            mock.patch.object(isolated_container, "IsolatedDockerPython"),
        ):
            sandbox = cohort_executor._default_sandbox_factory(
                REPO,
                REPO,
                container,
                uuid.UUID(int=0),
            )

        source = sandbox._PREFLIGHT
        self.assertIsInstance(source, str)
        compile(source, "<DockerSandboxAdapter._PREFLIGHT>", "exec")

    def test_source_integrity_command_survives_posix_shell_dequoting(self) -> None:
        shell = os.environ.get("MARB_TEST_POSIX_SHELL") or shutil.which("sh")
        if shell is None:
            self.skipTest("POSIX shell is unavailable")

        line = next(
            (
                row
                for row in DOCKERFILE.read_text(encoding="utf-8").splitlines()
                if "candidate_manifest" in row
                and row.lstrip().startswith("&& /usr/local/bin/python3 -c \"")
            ),
            None,
        )
        self.assertIsNotNone(line, "source-integrity Python command is missing")
        command = line.strip()
        self.assertTrue(command.startswith("&& "))
        self.assertTrue(command.endswith(" \\"))
        command = command.removeprefix("&& ").removesuffix(" \\")

        probe = (
            f"set -- {command}\n"
            'test "$#" -eq 3\n'
            '"$MARB_TEST_PYTHON" -c '
            "'import sys;compile(sys.argv[1],\"<Dockerfile RUN>\",\"exec\")' \"$3\""
        )
        environment = {"MARB_TEST_PYTHON": Path(sys.executable).as_posix()}
        completed = subprocess.run(
            [shell, "-c", probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        stderr = completed.stderr.decode(errors="replace")
        if "SyntaxError" in stderr:
            diagnostic = "Dockerfile Python source becomes invalid after POSIX shell dequoting"
        else:
            diagnostic = stderr[-1000:] or f"shell probe exited {completed.returncode}"
        self.assertEqual(completed.returncode, 0, diagnostic)

    def test_runtime_manifest_exactly_matches_executor_preflight(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        expected = {
            **cohort_executor.EXPECTED_RUNTIME,
            "cadclaw_pin_basis": cohort_executor.CADCLAW_PIN_BASIS,
            "run_limiter_sha256": "$RUN_LIMITER_SHA256",
        }
        encoded = json.dumps(expected, sort_keys=True, separators=(",", ":"))
        escaped = encoded.replace('"', '\\"')
        self.assertIn(
            f'''printf '%s\\n' "{escaped}" > /opt/marb/runtime.json''',
            text,
        )
        self.assertIn("json.loads(Path('/opt/marb/runtime.json').read_text()) == expected", text)
        self.assertIn("hashlib.sha256(Path('/opt/marb/run_limited.py').read_bytes())", text)

    def test_lock_is_exact_unique_and_matches_frozen_core(self) -> None:
        rows = [
            line.strip()
            for line in LOCK.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertTrue(rows)
        self.assertTrue(all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*==[^\s<>=!~]+", row) for row in rows))
        names = [row.split("==", 1)[0].replace("_", "-").lower() for row in rows]
        self.assertEqual(len(names), len(set(names)))
        versions = dict(row.split("==", 1) for row in rows)
        self.assertEqual(versions["cadclaw"], cohort_executor.EXPECTED_RUNTIME["cadclaw_version"])
        self.assertEqual(versions["cadquery"], cohort_executor.EXPECTED_RUNTIME["cadquery_version"])
        self.assertEqual(
            versions["cadquery-ocp"],
            cohort_executor.EXPECTED_RUNTIME["cadquery_ocp_version"],
        )
        self.assertEqual(
            hashlib.sha256(NATIVE_LOCK.read_bytes()).hexdigest(),
            cohort_executor.NATIVE_DEB_LOCK_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(NATIVE_VERIFIER.read_bytes()).hexdigest(),
            cohort_executor.NATIVE_BUNDLE_VERIFIER_SHA256,
        )

        native_lock = json.loads(NATIVE_LOCK.read_text(encoding="utf-8"))
        native_manifest = "".join(
            f"{item['sha256']}  native-debs/{Path(item['filename']).name}\n"
            for item in sorted(
                native_lock["packages"], key=lambda item: Path(item["filename"]).name
            )
        ).encode("ascii")
        self.assertEqual(
            hashlib.sha256(native_manifest).hexdigest(),
            cohort_executor.NATIVE_DEB_MANIFEST_SHA256,
        )

    def test_recipe_binds_exact_audited_wheel_and_offline_inputs(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        wheel = "/opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl"
        self.assertIn('cad == [\'cadclaw-0.10.0-py3-none-any.whl\']', text)
        self.assertIn(f'"$CADCLAW_WHEEL_SHA256" {wheel}', text)
        self.assertIn(f"--no-deps --only-binary=:all: {wheel}", text)
        self.assertIn("re.fullmatch(r'([0-9a-f]{64})  (wheelhouse/", text)
        self.assertIn("not p.is_symlink()", text)
        self.assertIn("len(wheel_entries) == len(wheel_lines)", text)
        self.assertIn("sorted(wheel_entries) == sorted(wheel_paths)", text)
        self.assertLess(
            text.index("sorted(wheel_entries) == sorted(wheel_paths)"),
            text.index("sha256sum -c runtime-wheelhouse.sha256"),
        )
        self.assertIn("observed == os.environ['CADCLAW_WHEEL_SHA256']", text)
        self.assertIn("dist.read_text('RECORD')", text)
        self.assertIn("base64.urlsafe_b64decode", text)
        self.assertIn("--no-index --only-binary=:all:", text)
        self.assertIn("ARG NATIVE_DEB_LOCK_SHA256", text)
        self.assertIn("ARG NATIVE_DEB_MANIFEST_SHA256", text)
        self.assertIn("ARG NATIVE_BUNDLE_VERIFIER_SHA256", text)
        self.assertIn("COPY native-debs.lock.json /opt/marb/native-debs.lock.json", text)
        self.assertIn("COPY verify_native_bundle.py /opt/marb/verify_native_bundle.py", text)
        self.assertIn("COPY native-debs.sha256 /opt/marb/native-debs.sha256", text)
        self.assertIn("COPY native-debs/ /opt/marb/native-debs/", text)
        self.assertIn("verify_native_bundle.py archives", text)
        self.assertIn("dpkg --unpack /opt/marb/native-debs/*.deb", text)
        self.assertIn("dpkg --configure --pending", text)
        self.assertIn("verify_native_bundle.py runtime", text)
        self.assertIn('["dpkg", "--audit"]', NATIVE_VERIFIER.read_text(encoding="utf-8"))
        self.assertLess(text.index("verify_native_bundle.py archives"), text.index("dpkg --unpack"))
        self.assertLess(text.index("dpkg --configure --pending"), text.index("verify_native_bundle.py runtime"))
        self.assertNotIn("apt-get", text)
        self.assertNotIn("apt ", text)
        self.assertIn("/opt/marb/build-provenance.json", text)
        self.assertIn("ARG DOCKERFILE_SHA256", text)
        self.assertIn("ARG CONTEXT_DOCKERIGNORE_SHA256", text)
        self.assertIn("ARG BUILD_CONTEXT_MANIFEST_SHA256", text)
        self.assertIn('org.sunnyday.marb.dockerfile-sha256="${DOCKERFILE_SHA256}"', text)
        self.assertIn(
            'org.sunnyday.marb.context-dockerignore-sha256="${CONTEXT_DOCKERIGNORE_SHA256}"',
            text,
        )
        self.assertIn(
            'org.sunnyday.marb.build-context-manifest-sha256="${BUILD_CONTEXT_MANIFEST_SHA256}"',
            text,
        )
        self.assertIn("COPY build-context.sha256 /opt/marb/build-context.sha256", text)
        self.assertIn("marb_h2b_image_build_provenance.v3", text)
        preflight_source = inspect.getsource(cohort_executor._default_sandbox_factory)
        for digest in (
            cohort_executor.NATIVE_DEB_LOCK_SHA256,
            cohort_executor.NATIVE_DEB_MANIFEST_SHA256,
            cohort_executor.NATIVE_BUNDLE_VERIFIER_SHA256,
        ):
            self.assertIn(digest, preflight_source)
        self.assertIn(
            "COPY runtime-contract.v0.13.json /opt/marb/runtime-contract.json",
            text,
        )
        self.assertIn(
            "COPY cadclaw-calibration.fad0dd55.json /opt/marb/cadclaw-calibration.json",
            text,
        )
        self.assertIn("runtime-contract\\.v0\\.13\\.json", text)
        self.assertIn("cadclaw-calibration\\.fad0dd55\\.json", text)
        self.assertIn("'runtime-contract.v0.13.json'", text)
        self.assertIn("'cadclaw-calibration.fad0dd55.json'", text)
        self.assertIn("candidate_manifest['files']", text)
        self.assertIn("GATE_SPEC_VERSION", text)
        self.assertIn("HARNESS_GATE_REGISTRY.version", text)
        self.assertNotIn("PLACEHOLDER", text)
        self.assertNotIn("0" * 64, text)
        self.assertNotIn(cohort_executor.FROZEN_CADCLAW_COMMIT, text)
        self.assertNotIn("marb-v0.12-h2b", text)
        self.assertNotIn("marb_v0.12_frozen_functional_core", text)
        for field in (
            "native_deb_lock_sha256",
            "native_deb_manifest_sha256",
            "native_bundle_verifier_sha256",
            "dockerfile_sha256",
            "context_dockerignore_sha256",
            "build_context_manifest_sha256",
        ):
            with self.subTest(field=field):
                self.assertIn(field, text)
        self.assertIn("context_entries['Dockerfile'] == os.environ['DOCKERFILE_SHA256']", text)
        self.assertIn(
            "context_entries['.dockerignore'] == os.environ['CONTEXT_DOCKERIGNORE_SHA256']",
            text,
        )
        self.assertIn('org.sunnyday.marb.base-image="${PYTHON_BASE_IMAGE}"', text)
        self.assertNotRegex(text, r"@sha256:[0-9a-f]{64}")
        self.assertNotIn("curl ", text)
        self.assertNotIn("git clone", text)

        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn("RepoDigest identifies the output", notes)
        self.assertIn("non-self-referential context manifest", notes)
        self.assertIn("canonical_manifest.py preview wheelhouse", notes)
        self.assertIn("canonical_manifest.py preview native-debs", notes)
        self.assertIn("preview build-context --root .", notes)
        self.assertIn(R4_NATIVE_MANIFEST_SHA256, notes)
        self.assertIn(R7_BUILD_CONTEXT_MANIFEST_SHA256, notes)
        self.assertNotIn("find wheelhouse", notes)
        self.assertNotIn("find native-debs", notes)
        self.assertNotIn("sort -k2", notes)
        self.assertIn("--build-arg \"DOCKERFILE_SHA256=$dockerfileSha\"", notes)
        self.assertIn("--build-arg \"NATIVE_DEB_LOCK_SHA256=$nativeDebLockSha\"", notes)
        self.assertIn("--build-arg \"NATIVE_DEB_MANIFEST_SHA256=$nativeDebManifestSha\"", notes)
        self.assertIn(
            "--build-arg \"NATIVE_BUNDLE_VERIFIER_SHA256=$nativeBundleVerifierSha\"",
            notes,
        )
        self.assertIn(
            "--build-arg \"CONTEXT_DOCKERIGNORE_SHA256=$contextDockerignoreSha\"",
            notes,
        )
        self.assertIn(
            "--build-arg \"BUILD_CONTEXT_MANIFEST_SHA256=$buildContextManifestSha\"",
            notes,
        )
        self.assertIn("private-build-record.json", notes)

    def test_native_repair_ledger_preserves_r5_and_binds_r6_scope(self) -> None:
        ledger = NATIVE_LEDGER.read_text(encoding="utf-8")
        self.assertIn("## R4 failed-closed result", ledger)
        self.assertIn(R4_BUILD_CONTEXT_MANIFEST_SHA256, ledger)
        self.assertIn("## R5 failed-closed result", ledger)
        self.assertIn("native_runtime_unresolved_libraries", ledger)
        self.assertIn("R5 cannot be replayed", ledger)
        self.assertIn("## R6 verifier-only prerequisite", ledger)
        self.assertIn(R6_BUILD_CONTEXT_MANIFEST_SHA256, ledger)
        self.assertIn(native_bundle.EXPECTED_NATIVE_PATH_VECTOR_SHA256, ledger)
        for relative in native_bundle.EXPECTED_NON_RUNTIME_MEMBERS:
            with self.subTest(relative=relative):
                self.assertIn(f"`{relative}`", ledger)
        self.assertIn("Debian `libxfixes3` is not added", ledger)
        self.assertNotIn("C:\\Users\\", ledger)

    def test_limiter_and_truthful_manual_gate_are_part_of_recipe(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        self.assertTrue(LIMITER.is_file())
        self.assertIn("COPY run_limited.py /opt/marb/run_limited.py", text)
        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn("no approved OCI RepoDigest yet", notes)
        self.assertIn("Mandatory no-provider runtime smoke", notes)
        self.assertIn("CI does not build, pull, push, or run Docker", notes)
        self.assertIn("only writable host bind exposed to the child", notes)
        self.assertIn("`/marb-export/workspace.tar`", notes)

    def test_private_build_inputs_are_anchored_and_ignored(self) -> None:
        ignored = set(GITIGNORE.read_text(encoding="utf-8").splitlines())
        self.assertTrue(
            {
                "/harness/container/wheelhouse/",
                "/harness/container/wheelhouse.sha256",
                "/harness/container/native-debs/",
                "/harness/container/native-debs.sha256",
                "/harness/container/build-context.sha256",
                "/harness/container/private-build-record.json",
            }.issubset(ignored)
        )
        self.assertTrue(MANIFEST_TOOL.is_file())
        self.assertNotIn(
            "canonical_manifest.py", DOCKERFILE.read_text(encoding="utf-8")
        )
        gitignore_notes = GITIGNORE.read_text(encoding="utf-8")
        self.assertIn("tracked default-deny .dockerignore", gitignore_notes)
        self.assertIn("harness/container/private-build-record.json", NOTES.read_text(encoding="utf-8"))
        docker_ignored = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
        self.assertIn("**", docker_ignored)
        self.assertFalse(any(line.startswith("!") for line in docker_ignored))
        self.assertTrue(
            {
                "private-build-record.json",
                "wheelhouse/",
                "wheelhouse.sha256",
                "native-debs/",
                "native-debs.sha256",
                ".env",
                "*secret*",
                "*credential*",
                "*token*",
                "*.key",
                "*.pem",
            }.issubset(docker_ignored)
        )

    def test_byte_hashed_h2b_inputs_are_lf_pinned_for_fresh_checkouts(self) -> None:
        attributes = set(GITATTRIBUTES.read_text(encoding="utf-8").splitlines())
        for relative in LF_PINNED_INPUTS:
            with self.subTest(relative=relative):
                self.assertIn(f"/{relative} text eol=lf", attributes)
                self.assertNotIn(b"\r", (REPO / relative).read_bytes())

    def test_operator_example_uses_only_the_canonical_prompt_variant(self) -> None:
        self.assertEqual(cohort_runner.CANONICAL_PROMPT_VARIANT, "frozen-core")
        notes = HARNESS_NOTES.read_text(encoding="utf-8")
        self.assertIn("--prompt-variant frozen-core", notes)
        self.assertNotIn("--prompt-variant frozen-v012", notes)

    def test_planner_stdout_capture_preserves_canonical_utf8_bytes(self) -> None:
        notes = HARNESS_NOTES.read_text(encoding="utf-8")
        normalized = " ".join(notes.split())
        self.assertIn("PowerShell 7.4", notes)
        self.assertIn("exact canonical UTF-8 stdout bytes", normalized)
        self.assertIn("final newline", normalized)
        self.assertIn("native `>` redirection", normalized)
        self.assertIn("`Out-File`", notes)
        self.assertIn("`Set-Content`", notes)
        self.assertIn("planner itself remains read-only", normalized)
        self.assertIn(".Hash.ToLowerInvariant()", notes)

    def test_operator_digest_examples_normalize_powershell_hashes_to_lowercase(self) -> None:
        executor_notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        container_notes = NOTES.read_text(encoding="utf-8")
        self.assertIn(
            "$dockerExecutableSha = (Get-FileHash -LiteralPath $dockerExecutable "
            "-Algorithm SHA256).Hash.ToLowerInvariant()",
            executor_notes,
        )
        self.assertIn(
            "$gitExecutableSha = (Get-FileHash -LiteralPath $gitExecutable "
            "-Algorithm SHA256).Hash.ToLowerInvariant()",
            executor_notes,
        )
        for variable in (
            "$lockSha",
            "$runLimiterSha",
            "$wheelhouseManifestSha",
            "$cadclawWheelSha",
            "$dockerfileSha",
            "$contextDockerignoreSha",
            "$buildContextManifestSha",
            "$dockerExeSha",
        ):
            with self.subTest(variable=variable):
                line = next(
                    row for row in container_notes.splitlines() if row.startswith(f"{variable} =")
                )
                self.assertIn(".Hash.ToLowerInvariant()", line)

    def test_operator_mount_contract_matches_isolation_constants(self) -> None:
        self.assertEqual(isolated_container.CONTAINER_HOST_WORKSPACE, "/marb-host-workspace")
        self.assertEqual(isolated_container.CONTAINER_INPUT_ROOT, "/marb-input")
        self.assertEqual(isolated_container.CONTAINER_INPUT, "/workspace/kit")
        self.assertEqual(isolated_container.CONTAINER_EXPORT, "/marb-export")
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        for path in (
            "/marb-host-workspace",
            "/marb-input",
            "/workspace/kit",
            "/marb-export/workspace.tar",
        ):
            with self.subTest(path=path):
                self.assertIn(f"`{path}`", notes)

    def test_operator_docs_keep_display_labels_out_of_model_identity(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertIn("`cell_label` and `model.name` are operator-supplied display labels", notes)
        self.assertIn("exact authorized `model.id`", notes)
        self.assertIn("no model-alias policy", notes)

    def test_operator_docs_disclose_attempt_budget_and_negative_modality_scope(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        normalized = " ".join(notes.split())
        self.assertIn("exactly one logical slot and its one retained attempt", normalized)
        self.assertIn("`max_cost_usd`", normalized)
        self.assertIn(
            "separately approved aggregate Nightwatch campaign authorization",
            normalized,
        )
        self.assertIn("reviewed per-slot H2b authorizations", normalized)
        self.assertIn("H2b does not implement aggregate authority or coordination", normalized)
        self.assertIn(
            "implements the separate serial local ledger and concurrency controller",
            normalized,
        )
        self.assertIn("`execution_modality`", normalized)
        self.assertIn("no native image-view tool", normalized)
        self.assertIn(
            "staged image bytes may be inspected only through model-authored Python",
            normalized,
        )
        self.assertIn("`vision_attested` is `false`", normalized)
        self.assertIn("`/marb-export/workspace.tar`", normalized)
        self.assertIn("runs/.slot-claims/<planned-run-id>.json", normalized)
        self.assertNotIn("slot.reservation", normalized)

    def test_operator_docs_scope_slot_claims_to_one_checkout(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("only within the same checkout", normalized)
                self.assertIn("`runs/` is ignored", normalized)
                self.assertIn("not a global lock", normalized)
                self.assertIn("Cross-clone and cross-host uniqueness", normalized)
                self.assertIn("operator/campaign-ledger coordination", normalized)
                self.assertIn("later registry/publication validation", normalized)

    def test_operator_docs_distinguish_owned_paths_from_captured_artifacts(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("`marb_plan_output_binding.v2`", normalized)
                self.assertIn("`executor_owned_outputs`", normalized)
                self.assertIn("reserved/bound output paths", normalized)
                self.assertIn("only `artifacts`", normalized)
                self.assertIn("failed", normalized)
                self.assertIn("partial", normalized)

    def test_operator_docs_cover_authenticated_git_executable_boundary(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("`marb_execution_authorization.v3`", normalized)
                self.assertIn("normalized absolute", normalized)
                self.assertIn("schema validation bind", normalized)
                self.assertIn(
                    "`validate-authorization` validates those declared bindings "
                    "but does not inspect the host executable",
                    normalized,
                )
                self.assertIn(
                    "Execution preflight verifies the actual host path chain "
                    "and executable file digest",
                    normalized,
                )
                self.assertIn("symlink/reparse", normalized)
                self.assertIn(
                    "Production committed-blob reads use only `authorized_git`; "
                    "no injected blob reader",
                    normalized,
                )
                self.assertIn(
                    "immediately before every committed-blob spawn", normalized
                )
                self.assertIn(
                    "each path component and the executable with native handles",
                    normalized,
                )
                self.assertIn("hash-to-process-creation interval", normalized)
                self.assertIn("exact resolved repository", normalized)
                self.assertIn("`safe.directory`", normalized)
                self.assertIn("never `*`", normalized)
                self.assertIn(
                    "closing the hash-to-spawn replacement window", normalized
                )
                self.assertIn("authorized absolute path as `argv[0]`", normalized)
                self.assertIn("never cwd or ambient `PATH`", normalized)
                self.assertIn("`--no-replace-objects`", normalized)
                self.assertIn("`GIT_NO_REPLACE_OBJECTS=1`", normalized)
                self.assertIn("stdout bytes", normalized)
                self.assertIn("elapsed timeout", normalized)
                self.assertIn("neutral", normalized)
                self.assertIn("basename/label", normalized)
                self.assertIn("verified SHA-256", normalized)

        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertNotIn("marb_execution_authorization.v1", notes)
        self.assertIn('$gitExecutable = "C:/Program Files/Git/cmd/git.exe"', notes)
        self.assertIn("--git-executable $gitExecutable", notes)
        self.assertIn("--git-executable-sha256 $gitExecutableSha", notes)

        parser = cohort_executor._parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, cohort_executor.argparse._SubParsersAction)
        )
        template_options = {
            option
            for action in subparsers.choices["authorization-template"]._actions
            for option in action.option_strings
        }
        self.assertIn("--git-executable", template_options)
        self.assertIn("--git-executable-sha256", template_options)

    def test_operator_docs_expose_complete_inputs_and_source_capture_boundary(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertIn("`container_root` as `/marb-input`", notes)
        self.assertIn("kit compatibility root as `/workspace/kit`", notes)
        self.assertIn("every safe regular file retained in the authored workspace", notes)
        self.assertIn("`MARB_SOURCE_MANIFEST.json`", notes)
        self.assertIn("`MARB_EXECUTION_STATUS.json`", notes)
        normalized = " ".join(notes.split())
        self.assertIn("content-bound", normalized)
        for change in ("addition", "removal", "replacement", "mutation"):
            with self.subTest(change=change):
                self.assertIn(change, normalized)

    def test_docs_distinguish_trusted_host_broker_from_untrusted_child_writes(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES, CHANGELOG):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("provider `write_file`", normalized.casefold())
                self.assertIn("trusted host", normalized.casefold())
                self.assertIn("retained", normalized.casefold())
                self.assertIn("tmpfs", normalized)
                self.assertRegex(
                    normalized,
                    r"(?:writable host bind|writable host export-file bind)",
                )

        container_notes = " ".join(NOTES.read_text(encoding="utf-8").split())
        self.assertIn("untrusted child-process filesystem writes", container_notes)
        self.assertIn("only writable host bind exposed to the child", container_notes)

    def test_operator_docs_cover_every_cli_stage_without_execution(self) -> None:
        parser = cohort_executor._parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, cohort_executor.argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparsers.choices),
            {
                "authorization-template",
                "seal-authorization",
                "validate-authorization",
                "execute",
            },
        )
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        for command in subparsers.choices:
            with self.subTest(command=command):
                self.assertIn(f"cohort_executor.py {command}", notes)
        self.assertIn("output basenames only", notes)
        self.assertIn("repository-relative `runs/<attempt>`", notes)
        self.assertIn("`retained_failure`", notes)
        self.assertIn("exits 2", notes)


class CadclawCalibrationUtilityTests(unittest.TestCase):
    """Board-policy-visible regressions for the local calibration utility."""

    def test_calibration_requires_exact_versioned_pins(self) -> None:
        _require_expected_commits(FROZEN_COMMIT, CANDIDATE_COMMIT)
        with self.assertRaisesRegex(CalibrationError, "unexpected_frozen_commit"):
            _require_expected_commits("0" * 40, CANDIDATE_COMMIT)
        with self.assertRaisesRegex(CalibrationError, "unexpected_candidate_commit"):
            _require_expected_commits(FROZEN_COMMIT, "f" * 40)
        with self.assertRaisesRegex(CalibrationError, "unexpected_candidate_commit"):
            _require_expected_commits(FROZEN_COMMIT, CANDIDATE_COMMIT[:12])

    def test_calibration_output_is_canonical_and_never_overwritten(self) -> None:
        self.assertEqual(
            _canonical_json({"z": 1, "a": {"b": True}}),
            b'{\n  "a": {\n    "b": true\n  },\n  "z": 1\n}\n',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "evidence.json"
            self.assertEqual(_new_output_path(target), target)
            target.write_text("historical evidence\n", encoding="utf-8")
            with self.assertRaisesRegex(CalibrationError, "output_must_be_new"):
                _new_output_path(target)

    def test_calibration_snapshot_aggregate_uses_path_sorted_manifest(self) -> None:
        entries = [("z.step", "2" * 64), ("a.step", "1" * 64)]
        expected = hashlib.sha256(
            (f"{'1' * 64}  a.step\n{'2' * 64}  z.step\n").encode("utf-8")
        ).hexdigest()
        self.assertEqual(_snapshot_aggregate(entries), expected)

    def test_calibration_report_normalization_retains_gate_semantics(self) -> None:
        report = {
            "duration_ms": 1.25,
            "overall": "fail",
            "meta": {
                "rules": "C:\\temp\\case.yaml",
                "gate_spec_version": "0.13.0",
                "gate_registry": {"version": "harness-gates.v1"},
            },
            "findings": [
                {
                    "id": "interference.clip",
                    "duration_ms": 0.5,
                    "evidence": {"status": "fail"},
                }
            ],
        }
        normalized = _normalize_report(report, {"C:\\temp": "<temp>"})
        self.assertNotIn("duration_ms", normalized)
        self.assertNotIn("duration_ms", normalized["findings"][0])
        self.assertEqual(normalized["meta"]["rules"], "<temp>\\case.yaml")
        self.assertEqual(normalized["meta"]["gate_spec_version"], "0.13.0")
        self.assertEqual(
            normalized["meta"]["gate_registry"]["version"], "harness-gates.v1"
        )
        self.assertEqual(normalized["findings"][0]["id"], "interference.clip")

    def test_calibration_git_reader_ignores_replace_refs_and_lazy_fetch(self) -> None:
        git_executable = shutil.which("git")
        if git_executable is None:
            self.skipTest("git executable is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*arguments: str) -> bytes:
                completed = subprocess.run(
                    [git_executable, "-C", str(root), *arguments],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode(errors="replace"),
                )
                return completed.stdout

            git("init", "-q")
            git("config", "user.name", "Calibration Test")
            git("config", "user.email", "calibration@example.invalid")
            fixture = root / "sample.txt"
            fixture.write_bytes(b"original\n")
            git("add", "sample.txt")
            git("commit", "-q", "-m", "original")
            original_commit = git("rev-parse", "HEAD").decode("ascii").strip()
            original_tree = git("rev-parse", "HEAD^{tree}").decode("ascii").strip()
            fixture.write_bytes(b"replacement\n")
            git("add", "sample.txt")
            git("commit", "-q", "-m", "replacement")
            replacement_commit = git("rev-parse", "HEAD").decode("ascii").strip()
            git("replace", original_commit, replacement_commit)

            environment = _safe_environment(root)
            self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
            reader = _LocalGit(Path(git_executable).resolve(), root, environment)
            self.assertEqual(reader.blob(original_commit, "sample.txt"), b"original\n")
            self.assertEqual(
                reader.text(
                    "rev-parse",
                    f"{original_commit}^{{tree}}",
                    reason_code="test_tree_lookup_failed",
                ),
                original_tree,
            )

    def test_calibration_helpers_fail_closed_on_socket_network_operations(self) -> None:
        for helper in (SYNTHETIC_GENERATOR, REVISION_PROBE, NIST_TEST_RUNNER):
            with self.subTest(helper_sha256=hashlib.sha256(helper.encode()).hexdigest()):
                self.assertIn("socket.create_connection = blocked", helper)
                self.assertIn("socket.getaddrinfo = blocked", helper)
                for method in (
                    "connect",
                    "connect_ex",
                    "send",
                    "sendall",
                    "sendto",
                    "sendmsg",
                ):
                    self.assertIn(f'"{method}"', helper)

    def test_calibration_archive_enforces_exact_paths_and_blob_bytes(self) -> None:
        source = inspect.getsource(_archive_commit)
        self.assertIn("git_archive_path_set_mismatch", source)
        self.assertIn("git_archive_blob_identity_mismatch", source)
        self.assertIn("_sha256_file(extracted)", source)
        self.assertIn("_sha256_bytes(git.blob(commit, path))", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
