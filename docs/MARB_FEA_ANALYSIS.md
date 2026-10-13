# Experimental MARB beam-subassembly FEA

The optional `grader/fea_analysis.py` command analyzes the actual submitted STEP
geometry of a complete, qualified beam subassembly. It is separate from MARB
v0.12 positional grading. Its result is an unranked development analysis.

The [implementation plan](MARB_FEA_IMPLEMENTATION_PLAN.md) records the route to
an independently qualified full M3 structural track.

## Inputs and trust boundary

The evaluator freezes a `marb_fea_contract.v0.1` JSON contract and records its
SHA-256 independently of the candidate. The contract contains task identity,
authored stock sources and hashes, local centerline stations, section axes,
material and section-property provenance, explicit support restraints, prescribed
loads, rigid-joint assumptions and service limits. Units are SI except local
station coordinates and output deflections, which are in millimetres.

The evaluator supplies a separate `marb_fea_bindings.v0.1` file. It binds the
exact candidate STEP hash to solid indices and the frozen contract hash. The
candidate does not choose its material, stiffness, support conditions or loads.
Manual binding makes this an assisted analysis. A later blind track requires
independently qualified automatic assignment.

All candidate solids must be mapped. Sources must be straight, prismatic stock.
The adapter verifies source hashes and congruence with the actual candidate solids.
Every structural station comes from a verified source-to-candidate transform.
Orthogonal placements are supported in this version. Arbitrary orientations,
ambiguous section axes, altered or unmapped solids, unqualified section properties
and declared joint anchors separated by more than 0.01 mm produce `not_evaluable`.
Anchors within that numerical closure tolerance use the first measured coordinate.
This may shift a shared station by at most 0.01 mm. It does not establish contact
or connection capacity.

The adapter verifies area and bending properties against the source section.
Torsion constants and material values remain declared, evaluator-qualified inputs.
A node shared by two members is an explicitly assumed rigid connection. Endpoint
proximity alone does not prove that a fastener, plate or contact can carry load.

## Running a controlled example

Use a separate environment with `requirements-fea.txt`. The implementation is
pinned to CadQuery 2.7.0, OCP 7.8.1.1.post1 and PyNiteFEA 2.4.1, with the NumPy
and SciPy versions recorded in that file. A differing runtime is refused.

From the repository root:

```powershell
python scripts/demo_fea_analysis.py --out-dir runs/fea-control-01
```

The demonstration creates simple rectangular beam stock, exports an independently
translated candidate STEP, writes an analytical control contract and evaluates
it. It is solver and adapter qualification. It is not an M3 candidate, a model
attempt or a leaderboard result. Choose a new output directory for every run.

For evaluator-supplied artifacts:

```powershell
python -m grader.fea_analysis `
  --step runs/candidate.step `
  --contract runs/frozen-contract.json `
  --contract-sha256 $ReviewedContractSha256 `
  --bindings runs/evaluator-bindings.json `
  --catalog-root runs/authored-catalog `
  --out runs/fea-analysis-01.json
```

`ReviewedContractSha256` must be the previously reviewed contract digest. A new
candidate or binding must never authorize a changed contract automatically.
Outputs use exclusive creation. Existing reports and input files are preserved.
Treat analysis reports as internal engineering evidence. They can contain
candidate placement and catalog information. They are not automatically public.

## Results and interpretation

- `simulated_pass`: every prescribed case converged, covered every member, passed
  equilibrium checks and stayed within the declared deflection and normal-stress
  limits under the stated idealization.
- `simulated_fail`: an evaluable prescribed case exceeded a declared limit.
- `not_evaluable`: input identity, geometry, mapping, model setup, runtime,
  convergence, demand coverage or finite/equilibrium checks were insufficient.

CLI exit codes are 0, 1 and 2 respectively. No result changes a legacy score.
Reports carry input and implementation hashes, task revision, runtime versions,
member demands, displacement and equilibrium evidence, assumptions and omissions.
Physical acceptance is always `not_evaluated`. Leaderboard eligibility is false.

The local command bounds nodes, member segments, load cases and input bytes.
It does not enforce native geometry-operation wall time or process memory limits.
Before accepting untrusted public submissions, isolate geometry and solver work
in a worker with enforced time and memory budgets.
That worker must parse immutable, hash-verified input snapshots inaccessible to
the submitter. The current local path assumes static, operator-controlled files.
Its pre/post hashes detect persistent changes but cannot exclude a temporary
swap and restoration during native parsing.

Normal stress is a conservative axial-plus-biaxial-bending envelope. This first
analysis excludes transverse shear deformation, shear and torsion strength, fastener strength, joint slip,
nonlinear contact, buckling, local plate stresses, fatigue and dynamics. The
reported service-load check does not establish an ultimate capacity or a safe
operating load. Engineering review and physical validation remain necessary.

## Admission of the M3 task

The current M3 reference idealization is useful historical analysis, but is not a
qualified candidate model. M3 admission requires exact profile and insert data,
qualified materials, attachment and support identities, connection behavior,
toolhead and motor mass, travel-dependent loading, and independent calibration.
These task inputs are not inferred from the old reference-frame coordinates.
The public benchmark method, result registry, website and Hugging Face leaderboard
remain unchanged by this implementation.
