# MARB blind-kit versions

Each benchmark run must record which kit version it used, so runs are comparable
only within a cohort. Kit = the driver brief + the authored `kit/` STEP files +
reference images, packaged as a zip.

| Version | Zip | Change | Cohort |
|---|---|---|---|
| **v1.1** | `m3_fusion_blind_kit_v1.1.zip`, `m3_cadquery_blind_kit.zip` | Original blind kit. Fusion stub has only the lazy-bbox + kit-only quirks. **No-hint baseline.** | Opus 4.7 CadQuery + Fusion, GPT-5 Codex CadQuery, and the Opus 4.8 Fusion attempt (incomplete). |
| **v1.2** | `m3_fusion_blind_kit_v1.2.zip` | **Fusion stub only**: added the `addExistingComponent` matrix law (`stored = M·T_bake`; `world_center = M·C0`; dynamic-C0 re-anchor trap; re-inventory-before-mutating) + "create a new named project, not root/Admin". Tool mechanics, not answer-leaking. CadQuery unchanged (still v1.1). | Fusion runs 2026-05-30. |
| **v1.3** | `m3_fusion_blind_kit_v1.3.zip` | **Fusion stub only**: added a guard to **use the provided Fusion MCP tools (`fusion_mcp_execute`/`fusion_mcp_read`) directly and not write a self-rolled access harness**, and to allow MCP tool-discovery to finish before assuming the tools are missing. (Cause: a VS Code run improvised its own Fusion bridge during MCP tool discovery.) CadQuery unchanged. | **Current** Fusion kit, runs 2026-05-30 on. |

Why v1.2 exists: every Fusion run was burning large token budget re-deriving the
`addExistingComponent` behaviour from scratch (likely starved the Opus 4.8 run).
v1.2 supplies it so the model skips the investigation. See
`benchmarks/m3_ai_assembly/results/prompt_framework_findings.md` §5 and
`benchmarks/m3_ai_assembly/prompts/stub_fusion.md` (the source of record).

**Comparability rule:** v1.2 Fusion runs are a NEW cohort. Compare them to each
other and use the v1.1 Fusion runs (4.7) as the no-hint control to measure how much
the hint saves (tokens + quality). Do not pool v1.1 and v1.2 Fusion runs on one
leaderboard without noting the kit version.

Regenerate a kit zip: edit `prompts/stub_fusion.md` (or `stub_cadquery.md`), run
`prompts/build_briefs.py`, then swap the new `FUSION_DRIVER_BRIEF.md` into the kit
zip (assets unchanged). A reproducible `build_blind_kit.py` is still TODO — today
the zips are stored artifacts.
