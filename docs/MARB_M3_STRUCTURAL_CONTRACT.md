# M3 structural contract and coverage audit

Status: draft engineering contract. M3 structural analysis remains experimental.

The [draft contract](../tasks/m3_crete/structural_contract.v0.1.json) records the
authored catalog, its structural roles, and the evidence needed for an M3
analysis. It is a readiness and coverage contract. It is not an accepted input to
the [beam solver](MARB_FEA_ANALYSIS.md). The audit does not change MARB scoring,
leaderboard data, or publication eligibility.

Experimental is the correct label for the M3 task. The bounded beam software has
qualification evidence. The machine still needs qualified materials, profiles,
connections, supports, component masses, operating loads, and service limits.
Waiting does not resolve these inputs. Specific capabilities can be verified
and validated now while the broader task remains experimental.

## What the audit establishes

`grader/m3_structural_audit.py` checks the authored STEP catalog against an
independently frozen contract hash. It checks catalog identities, file hashes,
solid counts, explicit part roles, and actual stock-profile geometry. A catalog
match establishes input identity. It does not establish a correct candidate
assembly, a complete load path, or adequate connection capacity.

Each required part needs an explicit disposition. Beam stock can contribute to
a qualified member model. A plate, spacer, wheel, bearing, or fastener can carry
load without being a beam. Such parts need a declared connection, mass, or other
structural treatment. An exclusion must state its basis. Excluding a part from
the beam model does not demonstrate that the machine can carry its loads.

An optional private inventory records expected occurrences against an
independently identified reference. This can audit inventory coverage. It does
not expose solved reference placement, infer connections, or replace the
candidate with a reference frame. Keep reference-derived inventory and all
solved geometry in gated storage. Public summaries must omit solved layouts,
station coordinates, and reference occurrence counts by part.

Missing, conflicting, or unsupported engineering evidence remains a blocker.
An exact catalog match cannot clear the structural requirements. A nonempty
provenance field cannot qualify a material or connection by itself.

The [static gantry diagnostic](MARB_M3_GANTRY_DIAGNOSTIC.md) implements a separate
nominal analysis for the owner-selected 49 N case. Candidate stock geometry
drives a restricted planar model. Splice and support behavior remain explicit
unmeasured hypotheses. It leaves every physical evidence gap open.

## Running the local audit

Freeze the draft digest independently before evaluating inputs. From the
repository root, with the pinned FEA environment:

```powershell
python -m grader.m3_structural_audit `
  --contract tasks/m3_crete/structural_contract.v0.1.json `
  --contract-sha256 $ReviewedDraftSha256 `
  --kit kits/m3_fusion_blind_kit_v1.3.zip `
  --out-dir runs/m3-coverage-01
```

Use a new output directory. The command checks all STEP entries, file identities,
imported solid counts, and the three actual stock profiles. It never admits a
task. Exit 0 means the audit completed. The report status remains
`audit_complete_admission_blocked`. Exit 2 means the audit was rejected.

The optional private inventory uses `marb_m3_private_inventory.v0.1`. It contains
only `schema_version`, `reference_sha256`, and an `instances` map from every
catalog source identifier to an occurrence count. Supply `--inventory`,
`--inventory-sha256`, `--reference`, and `--reference-sha256` together. Each digest
must identify a previously reviewed input. The audit checks the STEP solid total
independently, including multi-solid catalog parts. Matching totals do not verify
the identities or placements of every reference solid. Reports say this plainly.

The command assumes static operator-controlled inputs. It uses bounded files,
hash checks, private temporary catalog copies and exclusive report creation.
It is not an isolated public submission worker. Native STEP operations still
need enforced time and memory budgets before untrusted use. Keep inventory
reports local or gated. Aggregate role counts can reveal reference multiplicity.

For actual-profile numerical verification:

```powershell
python scripts/qualify_m3_stock.py `
  --contract tasks/m3_crete/structural_contract.v0.1.json `
  --contract-sha256 $ReviewedDraftSha256 `
  --kit kits/m3_fusion_blind_kit_v1.3.zip `
  --out-dir runs/m3-stock-controls-01
```

This preserves exact authored stock bytes. It independently places each profile
in an ideal cantilever fixture. Both principal bending directions receive 49 N
and 490 N tip loads. The twelve controls compare deflection and peak bending
stress with analytical beam equations. A 49 N control must pass its fixture
limit. A 490 N control must fail. Every result must pass equilibrium checks.

Material values and fixture limits are verification assumptions. They are not
approved M3 values. Torsion J is an explicit placeholder for these uncoupled
bending checks. The controls ignore shear-center eccentricity and warping.
Centroid loading does not establish torsion-free behavior in a physical open
section. The command does not validate an M3 connection, machine or safe load.

## Profile and material findings

The historical CADCLAW M3 frame uses a V-Slot 4080 section and assumes 6061-T6
material. Its source explains that supplier alloy and temper were unknown. The
authored M3 catalog includes C-Beam stock and inserts. Those parts require their
own section properties and load-transfer treatment. A symmetric V-Slot section
cannot qualify an asymmetric C-Beam section.

Official [OpenBuilds material information](https://support.openbuilds.com/support/solutions/articles/65000167811-what-is-v-slot-)
describes the V-Slot and C-Beam family as 6063 T-5. This identifies a product
family. It does not certify the material of each authored asset or fabricated
part. The historical 6061-T6 yield assumption cannot become an approved M3
material input without evidence of applicability.

Stock geometry can establish cross-sectional area, centroid, principal bending
axes, bending inertias, and extreme-fiber distances. Section moduli must use the
actual centroid and both sides of an asymmetric section. This verification does
not establish material strength, Saint-Venant torsion constants, shear-center
location, warping behavior, or the stiffness of a connection.

The historical torsion approximation uses the sum of the bending inertias. That
sum is not evidence of torsional stiffness for the actual C-Beam section.
Likewise, overlapping stock and inserts do not become a composite beam solely
because their geometry overlaps. Composite action requires load-transfer
evidence.

## Connection and load findings

The historical CADCLAW named-joint utilization table compares member moment
demands with extrusion section capacity. It does not evaluate fastener strength,
preload, pull-out, joint slip, bearing, or contact. Its pinned-joint description
also differs from the unreleased beam members used by the historical solver.
Retain those results as assumption-qualified analysis controls. They do not
qualify actual M3 joints.

The retained static cases specify a nominal printhead force and lateral pushes.
Their displacement limit and safety factor make the analysis repeatable. They
still need an engineering basis for the intended operating domain. The contract
must account for all relevant mass, support conditions, tool and motor loads,
force application offsets, and travel positions. Payload force and payload
self-weight must not be counted twice.

A fixed base and a rigid joint are mechanical assumptions. Endpoint proximity
does not prove either one. Relative tool-to-bed displacement also needs a
declared metric and service requirement. A global frame displacement alone does
not establish positioning accuracy.

## Readiness requirements

The draft contract keeps analysis, publication, and physical evidence separate.

| Evidence group | Required basis |
| --- | --- |
| `material_properties` | Applicable material sources, qualified strength and density, and consistent isotropic elastic properties |
| `torsion_and_shear_center` | Exact profile behavior and an explicit domain for torsion, load eccentricity, and omitted effects |
| `connections` | Splice, insert, plate, fastener, and carriage load transfer, stiffness, or a justified restricted idealization |
| `supports` | Identified anchors and applicable restraint or fixture behavior |
| `mass_and_payload` | Reconciled member mass, omitted-part mass, payload, and load attachments |
| `travel_loads` | Prescribed loads across the intended position and operating envelope |
| `service_limits` | Frozen displacement metrics, allowable-stress basis, and safety-factor rationale |
| `candidate_extraction` | Verified candidate identities, coordinates, section axes, and complete structural coverage |
| `independent_controls` | Independent expected outcomes, numerical tolerances, and reviewed M3 calibration controls |
| `blind_assignment` | Qualified automatic assignment without reference-layout assistance |
| `immutable_isolated_runtime` | Immutable input staging and isolated workers with enforced time and memory limits |
| `physical_comparison` | Relevant measurements or another qualified referent, with comparison criteria fixed in advance |

The first nine groups govern scoped analysis admission. Blind assignment and
isolated runtime govern public submission readiness. Physical comparison governs
claims about physical behavior. Passing one group does not automatically pass
the others.

## Qualification and advancement

1. Verify the catalog and coverage audit against actual authored stock. Include
   changed hashes, missing parts, duplicate identities, invalid classifications,
   unsupported profiles, and contradictory evidence as refusal controls.
2. Freeze the intended structural claim. Qualify exact section properties and
   applicable material values. Keep unsupported open-section behavior explicit.
3. Obtain connection, support, component-mass, and operating-load evidence.
   Establish service limits. Run sensitivity studies over defensible uncertainty
   bounds before accepting the idealization.
4. Calibrate the M3 analysis with independently reviewed correct, disconnected,
   altered, and overload controls. A geometric defect does not necessarily cause
   a structural failure. Preserve the geometric and structural results as
   separate checks.
5. Compare predictions with representative hardware or a qualified referent
   across the intended domain. Define measurement uncertainty and acceptance
   tolerances before viewing results. No physical load or motion test is
   authorized by this source qualification.
6. Admit only the qualified domain. Freeze evaluator and task identities before
   ranked use. Follow the existing repeat-run publication policy. Preserve all
   historical geometric scores.

The [qualification record](MARB_FEA_QUALIFICATION.md) describes the bounded beam
software controls. A successful catalog audit qualifies that audit path. It is
not a full M3 solve, a model attempt, a leaderboard result, or physical acceptance.

The distinction follows the model credibility approach in
[NASA-STD-7009B](https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf),
sections 4.2.3 through 4.2.6. Verification assesses implementation and solution
accuracy. Validation compares predicted behavior with the represented system or
an acceptable referent. This is methodological guidance. MARB does not claim NASA
compliance or certification.

The current [PyNite member documentation](https://pynite.readthedocs.io/en/latest/member.html)
also states that transverse shear deformation is not considered. Online
documentation may describe a newer version. MARB software qualification remains
bound to its pinned runtime and retained controls.
