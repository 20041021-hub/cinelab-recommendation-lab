# CineLab Windows学生发行候选 V1.3 RC1

本包待教师最终审阅，下发动作尚未执行。以教师已确认通过Win10/Win11功能验收的阶段A候选为基础；该确认来自本轮教师决定。工作区历史阶段A报告仍记录当时Win11待测，本轮没有取得新的Win11原始日志，也没有重新部署测试电脑。新增源码重建不属于既有功能验收。

## 能力与边界

- 保留正式数据、原初始化/迁移/认证/Provider接口和三个阶段A镜像；导入后首次运行可不联网下载镜像。
- 增加最小联网开发入口：build.ps1复用历史4E的英文临时上下文方式与现有Dockerfile；all/backend/frontend可分别构建，独立随机开发标签；Start复用已保存镜像。
- 原course、runtime、images、Compose、数据库初始化、后端/前端源码及Dockerfile未修改；仅重封build.ps1的发行工具摘要。04恢复阶段A；05保留卷；学生版用build Start。
- 联网重建仅做PowerShell语法和参数/命令路径静态检查，未经本轮Win11实测，未执行Docker构建。固定基础镜像可用性、网络代理、pip/npm依赖解析、新代码健康和不同Compose版本兼容性尚未验证。
- 没有离线wheelhouse/npm缓存/依赖镜像，不宣称完全离线重建通过。阶段B停止开发与测试。
- 未提供训练器、参考算法或模型；内容推荐、ItemCF、ALS由学生实现。新增依赖、Java/Spark和训练资源需按方案准备；当前构建白名单主要支持Python、SQL及前端src/public，新文件种类可能需教师调整。
- 无自动回滚：失败可能留下镜像或部分更新容器；04只恢复批准的运行镜像，不回滚数据库或宿主源码。开发镜像/构建缓存持续占用空间，本工具不清理。

## 完整性与空间

三个镜像归档保持阶段A原始字节，共339,573,760字节（约323.84MiB）。

| 归档 | 字节 | SHA-256 |
|---|---:|---|
| api.tar | 69,030,912 | 2c597d8dac2c41162f7cb36e6349c3542b6dabd2fd481d2354407a399bdd251b |
| web.tar | 21,458,432 | 11d6b4b5636267a3e18335a173fda5e8debd6b9e674470e29c70034c7a62fb33 |
| mysql.tar | 249,084,416 | 2d1432478a37654e0ffe66cdf487b3e1c69f3b60e8d7dda3300dff2cb5afa500 |

逐文件清单在[PACKAGE-CHECKSUMS.json](../PACKAGE-CHECKSUMS.json)，镜像清单在[SHA256SUMS.txt](../offline-images/SHA256SUMS.txt)，运行身份在[release.json](../deployment/offline/release.json)。ZIP准确大小和SHA-256见教师随包提供的外部发行清单，避免ZIP自包含校验循环。工程至少预留2GB、Docker盘建议20GB以上、C盘建议10GB以上；这是规划建议，未经独立峰值测量，开发和ALS可能更高。

学生只需[快速开始](学生快速开始-Windows.md)和[详细指南](Windows实验环境安装与启动指南.md)，无需教师验收报告。每组3—5人自主建立GitHub私有仓库，邀请组员并授予教师查看权限；保留六次课堂commit/push记录，交仓库URL及本次commit链接，入口按教师课堂通知提交。无统一仓库或Classroom，不强制PR。
