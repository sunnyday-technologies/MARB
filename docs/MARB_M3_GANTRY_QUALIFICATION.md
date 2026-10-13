# M3 gantry diagnostic qualification

Status: source and numerical qualification complete. The 49 N gantry analysis
remains experimental, nominal and unranked. Physical acceptance was not evaluated.

This record applies to the scoped [gantry diagnostic](MARB_M3_GANTRY_DIAGNOSTIC.md).
It follows the M3 structural contract merged through
[PR #25](https://github.com/sunnyday-technologies/MARB/pull/25).
It does not admit a full-machine structural analysis or change MARB scoring.

## Qualified identity

Qualification ran on 2026-10-13 from a fresh local clone of source commit
`483238f20f9ad53fc47a8b3e336a7dbe4727d3ce`. The clone used `--no-hardlinks`.
Its tracked source remained unchanged throughout the run.

| Input or implementation | SHA-256 |
| --- | --- |
| Frozen diagnostic packet | `086a44d0fd53cc19cde13fe860b53a6b894ff2277b1f1ed12cdd39e087562604` |
| Frozen M3 catalog contract | `f7f3576c6d6f8cef5ef4123f1f813312c5f3fda7b18eb83b969c8e413beebaa8` |
| Seven-file qualification implementation digest | `e1f6116f5edf47f62a61eb77f7daf51cb076e2d7347898f6e7274a60b0e10ed7` |

Exact Git attributes preserve the previously qualified catalog's CRLF bytes.
The new diagnostic and measurement packets use LF. Both frozen input hashes
matched in the fresh checkout. Candidate, kit and private evaluator bindings
were hashed and copied into exclusive frozen-input storage before analysis.
Their identities were checked again before the qualification completed.

The pinned Windows analysis environment used Python 3.11.15, CadQuery 2.7.0,
cadquery-ocp 7.8.1.1.post1, NumPy 2.4.4, SciPy 1.17.1 and PyniteFEA 2.4.1.
The new planar bending solver uses NumPy. The earlier beam-subassembly path
uses PyNite. Both paths were covered by the native test group.

## Observed checks

| Check | Result |
| --- | --- |
| FEA, M3 and gantry tests | 222 passed. No skips, failures or errors. |
| Existing harness and publication tests | 376 passed. Six platform skips. No failures or errors. |
| Total | 604 tests. 598 passed and six skipped. |
| Clean release build | Passed. 84 files, 359 links, 43 registry records and scoring version v0.12. |
| Source/output integrity | 84 of 84 build hashes matched. |
| Secret and answer-key guards | Passed for tracked source and the commit range. |
| Independent source review | No significant unresolved finding. |

The six skips cover Windows filename restrictions, unavailable symbolic-link
creation, POSIX byte-path behavior and an unavailable POSIX shell. All new
native gantry and M3 tests ran. No provider request or model inference ran.
The release build did not deploy a website.

The preserved authored-part qualification completed all eight controls:

| Control | Observed result |
| --- | --- |
| Supplied full candidate | Scoped diagnostic completed. Unselected parts remained omissions. |
| Selected authored-stock subset | Diagnostic completed. |
| Weak-axis placement | Diagnostic completed. Displacement ratio was 4.803349105461394, matching the source-section inertia ratio. |
| Stock gap | Refused because the stock splice did not close. |
| Missing insert | Refused because the required selected solid was absent. |
| Misplaced insert | Refused because the insert did not span the splice. |
| Altered stock | Refused because authored catalog geometry did not match. |
| 490 N numerical overload | Diagnostic completed. Force-only displacement ratio was exactly 10.0. Linear-assumption review was triggered. |

Each of the four completed controls evaluated 14 cases. Across those controls,
48 finite cases matched independent analytical equations. Eight free-splice
cases were refused as expected unstable mechanisms. No artificial stabilizing
spring was added. Checks included displacement extrema, bending demand,
individual reactions, splice rotation, end rotations and global equilibrium.
The qualifier also checked each geometry refusal's exact cause.

These fixtures retain authored STEP geometry. Changed copies are explicit
adversarial controls. They are not replacement benchmark parts or model attempts.

## Retained private evidence

The complete run is retained at:

`D:\SunnydayTech\.tmp\marb-m3-gantry-20261013\clean-qualification-run01\`

| Evidence | SHA-256 |
| --- | --- |
| `qualification.json` | `8dfa5bf8a66dc23fb07acf67964125cadfa0c7b5e4a28077fc1262dca555c9b0` |
| `gantry-controls/qualification.json` | `415a207eb1c4fe25c0e3fffd199984eccd9ab4491edd8dd1f7d8f003ac311532` |

Preserve the full directory, including logs, snapshots, generated control STEP
files, bindings, packets and diagnostic reports. Reports can contain private
candidate placements. Do not copy them into public documentation or publishing
output. Failed runs retain captured input and implementation identities.

The earlier `qualification-working01` run remains development evidence. Its
source changed during execution. Its separate identity note excludes it from
release qualification. The fresh clone run above supersedes it for this record.

## Remaining engineering qualification

The calculation models two main stock members and one geometric insert witness.
It assumes nominal material and density, shared vertical splice displacement,
and declared rotational and support stiffness hypotheses. It omits insert and
non-beam weights, translational splice slip, connection failure, shear-center
eccentricity, torsion, warping and transverse shear deformation. The stiffness
choices are unmeasured sensitivity values. They are not physical bounds.

Every report keeps `analysis_admitted`, `leaderboard_eligible` and
`whole_machine_coverage` false. Strength and service limits remain unqualified.
Physical acceptance remains `not_evaluated`.

The next milestone is a reviewed static comparison using the
[measurement template](../tasks/m3_crete/gantry_measurement_template.v0.1.json).
It requires specimen identity, measured masses and loads, qualified material
inputs, connection and support measurements, instrument calibration,
uncertainty, prior acceptance tolerances and independent validation records.
No machine was energized, moved or loaded for this qualification.

Retain the experimental label until measured comparisons support the intended
model domain. Whole-machine structural coverage and benchmark admission remain
separate later gates.
