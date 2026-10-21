# Shared Windows PowerShell 5 runtime guards. No builds, downloads, or cleanup.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -eq 'Windows_NT') {
    $env:PATHEXT = '.COM;.EXE;.BAT;.CMD;' + $env:PATHEXT
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
}
$script:DockerProgram = if ($env:OS -eq 'Windows_NT') { 'docker.exe' } else { 'docker' }
$script:CourseRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script:OfflineDir = $PSScriptRoot
$script:ReleasePath = Join-Path $PSScriptRoot 'release.json'
$script:SettingsPath = Join-Path $script:CourseRoot 'deployment/local.runtime.json'
$script:ExpectedRelease = 'cinelab-win-x64-1.3.0-a1'
$script:ExpectedManifest = 'fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e'

function Invoke-CourseDocker {
    & $script:DockerProgram --context $script:Runtime.context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). No reset or deletion was attempted." }
}
function Get-CourseHash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-CourseRelease {
    if (-not (Test-Path -LiteralPath $script:ReleasePath -PathType Leaf)) { throw 'Release manifest missing. Obtain the complete offline candidate from the teacher.' }
    $script:Release = Get-Content -LiteralPath $script:ReleasePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($script:Release.schema -ne 'cinelab-offline-release-v1' -or $script:Release.release -ne $script:ExpectedRelease -or $script:Release.platform -ne 'linux/amd64' -or $script:Release.datasetManifestSha256 -ne $script:ExpectedManifest) { throw 'Release identity/platform mismatch.' }
    $expected = @('cinelab-offline/api:1.3.0-a1','cinelab-offline/web:1.3.0-a1','cinelab-offline/mysql:8.0.46-a1')
    if (@($script:Release.images).Count -ne 3) { throw 'Exactly three approved runtime images are required.' }
    foreach ($tag in $expected) {
        if (@($script:Release.images | Where-Object { $_.reference -ceq $tag }).Count -ne 1) { throw "Unapproved release image set: $tag" }
    }
    foreach ($item in $script:Release.files) {
        if ($item.path -match '(^/|\\|:|(^|/)\.\.(/|$))') { throw 'Unsafe release input path.' }
        $path = Join-Path $script:CourseRoot $item.path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-CourseHash $path) -ne $item.sha256) { throw "Release tool/input mismatch: $($item.path). Restore the approved files." }
    }
    $dataManifest = Join-Path $script:CourseRoot 'data/packages/rec-course-v3-train5-1.0.0/manifest.json'
    if (-not (Test-Path -LiteralPath $dataManifest) -or (Get-CourseHash $dataManifest) -ne $script:ExpectedManifest) { throw 'Approved data manifest mismatch.' }
}
function Open-CourseRuntime([switch]$RequireSaved) {
    if (Test-Path -LiteralPath $script:SettingsPath) {
        $script:Runtime = Get-Content -LiteralPath $script:SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($script:Runtime.schema -ne 'cinelab-runtime-v1' -or $script:Runtime.release -ne $script:ExpectedRelease) { throw 'Saved runtime release mismatch. Keep the original settings, volume, and secrets.' }
        foreach ($pair in @(@('COMPOSE_PROJECT_NAME','project'),@('COURSE_WEB_PORT','port'),@('COURSE_SECRET_DIR','secretDir'),@('DOCKER_CONTEXT','context'))) {
            $value = [Environment]::GetEnvironmentVariable($pair[0], 'Process')
            if ($value -and $value -ne [string]$script:Runtime.($pair[1])) { throw "Environment conflicts with saved runtime: $($pair[0]). Use the original settings." }
        }
    } else {
        if ($RequireSaved) { throw 'Import offline images first; deployment/local.runtime.json is missing.' }
        $context = & $script:DockerProgram context show
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read Docker context.' }
        $script:Runtime = [pscustomobject][ordered]@{
            schema='cinelab-runtime-v1'; release=$script:ExpectedRelease
            project=$(if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'cinelab' })
            port=$(if ($env:COURSE_WEB_PORT) { $env:COURSE_WEB_PORT } else { '8080' })
            secretDir=$(if ($env:COURSE_SECRET_DIR) { $env:COURSE_SECRET_DIR } else { './deployment/private' })
            context=($context -join '').Trim(); engineId=''
        }
    }
    if ($script:Runtime.project -notmatch '^[a-z0-9][a-z0-9_-]*$') { throw 'Invalid Compose project name.' }
    if ([string]$script:Runtime.port -notmatch '^\d{1,5}$' -or [int]$script:Runtime.port -lt 1024 -or [int]$script:Runtime.port -gt 65535) { throw 'Choose a port from 1024 to 65535.' }
    if ([string]::IsNullOrWhiteSpace($script:Runtime.context) -or [string]::IsNullOrWhiteSpace($script:Runtime.secretDir)) { throw 'Invalid runtime context/secret directory.' }
    $env:COURSE_WEB_PORT = [string]$script:Runtime.port
    $env:COURSE_SECRET_DIR = [string]$script:Runtime.secretDir
    # Profiles inherited from another shell must not activate one-shot tools.
    $env:COMPOSE_PROFILES = ''
}
function Assert-CourseEngine {
    $infoText = Invoke-CourseDocker info --format '{{json .}}'
    $info = ($infoText -join "`n") | ConvertFrom-Json
    if ($info.OSType -ne 'linux' -or $info.Architecture -notin @('x86_64','amd64')) { throw 'Only a Linux/amd64 Docker Engine is supported. Do not enable emulation.' }
    if ($script:Runtime.engineId -and $script:Runtime.engineId -ne $info.ID) { throw 'Docker Engine identity changed. Refusing to attach this project to another Engine.' }
    $script:EngineId = $info.ID
    Invoke-CourseDocker compose version
}
function Save-CourseRuntime {
    if (Test-Path -LiteralPath $script:SettingsPath) { return }
    $script:Runtime.engineId = $script:EngineId
    $text = $script:Runtime | ConvertTo-Json -Depth 5
    # CreateNew prevents accidental replacement of an existing project identity.
    $stream = [IO.File]::Open($script:SettingsPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $bytes = [Text.UTF8Encoding]::new($false).GetBytes($text); $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
}
function Invoke-CourseCompose {
    Invoke-CourseDocker compose --project-directory $script:CourseRoot --env-file (Join-Path $script:OfflineDir 'runtime.env') --file (Join-Path $script:CourseRoot 'compose.yaml') --project-name $script:Runtime.project @args
}
function Get-CourseImage([string]$Reference) {
    $found = & $script:DockerProgram --context $script:Runtime.context image ls --quiet --no-trunc $Reference
    if ($LASTEXITCODE -ne 0) { throw 'Image inventory failed.' }
    if (-not $found) { return $null }
    $raw = Invoke-CourseDocker image inspect $Reference
    return @(($raw -join "`n") | ConvertFrom-Json)[0]
}
function Assert-CourseImage($Actual, $Expected) {
    if ($null -eq $Actual) { throw "Missing offline image: $($Expected.reference). Import images; no pull/build fallback." }
    if ($Actual.Os -ne 'linux' -or $Actual.Architecture -ne 'amd64') { throw "Wrong image platform: $($Expected.reference)" }
    if ($Actual.Id -cne $Expected.id) { throw "Image SHA-256 conflict: $($Expected.reference). Existing tag was not overwritten." }
    if (@($Actual.RootFS.Layers).Count -ne @($Expected.layers).Count -or (@($Actual.RootFS.Layers) -join ',') -cne (@($Expected.layers) -join ',')) { throw "Image layer identity mismatch: $($Expected.reference)" }
    if ($Expected.role -ne 'mysql') {
        $labels = $Actual.Config.Labels
        if ($null -eq $labels -or $labels.'org.cinelab.release' -cne $script:ExpectedRelease -or $labels.'org.cinelab.source-sha256' -cne $script:Release.sourceSha256 -or $labels.'org.cinelab.role' -cne $Expected.role) { throw "Image release labels mismatch: $($Expected.reference)" }
    }
}
function Assert-CourseImages {
    foreach ($image in $script:Release.images) { Assert-CourseImage (Get-CourseImage $image.reference) $image }
}
function Assert-CourseOwnership {
    $ids = Invoke-CourseDocker ps -aq --filter "label=com.docker.compose.project=$($script:Runtime.project)"
    foreach ($id in @($ids)) {
        if (-not $id) { continue }
        $raw = Invoke-CourseDocker inspect --format '{{json .Config.Labels}}' $id
        $labels = ($raw -join "`n") | ConvertFrom-Json
        $working = $labels.'com.docker.compose.project.working_dir'
        if (-not $working -or [IO.Path]::GetFullPath($working).TrimEnd('\','/') -ine [IO.Path]::GetFullPath($script:CourseRoot).TrimEnd('\','/')) { throw 'Compose project name is already owned by another checkout. Choose a new project before first import.' }
    }
    $volumeName = "$($script:Runtime.project)_mysql_data"
    $volumes = Invoke-CourseDocker volume ls --quiet --filter "name=^${volumeName}$"
    if ($volumes) {
        $raw = Invoke-CourseDocker volume inspect $volumeName
        $volume = @(($raw -join "`n") | ConvertFrom-Json)[0]
        if ($null -eq $volume.Labels -or $volume.Labels.'com.docker.compose.project' -cne $script:Runtime.project -or $volume.Labels.'com.docker.compose.volume' -cne 'mysql_data') { throw 'Database volume ownership mismatch.' }
        $secretDir = $script:Runtime.secretDir
        if (-not [IO.Path]::IsPathRooted($secretDir)) { $secretDir = Join-Path $script:CourseRoot $secretDir }
        foreach ($name in @('mysql-root-password','app-password','jwt-secret')) {
            if (-not (Test-Path -LiteralPath (Join-Path $secretDir $name) -PathType Leaf)) { throw 'An existing database volume requires its complete original secret set. Refusing to generate replacements.' }
        }
    }
}
function Assert-CourseConfiguration {
    $raw = Invoke-CourseCompose --profile tools config --format json
    $config = ($raw -join "`n") | ConvertFrom-Json
    foreach ($name in @('db','api','web','initialize')) {
        $service = $config.services.$name
        if ($service.PSObject.Properties.Name -contains 'build') { throw 'Runtime Compose must not contain build instructions.' }
        $expectedRole = if ($name -eq 'initialize') { 'api' } elseif ($name -eq 'db') { 'mysql' } else { $name }
        $expected = @($script:Release.images | Where-Object { $_.role -ceq $expectedRole })
        if ($expected.Count -ne 1 -or $service.image -cne $expected[0].reference -or $service.platform -ne 'linux/amd64' -or $service.pull_policy -ne 'never') { throw "Unsafe runtime image configuration: $name" }
    }
    Assert-CourseOwnership
}
