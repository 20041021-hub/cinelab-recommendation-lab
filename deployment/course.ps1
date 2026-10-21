param(
    [ValidateSet('prepare','check','initialize','start','stop','status')]
    [string]$Action = 'check'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/offline/runtime.ps1"
Read-CourseRelease
Open-CourseRuntime -RequireSaved
Push-Location $script:CourseRoot


function Protect-Secrets([string]$dir, [string[]]$names) {
    if ($env:OS -ne 'Windows_NT') { return }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $aclTool = "$env:SystemRoot\System32\icacls.exe"
    & $aclTool $dir /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Secret directory ACL setup failed.' }
    foreach ($name in $names) {
        # Files need direct rights; directory inheritance flags do not grant file access.
        & $aclTool (Join-Path $dir $name) /inheritance:r /grant:r "${identity}:F" 'SYSTEM:F' /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Secret file ACL setup failed. Do not continue.' }
    }
}

function Check-Environment {
    Assert-CourseEngine
    Assert-CourseImages
    Assert-CourseConfiguration
    Write-Output 'Offline release, image identities, Engine, Compose and data manifest checks passed.'
}

try {
    if ($Action -eq 'prepare') {
        Check-Environment
        $dir = if ($env:COURSE_SECRET_DIR) { $env:COURSE_SECRET_DIR } else { './deployment/private' }
        $names = @('mysql-root-password','app-password','jwt-secret')
        $present = @($names | Where-Object { Test-Path (Join-Path $dir $_) })
        if ($present.Count -gt 0 -and $present.Count -ne 3) {
            throw 'Partial secrets exist. Restore the complete original secret set; no files were overwritten.'
        }
        if ($present.Count -eq 3) {
            Protect-Secrets $dir $names
            foreach ($name in $names) {
                if ([Text.Encoding]::UTF8.GetByteCount([IO.File]::ReadAllText((Join-Path (Resolve-Path $dir) $name)).Trim()) -lt 32) {
                    throw 'Existing secret is invalid. No automatic credential rotation.'
                }
            }
            Write-Output 'Existing secrets preserved. Do not regenerate secrets for an existing volume.'
        } else {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try {
                foreach ($name in $names) {
                    $bytes = New-Object byte[] 32
                    $rng.GetBytes($bytes)
                    $value = -join ($bytes | ForEach-Object { $_.ToString('x2') })
                    $path = Join-Path (Resolve-Path $dir) $name
                    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
                    try { $content = [Text.Encoding]::ASCII.GetBytes($value); $stream.Write($content,0,$content.Length) }
                    finally { $stream.Dispose() }
                }
            } finally { $rng.Dispose() }
            Write-Output 'Three independent random secrets created. Back up this directory privately with the database volume.'
        }
        Protect-Secrets $dir $names
    } elseif ($Action -eq 'check') {
        Check-Environment
    } elseif ($Action -eq 'initialize') {
        Check-Environment
        Invoke-CourseCompose up -d --no-build --pull never --wait --wait-timeout 300 db
        Invoke-CourseCompose run --rm --no-deps --pull never initialize --confirm-business-initialize
        Write-Output 'Explicit initialization completed from verified offline images. Daily startup: 04 BAT.'
    } elseif ($Action -eq 'start') {
        Check-Environment
        Invoke-CourseCompose up -d --no-build --pull never --wait --wait-timeout 300 db api web
    } elseif ($Action -eq 'stop') {
        Assert-CourseEngine
        Assert-CourseOwnership
        Invoke-CourseCompose stop db api web
        Write-Output 'Services stopped; the database volume and secrets are preserved.'
    } else {
        Assert-CourseEngine
        Assert-CourseOwnership
        Invoke-CourseCompose ps -a
    }
} finally { Pop-Location }
