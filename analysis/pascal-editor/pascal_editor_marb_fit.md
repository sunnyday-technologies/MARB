# Pascal Editor Fit For MARB / CADCLAW

Date: 2026-07-05

Subject: `pascalorg/editor` at `https://github.com/pascalorg/editor`

## Determination

Do not add Pascal Editor as a MARB leaderboard cell yet. It is not a model and,
from the public repo/docs inspected today, it does not currently provide the
neutral STEP/B-rep output path MARB needs for scoring.

It is a plausible CADCLAW-adjacent tool surface to evaluate separately:

- as an MCP-controlled architectural scene authoring host;
- as a future adapter target if its scene graph can be exported to gradeable
  geometry; or
- as a candidate for a CADCLAW.io architectural/layout validation demo, distinct
  from MARB's mechanical assembly benchmark.

## Evidence

- The GitHub repo describes Pascal Editor as "a 3D building editor built with
  React Three Fiber and WebGPU."
- The repo is a Turborepo with `apps/editor`, `packages/core`,
  `packages/viewer`, and `packages/ui`.
- The public architecture docs describe a plugin contract with `Plugin`
  manifests, node definitions, panels, tools, renderers, systems, and MCP
  contributions.
- The MCP package describes headless scene mutations such as `create_room`,
  `create_wall`, `place_item`, `validate_scene`, `verify_scene`, and
  `check_collisions`.
- The MCP package documents `export_json` but says `export_glb` is stubbed and
  not implemented headlessly because GLB export depends on the browser renderer.
- MARB's current scoring path requires a submitted STEP assembly so the grader
  can compute GAP, POS, ORIENT, and secondary native CADCLAW gates.

## Fit Matrix

| Lane | Fit | Why |
|---|---:|---|
| MARB mechanical assembly scoring | Blocked | Needs neutral exported STEP assembly; Pascal's current public path is scene JSON/MCP, not mechanical STEP assembly output. |
| CADCLAW architecture/layout demo | Possible | MCP can create and validate rooms, walls, zones, items, and collision checks. This is closer to building-layout verification than MARB. |
| Model/tool surface evaluation | Possible after adapter | A fair test would drive the same Pascal MCP tools with different AI models and compare validated scene outputs, not compare Pascal itself as a model. |
| Public cadclaw.io content | Not yet | Needs a clean adapter, reproducible smoke run, and truthful wording before publishing claims. |

## Proposed Evaluation Gate

Before Pascal becomes a scoreable lane, require all of these:

1. A sealed driver that gives the AI model the same Pascal MCP surface for every
   run.
2. A deterministic export artifact suitable for grading. For MARB that means
   STEP or another path that can be converted to a CADCLAW-readable B-rep without
   losing part identity.
3. A benchmark prompt that does not leak MARB answer-key geometry or privileged
   placement metadata.
4. A grader adapter that records tool version, package versions, model name,
   run seed, timing, and exported artifact hash.
5. A claim boundary: report it as "Pascal MCP scene-authoring evaluation" unless
   a STEP export path is proven and graded by MARB.

## Recommended Next Step

Run the repeatable script in this folder. If it confirms the same export
boundary, create a separate CADCLAW.io internal note for a Pascal MCP
architecture-layout demo rather than placing Pascal on the MARB mechanical
assembly board.

## Addendum 2026-07-08 — source-level review revises the export-boundary finding

The 2026-07-05 runs never inspected source: both clones failed on network
(`runs/*/logs/git-clone.log`, exit 128). A fresh shallow clone of
`pascalorg/editor` succeeded today and the key blockers were checked in code.
Package versions are unchanged since 2026-06-10 (`@pascal-app/core` 0.9.1,
`@pascal-app/viewer` 0.9.1, `@pascal-app/mcp` 0.3.1, `@pascal-app/ifc-converter`
0.1.1, import-only).

Findings that revise the determination:

1. `ItemNode` (packages/core/src/schema/nodes/item.ts) carries full free-form
   `position` vec3, **full XYZ euler `rotation`**, `scale`, and an `asset.src`
   URL — arbitrary GLB assets, not just catalog items.
2. The MCP `place_item` tool is yaw-only (`rotation: [0, y, 0]`,
   packages/mcp/src/tools/place-item.ts) — but `apply_patch`
   (packages/mcp/src/tools/apply-patch.ts) accepts raw `AnyNode` create/update
   data, so a driver can set full 6-DOF item transforms headlessly.
3. `export_json` (packages/mcp/src/tools/export-json.ts) serializes the raw
   scene store — node transforms come out verbatim. Units are meters, Y-up
   (agent-guide.ts).

Consequence: MARB's grader does not need Pascal to export STEP. Because the
MARB task is placement of *authored* parts, a rehydrator can apply the
`export_json` item transforms (m→mm, Y-up→Z-up, euler-order mapping) back onto
the original kit STEP parts and emit a gradeable STEP assembly with part
identity intact. GAP/POS/ORIENT then run unchanged. The "Blocked" row in the
fit matrix downgrades to "Possible via JSON-transform rehydration adapter";
the five-point Evaluation Gate above still applies as written. Board cells
would read "Model · Pascal MCP" in a separate kit cohort (meshed-GLB kit).

Open items for a smoke run: confirm euler order/level-parent transform
composition in the rehydrator, confirm `apply_patch` bypasses editor snapping
(it mutates the store directly, so it should), and hash the GLB kit + scene
JSON for provenance.

## Addendum 2026-07-08b — architecture lane (MARB-A) built to suit the tool

Decision: instead of forcing Pascal through the mechanical STEP path, a
dedicated **architecture lane** was built around Pascal's native input
mechanism — a natural-language design program driven through the headless
`@pascal-app/mcp` server (`from_brief` prompt, `create_house_from_brief`,
semantic room/opening/furnishing tools, built-in asset catalog). Shipped:

- Task PH-1 "Bungalow": 6 rooms / 6 doors / 9 windows / 22 catalog items.
- Blind kit `kits/pascal_house_blind_kit_v0.1.zip` (brief + dimensioned goal
  floorplan render + pinned catalog manifest).
- Answer key `tasks/pascal_house/ph1_reference_layout.yaml` + grader
  `grader/pascal_scene_grade.py` grading the submitted `export_json` scene:
  WALL/OPEN/POS-A/ORIENT-A + BOM/collision/envelope gates. Self-test and
  90°-rotated-frame test pass.

This satisfies the Proposed Evaluation Gate above (sealed MCP driver,
deterministic export artifact, no answer-key leak beyond the goal render —
the same disclosure level as the mechanical kit's reference images,
provenance fields, and the "Pascal MCP scene-authoring evaluation" claim
boundary). The mechanical-lane rehydration adapter (2026-07-08 addendum)
remains a separate, unbuilt option.

