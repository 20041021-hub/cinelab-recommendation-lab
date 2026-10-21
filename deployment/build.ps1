# Online development only. Stage A imports/initialization remain unchanged.
# Reuses 4E's minimal ASCII temporary build context and existing Dockerfiles.
param(
    [ValidateSet('all','backend','frontend')][string]$Target = 'all',
    [ValidateSet('Build','Start')][string]$Action = 'Build'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/offline/runtime.ps1"
Read-CourseRelease
Open-CourseRuntime -RequireSaved
Assert-CourseEngine
Assert-CourseImages
Assert-CourseConfiguration
$statePath = Join-Path $PSScriptRoot 'local.development.json'
$context = Join-Path ([IO.Path]::GetTempPath()) ('cinelab-build-' + [Guid]::NewGuid().ToString('N'))
if ($context -match '[^\x00-\x7F]') { throw 'BuildKit needs an ASCII TEMP directory. Set TEMP/TMP to an existing private ASCII directory.' }
$images = @{}
try {
    New-Item -ItemType Directory -Path $context | Out-Null
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.schema -ne 'cinelab-development-v1' -or $saved.project -cne $script:Runtime.project -or $saved.engineId -cne $script:EngineId) { throw 'Development state identity mismatch. Preserve the state and ask the teacher.' }
        foreach ($entry in $saved.images) {
            if ($entry.service -notin @('api','web') -or $images.ContainsKey($entry.service)) { throw 'Invalid development service.' }
            $prefix = 'cinelab-student/' + $script:Runtime.project + '-' + $entry.service + ':'
            if ($entry.reference -cnotmatch ('^' + [regex]::Escape($prefix) + '[a-f0-9]{32}$')) { throw 'Invalid development image reference.' }
            $actual = Get-CourseImage $entry.reference
            if ($null -eq $actual -or $actual.Id -cne $entry.id -or $actual.Os -ne 'linux' -or $actual.Architecture -ne 'amd64') { throw 'Saved development image missing or changed. Preserve runtime settings and contact the teacher.' }
            $images[$entry.service] = $entry
        }
    }
    if ($Action -eq 'Start' -and $images.Count -eq 0) { throw 'Build student code first with -Action Build.' }
    if ($Action -eq 'Build') {
        # Only explicit build inputs; data, secrets, caches and teacher material excluded.
        $files = @()
        foreach ($dir in @('backend','recommender')) {
            $files += @(Get-ChildItem (Join-Path $script:CourseRoot $dir) -Recurse -File -Filter '*.py')
        }
        $files += @(Get-ChildItem (Join-Path $script:CourseRoot 'backend/migrations') -File -Filter '*.sql')
        foreach ($dir in @('frontend/src','frontend/public')) {
            $files += @(Get-ChildItem (Join-Path $script:CourseRoot $dir) -Recurse -File)
        }
        $items = @('.dockerignore','backend/requirements.lock.txt','deployment/requirements.lock.txt',
            'deployment/backend.Dockerfile','deployment/frontend.Dockerfile','deployment/nginx.conf',
            'scripts/student_package.py','frontend/package.json','frontend/package-lock.json',
            'frontend/index.html','frontend/vite.config.ts','frontend/tsconfig.json')
        $files += @(Get-ChildItem $PSScriptRoot -File -Filter '*.py')
        foreach ($relative in $items) { $files += Get-Item -LiteralPath (Join-Path $script:CourseRoot $relative) }
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($script:CourseRoot.Length + 1).Replace('\','/')
            if ($relative -match '(^|/)(private|node_modules|__pycache__|\.venv[^/]*|\.env[^/]*)(/|$)') { continue }
            # Reject reparse points at any level, including junction parents.
            $cursor = $file
            while ($cursor.FullName -ne $script:CourseRoot) {
                if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Symlink/junction build input is not supported.' }
                $cursor = Get-Item -LiteralPath (Split-Path $cursor.FullName -Parent)
            }
            $destination = Join-Path $context $relative
            New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $destination
        }
        $targets = if ($Target -eq 'all') { @('backend','frontend') } else { @($Target) }
        foreach ($name in $targets) {
            $service = if ($name -eq 'backend') { 'api' } else { 'web' }
            $tag = 'cinelab-student/' + $script:Runtime.project + '-' + $service + ':' + [Guid]::NewGuid().ToString('N')
            if ($null -ne (Get-CourseImage $tag)) { throw 'New development tag already exists. No tag was overwritten.' }
            Invoke-CourseDocker build --platform linux/amd64 --file (Join-Path $context "deployment/$name.Dockerfile") --tag $tag $context
            $actual = Get-CourseImage $tag
            if ($null -eq $actual -or $actual.Os -ne 'linux' -or $actual.Architecture -ne 'amd64') { throw 'Build did not produce a Linux/amd64 image.' }
            $images[$service] = [pscustomobject]@{service=$service;reference=$tag;id=$actual.Id}
        }
    }
    # Recheck ownership immediately before applying the development images.
    Assert-CourseOwnership
    $override = "services:`n"
    foreach ($service in @('api','web')) {
        if ($images.ContainsKey($service)) { $override += "  ${service}:`n    image: $($images[$service].reference)`n" }
    }
    $overridePath = Join-Path $context 'development.yaml'
    [IO.File]::WriteAllText($overridePath, $override, [Text.UTF8Encoding]::new($false))
    # No initialize override, build instruction, volume removal or credential change.
    Invoke-CourseDocker compose --project-directory $script:CourseRoot --env-file (Join-Path $script:OfflineDir 'runtime.env') --file (Join-Path $script:CourseRoot 'compose.yaml') --file $overridePath --project-name $script:Runtime.project up -d --no-build --pull never --wait --wait-timeout 300 api web
    $state = [pscustomobject]@{schema='cinelab-development-v1';project=$script:Runtime.project;engineId=$script:EngineId;images=@($images.Values)}
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Write-Output "Student code started: http://127.0.0.1:$($script:Runtime.port). Stop with 05; resume with build.ps1 -Action Start. 04 restores approved Stage A images."
} finally {
    # Only this invocation's randomly named temporary directory is removed.
    if (Test-Path -LiteralPath $context) { Remove-Item -LiteralPath $context -Recurse -Force }
}
