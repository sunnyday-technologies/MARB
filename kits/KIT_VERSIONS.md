# MARB blind-kit versions

A blind kit is the package handed to the driver for a benchmark run. It contains
the driver brief, the authored `kit/` STEP files, and the reference images,
delivered as one zip. It does not contain the answer key.

Every run must record the kit version it used. Runs are comparable only within a
single kit version. This page lists each version and the change that defines it.

| Version | Zip | Change | Cohort |
|---|---|---|---|
| **v1.1** | `m3_fusion_blind_kit_v1.1.zip`, `m3_cadquery_blind_kit.zip` | Original blind kit. The Autodesk Fusion stub carries only the lazy bounding-box and kit-only quirks. This is the no-hint baseline. | Opus 4.7 CadQuery and Fusion, GPT-5 Codex CadQuery, and the Opus 4.8 Fusion attempt (incomplete). |
| **v1.2** | `m3_fusion_blind_kit_v1.2.zip` | Fusion stub only. Added the `addExistingComponent` matrix law (`stored = M·T_bake`, `world_center = M·C0`, the dynamic-C0 re-anchor trap, and re-inventory before mutating). Also added the instruction to create a new named project rather than work in root or Admin. These are tool mechanics; they do not leak the answer key. The CadQuery kit is unchanged and stays at v1.1. | Fusion runs from 2026-05-30. |
| **v1.3** | `m3_fusion_blind_kit_v1.3.zip` | Fusion stub only. Added a guard to use the provided Fusion MCP tools (`fusion_mcp_execute` and `fusion_mcp_read`) directly, rather than write a self-rolled access harness, and to let MCP tool discovery finish before assuming the tools are missing. The cause was a VS Code run that improvised its own Fusion bridge during tool discovery. The CadQuery kit is unchanged. | Current Fusion kit, runs from 2026-05-30 onward. |

## Why v1.2 exists

Every Fusion run spent a large share of its token budget re-deriving the
`addExistingComponent` placement behavior from scratch. This likely starved the
Opus 4.8 run of the budget it needed to finish. Version 1.2 supplies the
behavior up front, so the model can skip the investigation. The source of record
is `results/prompt_framework_findings.md` section 5 and the Fusion stub at
`prompts/stub_fusion.md`.

## Comparability rule

The v1.2 Fusion runs form a new cohort. Compare them only to each other. Use the
v1.1 Fusion runs from Opus 4.7 as the no-hint control. This shows how much the
hint saves in both tokens and quality. Do not place v1.1 and v1.2 Fusion runs on
one leaderboard without recording the kit version.

## Regenerating a kit zip

To rebuild a kit zip, edit the relevant stub (`prompts/stub_fusion.md` or
`prompts/stub_cadquery.md`), run `prompts/build_briefs.py`, then swap the new
`FUSION_DRIVER_BRIEF.md` into the kit zip. The bundled assets do not change. The
zips are currently stored artifacts; a single-command kit builder is not yet
available.
