# H2b OCI runtime build and attestation

This directory defines the reproducible inputs for the untrusted Python runtime
used by `harness/isolated_container.py`. It does not contain or endorse a built
image. In particular, this repository has no approved OCI RepoDigest yet, and
CI does not build, pull, push, or run Docker.

The recipe makes every build input byte-identifiable through an exact context
manifest, including the Dockerfile and the effective temporary-context
`.dockerignore`; it does not claim that different Docker/BuildKit versions will
emit a bit-for-bit identical image. The built RepoDigest identifies the output,
not the recipe/context by itself, so the RepoDigest and build-tool identity
remain required additional attestation outputs.

The executor accepts only an immutable
`<lowercase-repository>@sha256:<64-lowercase-hex>` RepoDigest already present in
the local Docker engine. A tag, local image ID, placeholder digest, or digest
copied from an unverified build is not authorization.

## Versioned runtime contract

- execution-runtime contract `marb-v0.13-h2b`, with the exact SHA-256 of
  `runtime-contract.v0.13.json` bound by authorization and the image
- CADCLAW 0.10.0 from exact commit
  `fad0dd552a49a0b32336f1845c2b82873ad6360a`
- CADCLAW gate-spec `0.13.0` and gate registry `harness-gates.v1`
- pin basis `marb_v0.13_calibrated_cadclaw_fad0dd55`
- the checked-in `cadclaw-calibration.fad0dd55.json`, its exact SHA-256, and
  the calibrated CADCLAW package-source manifest SHA-256
- CadQuery 2.7.0
- cadquery-ocp 7.8.1.1.post1
- CPython 3.11 at `/usr/local/bin/python3`
- exact package resolution in `requirements.lock`
- exact aggregate-storage limiter source at `/opt/marb/run_limited.py`
- all wheel bytes bound by an operator-created `wheelhouse.sha256`
- the signed-metadata-derived 39-package Debian native closure in
  `native-debs.lock.json`, plus exact package bytes bound by an
  operator-created `native-debs.sha256`
- pre-install archive hash/size/control-field verification and post-install
  exact-package, exact-clean-benchmark-environment `ldd`, and fresh
  OCP/CadQuery/VTK import verification through `verify_native_bundle.py`
- exact Dockerfile, effective context-control `.dockerignore`, runtime contract,
  calibration evidence, and complete non-self-referential build-context
  manifest digests
- base image supplied by immutable digest
- authorization schema `marb_execution_authorization.v3`, run-log schema
  `marb_executor_run_log.v2`, and image provenance schema
  `marb_h2b_image_build_provenance.v3`

This commit was selected from CADCLAW `main` for this exact contract; it is not
a floating latest-upstream claim. Its executable source calibration is scoped
by the checked-in evidence and does not qualify a wheel, OCI image, or host.
The L4 grader remains frozen separately at
`marb_l4_eco_invariant.v0.12.0` and CADCLAW commit
`60fc271f68c8a794a4741f856b2dd4c9878416a6`. The v0.13 execution runtime binds
the calibrated compatibility relation without modifying that historical grade
contract or its evidence.

`requirements.lock` records the exact Python resolution observed for this
contract. `native-debs.lock.json` records the exact Debian 13 `trixie`
`linux/amd64` native closure derived for the immutable base. The retained Linux
wheelhouse exists outside Git, but the native package payloads and repaired
image have not been downloaded, built, or qualified by this source change.
Missing bytes, incompatible libraries, or a different dependency resolution
are blockers; do not relax a pin to make the build pass.

## Prepare byte-pinned build inputs

Perform this in a disposable, clean Linux CPython 3.11 build environment. Do
not put provider credentials in the build context or Docker configuration.

1. Start from a clean CADCLAW checkout at the exact commit above. Read back
   `HEAD`, build one wheel without local modifications, and name the resulting
   artifact `cadclaw-0.10.0-py3-none-any.whl`.
2. Create `harness/container/wheelhouse/` and download Linux wheels for every
   entry in `requirements.lock` using `--only-binary=:all:` and no dependency
   substitutions. Replace the registry CADCLAW wheel, if any, with the wheel
   built from the audited commit. Retain exactly one compatible wheel per lock
   entry and no source distributions; ambiguous duplicate wheels are a review
   blocker.
3. Verify the tracked `runtime-contract.v0.13.json` and
   `cadclaw-calibration.fad0dd55.json` before later copying them unchanged into
   the reviewed temporary context. Their hashes must match the active executor
   constants and the contract's calibration binding; a regenerated,
   reformatted, stale, or substituted file is a blocker.
4. From the `harness/container/` directory, create a byte-sorted manifest whose
   entries are relative paths such as
   `wheelhouse/cadquery-2.7.0-...whl`:

   ```bash
   find wheelhouse -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > wheelhouse.sha256
   sha256sum requirements.lock run_limited.py runtime-contract.v0.13.json cadclaw-calibration.fad0dd55.json wheelhouse.sha256 wheelhouse/cadclaw-0.10.0-py3-none-any.whl
   ```

5. Record those six preliminary SHA-256 values in
   `harness/container/private-build-record.json`. That named record is ignored
   by Git and must remain local; it contains public artifact identities and
   approval metadata only, never credentials. The digests are reviewed build
   inputs, not values to guess or commit as placeholders.
6. Use the base image already bound by `native-debs.lock.json`. It provides
   CPython 3.11, `/bin/sh`, `sha256sum`, `dpkg`, `dpkg-deb`, and `ldd`; the
   Dockerfile rejects a different or non-digest base reference. The lock records
   four root packages (`libexpat1`, `libgl1`, `libx11-6`, and `libxrender1`),
   their complete 39-package Debian dependency closure, 118 satisfied
   dependency groups, and 48,570,480 compressed package bytes.
7. Only under a separately approved qualification packet, download the exact
   39 `packages[].filename` payloads from the HTTPS base URL for their recorded
   suite into `native-debs/`. Do not run `apt-get`, resolve again, accept a
   mirror substitute, or add another package. Verify every recorded size,
   SHA-256, package name, version, and architecture, then write the path-sorted
   LF manifest:

   ```bash
   find native-debs -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > native-debs.sha256
   python3 verify_native_bundle.py archives \
     --lock native-debs.lock.json \
     --bundle native-debs \
     --manifest native-debs.sha256 \
     --expected-base-image '<approved-base>@sha256:<digest>'
   ```

   The metadata resolution itself downloaded no package payloads. Its three
   Debian `InRelease` signatures were verified offline with the `sqv` binary and
   Debian archive keyring already in the immutable base, and the three index
   hashes were matched to the signed release metadata. Those exact identities
   are recorded in the tracked lock.

The transient `wheelhouse/`, `wheelhouse.sha256`, `native-debs/`,
`native-debs.sha256`, and `build-context.sha256` are build inputs. Review their
inventory for unexpected packages and secrets before building. The tracked
source-directory `.dockerignore` denies the entire source directory as a direct
build context, including transient inputs and the private build record. It is
not the effective allowlist used for a build. This prevents an ambient file
from silently entering an image. Assemble the exact temporary allowlisted
context below only after review.

The Dockerfile rejects nested/extra wheel and Debian inputs and alternate
CADCLAW candidates; validates the complete context, exact package archives,
CADCLAW wheel, run-limiter, locks, runtime-contract, and calibration-evidence
digests; installs the native bundle with `dpkg --unpack` and
`dpkg --configure --pending` without an online package-manager step; then
installs the verified Python wheels. It requires the exact 39 installed Debian
package identities, zero unresolved OCP/VTK `ldd` entries under the exact clean
nine-key benchmark environment, and successful fresh isolated
OCP/CadQuery/VTK imports. It also checks the exact Python
distribution and gate/registry versions, installed-wheel integrity, and
calibrated source identity. It writes the exact `/opt/marb/runtime.json`
consumed by preflight and persists the runtime contract,
CADCLAW commit/pin/source/calibration identities, base, Python/native locks and
manifests, native verifier, limiter, Dockerfile, effective `.dockerignore`, and
context-manifest identities in image labels and
`/opt/marb/build-provenance.json` (`marb_h2b_image_build_provenance.v3`).
The 420-object gate establishes loader resolution for those recorded objects;
it is not a claim that every application-specific later `dlopen()` path has
executed. The mandatory runtime smoke remains the actual behavior gate.

## Offline build

Preload the separately approved base digest. Then assemble a fresh temporary
context whose build payload contains exactly `Dockerfile`, `requirements.lock`,
`run_limited.py`, `runtime-contract.v0.13.json`,
`cadclaw-calibration.fad0dd55.json`, `wheelhouse.sha256`,
`native-debs.lock.json`, `verify_native_bundle.py`, `native-debs.sha256`, the
reviewed top-level `wheelhouse/*.whl` files, and the reviewed top-level
`native-debs/*.deb` files, plus `build-context.sha256` and the effective
context-control `.dockerignore`. Write that `.dockerignore` with exact UTF-8/LF
bytes using these exact allowlist entries and no broader negation:

```text
**
!Dockerfile
!requirements.lock
!run_limited.py
!runtime-contract.v0.13.json
!cadclaw-calibration.fad0dd55.json
!wheelhouse.sha256
!native-debs.lock.json
!verify_native_bundle.py
!native-debs.sha256
!build-context.sha256
!wheelhouse/
!wheelhouse/*.whl
!native-debs/
!native-debs/*.deb
```

From inside that reviewed temporary context, create and verify the complete
non-self-referential context manifest. It lists every other allowed context
file, including the exact Dockerfile and effective `.dockerignore`; the
separately recorded digest of `build-context.sha256` binds the manifest itself.

```bash
{
  sha256sum Dockerfile .dockerignore requirements.lock run_limited.py runtime-contract.v0.13.json cadclaw-calibration.fad0dd55.json wheelhouse.sha256 native-debs.lock.json verify_native_bundle.py native-debs.sha256
  find wheelhouse -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
  find native-debs -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
} | LC_ALL=C sort -k2 > build-context.sha256
sha256sum -c build-context.sha256
sha256sum Dockerfile .dockerignore runtime-contract.v0.13.json cadclaw-calibration.fad0dd55.json build-context.sha256
```

Do not copy `private-build-record.json`, repository configuration, credentials,
or any other source file into that context. Build from the temporary directory
with network disabled. Angle-bracket values below are deliberately non-values;
replace them only from the reviewed build record.

```powershell
$contextRoot = (Resolve-Path -LiteralPath "<approved-temp-context>").Path
$baseImage = "<approved-python-base>@sha256:<verified-base-digest>"
$dockerfilePath = Join-Path $contextRoot "Dockerfile"
$lockSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "requirements.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
$runLimiterSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "run_limited.py") -Algorithm SHA256).Hash.ToLowerInvariant()
$runtimeContractSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "runtime-contract.v0.13.json") -Algorithm SHA256).Hash.ToLowerInvariant()
$calibrationEvidenceSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "cadclaw-calibration.fad0dd55.json") -Algorithm SHA256).Hash.ToLowerInvariant()
$wheelhouseManifestSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "wheelhouse.sha256") -Algorithm SHA256).Hash.ToLowerInvariant()
$cadclawWheelSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "wheelhouse/cadclaw-0.10.0-py3-none-any.whl") -Algorithm SHA256).Hash.ToLowerInvariant()
$nativeDebLockSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "native-debs.lock.json") -Algorithm SHA256).Hash.ToLowerInvariant()
$nativeDebManifestSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "native-debs.sha256") -Algorithm SHA256).Hash.ToLowerInvariant()
$nativeBundleVerifierSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "verify_native_bundle.py") -Algorithm SHA256).Hash.ToLowerInvariant()
$dockerfileSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "Dockerfile") -Algorithm SHA256).Hash.ToLowerInvariant()
$contextDockerignoreSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot ".dockerignore") -Algorithm SHA256).Hash.ToLowerInvariant()
$buildContextManifestSha = (Get-FileHash -LiteralPath (Join-Path $contextRoot "build-context.sha256") -Algorithm SHA256).Hash.ToLowerInvariant()
$dockerExe = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
$dockerExeSha = (Get-FileHash -LiteralPath $dockerExe -Algorithm SHA256).Hash.ToLowerInvariant()

# Compare $runtimeContractSha and $calibrationEvidenceSha with the exact
# reviewed values in cohort_executor.py and runtime-contract.v0.13.json before
# invoking Docker. Any mismatch stops qualification.

& $dockerExe build --pull=false --network=none `
  --build-arg "PYTHON_BASE_IMAGE=$baseImage" `
  --build-arg "REQUIREMENTS_LOCK_SHA256=$lockSha" `
  --build-arg "RUN_LIMITER_SHA256=$runLimiterSha" `
  --build-arg "WHEELHOUSE_MANIFEST_SHA256=$wheelhouseManifestSha" `
  --build-arg "CADCLAW_WHEEL_SHA256=$cadclawWheelSha" `
  --build-arg "NATIVE_DEB_LOCK_SHA256=$nativeDebLockSha" `
  --build-arg "NATIVE_DEB_MANIFEST_SHA256=$nativeDebManifestSha" `
  --build-arg "NATIVE_BUNDLE_VERIFIER_SHA256=$nativeBundleVerifierSha" `
  --build-arg "DOCKERFILE_SHA256=$dockerfileSha" `
  --build-arg "CONTEXT_DOCKERIGNORE_SHA256=$contextDockerignoreSha" `
  --build-arg "BUILD_CONTEXT_MANIFEST_SHA256=$buildContextManifestSha" `
  --tag marb-h2b-runtime:operator-candidate `
  --file $dockerfilePath $contextRoot
```

Read back and record the SHA-256 of the exact absolute `docker.exe` used for the
build and smoke; H2b binds both its normalized absolute path and file digest.
Building a local tag produces an image ID, not the RepoDigest required by H2b.
Publishing to an approved registry and pulling it back by digest are separate,
approval-gated operator actions. After that readback, use the exact RepoDigest
in the execution authorization and retain the image ID, base digest, lock
digest, run-limiter digest, runtime-contract digest, calibration-evidence and
CADCLAW source-manifest digests, wheelhouse-manifest digest, CADCLAW-wheel
digest, native-lock, native-manifest and native-verifier digests, native package
count and byte total, Dockerfile digest, effective context-control
`.dockerignore` digest, build-context-manifest digest, exact lowercase Docker
executable digest, build tool/version, and build timestamp in
`harness/container/private-build-record.json`.

## Mandatory no-provider runtime smoke

This gate is manual and pending until an approved RepoDigest exists. It must use
the same absolute Docker executable and host configuration that will be named
in execution authorization. It must not construct a provider session, load a
credential, or permit network access.

Use `IsolatedDockerPython` with a prior workspace containing only a non-secret
probe and a separate non-secret staged input root containing its required
`kit/` subtree. Read back and retain evidence that:

- local image inspection includes the exact authorized RepoDigest and the image
  declares no volumes;
- the created container references the inspected image ID and exact command;
- network mode is `none`, only loopback is visible, and no published port or
  Docker socket exists;
- the root filesystem is read-only, all capabilities are dropped,
  no-new-privileges and seccomp are active, and UID/GID is `65532:65532`;
- the prior host workspace is read-only at `/marb-host-workspace`, the full
  staged input root is read-only at `/marb-input`, and its `kit/` subtree is
  additionally read-only at `/workspace/kit` for brief-compatible geometry
  paths; the full-root readback includes root brief/docs, reference images,
  license, and every other staged member; untrusted child-process filesystem
  writes and the export parent are separate size-capped tmpfs mounts, and the
  only writable host bind exposed to the child is one precreated exact export
  file at `/marb-export/workspace.tar`, bounded by the file-size policy and
  host-side validation; environment keys are exactly
  the allowlisted runtime keys, and no host credential variable is present;
- the active `marb-v0.13-h2b` identity and hash, CADCLAW commit/gate/registry,
  source-manifest and calibration hashes, CADCLAW/CadQuery/OCP versions, and pin
  basis all match the versioned execution contract;
- `/opt/marb/runtime.json`, `/opt/marb/runtime-contract.json`,
  `/opt/marb/cadclaw-calibration.json`, `/opt/marb/build-provenance.json`, the
  retained `build-context.sha256`, and the image labels match each other and the
  reviewed runtime-contract, calibration, base, lock, wheelhouse-manifest,
  CADCLAW wheel, native lock, native archive manifest, native verifier,
  39-package/48,570,480-byte native inventory, run-limiter, Dockerfile,
  effective `.dockerignore`, and context-manifest identities in the private
  build record;
- stdout/stderr, process, memory, CPU, tmpfs, entry/path/depth, per-file, and
  aggregate-byte limits fail closed; the bounded export is written only to the
  exact precreated file, and its archive members are not extracted or allowed
  to replace prior state until container removal and absence readback succeed;
  and
- the named container is stopped/removed and absence is verified after success,
  failure, output overflow, workspace overflow, and timeout.

On Windows Docker Desktop, also prove that container UID/GID `65532:65532` can
write only the precreated export file while its parent and every other host path
remain unavailable or read-only. The corresponding file-bind and ownership
behavior is not assumed for Linux or rootless hosts. If any readback differs,
the image/host pair is unqualified and no provider call may follow.

Passing the smoke qualifies only the container/host pair for a separately
authorized attempt. It does not grade a run, publish a result, or authorize a
model call by itself.
