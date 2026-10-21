# CineLab Windows学生Starter Kit V1.3 RC1

先读[一页快速开始](docs/学生快速开始-Windows.md)，需要逐步操作时读[详细安装指南](docs/Windows实验环境安装与启动指南.md)。

适用Windows x64、Docker Desktop WSL2、Linux/amd64容器，不安装Ubuntu。环境安装需网络；导入教师三个镜像后，首次运行可不联网下载镜像；修改源码后可联网重建。

工具在deployment/student-bat：01→02→02a→首次03→04，下课05，排错06。已有原数据库时保留原密钥和运行设置，不重做新库。

源码修改保存于backend/app、recommender/student和frontend/src；按详细指南用build.ps1重建。04恢复教师阶段A镜像，学生版下次用build.ps1 -Action Start。新构建入口仅静态检查，未经本轮Win11实测，完全离线重建未实现。

每组3—5人自建GitHub私有仓库，邀请组员并授予教师查看权限；每次课commit并push，保留六次课堂记录。交仓库URL及本次commit链接，按教师课堂通知提交。不要上传数据、镜像、密钥或本地运行设置。

[算法接入](docs/student-algorithm-guide.md) · [评价约束](docs/evaluation-environment.md) · [冻结数据说明](data/packages/rec-course-v3-train5-1.0.0/README.md) · [发行边界](docs/发行说明-Windows.md)

发行文件校验：根目录Verify-Package.ps1及PACKAGE-CHECKSUMS.json。完整操作与安全停止条件见详细指南。
