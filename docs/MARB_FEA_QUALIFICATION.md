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
