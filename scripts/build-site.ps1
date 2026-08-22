param(
  [string]$PublishDir,
  [switch]$Release
)

# Build a WEB-ONLY artifact for marb.cadclaw.io from publishing/.
#
# The allowlist and validation gates fail closed on destination scope, rights,
# answer-key/CAD boundaries, machine-readable contracts, claims, references,
# security policy, and source/output integrity. This script never deploys.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir ".."))
$SiteRoot = Join-Path $RepoRoot "publishing"
$RightsPath = Join-Path $RepoRoot "publication-rights.json"
$ExpectedTarget = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot ".cloudflare/pages/marb"))

if ([string]::IsNullOrWhiteSpace($PublishDir)) {
  $Target = $ExpectedTarget
} elseif ([System.IO.Path]::IsPathRooted($PublishDir)) {
  $Target = [System.IO.Path]::GetFullPath($PublishDir)
} else {
  $Target = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $PublishDir))
}

if (-not [string]::Equals($Target, $ExpectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing alternate publish destination; expected .cloudflare/pages/marb"
}

function Get-RepoRelativePath {
  param([string]$FullName)
  return $FullName.Substring($RepoRoot.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
}

function Get-PublishRelativePath {
  param([string]$FullName)
  return $FullName.Substring($Target.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
}

function Assert-NoReparseAncestor {
  param([string]$Path)
  $current = [System.IO.DirectoryInfo](Split-Path -Parent $Path)
  while ($null -ne $current) {
    if ($current.Exists -and (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
      throw "Refusing publish destination beneath a reparse point"
    }
    if ([string]::Equals($current.FullName, $RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) { break }
    if (-not $current.FullName.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Publish destination escaped the repository root"
    }
    $current = $current.Parent
  }
}

# Rights are checked before clearing an existing output, so a failed release
# preflight does not erase the last locally validated artifact.
if (-not (Test-Path -LiteralPath $RightsPath -PathType Leaf)) {
  throw "Missing publication-rights.json"
}
try {
  $rights = Get-Content -LiteralPath $RightsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
  throw "Invalid publication-rights.json"
}

$requiredRightsProperties = @(
  "release_authorized",
  "release_hold",
  "supported_by_repository_hash",
  "rights_cleared_for_release",
  "owner_or_counsel_review_required",
  "excluded_from_release",
  "tracked_repository_hold"
)
foreach ($propertyName in $requiredRightsProperties) {
  if ($rights.PSObject.Properties.Name -notcontains $propertyName) {
    throw "publication-rights.json is missing required property: $propertyName"
  }
}

$supportedAssets = @($rights.supported_by_repository_hash | ForEach-Object { ([string]$_).Replace('\', '/') })
$clearedAssets = @($rights.rights_cleared_for_release | ForEach-Object { ([string]$_).Replace('\', '/') })
$reviewAssets = @($rights.owner_or_counsel_review_required | ForEach-Object { ([string]$_).Replace('\', '/') })
$excludedAssets = @($rights.excluded_from_release | ForEach-Object { ([string]$_).Replace('\', '/') })
$trackedHolds = @($rights.tracked_repository_hold | ForEach-Object { ([string]$_).Replace('\', '/') })

$imageExtensions = @(".png", ".jpg", ".jpeg", ".gif", ".svg")
$publicAssets = @(
  Get-ChildItem -LiteralPath $SiteRoot -Recurse -File -Force |
    Where-Object { $imageExtensions -contains $_.Extension.ToLowerInvariant() } |
    ForEach-Object { Get-RepoRelativePath $_.FullName } |
    Sort-Object -Unique
)
$recordedAssets = @($supportedAssets + $clearedAssets + $reviewAssets | Sort-Object -Unique)
$missingRightsRecords = @($publicAssets | Where-Object { $recordedAssets -notcontains $_ })
$staleRightsRecords = @($recordedAssets | Where-Object { $publicAssets -notcontains $_ })
if ($missingRightsRecords.Count -gt 0) {
  throw "Public visual assets lack a rights record: $($missingRightsRecords -join ', ')"
}
if ($staleRightsRecords.Count -gt 0) {
  throw "Rights manifest references missing public assets: $($staleRightsRecords -join ', ')"
}
# Excluded-rights assets are asserted absent from the publish OUTPUT below, once
# $publishFiles exists. They are deliberately NOT required to exist in the working
# tree: we are not licensed to redistribute them, so they are git-ignored and are
# legitimately absent from a clean checkout or a CI runner.
foreach ($holdPath in $trackedHolds) {
  if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $holdPath) -PathType Leaf)) {
    throw "Tracked-rights hold path is missing: $holdPath"
  }
}

if ($Release) {
  if ($rights.release_authorized -ne $true) {
    throw "Production release refused: publication-rights.json is not authorized"
  }
  if ($reviewAssets.Count -gt 0) {
    throw "Production release refused: owner or counsel review remains for public assets"
  }
  if ($trackedHolds.Count -gt 0) {
    throw "Production release refused: tracked repository rights holds remain"
  }
  if (-not [string]::IsNullOrWhiteSpace([string]$rights.release_hold)) {
    throw "Production release refused: release_hold is not empty"
  }
  $unclearedAssets = @($publicAssets | Where-Object { $clearedAssets -notcontains $_ })
  if ($unclearedAssets.Count -gt 0) {
    throw "Production release refused: retained public visual assets lack recorded rights clearance"
  }
}

Assert-NoReparseAncestor $Target
if (Test-Path -LiteralPath $Target) {
  $targetItem = Get-Item -LiteralPath $Target -Force
  if (($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to clear a reparse-point publish destination"
  }
  Remove-Item -LiteralPath $Target -Recurse -Force
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null

function Copy-PublicFile {
  param([string]$RelativePath)
  $source = Join-Path $SiteRoot $RelativePath
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Missing required public file: publishing/$RelativePath"
  }
  $destination = Join-Path $Target $RelativePath
  New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
  Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Copy-PublicDirectory {
  param([string]$RelativePath)
  $source = Join-Path $SiteRoot $RelativePath
  if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "Missing required public directory: publishing/$RelativePath"
  }
  $destination = Join-Path $Target $RelativePath
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $destination -Recurse -Force
}

$rootFiles = @(
  "index.html",
  "404.html",
  "CADCLAW_logo.jpg",
  "llms.txt",
  "portfolio-loop.css",
  "robots.txt",
  "sitemap.xml",
  "styles.css",
  "_headers"
)
foreach ($file in $rootFiles) { Copy-PublicFile $file }

$publicDirectories = @("first-results", "media", "pascal", "recap", "studies", ".well-known")
foreach ($directory in $publicDirectories) { Copy-PublicDirectory $directory }

$publishFiles = @(Get-ChildItem -LiteralPath $Target -Recurse -File -Force)
if ($publishFiles.Count -eq 0) { throw "Publish output is empty" }

foreach ($excludedPath in $excludedAssets) {
  $excludedLeaf = Split-Path -Leaf $excludedPath
  $leaked = @($publishFiles | Where-Object { $_.Name -eq $excludedLeaf })
  if ($leaked.Count -gt 0) {
    throw "Excluded-rights asset reached publish output: $excludedPath"
  }
}

$blockedPathPattern = '(?i)(^|[\/])(fonts|private|publication|__pycache__|\.git)([\/]|$)'
$blockedNamePattern = '(?i)(answer[_-]?key|leak[_-]?signature|reference[_-]?assembly)'
$blockedPaths = @(
  $publishFiles | Where-Object {
    (Get-PublishRelativePath $_.FullName) -match $blockedPathPattern -or
    $_.Name -match $blockedNamePattern
  }
)
if ($blockedPaths.Count -gt 0) {
  throw "Blocked path reached publish output: $((@($blockedPaths | ForEach-Object { Get-PublishRelativePath $_.FullName }) | Sort-Object -Unique) -join ', ')"
}

$blockedExtensions = @(
  ".stl", ".step", ".stp", ".3mf", ".f3d", ".f3z", ".sldprt", ".sldasm",
  ".ipt", ".iam", ".iges", ".igs", ".x_t", ".x_b", ".dwg", ".dxf",
  ".yaml", ".yml", ".env", ".pem", ".key", ".otf", ".ttf", ".woff", ".woff2"
)
$blockedFiles = @($publishFiles | Where-Object { $blockedExtensions -contains $_.Extension.ToLowerInvariant() })
if ($blockedFiles.Count -gt 0) {
  throw "Blocked file type reached publish output: $((@($blockedFiles | ForEach-Object { Get-PublishRelativePath $_.FullName })) -join ', ')"
}
$oversizeFiles = @($publishFiles | Where-Object { $_.Length -gt 25MB })
if ($oversizeFiles.Count -gt 0) {
  throw "File exceeds the 25 MiB asset limit: $((@($oversizeFiles | ForEach-Object { Get-PublishRelativePath $_.FullName })) -join ', ')"
}

# Scan likely-text surfaces. Report filenames only; never print matching data.
$textExtensions = @(".html", ".js", ".mjs", ".css", ".json", ".jsonl", ".txt", ".xml", ".svg", ".md")
$secretPatterns = @(
  'AKIA[0-9A-Z]{16}',
  'AIza[0-9A-Za-z_-]{30,}',
  '-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----',
  'gh[pousr]_[A-Za-z0-9_]{20,}',
  'glpat-[A-Za-z0-9_-]{20,}',
  'xox[baprs]-[A-Za-z0-9-]{20,}',
  'sk_live_[A-Za-z0-9]{20,}',
  'rk_live_[A-Za-z0-9]{20,}',
  'sk-(proj-)?[A-Za-z0-9_-]{32,}',
  '(?i)["'']?(api[_-]?key|client[_-]?secret|access[_-]?token|password)["'']?\s*[:=]\s*["''][^"''\r\n]{12,}["'']'
)
$secretHitFiles = @()
foreach ($file in ($publishFiles | Where-Object { $textExtensions -contains $_.Extension.ToLowerInvariant() })) {
  $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
  $scan = [regex]::Replace($content, 'data:font/[^;]+;base64,[A-Za-z0-9+/=]+', 'data:font/stripped;base64,')
  foreach ($pattern in $secretPatterns) {
    if ($scan -cmatch $pattern) {
      $secretHitFiles += (Get-PublishRelativePath $file.FullName)
      break
    }
  }
}
if ($secretHitFiles.Count -gt 0) {
  throw "Secret-like pattern found; inspect these files without printing values: $((@($secretHitFiles) | Sort-Object -Unique) -join ', ')"
}

$jsonFiles = @($publishFiles | Where-Object { $_.Extension.ToLowerInvariant() -eq ".json" })
foreach ($file in $jsonFiles) {
  try {
    $null = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    throw "Invalid JSON: $(Get-PublishRelativePath $file.FullName)"
  }
}

$xmlFiles = @($publishFiles | Where-Object { @(".xml", ".svg") -contains $_.Extension.ToLowerInvariant() })
foreach ($file in $xmlFiles) {
  try {
    [xml]$null = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
  } catch {
    throw "Invalid XML/SVG: $(Get-PublishRelativePath $file.FullName)"
  }
}

try {
  [xml]$sitemapXml = Get-Content -LiteralPath (Join-Path $Target "sitemap.xml") -Raw -Encoding UTF8
} catch {
  throw "Invalid sitemap.xml"
}
$sitemapNodes = @($sitemapXml.SelectNodes("//*[local-name()='url']"))
$sitemapUrls = @($sitemapNodes | ForEach-Object { $_.SelectSingleNode("./*[local-name()='loc']").InnerText })
$requiredSitemapUrls = @(
  "https://marb.cadclaw.io/",
  "https://marb.cadclaw.io/first-results/",
  "https://marb.cadclaw.io/pascal/",
  "https://marb.cadclaw.io/recap/",
  "https://marb.cadclaw.io/studies/"
)
foreach ($requiredUrl in $requiredSitemapUrls) {
  if ($sitemapUrls -notcontains $requiredUrl) { throw "Sitemap is missing required URL: $requiredUrl" }
}
if ($sitemapUrls.Count -ne $requiredSitemapUrls.Count) {
  throw "Sitemap must enumerate exactly the five local HTML routes"
}
foreach ($node in $sitemapNodes) {
  $lastmod = $node.SelectSingleNode("./*[local-name()='lastmod']")
  if ($null -eq $lastmod -or $lastmod.InnerText -ne "2026-08-11") {
    throw "Every sitemap route must carry lastmod 2026-08-11"
  }
}

function Get-Sha256Base64 {
  param([string]$Text)
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { $hash = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }
  return [Convert]::ToBase64String($hash)
}

function Resolve-PublishReference {
  param(
    [System.IO.FileInfo]$SourceFile,
    [string]$Reference
  )
  if ([string]::IsNullOrWhiteSpace($Reference)) { return $null }
  if ($Reference -match '^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)') { return $null }
  if ($Reference.StartsWith('#')) { return $null }

  $clean = [regex]::Split($Reference, '[?#]')[0]
  if ([string]::IsNullOrWhiteSpace($clean)) { return $null }
  $clean = [System.Uri]::UnescapeDataString($clean)
  $nativeClean = $clean.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
  if ($clean.StartsWith('/')) {
    $nativeClean = $nativeClean.TrimStart([char[]]@('\', '/'))
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Target $nativeClean))
  } else {
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $SourceFile.DirectoryName $nativeClean))
  }

  $targetPrefix = $Target.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
  if (-not [string]::Equals($candidate, $Target, [System.StringComparison]::OrdinalIgnoreCase) -and
      -not $candidate.StartsWith($targetPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Local reference escapes publish output in $(Get-PublishRelativePath $SourceFile.FullName)"
  }
  if ($clean.EndsWith('/') -or (Test-Path -LiteralPath $candidate -PathType Container)) {
    $candidate = Join-Path $candidate "index.html"
  }
  return $candidate
}

$headersText = Get-Content -LiteralPath (Join-Path $Target "_headers") -Raw -Encoding UTF8
$htmlFiles = @($publishFiles | Where-Object { $_.Extension.ToLowerInvariant() -eq ".html" })
$linkCount = 0
$jsonLdCount = 0
$jsonLdHashes = @()
$attributePattern = '(?i)\b(?:href|src)\s*=\s*["'']([^"'']+)["'']'
$jsonLdPattern = '(?is)<script\s+type=["'']application/ld\+json["'']>([\s\S]*?)</script>'
foreach ($htmlFile in $htmlFiles) {
  $raw = Get-Content -LiteralPath $htmlFile.FullName -Raw -Encoding UTF8
  if ($raw -match '(?i)\son[a-z]+\s*=') {
    throw "Inline event handler is forbidden: $(Get-PublishRelativePath $htmlFile.FullName)"
  }
  if ($raw -match '(?i)javascript:') {
    throw "javascript: reference is forbidden: $(Get-PublishRelativePath $htmlFile.FullName)"
  }
  if ($raw -match '(?i)<(?:iframe|object|embed|form)\b') {
    throw "Interactive or embedded element is forbidden on this static evidence site: $(Get-PublishRelativePath $htmlFile.FullName)"
  }

  $ids = @{}
  foreach ($idMatch in [regex]::Matches($raw, '(?i)\bid\s*=\s*["'']([^"'']+)["'']')) {
    $ids[$idMatch.Groups[1].Value] = $true
  }
  foreach ($attributeMatch in [regex]::Matches($raw, $attributePattern)) {
    $reference = $attributeMatch.Groups[1].Value
    $linkCount += 1
    if ($reference.StartsWith('#') -and $reference.Length -gt 1) {
      $anchor = $reference.Substring(1)
      if (-not $ids.ContainsKey($anchor)) {
        throw "Missing same-page anchor in $(Get-PublishRelativePath $htmlFile.FullName): #$anchor"
      }
      continue
    }
    $resolved = Resolve-PublishReference -SourceFile $htmlFile -Reference $reference
    if ($null -ne $resolved -and -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Broken local reference in $(Get-PublishRelativePath $htmlFile.FullName): $reference"
    }
  }

  $allScriptCount = [regex]::Matches($raw, '(?is)<script\b').Count
  $jsonLdMatches = [regex]::Matches($raw, $jsonLdPattern)
  if ($allScriptCount -ne $jsonLdMatches.Count) {
    throw "Only application/ld+json script blocks are permitted: $(Get-PublishRelativePath $htmlFile.FullName)"
  }
  foreach ($jsonLdMatch in $jsonLdMatches) {
    $jsonLd = $jsonLdMatch.Groups[1].Value
    try { $null = $jsonLd | ConvertFrom-Json } catch {
      throw "Invalid embedded JSON-LD: $(Get-PublishRelativePath $htmlFile.FullName)"
    }
    $canonicalJsonLd = $jsonLd -replace "`r`n", "`n" -replace "`r", "`n"
    $hashToken = "sha256-$(Get-Sha256Base64 $canonicalJsonLd)"
    if ($headersText -notmatch [regex]::Escape("'$hashToken'")) {
      throw "CSP is missing a JSON-LD hash for $(Get-PublishRelativePath $htmlFile.FullName)"
    }
    $jsonLdHashes += $hashToken
    $jsonLdCount += 1
  }
}

if ($jsonLdCount -lt 1) { throw "No JSON-LD found in published HTML" }
if ($headersText -match "script-src\s+'none'" -or $headersText -match "script-src[^;]*'unsafe-inline'" -or $headersText -match "script-src[^;]*'unsafe-eval'") {
  throw "CSP script policy must allow only reviewed JSON-LD hashes"
}
$cspHashMatches = @([regex]::Matches($headersText, "'sha256-[A-Za-z0-9+/=]+'") | ForEach-Object { $_.Value.Trim("'") } | Sort-Object -Unique)
$expectedHashes = @($jsonLdHashes | Sort-Object -Unique)
if ($cspHashMatches.Count -ne $expectedHashes.Count) { throw "CSP contains stale or missing script hashes" }
foreach ($hash in $expectedHashes) {
  if ($cspHashMatches -notcontains $hash) { throw "CSP hash set does not match embedded JSON-LD" }
}

$styleFiles = @($publishFiles | Where-Object { $_.Extension.ToLowerInvariant() -eq ".css" })
foreach ($styleFile in $styleFiles) {
  $styleText = Get-Content -LiteralPath $styleFile.FullName -Raw -Encoding UTF8
  if ($styleText -match '(?i)fonts\.googleapis\.com|fonts\.gstatic\.com|@font-face') {
    throw "External or bundled web-font declaration is not permitted: $(Get-PublishRelativePath $styleFile.FullName)"
  }
  foreach ($urlMatch in [regex]::Matches($styleText, '(?i)url\(\s*["'']?([^"'')]+)')) {
    $reference = $urlMatch.Groups[1].Value.Trim()
    $resolved = Resolve-PublishReference -SourceFile $styleFile -Reference $reference
    if ($null -ne $resolved -and -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Broken local CSS reference in $(Get-PublishRelativePath $styleFile.FullName): $reference"
    }
  }
}

foreach ($svgFile in ($publishFiles | Where-Object { $_.Extension.ToLowerInvariant() -eq ".svg" })) {
  $svgText = Get-Content -LiteralPath $svgFile.FullName -Raw -Encoding UTF8
  foreach ($hrefMatch in [regex]::Matches($svgText, '(?i)\b(?:href|xlink:href)\s*=\s*["'']([^"'']+)["'']')) {
    $reference = $hrefMatch.Groups[1].Value
    $resolved = Resolve-PublishReference -SourceFile $svgFile -Reference $reference
    if ($null -ne $resolved -and -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Broken local SVG reference in $(Get-PublishRelativePath $svgFile.FullName): $reference"
    }
  }
}

$registryPath = Join-Path $RepoRoot "results/marb_runs.json"
$specPath = Join-Path $RepoRoot "spec/MARB_SCORING.md"
try { $registry = Get-Content -LiteralPath $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw "Invalid results/marb_runs.json" }
$scoringVersion = [string]$registry.marb_scoring_version
$registryCount = @($registry.runs).Count
if ([string]::IsNullOrWhiteSpace($scoringVersion) -or $registryCount -lt 1) { throw "Run registry version/count is missing" }
$specText = Get-Content -LiteralPath $specPath -Raw -Encoding UTF8
if ($specText -notmatch [regex]::Escape("Status: $scoringVersion")) { throw "Scoring spec status and registry version differ" }

$homeText = Get-Content -LiteralPath (Join-Path $Target "index.html") -Raw -Encoding UTF8
$firstResultsText = Get-Content -LiteralPath (Join-Path $Target "first-results/index.html") -Raw -Encoding UTF8
$recapText = Get-Content -LiteralPath (Join-Path $Target "recap/index.html") -Raw -Encoding UTF8
$llmsText = Get-Content -LiteralPath (Join-Path $Target "llms.txt") -Raw -Encoding UTF8
$manifestPath = Join-Path $Target ".well-known/mcp-manifest.json"
$manifestText = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
$manifest = $manifestText | ConvertFrom-Json

foreach ($surface in @($homeText, $firstResultsText, $recapText, $llmsText, $manifestText)) {
  if ($surface -notmatch [regex]::Escape($scoringVersion)) { throw "A public benchmark surface is missing the source-derived scoring version" }
}
if ($llmsText -notmatch [regex]::Escape("registry contains $registryCount records")) { throw "llms.txt registry count is stale" }
if ([int]$manifest.registry_record_count -ne $registryCount) { throw "Discovery registry count is stale" }
if ($manifest.scoring_version -ne $scoringVersion) { throw "Discovery scoring version is stale" }
if ($homeText -notmatch 'site snapshot dated 2026-06-11') { throw "Homepage board snapshot date/boundary is missing" }
if ($recapText -notmatch 'snapshot updated 2026-06-12') { throw "Recap snapshot date/boundary is missing" }

if ($manifest.status -ne "experimental-nonstandard-static-discovery") { throw "MCP discovery status must remain experimental and non-standard" }
if (@($manifest.tools).Count -ne 0) { throw "Static site must not advertise hosted MCP tools" }
if ($manifest.hosted_mcp -ne $false -or $manifest.payments -ne $false -or $manifest.transaction_authority -ne $false) {
  throw "Static discovery must deny hosted MCP, payments, and transaction authority"
}
if ($manifest.commercial_inquiry.creates_order -ne $false) { throw "Commercial inquiry must not create an order" }
foreach ($resource in @($manifest.resources)) {
  if ($resource.method -ne "GET") { throw "Static discovery resources must be read-only GET resources" }
}

$robotsText = Get-Content -LiteralPath (Join-Path $Target "robots.txt") -Raw -Encoding UTF8
if ([regex]::Matches($robotsText, '(?im)^User-agent:\s*\*$').Count -ne 1) { throw "robots.txt must contain one global crawler group" }
if ($robotsText -notmatch '(?im)^Content-Signal:\s*search=yes,\s*ai-input=yes,\s*ai-train=no\s*$') { throw "robots.txt Content-Signal policy is missing or inconsistent" }
if ($headersText -notmatch '(?im)^\s*Content-Signal:\s*search=yes,\s*ai-input=yes,\s*ai-train=no\s*$') { throw "Origin Content-Signal header policy is missing or inconsistent" }
if (($robotsText + "`n" + $headersText) -match '(?i)\buse\s*=') { throw "Undocumented Content-Signal directives are forbidden" }
if ($headersText -notmatch '(?im)^\s*Link:\s*</llms\.txt>;\s*rel="alternate";\s*type="text/plain"\s*$') { throw "llms.txt discovery Link header is missing" }
$requiredHeaders = @("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Permissions-Policy", "Strict-Transport-Security", "Content-Security-Policy")
foreach ($headerName in $requiredHeaders) {
  if ($headersText -notmatch ("(?im)^\s*" + [regex]::Escape($headerName) + ":")) { throw "Missing required security header: $headerName" }
}
foreach ($machinePath in @("/llms.txt", "/robots.txt", "/sitemap.xml", "/.well-known/mcp-manifest.json", "/.well-known/security.txt")) {
  if ($headersText -notmatch ("(?m)^" + [regex]::Escape($machinePath) + "\s*$")) { throw "Missing explicit machine-resource header block: $machinePath" }
}

$securityText = Get-Content -LiteralPath (Join-Path $Target ".well-known/security.txt") -Raw -Encoding UTF8
$expiresMatch = [regex]::Match($securityText, '(?im)^Expires:\s*(\S+)\s*$')
if (-not $expiresMatch.Success) { throw "security.txt is missing Expires" }
try { $securityExpiry = [DateTimeOffset]::Parse($expiresMatch.Groups[1].Value) } catch { throw "security.txt Expires is invalid" }
if ($securityExpiry -le [DateTimeOffset]::UtcNow.AddDays(30)) { throw "security.txt expires in 30 days or less" }

$notFoundText = Get-Content -LiteralPath (Join-Path $Target "404.html") -Raw -Encoding UTF8
if ($notFoundText -notmatch '(?i)<meta\s+name="robots"\s+content="noindex') { throw "404.html must be noindex" }
if (Test-Path -LiteralPath (Join-Path $Target "_redirects") -PathType Leaf) {
  $redirectsText = Get-Content -LiteralPath (Join-Path $Target "_redirects") -Raw -Encoding UTF8
  if ($redirectsText -match '(?m)^\s*/\*\s+') { throw "Catch-all redirect would defeat native 404 behavior" }
}

$claimSurfaces = (@($htmlFiles | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 }) -join "`n") + "`n" + $llmsText + "`n" + $manifestText
$blockedClaimPatterns = @(
  '(?i)first cross-tool',
  '(?i)first scored',
  '(?i)industry already trusts',
  '(?i)candidate standard',
  '(?i)proprietary engine',
  '(?i)1-10-100',
  '(?i)positive expected value',
  '(?i)paying quadruple',
  '(?i)only variable',
  '(?i)from one picture',
  '(?i)from one photo',
  '(?i)sealed session',
  '(?i)the most careful check',
  '(?i)cannot reveal units',
  '(?i)value corner',
  '(?i)grading ceiling',
  '(?i)none buildable yet',
  '(?i)every result on this site traces',
  '(?i)full provenance'
)
foreach ($pattern in $blockedClaimPatterns) {
  if ($claimSurfaces -match $pattern) { throw "Blocked stale public-claim pattern remains" }
}

$hashCount = 0
foreach ($outputFile in $publishFiles) {
  $relative = Get-PublishRelativePath $outputFile.FullName
  $sourceFile = Join-Path $SiteRoot $relative
  if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) { throw "Output lacks a source counterpart: $relative" }
  $sourceHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
  $outputHash = (Get-FileHash -LiteralPath $outputFile.FullName -Algorithm SHA256).Hash
  if ($sourceHash -ne $outputHash) { throw "Source/output hash mismatch: $relative" }
  $hashCount += 1
}

$bytes = ($publishFiles | Measure-Object -Property Length -Sum).Sum
$releaseMode = if ($Release) { "yes" } else { "no" }
$rightsState = if ($rights.release_authorized -eq $true) { "authorized" } else { "hold-active" }
Write-Output "marb publish dir ready: $Target"
Write-Output "Release mode: $releaseMode"
Write-Output "Rights state: $rightsState"
Write-Output "Files: $($publishFiles.Count)"
Write-Output "Bytes: $bytes"
Write-Output "HTML: $($htmlFiles.Count)"
Write-Output "JSON: $($jsonFiles.Count)"
Write-Output "JSON-LD: $jsonLdCount"
Write-Output "Links checked: $linkCount"
Write-Output "Sitemap URLs: $($sitemapUrls.Count)"
Write-Output "Registry records: $registryCount"
Write-Output "Scoring version: $scoringVersion"
Write-Output "Source/output hashes: $hashCount/$($publishFiles.Count)"
