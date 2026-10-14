# Exact-byte release exception for one authored-hand diagnostic route.
$UsdBrepRoute = "robotic-hand/usd-brep-comparison"
$UsdBrepCsp = "default-src 'none'; base-uri 'none'; connect-src 'self'; font-src 'self'; form-action 'none'; frame-ancestors 'none'; img-src 'self' data:; manifest-src 'none'; media-src 'none'; object-src 'none'; script-src 'self'; style-src 'self'; worker-src 'none'; upgrade-insecure-requests"
$UsdBrepInventorySha256 = "fa09302673e9ce8adce738d74aad9247ddeb73bdc497c25ba0f180a97146a790"

function Get-UsdBrepTextSha256 {
  param([string]$Text)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '').ToLowerInvariant() }
  finally { $algorithm.Dispose() }
}

function Assert-UsdBrepComparisonRelease {
  param([string]$RepoRoot, $Rights)
  $directory = Join-Path $RepoRoot "publishing/$UsdBrepRoute"
  $records = @(if ($Rights.PSObject.Properties.Name -contains "interactive_artifact_releases") { $Rights.interactive_artifact_releases })
  if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
    if ($records.Count -gt 0) { throw "Reviewed USD BRep route is missing" }
    return [pscustomobject]@{ Enabled = $false }
  }
  if ($records.Count -ne 1) { throw "USD BRep route needs one exact reviewed release record" }
  $record = $records[0]
  if ($record.id -ne "halden-authored-usd-brep-diagnostic-20261014" -or $record.route -ne "/$UsdBrepRoute/" -or
      $record.owner -ne "Sunnyday Technologies" -or $record.reviewed_on -ne "2026-10-14" -or
      $record.source_geometry_included -ne $true -or $record.private_evaluator_answer_key_files_included -ne $false -or
      $record.benchmark_scores_changed -ne $false -or $record.NVIDIA_drop_in_ready -ne $false -or
      $record.source_step_sha256 -ne "356ed1fd967696bd9bf9393260950db2556649fd867ea5f52cf81e381721e64d" -or
      $record.matching_source_commit -ne "edbd541b1a9d2e9eb7eb5706309239aa3d94e2e6" -or
      $record.measured_candidate_commit -ne "123aad33e6423145a4fc6f1ee0c889938a9b0e0c") {
    throw "USD BRep release identity or scope differs from the reviewed diagnostic"
  }
  $artifacts = @{}; $inventoryLines = @()
  foreach ($artifact in @($record.artifacts)) {
    $relative = [string]$artifact.path
    if ($relative -notmatch '^publishing/robotic-hand/usd-brep-comparison/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*$' -or
        [IO.Path]::GetExtension($relative).ToLowerInvariant() -notin @('.html', '.css', '.js', '.json', '.txt', '.bin', '.usda') -or
        $artifact.sha256 -notmatch '^[0-9a-f]{64}$' -or $artifact.bytes -is [bool] -or
        ($artifact.bytes -isnot [int] -and $artifact.bytes -isnot [long]) -or $artifact.bytes -lt 1 -or
        $artifact.bytes -gt 25MB -or $artifacts.ContainsKey($relative)) { throw "Invalid USD BRep asset inventory entry" }
    $path = Join-Path $RepoRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne $artifact.bytes -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifact.sha256) { throw "Unreviewed USD BRep asset bytes: $relative" }
    $artifacts[$relative] = $artifact
    $inventoryLines += "$relative`t$($artifact.sha256)`t$($artifact.bytes)"
  }
  $inventory = ($inventoryLines -join "`n") + "`n"
  if ($artifacts.Count -ne 17 -or $record.inventory_sha256 -ne $UsdBrepInventorySha256 -or
      (Get-UsdBrepTextSha256 $inventory) -ne $UsdBrepInventorySha256) { throw "USD BRep inventory is not the exact reviewed release" }
  $items = @(Get-ChildItem -LiteralPath $directory -Recurse -Force)
  if (@($items | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) { throw "USD BRep route contains a reparse point" }
  $files = @($items | Where-Object { -not $_.PSIsContainer })
  if ($files.Count -ne $artifacts.Count) { throw "USD BRep route inventory is incomplete" }
  foreach ($file in $files) {
    $relative = $file.FullName.Substring($RepoRoot.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
    if (-not $artifacts.ContainsKey($relative)) { throw "Unreviewed USD BRep route file" }
    if ($file.Extension -in @('.html', '.json', '.usda') -or ($file.Extension -eq '.js' -and $relative -notmatch '/vendor/')) {
      $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
      if ($text -match '(?i)GX10-2|localhost|127\.0\.0\.1|[A-Za-z]:\\|/home/|/Users/|TensorSlice') { throw "Private identifier or local path in USD BRep route: $relative" }
    }
  }
  foreach ($notice in @($record.third_party_notices)) {
    if (-not $artifacts.ContainsKey([string]$notice) -or [IO.Path]::GetExtension($notice) -ne '.txt') { throw "USD BRep third-party notice is outside the reviewed inventory" }
  }
  if (@($record.third_party_notices).Count -ne 2) { throw "USD BRep third-party notices are incomplete" }
  $entry = [string]$record.entry_module.url
  if ($entry -notmatch '^\./viewer\.js\?v=[0-9a-f]{16}$' -or $record.entry_module.integrity -notmatch '^sha256-[A-Za-z0-9+/=]+$') { throw "Invalid USD BRep entry module" }
  $viewer = Join-Path $directory 'viewer.js'
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { $entrySri = 'sha256-' + [Convert]::ToBase64String($algorithm.ComputeHash([IO.File]::ReadAllBytes($viewer))) } finally { $algorithm.Dispose() }
  if ($record.entry_module.integrity -ne $entrySri -or -not $entry.EndsWith(((Get-FileHash -LiteralPath $viewer -Algorithm SHA256).Hash.ToLowerInvariant()).Substring(0,16))) { throw "USD BRep entry SRI or cache key mismatch" }
  $gx10 = Get-Content -LiteralPath (Join-Path $directory 'assets/gx10-metrics.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($gx10.source_step_sha256 -ne $record.source_step_sha256 -or $gx10.code_revisions.candidate -ne $record.measured_candidate_commit -or
      $gx10.image_gate.passed -ne $false -or $gx10.image_gate.pairs_passed -ne 2 -or
      $gx10.backend_comparison[2].nvidia.groups_failed[0] -ne 47 -or $gx10.palm_mesh.gx10_ARM_spine_edges -ne 388 -or
      $gx10.palm_mesh.historical_Windows_spine_edges -ne 1356) { throw "USD BRep measurements or retained failure boundaries changed" }
  $geometryAssets = @($record.artifacts | Where-Object { [IO.Path]::GetExtension($_.path) -in @('.bin', '.usda') } | ForEach-Object { $_.path.Substring('publishing/'.Length) })
  return [pscustomobject]@{ Enabled = $true; EntryUrl = $entry; EntrySri = $entrySri; ApprovedGeometryAssets = $geometryAssets }
}

function Assert-UsdBrepComparisonMarkup {
  param([string]$Html, $Release)
  $scripts = @([regex]::Matches($Html, '(?is)<script\b([^>]*)>(.*?)</script>'))
  if ($scripts.Count -ne 1 -or $scripts[0].Groups[2].Value.Trim().Length -ne 0) { throw "USD BRep page needs one external reviewed entry module" }
  $expected = '<script type="module" src="' + $Release.EntryUrl + '" integrity="' + $Release.EntrySri + '" crossorigin="anonymous"></script>'
  if ($scripts[0].Value -ne $expected) { throw "USD BRep entry module markup differs from the reviewed SRI tag" }
  if ($Html -match '(?i)<meta[^>]+http-equiv=["'']Content-Security-Policy') { throw "USD BRep route must use its one response-header CSP, not a second meta policy" }
  foreach ($boundary in @('noindex,nofollow', 'EXPERIMENTAL', 'UNSCORED', 'qualification failed', 'OCP is the default', 'not running in this browser')) {
    if ($Html -notmatch [regex]::Escape($boundary)) { throw "USD BRep page is missing a reviewed scope boundary" }
  }
  return 1
}

function Assert-UsdBrepComparisonHeaders {
  param([string]$Headers)
  $blocks = @([regex]::Matches($Headers, '(?m)^([^\s#][^\r\n]*)\r?\n((?:[ \t]+[^\r\n]*(?:\r?\n|$))+)'))
  $found = 0
  foreach ($block in $blocks) {
    $route = $block.Groups[1].Value.Trim(); $body = $block.Groups[2].Value
    if ($route -eq "/$UsdBrepRoute/*") {
      $lines = @($body -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
      if ($lines.Count -ne 2 -or $lines[0] -ne '! Content-Security-Policy' -or $lines[1] -ne "Content-Security-Policy: $UsdBrepCsp") { throw "USD BRep route must detach then set exactly its reviewed response CSP" }
      $found += 1
    } elseif ($body -match '(?mi)^\s*!\s*Content-Security-Policy' -or $body -match "script-src[^;]*'self'") { throw "USD BRep CSP exception escaped its route" }
  }
  if ($found -ne 1) { throw "USD BRep route needs one scoped CSP replacement" }
}
