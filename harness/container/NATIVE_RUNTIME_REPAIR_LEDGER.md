# MARB v0.13 native runtime repair ledger

This public-safe ledger separates source repair from runtime qualification. It
contains no local paths, credentials, private build-record contents, provider
details, model-call data, answer keys, scores, or publication claims.

## R3 fail-closed result

- MARB source base: `f36313534f0b0fd10dab67b48ed225395007c982`.
- CADCLAW runtime pin: `fad0dd552a49a0b32336f1845c2b82873ad6360a`.
- The exact retained 46-wheel input set passed its pre-build hash and inventory
  checks under the approved offline build boundary.
- The build stopped at the first native import because `libGL.so.1` was absent.
- No candidate image, runtime tag, registry operation, RepoDigest, runtime
  qualification, model call, benchmark attempt, grading, board mutation, or
  publication followed that failure.

## Focused diagnosis

The diagnostic containers used only the already cached immutable Python base,
the retained wheels, read-only inputs, `--pull=never`, and `--network=none`.
Across the CadQuery/OCP/VTK boundary they inspected 420 native objects. Sixty-six
objects had at least one unresolved dependency. The complete directly missing
SONAME set was:

| SONAME | affected native objects |
| --- | ---: |
| `libGL.so.1` | 48 |
| `libX11.so.6` | 63 |
| `libXrender.so.1` | 46 |
| `libexpat.so.1` | 17 |

The base is Debian 13 (`trixie`) `linux/amd64`. Debian's version comparator was
used inside that exact base to resolve the four providing root packages against
the base's installed package set. The result is a 39-package, 48,570,480-byte
compressed closure with 118 dependency groups satisfied and no unresolved or
unsatisfied groups.

Only official Debian `InRelease` and `Packages.xz` metadata was retrieved for
the diagnosis. All three `InRelease` signatures were verified offline using the
`sqv` binary and Debian archive keyring already present in the immutable base,
and each index digest matched its signed release entry. No `.deb` payload was
downloaded. `native-debs.lock.json` records the exact metadata, package paths,
versions, architectures, sizes, and SHA-256 values.

## Source repair

- `native-debs.lock.json` freezes the diagnosed base and complete package
  closure.
- `verify_native_bundle.py archives` rejects any missing, extra, renamed,
  symlinked, wrong-sized, wrong-hash, or wrong-control-metadata archive before
  package installation.
- The Dockerfile uses only `dpkg --unpack` and
  `dpkg --configure --pending` on the verified local bundle. It contains no
  `apt-get`, repository update, or online dependency-resolution step.
- `verify_native_bundle.py runtime` requires all 39 exact dpkg identities,
  checks all 420 pinned CadQuery/OCP/VTK native objects with `ldd` under the
  exact clean nine-key benchmark environment, requires zero unresolved
  libraries, and imports OCP, CadQuery, and VTK in a fresh isolated subprocess.
- The native lock, bundle manifest, verifier, package count, and byte total are
  included in image labels and `marb_h2b_image_build_provenance.v3`.
- Static and mocked-boundary regressions cover exact inventory acceptance and
  rejection, changed-resolution rejection, dpkg identity checks, the 420-object
  exact-environment `ldd` boundary, fresh import checks, ambient-loader-variable
  rejection, and unresolved-SONAME failure.

## Qualification boundary

This repair does not claim a repaired image or passing runtime qualification.
The 39 package payloads have not been downloaded and the repaired Dockerfile has
not been built. Those actions, registry work, RepoDigest capture, and the full
no-provider/no-network qualification suite remain blocked until the exact R4
packet is reviewed and approved.

Rollback is source-only: revert the focused repair commit to restore the prior
recipe. No registry, result, board, task, grader, publishing source, deployment
workflow, provider, or model state is changed by this tranche.
