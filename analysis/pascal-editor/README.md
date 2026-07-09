# Pascal Editor MARB/CADCLAW Evaluation

This folder is a repeatable analysis packet for evaluating
[`pascalorg/editor`](https://github.com/pascalorg/editor) as a possible MARB or
CADCLAW tool surface.

Current determination: Pascal Editor is worth tracking as an AI-editable
architectural scene host, especially because it exposes an MCP package, but it
is not directly MARB-gradeable today. MARB grades blind mechanical assembly
outputs as STEP geometry. Pascal currently presents a building editor scene
graph and MCP mutation tools; the public docs identify JSON export, scene
validation, and headless MCP operation, while headless GLB export is documented
as not implemented.

Use [`pascal_editor_marb_fit.md`](pascal_editor_marb_fit.md) for the current
human-readable assessment.

## Repeatable Analysis

Run the metadata-only analysis from this folder:

```powershell
.\run_pascal_editor_analysis.ps1
```

The script creates a timestamped folder under `runs/`, clones a fresh copy of
the public repo, and writes:

- `analysis.md`
- `analysis.json`
- `source_manifest.json`

By default the script does not install dependencies or execute project code. Use
`-RunInstall` or `-RunChecks` only in an isolated environment because those modes
execute third-party package/tooling code.

To analyze an existing local checkout instead of cloning:

```powershell
.\run_pascal_editor_analysis.ps1 -SourcePath D:\path\to\editor
```

