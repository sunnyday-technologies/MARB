# M3 static gantry diagnostic

Status: experimental and unranked. The owner has accepted a prescribed 49 N
diagnostic case. Physical input qualification remains pending.

The [diagnostic packet](../tasks/m3_crete/gantry_diagnostic.v0.1.json) freezes a
restricted static analysis for reproducibility. The solver uses planar
Euler-Bernoulli beam equations and explicit connection and support hypotheses.
It derives the selected stock placement from the submitted STEP. It does not
use the old authored reference frame as the candidate model.

This work extends the [M3 structural contract and coverage audit](MARB_M3_STRUCTURAL_CONTRACT.md).
The preceding source qualification was merged through
[PR #25](https://github.com/sunnyday-technologies/MARB/pull/25), at commit
`abd7b9774c347c16397715809e1c501dba11367b`. That source qualification did not
admit a physical M3 analysis or change the leaderboard.

## Candidate and connection boundary

The evaluator supplies private bindings for the gantry stock and splice insert.
Bindings identify the complete candidate STEP hash and the frozen packet and
catalog identities. The adapter verifies selected solids against the exact
authored catalog. Stock centroids, section axes, member endpoints, and splice
relationships follow the verified candidate geometry.

The insert is a verified connection witness. It contributes no composite
bending stiffness or connection capacity. Its presence does not prove bonding,
frictional shear transfer, contact stiffness, or moment continuity. Splice
transfer is an explicit parameter hypothesis.

Every splice case shares vertical displacement between the stock ends. This
assumes complete shear transfer. The sensitivity varies bending rotation and
moment continuity. It does not model translational splice slip, opening or
shear-transfer failure. Those connection behaviors remain unqualified.

The diagnostic distinguishes a rigid splice limit, a free splice, and finite
positive rotational stiffness. It can also evaluate declared vertical support
spring hypotheses. These values are unmeasured. They are not physical bounds.
A rigid limit describes one mathematical model. A free or disconnected splice
can produce a mechanism. Preserve that result rather than adding invented
supports or a weak spring to obtain a solve.

The end fixtures represent ideal vertical carriage supports with free bending
rotation. They do not qualify actual wheels, plates, fasteners, carriage
compliance, or the surrounding frame. Numerical connection stiffness and
support stiffness must remain separate from measured physical properties.

Rotational hypotheses use `k_theta = normalized * E * I / span` in N m/rad.
Vertical support hypotheses use `k_vertical = normalized * E * I / span^3` in
N/m. Reports record each resulting SI stiffness and the splice rotation jump.
The normalized values are sensitivity choices. They are not measured stiffness
or physical uncertainty bounds.

Unselected candidate solids remain visible as scoped omissions. Their presence
and count do not establish their identity, attachment, weight, or structural
adequacy. This is a selected gantry analysis. Whole-machine structural coverage
remains false. Bindings and diagnostic reports can contain candidate placement
information. Keep them in local or gated engineering storage.

## Force, material, and section scope

The 49 N force is prescribed. It is not a measured printhead, hose, or carriage
load. The diagnostic can include only the selected C-Beam stock self-weight,
using exact stock geometry and a nominal density. Insert self-weight is omitted.
Non-beam component weights and their attachment loads remain missing. The packet
sets `self_weight_scope` to `two_main_stock_members_only` explicitly.

The applied force follows the verified centroid line in an uncoupled planar
model. Shear-center eccentricity, torsion, warping, transverse shear
deformation, and flexural-torsional coupling are not modeled. Loading an open
C-Beam through its centroid does not prove that its physical response is free
of torsion. The planar calculation does not need an invented torsion constant.
It must not be presented as a physical response prediction that includes those
omitted effects.

Exact authored geometry supports area, centroid, principal bending inertia,
and conservative extreme-fiber section modulus. It does not certify material
properties. The packet records nominal elastic and density assumptions.
These assumptions remain distinct from supplier-qualified or measured inputs.

[OpenBuilds material information](https://support.openbuilds.com/support/solutions/articles/65000167811-what-is-v-slot-)
describes the V-Slot and C-Beam family as 6063 T-5. Family information does not
identify the supplier or material of each frozen task part. The current
[Hydro 6063 sheet](https://www.hydro.com/globalassets/01-products--services/extruded-profiles/americas/ena-resources/alloy-data-sheets/hydro_2026_data_sheet_6063-rev-062026.pdf)
also distinguishes minimum properties by temper and specimen thickness. A
historical typical yield value of 145 MPa is not a guaranteed T5 minimum.

The diagnostic reports stress demands. It does not grade yield, an approved
service limit, safe load, or ultimate capacity. Any illustrative comparison
with a nominal yield value must retain that qualification. Increasing the
prescribed force checks numerical scaling. It does not establish an accepted
operating envelope.

The frozen MARB belt task remains distinct from the later M3-CRETE leadscrew
redesign. Current BOM notes, motor specifications, support stacks, and physical
build observations cannot be transferred automatically to the frozen task.
The current BOM describes friction-fit inserts with printed retainers and
explicitly excludes adhesive from that method. That observation supplies no
validated splice stiffness for the frozen MARB gantry.

## Running the local diagnostic

Use the pinned analysis environment. Choose a new output directory for every
run. Record both expected hashes independently before analysis. Candidate or
binding inputs cannot authorize a different packet or catalog.
Create the ignored `runs/` parent once before using the example. Outputs inside
the repository are restricted to that directory. Reports can contain gated
candidate placements and must remain local or private.

```powershell
python -m grader.m3_gantry_diagnostic `
  --step $CandidateStep `
  --bindings $EvaluatorBindings `
  --packet tasks/m3_crete/gantry_diagnostic.v0.1.json `
  --packet-sha256 $ReviewedPacketSha256 `
  --catalog-contract tasks/m3_crete/structural_contract.v0.1.json `
  --catalog-contract-sha256 $ReviewedCatalogSha256 `
  --kit kits/m3_fusion_blind_kit_v1.3.zip `
  --out-dir runs/m3-gantry-diagnostic-01
```

Outputs use exclusive creation. Preserve packet, binding, source and candidate
identities with the result. Preserve rejected geometry, disconnected cases,
mechanisms, and failed calibration evidence. A completed diagnostic remains
unranked. No result changes a legacy MARB score.

## Numerical qualification

Independent analytical controls establish the implementation boundary.

- Compare a simply supported beam under a centered point load and uniform
  self-weight with the closed-form displacement and reaction equations.
- Check finite support springs and rotational splice springs against
  independent displacement, rotation, and reaction expectations.
- Check the rigid limit, a free splice, and a disconnected splice. Recognize
  unstable mechanisms without artificial stabilization.
- Reject changed source geometry, invalid bindings, a wrong splice gap, and
  unsupported member or section alignment.
- Check weak-axis placement and increased-force controls. Confirm the expected
  change in bending demand and linear scaling.
- Require finite complete demands, consistent units, and global force and
  moment equilibrium. Record numerical tolerances before comparing results.

These controls verify extraction and mathematical implementation. They do not
measure the actual joint or validate the fabricated gantry. The release
qualification record must identify the executed controls, source identity,
observed errors, and retained evidence.

Large displacement or rotation can trigger review of the linear assumptions.
Those numerical triggers do not qualify a physical linear domain. A result
within the triggers still retains every engineering evidence gap.

For reproducible qualification using authored stock, run `scripts/qualify_m3_gantry.py`
with the same input flags and a new `--out-dir`. The qualifier freezes verified
input snapshots. It retains the supplied candidate and scoped stock controls.
It checks a stock subset, weak-axis placement, a stock gap, a missing insert,
a misplaced insert, altered stock and a 490 N numerical overload. Refusal causes
are checked explicitly. Weak-axis compliance and overload scaling have
independent expected ratios. None of these controls is a model attempt or a
physical load test. Store the entire output locally or privately.

The [clean-source qualification record](MARB_M3_GANTRY_QUALIFICATION.md) identifies
the executed controls, pinned source, test results and retained private evidence.

## Next physical comparison packet

Physical comparison is a separate proposed milestone. No machine was energized,
moved, or loaded for this source work. Any physical test needs its own approved
setup, responsible engineering review, fixture and load limits, and attendance.

The [measurement template](../tasks/m3_crete/gantry_measurement_template.v0.1.json)
records the planned evidence and acceptance criteria. An unfilled template is
not measurement evidence or physical acceptance.

First identify the actual stock supplier, alloy and temper, motor and carriage
components, insert fit, retainers, and splice hardware. Measure relevant masses.
Record the assembly revision and inspection state. These facts must apply to
the tested specimen and the intended model domain.

Then define a bounded static comparison before taking data. Use calibrated
force and displacement measurements. Specify load attachment, reference points,
support locations, measurement uncertainty, and acceptance tolerances. Compare
gantry displacement relative to its supports. Record section twist, support
movement, splice rotation or slip, and unloaded recovery. Do not use an
energized motion test as a substitute for this static comparison.

Fit connection and support parameters only where the measurements support
their assumed behavior. Reserve independent measurements for the comparison
of predictions. Nonlinear slip, hysteresis, appreciable torsion, or changing
contact can require a different model. A single fitted spring value does not
validate every force, travel position, temperature, or assembly condition.

This distinction follows
[NASA-STD-7009B](https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf),
sections 4.2.3 through 4.2.6. Verification addresses implementation and numerical
accuracy. Validation compares modeled behavior with the represented system or
an acceptable referent. This is methodological guidance. MARB does not claim
NASA compliance or physical certification.

Until that comparison supports the intended domain, retain the experimental
label. A reproducible candidate-derived diagnostic is useful engineering
evidence. It does not establish physical readiness.
