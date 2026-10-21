# Verify before first use, against the PACKAGE-CHECKSUMS.json hash supplied by teacher.
param([string]$Root=$PSScriptRoot)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Root=(Resolve-Path -LiteralPath $Root).Path
$manifest=Get-Content -LiteralPath (Join-Path $Root 'PACKAGE-CHECKSUMS.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($manifest.schema -ne 'cinelab-student-package-v1' -or $manifest.release -ne 'cinelab-win-x64-1.3.0-a1'){throw 'Wrong student package identity.'}
foreach($item in $manifest.files){
    if($item.path -match '(^/|\\|:|(^|/)\.\.(/|$))'){throw 'Unsafe package path.'}
    $path=Join-Path $Root $item.path
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing package file: $($item.path)"}
    if((Get-Item -LiteralPath $path).Length -ne [long]$item.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $item.sha256){throw "Package SHA-256 mismatch: $($item.path)"}
}
Write-Output "PASS: $(@($manifest.files).Count) package files verified. This verifies bytes, not Windows/offline acceptance."
