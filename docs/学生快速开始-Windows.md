# CineLab Windows学生快速开始 V1.3 RC1

完整操作见[Windows详细指南](Windows实验环境安装与启动指南.md)。适用Windows x64、Docker Desktop WSL2、Linux/amd64容器；不安装Ubuntu，无需宿主Python、Node或MySQL。**环境安装需要网络；环境就绪后首次运行可不联网下载镜像；源码重新构建可能需要互联网。**

1. 新电脑先按详细指南安装WSL2与Docker Desktop；已有Docker直接检查。打开Desktop，确认WSL2后端和Linux容器；普通PowerShell执行`docker version`、`docker info --format '{{.OSType}}'`、`docker compose version`，须有Server、linux、退出0。
2. 记录C盘、工程盘、Docker数据盘位置和剩余空间。课程文件至少预留2GB；Docker数据盘建议20GB以上、系统盘建议10GB以上余量，均为规划建议，重建/ALS可能更多。项目放D/E盘不代表Docker也在那里。
3. 按教师公布的SHA-256校验ZIP，右键“全部解压”。进入同时含`compose.yaml`、`backend`、`frontend`、`deployment`、`data`和`offline-images`的CineLab-Student-Windows-1.3.0-rc1目录，勿在ZIP内执行。
4. 依次双击`deployment\student-bat`里的工具，每项退出0再继续：

| 顺序 | 文件 | 成功判据 |
|---|---|---|
| 01 | 01-检查电脑环境.bat | 自动检查通过，并人工确认虚拟化、WSL2与空间 |
| 02 | 02-检查课程文件.bat | 文件/正式manifest存在且身份正确 |
| 02a | 02a-导入离线镜像.bat | 三镜像核验/导入通过；此步不建库 |
| 03（只在首次） | 03-首次初始化.bat | 确认是本人新课程项目，输入INITIALIZE，最终status passed |
| 04 | 04-启动推荐系统.bat | 三服务healthy，网页ready成功 |
| 05（下课） | 05-停止推荐系统.bat | 保留数据库卷与原密钥 |
| 06（排错） | 06-检查与诊断.bat | 在运行状态检查；遇首错停止 |

首次03前核对没有来源不明的同名项目或`cinelab_mysql_data`卷。有旧库、缺密钥、端口占用、标签冲突或安全策略拦截就停下来请教师处理，不删卷、不重置Docker/WSL。每次上课先启动Desktop，再04；已有数据不重复03。

5. 打开`http://127.0.0.1:8080`（以工具显示端口为准），注册自己的账号，登录、搜索Toy Story、评分、收藏；05→04后检查记录仍在。请用127.0.0.1；算法501是Starter Kit尚未实现的预期状态。
6. 算法保存到`recommender/student/`，Vue保存到`frontend/src/`。在根目录打开普通PowerShell，按详细指南设置可写的英文TEMP目录后执行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\deployment\build.ps1 -Target all
```

成功会直接启动新代码，不需03，也不要接着04。可把all改成backend或frontend。下课仍05；下次运行`build.ps1 -Action Start`的完整命令见详细指南。04会恢复教师阶段A镜像，磁盘源码仍保留。**这条联网开发路径仅完成静态检查，未经本轮Win11实际构建验证；不代表完全离线重建通过。**

7. 每组3—5人，由组长建立GitHub Private仓库，邀请组员并授予教师查看权限。每次课结束前至少commit并push一次，保留六次课堂开发记录；提交小组仓库URL和本次commit链接，具体入口“按教师课堂通知提交”。[建仓与提交步骤](Windows实验环境安装与启动指南.md#10-github协作与每次课堂提交)。不强制Classroom、PR或复杂分支。不要上传密钥、本地运行设置、镜像、卷、大数据、缓存或教师参考实现。
