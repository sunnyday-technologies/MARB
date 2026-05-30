CadQuery 2.7 mechanics + design requirements. (Tool mechanics, a functional spec
clarification, and engineering OBJECTIVES the benchmark measures — NOT a solution.
No placements, counts, orientations, or joint designs are given; the brief + kit
remain the only design inputs. You decide HOW to meet these objectives.)

CADQUERY 2.7 MECHANICS — use these exact idioms; do not re-derive them.
- part = cq.importers.importStep("kit/<file>")   # Workplane;  shape = part.val()
- bb = shape.BoundingBox()  (bb.xlen/ylen/zlen, bb.center);  com = shape.Center() (com.x/.y/.z)
- Some parts (belts) import as wires/compounds, not solids (shape.ShapeType()); still addable.
- Place/orient: asm = cq.Assembly();
  asm.add(part, name="unique", loc=cq.Location(cq.Vector(x,y,z), cq.Vector(ax,ay,az), deg))
  (rotate 'deg' about axis (ax,ay,az), then translate). Nothing is pre-rotated.
- EXPORT (the step that most often kills the run): an Assembly is NOT a Shape, so
  cq.exporters.export(asm, ...) FAILS and asm.val() does not exist. Use
  asm.save("export.step")  or  cq.exporters.export(asm.toCompound(), "export.step").

WORK REGION IS FUNCTIONAL, NOT A SIZE LIMIT
- The 2000 x 1000 x 1000 mm figure is the PRINT/BUILD VOLUME the machine must serve, not
  the machine's outer size and not a box to fit inside. Do NOT constrain, pad, shrink, or
  rescale the structure toward any outer dimension — size every part from its real kit
  dimensions and let the machine be whatever size good function and rigidity require.

DESIGN OBJECTIVES — this benchmark tests real mechanical-design capability, not just part
placement. Design a machine that addresses each mode below, and briefly DOCUMENT your
reasoning (a comment block or printed summary). Qualitative reasoning is expected; no FEA
is required. You choose the means — do not just set parts next to each other.
- Structural rigidity: form load-bearing joints that constrain all six degrees of freedom
  at each junction, so the frame resists the moving toolhead's off-axis forces. Adjacency
  is not a joint.
- Manufacturability / machinability: parts must be makeable as placed/featured.
- Assemblability: every fastener and adjustment must be reachable with a tool — do not bury
  a screw you could never drive.
- Thermal stability, vibration, fatigue & creep: arrange and constrain the structure to
  limit deflection, resonance, and long-term creep under sustained printing loads.
- Kinematics: each axis needs a clear, collision-free motion path across its full travel;
  moving parts must not strike static ones.

STRATEGY (about 8 turns — do not spiral)
- Probe in <=1-2 turns, then write ONE build.py that places the full BOM and EXPORTS.
- Get a valid export.step on disk EARLY (even partial), then refine, so an artifact always
  exists. Wrap each placement so one bad part cannot abort the export.
- Follow the per-part orientations the brief specifies; the brief is the source.
