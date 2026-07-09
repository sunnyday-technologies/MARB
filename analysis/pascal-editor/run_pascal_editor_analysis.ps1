param(
    [string]$RepoUrl = "https://github.com/pascalorg/editor.git",
    [string]$Ref = "HEAD",
    [string]$SourcePath = "",
    [string]$OutRoot = "",
    [switch]$RunInstall,
    [switch]$RunChecks
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "[pascal-analysis] $Message"
}

function Resolve-FullPath {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    return $item.FullName
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$LogPath
    )

    $oldLocation = Get-Location
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        Set-Location -LiteralPath $WorkingDirectory
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        Set-Location -LiteralPath $oldLocation
    }

    $outputText = ($output | ForEach-Object { $_.ToString() }) -join "`n"
    $log = @(
        "command: $FilePath $($Arguments -join ' ')",
        "working_directory: $WorkingDirectory",
        "exit_code: $exitCode",
        "",
        "[output]",
        $outputText
    ) -join "`n"
    Set-Content -LiteralPath $LogPath -Value $log -Encoding UTF8

    return [pscustomobject]@{
        command = "$FilePath $($Arguments -join ' ')"
        exit_code = $exitCode
        log = $LogPath
    }
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-RelativePathSafe {
    param(
        [string]$Base,
        [string]$Path
    )
    return [System.IO.Path]::GetRelativePath($Base, $Path).Replace("\", "/")
}

function Get-TextFiles {
    param([string]$Root)
    $skipDirs = @(".git", "node_modules", ".next", "dist", "build", ".turbo")
    $textExts = @(".md", ".json", ".jsonc", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".yaml", ".yml", ".toml", ".txt")

    Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
        Where-Object {
            $parts = $_.FullName.Substring($Root.Length).TrimStart("\", "/") -split "[\\/]"
            -not ($parts | Where-Object { $skipDirs -contains $_ })
        } |
        Where-Object { $textExts -contains $_.Extension.ToLowerInvariant() }
}

function Find-PatternHits {
    param(
        [string]$Root,
        [hashtable]$Patterns
    )

    $hits = @()
    $files = @(Get-TextFiles -Root $Root)
    foreach ($file in $files) {
        foreach ($name in $Patterns.Keys) {
            $matches = Select-String -LiteralPath $file.FullName -Pattern $Patterns[$name] -AllMatches -ErrorAction SilentlyContinue
            foreach ($match in $matches) {
                $hits += [pscustomobject]@{
                    pattern = $name
                    file = Get-RelativePathSafe -Base $Root -Path $file.FullName
                    line = $match.LineNumber
                }
            }
        }
    }
    return $hits
}

function Get-EnvFileStatus {
    param([string]$Root)

    $envFiles = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
        Where-Object {
            $_.Name -eq ".env" -or
            $_.Name -like ".env.*" -or
            $_.Name -eq ".npmrc"
        } |
        Where-Object {
            $_.FullName -notmatch "[\\/](node_modules|\.git)[\\/]"
        })

    $rows = @()
    foreach ($file in $envFiles) {
        $relative = Get-RelativePathSafe -Base $Root -Path $file.FullName
        $keys = @()
        if ($file.Name -like ".env.example" -or $file.Name -like "*.example") {
            $lines = Get-Content -LiteralPath $file.FullName -Encoding UTF8 -ErrorAction SilentlyContinue
            foreach ($line in $lines) {
                if ($line -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=") {
                    $keys += $Matches[1]
                }
            }
        }
        $rows += [pscustomobject]@{
            file = $relative
            status = if ($file.Name -like "*.example") { "example_present" } else { "present_review_without_printing_values" }
            key_names = @($keys | Sort-Object -Unique)
        }
    }
    return $rows
}

if ([string]::IsNullOrWhiteSpace($OutRoot)) {
    $OutRoot = Join-Path $PSScriptRoot "runs"
}

New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $OutRoot $timestamp
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$logsDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Write-Step "output: $runDir"

if ([string]::IsNullOrWhiteSpace($SourcePath)) {
    $sourceDir = Join-Path $runDir "source"
    Write-Step "cloning $RepoUrl"
    $clone = Invoke-LoggedCommand -FilePath "git" -Arguments @("clone", "--depth", "1", $RepoUrl, $sourceDir) -WorkingDirectory $runDir -LogPath (Join-Path $logsDir "git-clone.log")
    if ($clone.exit_code -ne 0) {
        throw "git clone failed; see $($clone.log)"
    }

    if ($Ref -ne "HEAD") {
        Write-Step "checking out $Ref"
        $fetch = Invoke-LoggedCommand -FilePath "git" -Arguments @("fetch", "--depth", "1", "origin", $Ref) -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "git-fetch-ref.log")
        if ($fetch.exit_code -ne 0) {
            throw "git fetch failed; see $($fetch.log)"
        }
        $checkout = Invoke-LoggedCommand -FilePath "git" -Arguments @("checkout", "--detach", "FETCH_HEAD") -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "git-checkout-ref.log")
        if ($checkout.exit_code -ne 0) {
            throw "git checkout failed; see $($checkout.log)"
        }
    }
} else {
    $sourceDir = Resolve-FullPath -Path $SourcePath
    Write-Step "analyzing existing source: $sourceDir"
}

$headLog = Join-Path $logsDir "git-rev-parse.log"
$headResult = Invoke-LoggedCommand -FilePath "git" -Arguments @("rev-parse", "HEAD") -WorkingDirectory $sourceDir -LogPath $headLog
$headSha = ""
if ($headResult.exit_code -eq 0) {
    $headSha = (Get-Content -LiteralPath $headLog -Encoding UTF8 | Select-String -Pattern "^[0-9a-f]{40}$" | Select-Object -First 1).Line
}

$rootPackage = Read-JsonFile -Path (Join-Path $sourceDir "package.json")
$mcpPackage = Read-JsonFile -Path (Join-Path $sourceDir "packages/mcp/package.json")
$corePackage = Read-JsonFile -Path (Join-Path $sourceDir "packages/core/package.json")
$viewerPackage = Read-JsonFile -Path (Join-Path $sourceDir "packages/viewer/package.json")

$signalPatterns = @{
    "mcp" = "\bMCP\b|Model Context Protocol|pascal-mcp"
    "plugin_contract" = "\bPlugin\b|setPluginDiscovery|NodeDefinition"
    "step" = "\bSTEP\b|\.step\b|\.stp\b"
    "glb_export" = "export_glb|GLB export|\.glb\b"
    "json_export" = "export_json"
    "validation" = "validate_scene|verify_scene|check_collisions"
    "webgpu_three" = "WebGPU|React Three Fiber|three\.js|Three\.js"
}

$secretPatterns = @{
    "private_key_header" = "BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY"
    "aws_access_key_id_shape" = "AKIA[0-9A-Z]{16}"
    "token_assignment_shape" = "(API_KEY|TOKEN|SECRET|PASSWORD)\s*="
}

$signals = @(Find-PatternHits -Root $sourceDir -Patterns $signalPatterns)
$secretShapeHits = @(Find-PatternHits -Root $sourceDir -Patterns $secretPatterns)
$envStatus = @(Get-EnvFileStatus -Root $sourceDir)

$commands = @()
if ($RunInstall) {
    Write-Step "running bun install; this executes third-party dependency tooling"
    $commands += Invoke-LoggedCommand -FilePath "bun" -Arguments @("install", "--frozen-lockfile") -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "bun-install.log")
}

if ($RunChecks) {
    Write-Step "running project checks"
    $commands += Invoke-LoggedCommand -FilePath "bun" -Arguments @("check") -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "bun-check.log")
    $commands += Invoke-LoggedCommand -FilePath "bun" -Arguments @("check-types") -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "bun-check-types.log")
    $commands += Invoke-LoggedCommand -FilePath "bun" -Arguments @("run", "--cwd", "packages/mcp", "build") -WorkingDirectory $sourceDir -LogPath (Join-Path $logsDir "bun-mcp-build.log")
}

$signalSummary = $signals |
    Group-Object -Property pattern |
    Sort-Object Name |
    ForEach-Object {
        [pscustomobject]@{
            pattern = $_.Name
            count = $_.Count
            sample_files = @($_.Group | Select-Object -First 5 | ForEach-Object { $_.file } | Sort-Object -Unique)
        }
    }

$analysis = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    repo_url = $RepoUrl
    ref_requested = $Ref
    source_path = $sourceDir
    head_sha = $headSha
    conclusion = "not_directly_marb_gradeable_without_adapter"
    rationale = @(
        "MARB grades blind mechanical assembly outputs as STEP geometry.",
        "Pascal Editor is an architectural scene editor with MCP scene mutation tools.",
        "The public MCP docs expose JSON export and validation tools; headless GLB export is documented as not implemented.",
        "A fair evaluation should first be a Pascal MCP scene-authoring lane or an adapter proof, not a MARB leaderboard score."
    )
    root_package = if ($null -eq $rootPackage) { $null } else {
        [pscustomobject]@{
            name = $rootPackage.name
            package_manager = $rootPackage.packageManager
            engines = $rootPackage.engines
            workspaces = $rootPackage.workspaces
            scripts = ($rootPackage.scripts | Get-Member -MemberType NoteProperty | ForEach-Object { $_.Name } | Sort-Object)
        }
    }
    packages = @(
        if ($null -ne $corePackage) {
            [pscustomobject]@{ name = $corePackage.name; version = $corePackage.version; description = $corePackage.description }
        }
        if ($null -ne $viewerPackage) {
            [pscustomobject]@{ name = $viewerPackage.name; version = $viewerPackage.version; description = $viewerPackage.description }
        }
        if ($null -ne $mcpPackage) {
            [pscustomobject]@{ name = $mcpPackage.name; version = $mcpPackage.version; description = $mcpPackage.description }
        }
    )
    signal_summary = @($signalSummary)
    signal_hits = @($signals | Select-Object -First 250)
    env_files = @($envStatus)
    secret_shape_status = if ($secretShapeHits.Count -eq 0) { "no_secret_shapes_detected_by_limited_scan" } else { "review_needed_without_printing_values" }
    secret_shape_hits = @($secretShapeHits | Select-Object pattern, file, line)
    command_results = @($commands)
    next_gates = @(
        "Prove deterministic export to STEP or CADCLAW-readable geometry.",
        "Build a sealed Pascal MCP driver that every AI model uses identically.",
        "Record package versions, commit SHA, model name, seed, timing, artifact hash, and validation output.",
        "Keep public wording scoped to scene-authoring evaluation until MARB-gradeable geometry exists."
    )
}

$jsonPath = Join-Path $runDir "analysis.json"
$analysis | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$manifest = [pscustomobject]@{
    repo_url = $RepoUrl
    ref_requested = $Ref
    head_sha = $headSha
    source_path = $sourceDir
    generated_at = $analysis.generated_at
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir "source_manifest.json") -Encoding UTF8

$md = @"
# Pascal Editor Analysis

Generated: $($analysis.generated_at)

Repo: $RepoUrl

Commit: $headSha

## Conclusion

`pascalorg/editor` is not directly MARB-gradeable without an adapter. Treat it
as a candidate Pascal MCP scene-authoring lane or CADCLAW architecture/layout
demo until a deterministic STEP or CADCLAW-readable geometry export path is
proven.

## Signals

$(
    if ($signalSummary.Count -eq 0) {
        "- No configured signals found."
    } else {
        ($signalSummary | ForEach-Object { "- $($_.pattern): $($_.count) hits" }) -join "`n"
    }
)

## Secret Handling

The scan reports only key names, file paths, statuses, and pattern labels. It
does not print secret values. Secret-shape status:
`$($analysis.secret_shape_status)`.

## Next Gates

$(
    ($analysis.next_gates | ForEach-Object { "- $_" }) -join "`n"
)

"@

Set-Content -LiteralPath (Join-Path $runDir "analysis.md") -Value $md -Encoding UTF8

Write-Step "wrote $jsonPath"
Write-Step "wrote $(Join-Path $runDir 'analysis.md')"
