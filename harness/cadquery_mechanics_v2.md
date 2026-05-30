CadQuery 2.7 mechanics — use these exact idioms; do not re-derive them. (These are
tool mechanics and agentic strategy only. They contain NO placements, counts, or
orientations — the brief + kit remain the only design inputs.)

IMPORT & PROBE
- part = cq.importers.importStep("kit/<file>")   # returns a Workplane
- shape = part.val()                              # the underlying Shape
- bb = shape.BoundingBox()                         # bb.xlen/ylen/zlen, bb.xmin..bb.zmax, bb.center
- com = shape.Center()                             # a Vector -> com.x, com.y, com.z
- Some parts (e.g. belts) import as wires/edges/compounds, not solids; shape.ShapeType()
  tells you. They can still be added to the assembly — do not get stuck on this.

PLACE & ORIENT  (nothing is pre-rotated — you compute every orientation)
- asm = cq.Assembly()
- asm.add(part, name="unique", loc=cq.Location(cq.Vector(x,y,z), cq.Vector(ax,ay,az), deg))
  # rotates 'deg' degrees about axis (ax,ay,az), then translates to (x,y,z)
- Add the Workplane (part), not part.val().

EXPORT — this is the step that most often kills the run. Get it right the FIRST time:
- An Assembly is NOT a Shape. `cq.exporters.export(asm, "export.step")` FAILS,
  and `asm.val()` does not exist.
- Correct:  asm.save("export.step")                          # preferred
       or:  cq.exporters.export(asm.toCompound(), "export.step")
- Whole-assembly bbox: asm.toCompound().BoundingBox().

STRATEGY  (you have ~8 turns — do not spiral on the API)
- Spend at most 1–2 turns probing. Then write ONE build.py that imports every part,
  places the instances, and EXPORTS — and run it.
- Get a valid export.step on disk EARLY (even a partial assembly), then refine in later
  turns, so a loadable artifact always exists when turns run out.
- Wrap each placement so one failing part cannot abort the whole export.
- Place every instance the BOM calls for; do not collapse the machine to a token frame.
