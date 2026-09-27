# Dexter: from an assembly benchmark to a manufacturing design brief

**Sunnyday Technologies · Design review · September 27, 2026**

Dexter is an AI-assisted robotic-hand design project with human direction and review. Its current public exhibit is labelled **Halden Mk-IV**, the latest revision of a name introduced by the design agent. It remains an unbuilt concept, separate from the scored or unranked reconstruction attempts in MARB HandBench.

![Actual published hand in its rest pose.](/media/dexter/dexter-rest.png)

*Figure 1. The published exhibit captured September 27. The 59 displayed components describe a digital assembly. The interface’s “functional hand” label is not evidence of physical operation.*

## How the question changed

The original HandBench work asked models to reconstruct existing hands. The source audit found a practical obstacle: publicly visible geometry did not always come with the component identities, assembly references and manufacturing information needed for a reproducible test. This was a finding about the inspected packages, not a count of all open-source hands.

An exploratory assembly request then produced an independently generated visual hand rather than the requested Amazing Hand. That was not a successful reconstruction. Sunnyday subsequently changed the objective: develop a hand around economic value, considering useful motion, accessible components, fabrication and assembly.

The build history contains repeated human interventions, including thumb corrections, material questions and bill-of-materials revisions. It does not support a one-prompt or fully autonomous engineering claim. The earlier [HandBench technical report](https://marb.cadclaw.io/robotic-hand/handbench-technical-report/) documents the reconstruction pilot; its scores and budgets do not measure Dexter’s design quality.

## What the current concept contains

| Feature | Current proposal | What it establishes |
|---|---|---|
| Digital assembly | 59 displayed components | A visual inventory, not 59 purchased items |
| Actuation | Six position-controlled motors (servos): four fingers, thumb bending and thumb opposition | A proposed mechanism; independent control of every finger joint is not implied |
| Structure and contact | PETG plastic bones, flexible TPU pads, tendons and elastic returns | Material and construction choices awaiting fabrication tests |
| Wear components | Steel pins and aluminum tendon drums | A proposal to concentrate metal where joints and tendons contact it |
| Cost | $162.22 parts/material estimate, dated September 23 | A planning subtotal, not a supplier quote or a finished-hand cost |
| Physical status | No completed physical build or test established in the reviewed records | Grasp force, reliability and service life remain unverified |

The [current exhibit](https://hand.marb.cadclaw.io/) exposes the parts and grip presets. Its shopping list should be treated as a preliminary purchasing and fabrication plan. Labor, tooling, integration and validation costs require their own accounting; availability and delivered prices need checking before purchase.

## Why the thumb changed the design

Bending a thumb is different from turning it toward the fingers. A convincing pose must also correspond to a mechanism that can produce it. Human review identified that early thumb motion did not meet that requirement.

The development record shows a five-servo alternative followed by a return to six servos and revisions to the thumb hinge. The current exhibit uses an angled thumb hinge, with bending controlled separately. That history illustrates the economic trade-off: removing a motor reduces the parts subtotal, but only helps if the remaining mechanism can perform the intended tasks.

![Actual published pinch preset and its controls.](/media/dexter/dexter-pinch.png)

*Figure 2. The current pinch preset, captured from the live exhibit set to a 140° opposition angle. A rendered pose does not establish tendon routing, contact force or a successful physical grasp.*

Source documentation reports solid-overlap checks for selected poses and simplified strength calculations. Those checks were not independently rerun for this editorial review. They do not constitute finite-element validation or a physical test, and isolated poses do not establish a collision-free path between them.

## What the development record can measure

The recovered Cursor conversations contain the economic design brief and subsequent revisions. Cursor identifies the application in which those records reside. It does not, by itself, identify the model responsible for every response. The records include a request to revisit the design with Grok, but per-response model identities have not been verified. Exclusive GPT or Grok attribution is therefore not established.

| Accounting item | Finding |
|---|---|
| Selected design-related turns | 18 turns across two conversations |
| Sum of displayed work durations | Approximately four hours ten minutes |
| Scope | From the economic design brief onward: design analysis, implementation and revisions; abandoned design alternatives included |
| Exclusions | Earlier exploratory build, benchmark activity, publishing/setup work, and turns mixing design with deployment |
| Complete elapsed build time | Not established |
| Build-only token use and compute cost | Not available from the reviewed records |

These are interface-reported durations, not a provider usage export. Their sum is a partial activity measure, not a continuous session or a complete engineering labor account. Human review time, gaps between turns and possible child-task overlap cannot be resolved from those labels. The concept’s separate 195-minute bench allowance is an estimate for physical assembly, not AI development time.

## Why this is a useful test case

A hand combines different motions in a small space. Joint placement affects reach; pins and tendons affect wear; access affects assembly and repair. Common parts may simplify stocking replacements, while unnecessary components can add purchasing, installation and maintenance costs. These are design motivations, not measured savings for Dexter.

That makes the hand a useful test for an eventual engineering workflow: place components, adjust a constrained design, evaluate geometry and loads, then compare predictions with a physical prototype. Finite-element analysis (FEA)—a numerical method for estimating how parts respond to loads—and repeated design optimization remain future work. A defensible economic comparison would also need task success rates, assembly labor, service life and replacement costs.

The present result is a reviewable design concept. The next evidence should come from fabrication, motion and load tests, with revisions recorded against the same design version.
