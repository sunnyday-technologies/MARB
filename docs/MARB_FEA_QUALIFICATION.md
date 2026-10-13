# Experimental FEA source qualification

Date: 2026-10-13
Method: `marb_fea_analysis.v0.1.0`
Scope: complete analytical beam subassemblies with evaluator-authored bindings
Status: local control qualification passed; full M3 task unqualified

## Runtime

The existing local runtime provided Python 3.11.15, CadQuery 2.7.0,
cadquery-ocp 7.8.1.1.post1, PyNiteFEA 2.4.1, NumPy 2.4.4 and SciPy 1.17.1.
No dependencies were installed or upgraded. The optional command checks exact
package versions before analysis and records its implementation hash.

## Actual STEP and solver controls

The control uses authored-for-testing rectangular stock with a 1 m span,
40 mm local-y depth and 20 mm local-z width. The declared elastic modulus is
69 GPa. It has a fixed end and a transverse tip force. Candidate STEP is
translated independently of the source. Its frame stations come from verified
source-to-candidate geometry, not a reference frame.

| Prescribed tip force | Maximum displacement | Normal-stress envelope | Result |
| --- | --- | --- | --- |
| 49 N | 2.219203 mm | 9.187500 MPa | simulated_pass |
| 490 N | 22.192029 mm | 91.875000 MPa | simulated_fail |

The pass case matches the independent cantilever equations
`delta = P L^3 / (3 E Iz)` and `stress = P L / Sz`. The declared displacement
limit is 3 mm and material yield input is 145 MPa with a safety factor of 1.65.
Both cases pass independently reconstructed global equilibrium checks. These
limits and material values belong only to this analytical control.

Additional controls cover weak-axis rotation, nodal moments, uniform self-weight,
arbitrary gravity components, unstable and disconnected models, complete finite
member demands, authored prismatic section checks, extra/missing/altered solids,
duplicate mappings, unclosed joint anchors and modified inputs. Tests also remove
or falsify solver evidence while retaining pass labels; the orchestrator rejects
those reports.

## Automated validation

- 84 FEA tests ran. 83 passed and one hardlink-preservation test was skipped because
  the sandbox filesystem denied hardlink creation. Direct alias and existing-output
  preservation tests passed.
- A clean local clone then passed all 84 FEA tests with no skips, including the
  hardlink-preservation check. This verified implementation commit `0f222bf`.
- 59 existing tests passed: three leaderboard-surface tests, sixteen task-registry
  tests and forty frontier-publication-policy tests.
- The frontier publication validator passed. The method, registry, retained score
  files, site and Hugging Face board were not edited.
- Secret and answer-key checks and staged review passed. The clean-checkout
  release build passed with 84 files, 359 checked links and unchanged v0.12
  scoring. Committed secret and answer-key guards passed.

## Merge-stage qualification

On 2026-10-13, a clean checkout of proposed head `599f6f8` passed a fresh
qualification. All 84 FEA tests passed with no skips. The existing fake-only CI
suite ran 382 tests: 376 passed and six platform-specific checks were skipped.
No failures or errors remained. The initial custom-runner output-capture error
was corrected in the runner; repository code was unchanged for that correction.

The actual STEP 49 N control returned `simulated_pass`. The 490 N overload
returned `simulated_fail`. Both passed independently reconstructed equilibrium
checks. Release build, frontier publication validation, and tree and commit-range
secret and answer-key guards passed.

[PR #23](https://github.com/sunnyday-technologies/MARB/pull/23) passed its GitHub
checks and merged as `6769cce`. Its merged tree matched the qualified head.
The merge commit's board-policy, answer-key and CodeQL checks also passed.
Existing scoring, registry, leaderboard, website and Hugging Face files were
unchanged. This publishes source and documentation, not an M3 structural grade
or a public-submission service.

## M3 catalog and stock qualification

On 2026-10-13, clean source commit
`970977f963c0c5c24667f16bb77de766f2d84692` passed the M3 coverage and
authored-stock qualification. The numerical analysis version is now v0.1.1.
The M3 draft digest is
`f7f3576c6d6f8cef5ef4123f1f813312c5f3fda7b18eb83b969c8e413beebaa8`.

- All 146 FEA, draft-contract, coverage and stock-control tests passed. No
  optional-runtime or filesystem checks were skipped in this group.
- The existing fake-only CI suite ran 382 tests. 376 passed and six checks were
  skipped. No failures or errors remained. The added audit and stock formula
  tests are also included in the board-policy workflow.
- All four frozen M3 kit archives passed. Each catalog contains 14 STEP files,
  15 valid solids and three verified prismatic stock profiles.
- The private reference inventory contains 100 occurrences. Its STEP imports
  as 101 valid solids. Solid multiplicity is explicit. All 20 reference beam
  solids passed exact authored-source congruence. Reference placements and
  per-part counts remain private. Full nonbeam identities and load paths were
  not qualified by this check.
- All twelve actual-stock controls passed their expected numerical outcomes.
  Six 49 N controls returned `simulated_pass`. Six 490 N controls returned
  `simulated_fail`. Both principal bending directions were exercised for each
  profile. Analytical deflection and bending stress agreed within the declared
  relative tolerance of `1e-6`. The largest observed relative difference was
  `6.66e-16`. Every control passed global equilibrium checks.
- The clean release build passed with 84 files, 359 checked links, 43 registry
  records and v0.12 scoring. Secret and answer-key guards passed. The benchmark
  definition, score files, registry, website and Hugging Face files were unchanged.

The initial working-tree regression ran 146 tests and encountered one error in
a new output-write failure test. Its fixture mocked file opening before
calculating its own input digests. Computing the arguments first fixed the test.
The later working-tree and clean-source runs passed. The initial record is
preserved separately.

The adapter now derives centroidal bending axes from the actual source. The
C-Beam centroid differs from its bounding-box axis. Asymmetric section moduli
use the farther extreme fiber. Inconsistent isotropic elastic constants are
refused. These improvements verify geometry and the declared material model.
They do not qualify supplier strength, torsion or shear-center behavior.

The stock fixtures use assumed material values, ideal fixed supports and
fixture-specific limits. Large overload responses are linear equation controls.
Physical interpretation would require a qualified nonlinear domain. These runs
do not validate M3 service limits, splice or carriage connections, fixtures,
payload or operating loads. No full-machine numerical solve or physical test
was performed. Every catalog audit reported `audit_complete_admission_blocked`.

The private clean-source qualification report digest is
`59eb6872803e993241019f50b5053768d9605f3c236d0a89474ea7ab2cd0acde`.
Raw inputs, reports, refusal controls and reference-derived counts are retained
as separate local operator evidence. The
[M3 contract guide](MARB_M3_STRUCTURAL_CONTRACT.md) records the remaining gates.

## What this qualifies

This evidence qualifies the bounded source path on analytical and adversarial
controls. It does not qualify the M3 machine, authored extrusion suppliers,
connection capacities, a blind model cohort, an ultimate load capacity, physical
performance, a new leaderboard or a public-submission service.

The operator must keep inputs static during the local analysis. Public admission
requires immutable input staging inaccessible to submitters and isolated workers
with enforced time and memory limits. Separate M3 profile, connection, support,
load and calibration work is listed in the
[implementation plan](MARB_FEA_IMPLEMENTATION_PLAN.md).
