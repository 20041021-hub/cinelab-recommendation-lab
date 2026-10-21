# CineLab Windows实验环境安装与启动指南 V1.3 RC1

日期：2026-10-19。与[学生快速开始](学生快速开始-Windows.md)配套。发行候选保留阶段A的三个离线运行镜像、正式数据、数据库初始化和认证流程。教师已确认阶段A在Windows 10/11功能验收通过；本轮只做发行静态检查，不重复Win11部署。新增联网源码构建仅静态验证，未经本轮Win11实测；不宣称完全离线重建通过。

## 1. 先检查电脑

Win+R输入`winver`记录系统版本；设置→系统→关于确认x64。任务管理器→性能→CPU确认虚拟化已启用。课程只支持Windows x64和Linux/amd64容器，不适用ARM模拟。内存至少8GB，建议16GB或以上；ALS训练可能需更多资源。

Windows版本和服务资格按[Docker Windows安装要求](https://docs.docker.com/desktop/setup/install/windows-install/)核对。Windows 10已有功能通过不等于仍在厂商支持周期；由教师/管理员确认受支持组合。已有Docker且可用时无需重装WSL或Ubuntu。系统组件安装按学校管理员要求进行，日常运行使用普通账号。

## 2. 安装WSL2，不安装Ubuntu

仅未配置WSL的新电脑，由管理员打开PowerShell执行：

```powershell
wsl --install --no-distribution
```

按提示重启。如需更新，由管理员执行`wsl --update`。普通PowerShell核对：

```powershell
wsl --version
wsl --status
wsl -l -v
```

WSL软件版本须至少2.1.5；状态中的“默认版本2”不能代替软件版本检查。`--no-distribution`不安装Ubuntu；不要执行不带此选项的安装命令。`docker-desktop`属于Docker内部组件，不能删除或注销。命令依据[微软WSL说明](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)。离线镜像包不包含Windows、WSL或Docker安装程序，新电脑准备环境一般需要互联网。

## 3. Docker和磁盘位置

从[Docker官方Windows安装页](https://docs.docker.com/desktop/setup/install/windows-install/)下载x86_64安装程序，按学校要求安装。选择WSL2后端，使用Linux容器；按提示重启。无需启用Ubuntu WSL Integration，无需安装宿主Python、Node或MySQL。

在首次导入前，Desktop→Settings→Resources→Advanced查看Disk image location（版本不同入口可能变化，参考[Docker设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/)）。记录实际Docker数据盘。全新环境可通过受支持的界面选择空间足够的位置；已有其他课程资源时不要自行迁移VHDX或重置数据。

| 位置 | 内容 | 预留建议 |
|---|---|---|
| 工程目录 | ZIP、解压源码、冻结数据、三个镜像归档 | 至少2GB，开发模型另留空间 |
| Docker数据盘 | 镜像、数据库卷、构建缓存 | 建议至少20GB可用；重建/ALS可能显著更多 |
| 系统盘C | WSL/Desktop、更新、用户配置及TEMP | 建议至少10GB可用；不是安全下限保证 |
| 英文临时目录 | 源码重建的精简上下文 | 本人可写、实际存在；默认TEMP符合条件即可 |

这些是规划建议，非实测峰值。C盘与Docker数据盘相同时按合计需求考虑。工程在D/E盘不代表Docker不占C盘。阶段A运行归档精确大小见[发行说明](发行说明-Windows.md)，不要拿压缩ZIP大小作为运行空间需求。

从开始菜单启动Docker Desktop，等候Engine就绪，普通PowerShell逐条执行，每条后查看`$LASTEXITCODE`须为0：

```powershell
docker version
docker info --format '{{.OSType}}'
docker compose version
docker context show
```

必须有Server、OSType为linux、Compose可用，context为本人本机Desktop Linux引擎；另外在Desktop设置人工确认WSL2后端。鲸鱼图标和linux输出不能独立证明所有条件满足。

## 4. 获取、校验和解压

从教师可信渠道获取`CineLab-Student-Windows-1.3.0-rc1.zip`及其`.sha256`，先在ZIP所在目录打开PowerShell：

```powershell
Get-FileHash -LiteralPath .\CineLab-Student-Windows-1.3.0-rc1.zip -Algorithm SHA256
```

将Hash与教师单独公布的校验值逐字核对，不一致则停止。右键ZIP→全部解压到本人可写的本地目录；避开同步盘和系统目录，不在ZIP窗口内双击脚本。根目录结构如下：

```text
CineLab-Student-Windows-1.3.0-rc1/
  README.md  compose.yaml  PACKAGE-CHECKSUMS.json
  backend/  frontend/  recommender/  scripts/  models/business/
  data/packages/rec-course-v3-train5-1.0.0/manifest.json
  deployment/course.ps1  deployment/build.ps1
  deployment/offline/  deployment/student-bat/
  offline-images/api.tar  web.tar  mysql.tar  SHA256SUMS.txt
  docs/  Verify-Package.ps1
```

在这层目录的地址栏输入`powershell`打开普通窗口。可先用下列工具核对全部发行文件（修改代码后不再用于判断作业正确性）：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\Verify-Package.ps1
```

必须显示全文件校验成功、退出0。该校验清单不包含自身；其SHA-256由ZIP旁发行清单单独锚定。任何脚本被组织安全策略阻拦时停止，联系管理员；不修改永久策略、不关闭防护。课程BAT使用同样的进程级策略参数，不赋予管理员权限，也不能覆盖组织策略。

## 5. 首次导入离线运行镜像

先只读盘点，避免选到旧课或别的项目：

```powershell
docker compose ls -a
docker ps -a
docker volume ls
docker volume ls --filter name=cinelab_mysql_data
Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8080
```

默认项目为cinelab、端口8080、密钥目录deployment/private。发现旧课程卷、同名项目、端口占用、远程context或不明环境覆盖时先交教师核对。已有原库时保留原目录、原三密钥和local.runtime.json，不创建替代密码。不要删除其他课程容器/镜像/卷来腾空间。

进入`deployment\student-bat`，依次双击01环境、02文件、02a导入。每项退出0再继续。02a默认从根目录`offline-images`读取三个tar，先全部检查大小及SHA-256，再检查已有标签身份，确认后才load。相同镜像跳过；同名异内容拒绝覆盖。此步只保存课程运行设置，不生成密钥或创建数据库。工具核验平台、镜像ID、层和发行标签；无pull或构建回退。

成功后会生成`deployment/local.runtime.json`，绑定本目录所用context、Engine、项目、端口与密钥路径，勿修改或上传。导入和后续运行可不联网下载镜像；无需额外MySQL或初始化镜像。先导入再03，顺序不能交换。

确需自定义项目/端口时由教师确认，在**首次导入前**于同一个PowerShell窗口设置`COMPOSE_PROJECT_NAME`、`COURSE_WEB_PORT`并使用下一节备用入口的import动作。之后沿用保存设置；不能改项目名绕过旧库问题。

## 6. 首次初始化

只有新课程项目、环境和导入检查均通过后，双击03。仔细核对目标，输入大写`INITIALIZE`确认，其他输入取消。工具依次验证离线镜像、生成或保留三份独立随机密钥、检查部署、启动db并等待健康，调用原初始化程序校验全部冻结文件、导入正式business数据、执行原迁移及授权检查。**此过程不构建源码、不下载镜像。**

正式目录包含8,459部电影、5,000位历史用户及798,775条业务历史评分；历史用户不是真实登录账号。初始账号、个人评分和收藏为空，学生自行注册。成功应有最终`status: passed`（JSON格式显示可能带引号）、退出0；之后04启动网站。重复初始化会按原规则复核，而非重置数据；日常无需03。

长时间无新输出先等候，记录开始时间和末条输出，约10分钟无变化可联系教师观察，不是强制中断时限。不要同时运行多个03。导入或迁移失败留现场，不反复初始化或删卷；DDL失败不保证整体事务回滚。

## 7. 日常启动、停止与诊断

每次先开启Desktop并完成第3节Engine检查，双击04。成功为db、api、web均healthy、网页ready成功，默认地址`http://127.0.0.1:8080`。initialize是一次性工具，不应常驻。下课双击05，保留卷、账号、评分、收藏和密钥。06在运行状态执行原数据库、Provider契约、冻结输入及ready检查，遇首错停止；停止状态下06失败不代表数据丢失。

BAT受阻且管理员允许课程进程级脚本入口时，在真实根目录用：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\deployment\student-bat\student-tools.ps1 -Action files
```

仅将files替换为需要的动作，不整表连续执行：

| BAT | Action |
|---|---|
| 01 | environment |
| 02 | files |
| 02a | import |
| 03 | initialize（仍交互确认） |
| 04 | start |
| 05 | stop |
| 06 | diagnose |

相对路径入口须从根目录执行；BAT会自己定位。每次看`$LASTEXITCODE`，0以外停止。不要用省略项目和context参数的Compose命令替代工具。private、local.runtime.json与数据库卷是配套的本人运行状态，换电脑/恢复备份由教师指导。

## 8. 浏览器功能确认

访问工具显示的127.0.0.1网址，完成：注册新账号→登录→搜索Toy Story→详情页保存0.5至5.0的半星评分→收藏→个人中心核对→05停止→04再启动后复查记录。不要改用localhost，Cookie和Origin按127.0.0.1配置。

推荐实验室中的内容推荐、ItemCF、ALS等尚未提供算法，501或“尚未实现”是预期。完成真实算法与模型后页面才能显示真实结果；不要伪造结果或修改认证绕过检查。

## 9. AI辅助修改算法和Vue源码

先复制自己的源码或用Git提交一个基线。在编辑器打开解压根目录；算法新增到`recommender/student/`，Provider注册依照[学生算法接入](student-algorithm-guide.md)；Vue页面和组件在`frontend/src/`，后端在`backend/app/`。保存Ctrl+S，代码在宿主文件中保存，不在容器里临时改代码。

可以向AI描述：“按现有RecommendationRequest/RecommendationResult与Provider接口实现我的ItemCF；仅从允许的训练数据拟合；保留数据划分、认证和接口；解释修改与验证方法。”内容推荐只能使用真实已有特征。ALS的Python库/Java/Spark环境由学生按课程方案增加依赖，当前运行镜像没有承诺包含训练环境或任何参考算法。不要把private、密码、Cookie或个人资料发送给AI。

先阅读[评价约束](evaluation-environment.md)，不把test、validation或cold query用于拟合。模型与真实manifest放`models/business/`，此目录按原配置只读挂载；训练输出在宿主生成或由明确的训练工具导出，API不自动训练。新增Python依赖写入`backend/requirements.lock.txt`并保持版本约束；前端依赖同时更新package.json和package-lock.json。原Dockerfile复用pip install与npm ci，未提供离线依赖缓存。增加Java/Spark或额外文件类型需要教师审查Dockerfile/构建输入，不能假定现有镜像已支持。

**联网重建路径仅静态验证，未经本轮Win11真实构建/启动；不代表完全离线重建通过。** 必须先完成原03及04。重建可能联网拉取固定摘要的Python/Node/Nginx基础镜像并下载pip/npm依赖；镜像仓库代理、网络限制及新依赖兼容性由实际环境决定。

在根目录打开普通PowerShell，查看`$env:TEMP`。若含中文，先在实际有空间的盘建本人可写英文目录（不假定D/E盘存在），再于当前窗口设置：

```powershell
$courseTemp = Read-Host '输入已存在、本人可写、空间足够的英文临时目录完整路径'
if (-not [IO.Path]::IsPathRooted($courseTemp) -or $courseTemp -match '[^\x00-\x7F]' -or -not (Test-Path -LiteralPath $courseTemp -PathType Container)) { throw '临时目录不符合要求，停止' }
$env:TEMP = $courseTemp
$env:TMP = $courseTemp
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\deployment\build.ps1 -Target all
```

命令成功后会直接用新镜像启动API和前端，等候健康；无需再次03或04。只改算法/Python时可用`-Target backend`，只改Vue时可用`-Target frontend`，另一个服务沿用已保存开发镜像或原阶段A镜像。源码不是热挂载，单独刷新网页或restart不会更新代码。所有构建使用linux/amd64；新的随机开发标签不覆盖教师阶段A或其他课程镜像。

下课仍双击05。下次使用已构建学生版本（不重新构建、不主动下载依赖）：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\deployment\build.ps1 -Action Start
```

双击04始终恢复教师阶段A运行镜像，保留学生磁盘源码及数据库；需要学生版本再次用上面Start。构建状态记录在`deployment/local.development.json`，勿手改或上传。诊断06可检查正在运行的学生容器，但它的镜像检查只锚定原阶段A标签，不等价于新算法正确性或新镜像完整验收。

每次修改后重新做第8节功能确认和06。开发代码拥有原API业务权限，错误代码可能修改业务数据，应保留本人数据备份并遵守只读历史约束。失败时没有自动回滚：构建失败可能留下未使用镜像；启动失败可能已有部分服务更新；保存现场后用04恢复已批准运行镜像。源码和数据库不会因此回到修改前，不能靠04撤销学生代码造成的数据写入。新增开发镜像和缓存会累积，本工具不自动清理。

## 10. GitHub协作与每次课堂提交

本课程不设统一GitHub仓库，不使用GitHub Classroom。每组3—5人，由组长创建一个GitHub **Private**仓库，邀请组员协作，并授予教师查看权限。无需强制PR或复杂分支管理；组员修改同一文件前先沟通，每次开始先Pull同步。

操作参考：[GitHub建仓说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[GitHub Desktop添加本地仓库](https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-a-repository-from-your-local-computer-to-github-desktop)。

1. 组长登录GitHub，点击右上角“+”→New repository，填写小组仓库名，选择Private。建议保持远端空仓库，便于导入本地Starter Kit源码。
2. 仓库Settings→Collaborators（名称可能因界面版本不同）→Add people，按教师公布的GitHub用户名邀请组员和教师；对方接受邀请后才有访问权限。不要为了让教师查看改成公开仓库。
3. 安装并登录GitHub Desktop。File→Add Local Repository选择解压根目录；若提示不是Git仓库，按“create a repository here”在**此根目录**初始化，确认没有另造一层目录。在Repository Settings的Remote中填入组长创建的真实私有仓库URL。若使用Publish repository方式创建，则由组长勾选Keep this code private；两种建仓方式选一种，不重复建两个仓库。
4. 首次提交前，在Changes逐项查看：仅提交Starter Kit必要源码、配置和说明，以及自己的改动。包内.gitignore已排除密钥、本地状态、离线镜像、数据库卷、缓存、虚拟环境及大型数据；仍须人工确认。冻结数据留在本地，各组员分别解压教师包后同步组内源码。
5. 每次实验课结束前，在Summary写“第N次课：本次实现与检查结果”，Commit to main后Push origin。至少保留六次课堂开发记录，首次导入不能替代后续开发记录。其他组员按教师要求在自己的本地仓库Fetch/Pull同步；冲突时保留双方代码讨论处理，不强制覆盖。
6. 在GitHub网页打开仓库→Commits→本次提交，复制地址栏的完整commit链接。在课程平台提交**小组仓库URL和本次commit链接**，必要时附截图；具体入口**按教师课堂通知提交**。仓库首页链接不能唯一定位本次作业，未Push的本地commit教师无法查看。

不强制使用命令行Git。组长可直接把私有仓库URL分享给已获邀请的组员和教师。提交前确认教师已接受邀请，提供链接时不发送访问令牌或密码。

严禁提交密钥、密码、`deployment/private/`、`local.runtime.json`、`local.development.json`、`.env`、Docker镜像、数据库卷/VHDX、node_modules、虚拟环境、大型数据、教师参考实现及个人日志。不要上传整个发行ZIP。误提交凭据请立即联系教师，删除当前文件不等于清除Git历史。

## 11. 常见错误与安全停止条件

| 现象 | 操作 |
|---|---|
| Docker无Server、WSL错误或非linux | 等待Desktop，核对后端/重启提示；环境未通过不初始化 |
| SHA不符、缺文件、导入退出60 | 停止并向教师重取完整包；不手改release.json或覆盖同名镜像 |
| 已有卷缺密钥、Engine/项目/目录冲突 | 保留原卷与设置交教师；不重命名项目躲保护 |
| 8080占用 | 查归属；首次导入前由教师指定其他端口，已有状态不随意改 |
| PowerShell禁止执行/闪退 | 保存首错和退出码，联系管理员；不永久修改策略或关防护 |
| 初始化失败/503或迁移中断 | 保留现场，不删卷、不换密码、不并发03 |
| 403/登录异常或429 | 使用准确127.0.0.1网址，等待后重登，不关闭CSRF/限流 |
| 501 | 尚未实现算法，依照接口开发 |
| pip/npm/基础镜像下载失败 | 联网重建需要可信网络/代理，检查依赖锁；不要重跑03 |
| 构建TEMP含中文或无空间 | 当前窗口设真实可写英文TEMP，磁盘紧张先停止求助 |
| 重建后仍是旧页面 | 确认保存及构建成功；04会恢复阶段A，学生代码用build Start |
| 中文日志乱码 | 核对真实路径/首错和退出码，不改内部目录或系统区域设置 |

内部环境错误常为10、文件错误20、取消30、原工具失败40、网页失败50、镜像导入失败60；宿主加载失败可能退出1，build.ps1失败也可能退出1。退出码须结合首错判断。截图含工具名称、时间、首错和退出码，分享前遮住用户名/机器路径；不提供密码、Cookie或private目录。不要使用prune、down -v、Docker恢复出厂设置、WSL注销或清理其他课程资源。
