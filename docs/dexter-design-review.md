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

The [current exhibit and shopping list](https://hand.marb.cadclaw.io/) expose the parts, grip presets and preliminary fabrication plan. The prices are dated September 23, not current supplier quotes; the listed servo was out of stock then. Availability, exact specifications and delivered prices need checking before purchase.

## Commercial usefulness and sourcing

The pinch and fist presets illustrate two intended uses: picking small parts and holding larger objects. Neither establishes a physical grasp. For commercial work, reach and repeatable release matter alongside grip; task success, payload, cycle time and service life still need measurement.

Several published design choices make the cost question concrete:

| Design choice | Potential commercial value | What needs checking |
|---|---|---|
| Separate thumb bending and opposition | Intended to turn the pad toward objects while retaining separate bending control | Useful reach, contact force and motion under load |
| Six servos of one proposed type | Fewer motor variants to purchase and stock; the dated servo subtotal is $95.94 | Availability, duty cycle and power requirements |
| Standard M3 screw shafts as joint pins | Avoids custom pin machining | Fit, friction, thread clearance and wear |
| Printed PETG structure and soft TPU pads | Accessible fabrication and contact surfaces that can be revised separately | Print consistency, grip and pad replacement time |
| Aluminum tendon drums with stock servo horns | Places metal at a wear surface without cutting a custom horn spline | Machining time, tendon wear and assembly fit |

Broader sourcing should use publicly specified parts from commercial servo/electronics vendors, hardware suppliers, filament suppliers and independent printing or machining services. Supplier alternatives require compatibility checks. Commercial availability does not by itself establish open-source licensing or interchangeable parts.

## Parts cost is only part of the cost

The public shopping list allows **195 minutes (3.25 hours) of bench work**, including **25 minutes for five tendon drums**. This is an unmeasured fabrication-and-assembly allowance. Applying an illustrative skilled-labor rate gives:

| Assumed labor rate | Labor for 3.25 hours | Parts/materials plus labor |
|---|---|---|
| $40/hour | $130.00 | $292.22 |
| $50/hour | $162.50 | $324.72 |
| $60/hour | $195.00 | $357.22 |

Calculation: **$162.22 + 3.25 × hourly rate**. These subtotals exclude equipment, overhead, shipping/tax, integration, validation and rework. Commercial fabrication quotes may replace the corresponding material and labor allowances; they are not included here. Bench time is not total production lead time.

At these rates, removing 15 minutes of fitting, tendon threading or adjustment would save $10–$15 per hand. That is a sensitivity calculation, not a measured saving. The useful comparison is cost per reliable task over the hand's service life, including assembly, downtime and replacement—not simply the lowest parts subtotal.

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

These are interface-reported durations, not a provider usage export. Their sum is a partial activity measure, not elapsed build time or a complete engineering labor account. Human review time, gaps and possible child-task overlap remain unresolved. The separate 195-minute bench allowance above is not AI development time.

## Why this is a useful test case

A hand combines different motions in a small space. Joint placement affects reach, wear and access for assembly or repair. If production expands, those details repeat across new units and replacement parts.

That makes the hand a useful test for an eventual engineering workflow: place components, adjust a constrained design, evaluate geometry and loads, then compare predictions with a physical prototype. Finite-element analysis (FEA)—a numerical method for estimating how parts respond to loads—and repeated design optimization remain future work. A defensible economic comparison would also need task success rates, assembly labor, service life and replacement costs.

The present result is a reviewable design concept. The next evidence should come from fabrication, motion and load tests, with revisions recorded against the same design version.
