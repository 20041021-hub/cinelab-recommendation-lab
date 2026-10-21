param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('environment','files','import','initialize','start','stop','diagnose')]
    [string]$Action,
    [string]$ArchiveDirectory = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:FailureCode = 10
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$course = Join-Path $root 'deployment/course.ps1'
$powershell = Join-Path $PSHOME 'powershell.exe'
. (Join-Path $root 'deployment/offline/runtime.ps1')

function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败，原退出码=$LASTEXITCODE；已停止后续步骤。"
    }
}
function Course([string]$Step) {
    $script:FailureCode = 40
    Write-Host "[执行4E工具] course.ps1 $Step"
    Run-Native $powershell @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$course,'-Action',$Step)
}
function Engine {
    $script:FailureCode = 10
    if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
        throw '未找到Docker CLI。安装Docker Desktop后重新打开窗口；不要安装Ubuntu。'
    }
    Open-CourseRuntime
    Assert-CourseEngine
    Write-Host '[通过] Docker Engine为Linux，Compose可用。WSL2后端仍需在Desktop设置中人工确认。'
}
function Files {
    $script:FailureCode = 20
    $required = @('deployment/offline/runtime.ps1','deployment/offline/images.ps1','deployment/offline/release.json','deployment/offline/runtime.env','compose.yaml','deployment/course.ps1','deployment/build.ps1',
        'deployment/initialize.py','deployment/serve.py','deployment/verify.py',
        'deployment/backend.Dockerfile','deployment/frontend.Dockerfile','deployment/nginx.conf',
        'deployment/requirements.lock.txt','deployment/__init__.py','.dockerignore',
        'backend/requirements.lock.txt','backend/app','backend/migrations',
        'backend/migrate.py','backend/bootstrap.py','recommender','scripts/student_package.py',
        'frontend/src','frontend/public','frontend/package.json','frontend/package-lock.json',
        'frontend/index.html','frontend/vite.config.ts','frontend/tsconfig.json','models/business')
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative))) {
            throw "缺少课程文件：$relative 。重新解压教师完整包到新目录，保留原数据卷与密钥，请勿自行生成替代数据。"
        }
    }
    $package = Join-Path $root 'data/packages/rec-course-v3-train5-1.0.0'
    $manifest = Join-Path $package 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw '缺少正式数据包manifest.json，请向教师获取。' }
    if ((Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e') {
        throw 'manifest SHA-256不匹配。请向教师核对正式版本，不修改划分。'
    }
    $metadata = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($file in $metadata.files.PSObject.Properties.Name) {
        if (-not (Test-Path -LiteralPath (Join-Path $package $file) -PathType Leaf)) {
            throw "缺少正式包文件：$file 。向教师获取完整包。"
        }
    }
    Write-Host '[通过] 部署/构建输入及正式包文件存在，manifest身份正确；完整数据哈希与划分由原初始化工具校验。'
}
function Web-Ready {
    $script:FailureCode = 50
    Open-CourseRuntime -RequireSaved
    $port = [string]$script:Runtime.port
    $url = "http://127.0.0.1:$port"
    $response = Invoke-WebRequest "$url/health/ready" -UseBasicParsing -TimeoutSec 30
    if ($response.StatusCode -ne 200) { throw '网页入口ready未返回HTTP 200，请运行06并查看API日志。' }
    Write-Host "[通过] 网页入口就绪：$url ；请使用此地址完成注册、查询和评分人工验收。"
}

Push-Location -LiteralPath $root
try {
    Write-Host "课程项目：$root"
    Write-Host "操作：$Action；仅作用于当前Compose项目，保留数据库卷与原密钥。"
    if (Test-Path -LiteralPath $script:SettingsPath) {
        Open-CourseRuntime -RequireSaved
        $project = $script:Runtime.project
    } else {
        $project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'cinelab' }
    }
    Write-Host "Compose项目：$project"
    if ($Action -eq 'environment') {
        $os = Get-CimInstance Win32_OperatingSystem
        Write-Host "Windows：$($os.Caption)，Build $($os.BuildNumber)"
        Write-Host ('内存：{0:N1} GB' -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB))
        $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        Write-Host "当前窗口管理员：$admin（日常运行应使用普通窗口）"
        if ([int]$os.BuildNumber -lt 22000) { Write-Host '[提示] 当前是Windows 10；本次检查不能代表Windows 11验收。' }
        Write-Host '[人工检查] 任务管理器→性能→CPU：虚拟化已启用；核对磁盘空间与Docker设置。'
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw '未找到WSL；按安装指南完成WSL2安装并重启。' }
        $savedEncoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [Text.Encoding]::Unicode
            $wslVersion = & wsl.exe --version
            $wslExit = $LASTEXITCODE
        } finally { [Console]::OutputEncoding = $savedEncoding }
        if ($wslExit -ne 0) { throw 'WSL版本检查失败；按指南执行wsl --update，重启后复查。' }
        $versionText = ($wslVersion -join "`n") -replace "`0", ''
        Write-Host $versionText
        $match = [regex]::Match($versionText, '\d+\.\d+\.\d+(?:\.\d+)?')
        if (-not $match.Success -or [version]$match.Value -lt [version]'2.1.5') { throw 'WSL版本不可识别或低于2.1.5，请按指南更新。' }
        try {
            [Console]::OutputEncoding = [Text.Encoding]::Unicode
            $wslStatus = & wsl.exe --status
            $wslExit = $LASTEXITCODE
        } finally { [Console]::OutputEncoding = $savedEncoding }
        Write-Host (($wslStatus -join "`n") -replace "`0", '')
        if ($wslExit -ne 0) { throw 'WSL状态检查失败，请按指南更新WSL并重启。' }
        Engine
        Write-Host '[通过] 自动环境检查完成；未自动确认硬件虚拟化和WSL2设置，请补做人工检查。'
    } elseif ($Action -eq 'files') {
        Files
    } elseif ($Action -eq 'import') {
        Files
        Engine
        $script:FailureCode = 60
        $arguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $root 'deployment/offline/images.ps1'),'-Action','import')
        if ($ArchiveDirectory) { $arguments += @('-ArchiveDirectory',$ArchiveDirectory) }
        Run-Native $powershell $arguments
        Write-Host '[通过] 离线镜像导入/核验完成，尚未生成密钥或初始化数据库。'
    } elseif ($Action -eq 'initialize') {
        Files
        Engine
        Write-Host '[确认] 将核验已导入离线镜像、生成/保留随机密钥，并调用原安全工具初始化或复核business库。'
        Write-Host '已有库交由原工具保护；凭据、版本或未完成库不匹配时拒绝，不清空、不重置。'
        $answer = Read-Host '确定继续请输入 INITIALIZE；其他输入取消'
        if ($answer -cne 'INITIALIZE') { $script:FailureCode = 30; throw '已取消，未执行prepare或数据库初始化。' }
        $script:FailureCode = 60
        Run-Native $powershell @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $root 'deployment/offline/images.ps1'),'-Action','verify')
        Course 'prepare'
        Course 'check'
        Course 'initialize'
        Write-Host '[通过] 初始化工具成功结束。下一步双击04启动并验收浏览器。'
    } elseif ($Action -eq 'start') {
        Files
        Engine
        Course 'start'
        Course 'status'
        Web-Ready
    } elseif ($Action -eq 'stop') {
        if (-not (Test-Path -LiteralPath $course -PathType Leaf)) { $script:FailureCode = 20; throw '缺少deployment/course.ps1。' }
        Engine
        Course 'stop'
        Course 'status'
        Write-Host '[通过] 服务已停止；数据库卷和密钥保留。下次双击04。'
    } else {
        Files
        Engine
        Course 'check'
        Course 'status'
        $script:FailureCode = 40
        Open-CourseRuntime -RequireSaved
        Assert-CourseEngine
        Assert-CourseOwnership
        Invoke-CourseCompose exec -T api python -m deployment.verify
        Invoke-CourseCompose exec -T api python -m recommender.check selfcheck
        Invoke-CourseCompose exec -T api python -m recommender.check evaluation-inputs
        Web-Ready
        Write-Host '[通过] 只读诊断完成；浏览器验收请单独填写。'
    }
    exit 0
} catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host '请截图本窗口并联系教师；不要删除卷、密钥、重装Docker或重置WSL。'
    Write-Host '日志请交教师按保存的context和项目名读取；不要运行无项目参数的Compose命令。'
    Write-Host '日志分享前遮住用户名/机器路径等个人信息，禁止发送private目录或密码。'
    exit $script:FailureCode
} finally {
    Pop-Location
}
