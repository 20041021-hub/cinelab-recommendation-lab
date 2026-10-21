param(
    [ValidateSet('import','verify','check')][string]$Action = 'verify',
    [string]$ArchiveDirectory = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$exitCode = 60
try {
    . "$PSScriptRoot/runtime.ps1"
    Read-CourseRelease
    Open-CourseRuntime
    Assert-CourseEngine
    Assert-CourseOwnership
    if ($Action -eq 'import') {
        if (-not $ArchiveDirectory) { $ArchiveDirectory = Join-Path $script:CourseRoot 'offline-images' }
        $ArchiveDirectory = (Resolve-Path -LiteralPath $ArchiveDirectory).Path
        # Validate EVERY archive and tag before the first mutating Docker call.
        foreach ($image in $script:Release.images) {
            if ($image.archive -notmatch '^[a-z0-9][a-z0-9_.-]*\.tar$' -or $image.archiveSha256 -notmatch '^[a-f0-9]{64}$' -or $image.id -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Invalid image archive identity.' }
            $archive = Join-Path $ArchiveDirectory $image.archive
            if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw "Missing archive: $($image.archive)" }
            if ((Get-Item -LiteralPath $archive).Length -ne [long]$image.archiveBytes -or (Get-CourseHash $archive) -cne $image.archiveSha256) { throw "Archive SHA-256/size mismatch: $($image.archive). No image was imported." }
            $actual = Get-CourseImage $image.reference
            if ($null -ne $actual) { Assert-CourseImage $actual $image }
        }
        foreach ($image in $script:Release.images) {
            if ($null -eq (Get-CourseImage $image.reference)) {
                Invoke-CourseDocker image load --input (Join-Path $ArchiveDirectory $image.archive)
            } else { Write-Output "Verified existing image; skipped: $($image.reference)" }
            Assert-CourseImage (Get-CourseImage $image.reference) $image
        }
        Assert-CourseImages
        Save-CourseRuntime
        Write-Output "Offline import passed. Release=$($script:Release.release); Project=$($script:Runtime.project); Context=$($script:Runtime.context). No secrets, containers, or database were created."
    } elseif ($Action -eq 'verify') {
        Assert-CourseImages
        Write-Output 'All runtime image SHA-256, layers, platform and release identities verified.'
    } else {
        Write-Output 'Release inputs and Linux/amd64 Engine verified. No import/build/pull was attempted.'
    }
    exit 0
} catch {
    Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No automatic tag replacement, image removal, volume deletion, credential rotation, or Docker/WSL reset.'
    exit $exitCode
}
