# MARB FEA analysis plan

Date: 2026-10-13
Owner: Sunnyday Technologies
Status: experimental implementation verified on controls; full M3 task unqualified

## Purpose

FEA is a logical next analysis layer after assembly geometry. Placement metrics
can expose incorrect relationships. A structural model can then estimate how a
qualified assembly responds to declared loads. Neither result proves physical
readiness.

The existing MARB v0.12 score and leaderboard remain unchanged. This work adds
an experimental, separately versioned beam-subassembly analysis. Entrants may
use any CAD tool that exports the required STEP geometry. The new analysis does
not require the entrant to use CADCLAW.

## Why the existing reference FEA is insufficient

CADCLAW has solved M3 reference-frame load cases. Its model is an authored
centerline idealization. It does not extract each submitted STEP assembly.
The current full-frame idealization uses a common section for members that the
assembly kit represents with different extrusion profiles and splice inserts.
It therefore cannot become a candidate grader by copying reference coordinates.

## Implementation sequence and acceptance

1. Freeze a structural contract independent of the candidate. Record task and
   revision, authored source hashes, local centerline stations, section axes,
   SI material and section properties, support restraints, prescribed loads,
   rigid-joint assumptions, and service deflection and stress limits.
2. Bind the actual candidate STEP to that contract. A separate evaluator-authored
   binding records the exact candidate hash and solid indices. Verify each solid
   is congruent with its authored source under a supported rigid placement.
   Derive every analysis node from that verified placement. Reject changed parts,
   ambiguous section orientation, extra or missing solids, disconnected load
   paths, and declared joint anchors separated by more than 0.01 mm. Anchors
   within that numerical tolerance use the first measured coordinate. This is
   a declared idealization, not evidence of physical connection capacity.
   Never substitute reference coordinates.
3. Run the independent, pinned PyNite beam solver. Check finite demand coverage,
   stability and global force/moment equilibrium. Report prescribed-load results,
   member normal-stress envelopes, displacements, solver versions and immutable
   input hashes. Return simulated_pass, simulated_fail, or not_evaluable.
4. Qualify the implementation against analytical beam solutions and adversarial
   STEP controls. Include a known-correct cantilever, overload, added or missing
   parts, changed geometry, weak-axis rotation, separated joints, bad bindings,
   unstable frames, non-finite inputs and a self-weight equilibrium case.
5. Qualify an M3-specific task before any scored use. Inventory each load-bearing
   extrusion and insert. Verify section area, both bending inertias, torsion
   constants and section moduli from exact profile data. Record material provenance
   and connection assumptions. Identify supported anchors and motor/toolhead
   masses. Establish static loads across the intended travel envelope. Validate
   against an independently reviewed reference and physical measurements where
   available. Keep solved references in gated/private storage.
6. Admit a new structural track only after task, evaluator, calibration and runtime
   identities are frozen and repeat-run publication rules are met. Preserve old
   results. Publish structural results separately with their assumptions. Any
   composite readiness score requires its own version and explicit owner decision.
   Public admission also requires immutable, hash-verified input staging that the
   submitter cannot modify, plus isolated geometry and solver workers with enforced
   time and memory budgets. Local pre/post hashes alone are insufficient for this.

## First implementation boundary

The first path accepts a complete, small beam subassembly consisting only of
verified authored straight stock. It supports orthogonal placements and explicit
centerline stations. The evaluator supplies candidate bindings. This is assisted
development analysis, not blind reconstruction or a ranked model result.

Rigid joints and ideal fixture supports are declared assumptions. The solver uses
static linear elasticity. Normal stress is a conservative envelope of axial and
bending demands. The analysis does not check transverse shear deformation,
shear or torsion strength, buckling,
nonlinear contact, fastener strength, connection slip, fatigue, dynamics, local
plate stresses, or physical performance. A prescribed-load pass is not an ultimate
load-capacity measurement or certification.

All candidate solids must be mapped. Arbitrary full-machine STEP files and
unqualified profile/connection data are refused. The implementation must not
merge nearby geometry into an invented physical connection.

## Deliverables

- `grader/fea_analysis.py`: contract validation, provenance, CLI and separate report.
- `grader/fea_step.py`: authored-source verification and candidate-derived frame.
- `grader/fea_backend.py`: optional pinned solver and finite/equilibrium checks.
- `requirements-fea.txt`: separate dependency pin set.
- Focused tests and an executable analytical control demonstration.
- This plan, an operator guide and local qualification evidence.

## Release and recovery

The implementation and operator documentation were pushed and merged through
[PR #23](https://github.com/sunnyday-technologies/MARB/pull/23) on 2026-10-13.
Merge commit `6769cce` preserves the qualified implementation and source identity.
The merge-stage qualification and its limits are recorded in
[the qualification record](MARB_FEA_QUALIFICATION.md).

Raw control artifacts remain local engineering evidence. Merged source does not
admit a structural task or authorize changes to benchmark scoring, registry rows,
public website, Hugging Face data, provider settings or running services.
No model inference was needed for this source qualification.
Rollback is removal or disabling of the optional FEA command. Existing graders
do not import it and retain their prior behavior.

## Current completion record

Steps 1 through 4 are implemented for the bounded, evaluator-bound beam control
path. The [qualification record](MARB_FEA_QUALIFICATION.md) reports the analytical
results, adversarial checks, runtime identity and limitations. The optional command
can run locally now.

Steps 5 and 6 remain task and operational qualification work. No full M3 candidate
has been structurally graded. No ranked cohort, new readiness score, public FEA
leaderboard row, physical acceptance or public-submission service is established.

## Ordered remaining work

1. Freeze an M3 structural contract with verified profiles and inserts, material
   and section properties, support and connection behavior, component masses,
   travel-dependent loads and service limits.
2. Qualify full-machine structural coverage. The current adapter accepts only
   complete straight-stock beam subassemblies. M3 needs a declared extraction
   policy or expanded adapter coverage for its non-beam parts and asymmetric
   profiles. Preserve full-assembly geometry verification and candidate-derived
   coordinates. Do not substitute a reference frame for the candidate.
3. Calibrate the M3 task against independently reviewed correct, misplaced,
   disconnected and overload controls. Validate connections and compare with
   physical measurements before claiming physical readiness or safe capacity.
4. Qualify automatic part assignment for a blind track. Add immutable input
   staging and isolated workers with enforced time and memory limits before
   accepting untrusted submissions.
5. Freeze task, evaluator, calibration and runtime identities. Admit and run a
   repeat-run structural cohort under the publication policy. Keep its results
   separate from historical geometric scores. Any composite score needs a new
   version and an explicit owner decision.
6. Update the website and Hugging Face communication for the admitted state and
   available measured results. The current leaderboard does not include FEA.
