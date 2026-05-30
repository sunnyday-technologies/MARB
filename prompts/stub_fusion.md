<!-- track: fusion_native_driver -->
## How to drive — Autodesk Fusion (via the Fusion MCP)

Build in a live Fusion via the Fusion MCP tools available in this session.

**Use the provided Fusion MCP tools directly** — `fusion_mcp_execute` (run Fusion
Python API scripts) and `fusion_mcp_read` (screenshots / inventory / API docs). Do
**not** write your own access layer, socket bridge, or harness to reach Fusion. If
the tools do not appear the instant the session starts, allow a moment for MCP tool
discovery to finish before assuming they are missing — do not improvise around them.

1. **Create a new, clearly-named Fusion project** (via the Data Panel) and create the
   assembly design inside it, then save. Do **not** build in the default root / Admin
   project — the assembly gets buried there and is hard to find later. (Creating a
   fresh *output* project is fine and does not touch the fairness wall; you still
   import parts only from the local `kit/` folder, never from any existing project.)
2. Import each kit part from the local **`kit/`** folder of this kit, via
   `fusion_mcp_execute` `featureType:"script"` (Fusion Python API:
   `app.importManager.createSTEPImportOptions(path)` then `importToTarget`). The kit
   parts are the STEP files in `kit/` (names match the kit table above) — they are
   the **only** part source.
3. Position/orient each occurrence with `transformBy` / `Matrix3D`. Look up the exact
   API via `fusion_mcp_read` `queryType:"apiDocumentation"`.
4. To inspect your work, capture the live viewport with `fusion_mcp_read`
   `queryType:"screenshot"` from any camera orientation — front, top, right, or
   isometric (drive the viewport via the Fusion API / named views). The target
   reference images are in this kit folder (see the brief).
5. Export the design to a single STEP via `ExportManager` / `STEPExportOptions` to
   `fusion_native_export.step` in your run folder.

Tooling quirks (from prior runs — operational, not solution hints):
- A freshly-imported body's `boundingBox` can read zero in the **same** script call
  (lazy geometry load). Re-read it on a *later* MCP call before using it to position
  anything. (`transform.asArray()`, including rotation, IS available immediately.)
- **`addExistingComponent(comp, M)` does not place geometry where the raw matrix
  implies.** It stores `M · T_bake`, where `T_bake` is a baked origin offset tied to
  the component's CURRENT FIRST occurrence. So `world_center = M · C0` and
  `final_orientation = R · R_b`, where `C0` (the first occurrence's bbox center) and
  `R_b` (its rotation) are read from that occurrence. To land a part's center at
  `target` with rotation `R_desired`, set `M = [ R_rel | target − R_rel·C0 ]` with
  `R_rel = R_desired · R_bᵀ`. Measure `C0` / `R_b` empirically from an identity
  occurrence; do not assume them. Fresh STEP imports come in clean (`R_b = I`,
  `C0 = authored center`).
- **Deleting a component's first occurrence silently re-anchors `C0` / `R_b`** to the
  new first occurrence for all FUTURE placements (already-placed occurrences are
  immutable). This can fling re-placed parts ~1 m off. Keep each freshly-imported
  occurrence ALIVE until that component is fully placed AND verified; fix any bad
  placement before deleting it.
- **Re-inventory occurrences before every mutation** and delete by collected
  references, not by index — leftover diagnostic/import occurrences shift indices and
  cause off-by-N `C0` reads.
- Import **only from the `kit/` folder**. Do **not** search Fusion Team/cloud
  projects for parts — a same-named file in another project (e.g. **M3-CRETE, which
  holds the reference design / answer key**) would invalidate the run.

In the run log, set `track: fusion_native_driver` and
`host_application: Autodesk Fusion (via Fusion MCP)`.
