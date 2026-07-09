# PH-1 "Bungalow" — MARB-A Architecture Lane (Pascal MCP track)

> **MARB-A shared core + Pascal stub, v0.1.** The mechanical board (M3-CRETE)
> grades STEP assemblies; this architecture lane grades a Pascal scene built
> from a text program — Pascal's native input. Frozen + versioned per release —
> **do not tune it to make a run pass.** It states the target (the "what"),
> never a build order (the "how").

## Task

Build the PH-1 single-story house in Pascal from the design program below, then
export the scene JSON + a run log. You are being timed and graded.

- Drive Pascal ONLY through its MCP tools (`@pascal-app/mcp`, headless). No
  browser editing, no hand-written scene files loaded from disk.
- Use the built-in MCP asset catalog for all furnishings (`search_assets`
  lists it). **Place catalog items only — do not invent custom assets.**
- **You decide tool usage and build approach.** Semantic tools
  (`create_room`, `add_door`, `add_window`, `place_item`) and `apply_patch`
  are both allowed.
- Your kit includes `reference_floorplan.png`, a dimensioned goal plan —
  compare your build against it as you go, exactly like the goal renders on
  the mechanical board.

## Fairness wall (critical — do not break this)

Build only from this brief + the provided kit. Do **not** open, search for, or
consult any reference scene, answer key, grader source, or project memory —
even if your environment makes one reachable. Using them invalidates the run.

## Design program (the target — the "what," not the "how")

Site: flat. One building, one occupied level, wall height **2.6 m**, wall
thickness **0.2 m**. Outer footprint **12.0 m (X, east–west) × 8.0 m (Z,
north–south)**. Place the southwest outer corner at the origin, +X = east,
+Z = north. All positions below are in this frame; "centered at x" means the
element's center sits at that coordinate.

### Room schedule (interior clear dimensions)

| Room | Clear size (X × Z) | Location |
|---|---|---|
| Living room | 5.8 × 4.4 m | southwest quadrant |
| Kitchen–dining | 3.8 × 3.0 m | northwest corner |
| Bathroom | 1.8 × 3.0 m | north, between kitchen and hallway |
| Hallway | 1.8 × 7.6 m | full-depth north–south spine, center-east |
| Bedroom 2 | 3.6 × 4.4 m | southeast quadrant |
| Bedroom 1 | 3.6 × 3.0 m | northeast corner |

Layout logic: two full-depth partitions run north–south with centerlines at
**x = 6.1** and **x = 8.1**, making three blocks. West block splits at
**z = 4.7** into living (south) and kitchen+bathroom (north); kitchen and
bathroom split at **x = 4.1**. The center block is the hallway. East block
splits at **z = 4.7** into bedroom 2 (south) and bedroom 1 (north).

### Openings

Doors are 0.9 × 2.1 m hinged; windows are 1.2 m wide × 1.2 m high, sill 0.9 m.
Positions are the opening's center on its wall:

| Opening | Wall | Center |
|---|---|---|
| Front door | south exterior | x = 7.1 (into hallway) |
| Living ↔ hallway door | x = 6.1 partition | z = 2.4 |
| Living ↔ kitchen door | z = 4.7 partition (west) | x = 2.1 |
| Bathroom ↔ hallway door | x = 6.1 partition | z = 6.3 |
| Bedroom 2 ↔ hallway door | x = 8.1 partition | z = 2.4 |
| Bedroom 1 ↔ hallway door | x = 8.1 partition | z = 6.3 |
| Living window (south) | south exterior | x = 3.1 |
| Living window (west) | west exterior | z = 2.4 |
| Kitchen window (north) | north exterior | x = 2.1 |
| Kitchen window (west) | west exterior | z = 6.3 |
| Bathroom window (north) | north exterior | x = 5.1 |
| Bedroom 1 window (north) | north exterior | x = 10.0 |
| Bedroom 1 window (east) | east exterior | z = 6.3 |
| Bedroom 2 window (east) | east exterior | z = 2.4 |
| Bedroom 2 window (south) | south exterior | x = 10.0 |

### Furnishing schedule (catalog asset id — placement)

Every item comes from the built-in MCP catalog; ids are exact. "Against" a
wall means the item's back face is at that wall with its authored front facing
into the room.

| Asset id | Qty | Placement |
|---|---|---|
| `sofa` | 1 | living, against west wall, centered at z = 2.4, facing east |
| `coffee-table` | 1 | living, centered at (2.5, 2.4), long axis north–south |
| `tv-stand` | 1 | living, against east wall, centered at z = 2.4, facing west |
| `livingroom-chair` | 1 | living, centered at (4.2, 1.2), facing north |
| `kitchen` | 1 | kitchen, against north wall, centered at x = 1.45, facing south |
| `fridge` | 1 | kitchen, against north wall, centered at x = 3.3, facing south |
| `dining-table` | 1 | kitchen, centered at (2.1, 5.8), long axis east–west |
| `dining-chair` | 4 | at (1.35, 5.05) and (2.85, 5.05) facing north; at (1.35, 6.55) and (2.85, 6.55) facing south |
| `shower-square` | 1 | bathroom northwest corner, centered at (4.7, 7.3) |
| `washing-machine` | 1 | bathroom, against east wall, centered at z = 6.3 |
| `toilet` | 1 | bathroom, against west wall, centered at z = 5.3, facing east |
| `coat-rack` | 1 | hallway, centered at (6.5, 0.6) |
| `double-bed` | 1 | bedroom 1, headboard against north wall, centered at x = 9.8 |
| `bedside-table` | 2 | bedroom 1, flanking the bed against the north wall, at x = 8.5 and x = 11.15 |
| `single-bed` | 1 | bedroom 2, headboard against south wall, centered at x = 9.2 |
| `bedside-table` | 1 | bedroom 2, against south wall at x = 10.3 |
| `closet` | 1 | bedroom 2, against east wall, centered at z = 3.4, facing west |
| `dresser` | 1 | bedroom 2, against north partition, centered at x = 9.0, facing south |

No roof, stairs, landscaping, materials, or second level this round — mark
anything you skip as out-of-scope rather than improvising extras.

## How to drive (Pascal stub)

- Launch headless: `bunx pascal-mcp` (stdio). Requires Bun; no browser.
- Scene units are **meters**; X/Z are the floor-plan axes, Y is up — the same
  frame as the design program.
- `place_item` accepts **Y-axis (yaw) rotation only**. That is sufficient for
  every item in this program. `apply_patch` accepts full node data if you
  need exact control; doors/windows are children of their wall with
  wall-local coordinates.
- Validate as you go: `validate_scene`, `verify_scene`, `check_collisions`,
  and `measure`. A fault is cheapest to fix before other elements depend on
  it. `validate_scene` and `verify_scene` must be clean at the end. Note:
  `check_collisions` tests **unrotated** item footprints, so it can report
  false positives for correctly placed rotated furniture — investigate its
  hits, but do not move an item off its specified position to silence one.
- **The submitted artifact is `export_json`** (pretty). Save it as
  `run_scene.json`. GLB export is not available headlessly — do not attempt
  it.

## Run log (you ARE being measured on effort)

Capture model/tool versions, elapsed wall-clock, attempts, retries,
corrections, human interventions, and tokens if your host exposes them. Save
as `run_log.yaml`:

```yaml
schema_version: pascal_house_run_log.v0.1
run_id: pascal_<model>_<yyyymmdd>_<n>
benchmark_id: pascal_house_ph1
track: pascal_mcp
status: complete
driver:
  ai_driver: <your model name + version>
  host_application: "@pascal-app/mcp <version> / core <version>"
timing: {started_utc: null, ended_utc: null, elapsed_minutes: null}
token_usage: {capture_status: unavailable, total_tokens: null}
attempts:
  - {attempt_id: A01, is_retry: false, driver_action: <what you did>, result: pending, corrections: [], human_interventions: [], notes: null}
summary: {attempt_count: null, retry_count: null, correction_count: null, human_intervention_count: null, residual_not_built: []}
privacy_review: {secrets_checked: false, notes: No credentials or private fields.}
```

## When you are done

Report: the `run_scene.json` path, the run-log path, anything left not-built,
and a one-paragraph summary of where you struggled. **Stop there** — grading
is done by a separate session. Do not grade yourself, and do not tune toward
any gate.
