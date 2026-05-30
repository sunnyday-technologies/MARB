CadQuery 2.7 mechanics — exact idioms; do not re-derive them. (Tool mechanics and
agentic strategy only. No placements, counts, orientations, or design solutions — the
brief + kit remain the only design inputs.)

PROBE — get coordinates with these EXACT attributes; if one line errors, do NOT retry
variants in a loop. Fall back to xlen/ylen/zlen and move on to building.
- part = cq.importers.importStep("kit/<file>")          # a Workplane
- bb = part.val().BoundingBox()                          # bb.xlen, bb.ylen, bb.zlen
- bb.center.x, bb.center.y, bb.center.z                   # center is a Vector; lowercase .x/.y/.z
- part.val().Center().x / .y / .z                         # also lowercase .x (NOT .X)
- Some parts (e.g. VS_Belt_Pinion, belts) are wires/compounds, not solids
  (part.val().ShapeType()); still addable — do not get stuck probing them.
- HARD RULE: spend at most ONE turn probing. If an attribute fails once, switch to
  bb.xlen/ylen/zlen and start building. Never repeat the same probe — that loop has
  burned entire runs to nothing.

PLACE & ORIENT  (nothing is pre-rotated — you compute every orientation)
- asm = cq.Assembly()
- asm.add(part, name="unique", loc=cq.Location(cq.Vector(x,y,z), cq.Vector(ax,ay,az), deg))
  # rotate 'deg' about axis (ax,ay,az), then translate. Add the Workplane, not part.val().

EXPORT — the step that most often kills the run. An Assembly is NOT a Shape, so
cq.exporters.export(asm, ...) FAILS and asm.val() does not exist.
- Correct:  asm.save("export.step")   or   cq.exporters.export(asm.toCompound(), "export.step")
- Get a valid export.step on disk EARLY (even partial), then refine, so an artifact always
  exists. Wrap each placement so one bad part cannot abort the export.

SIZE — build to real dimensions, not to a number.
- Use every part's true kit dimensions. NEVER resize, rescale, or shrink a part or the
  frame to fit any target number. The machine is whatever size correct, rigid placement
  makes it; do not trim the structure to hit an envelope.

STRATEGY (~8 turns)
- Probe <=1 turn, then write ONE build.py that places the full BOM and EXPORTS; run it;
  refine. Place every instance the BOM calls for; do not collapse to a token frame.
- Follow the per-part orientations the brief specifies; the brief is the source.
