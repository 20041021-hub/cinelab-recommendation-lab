# 实验五 S3：Kafka 扩展的部署说明

本目录是学生在课程离线环境之上自备的扩展层（S3 Kafka 事件驱动），**不属于课程镜像内容**。
`docs/starter-api-4b.md` 第 65 行明确：S1 RRF、S2 LLM、S3 Kafka 只保留原教学任务，starter 不提供依赖、服务或额外业务表；因此这里的东西全部由学生自己维护。

## 文件

| 文件 | 作用 |
| --- | --- |
| `compose.lab5.yaml` | 叠加在课程 `compose.yaml` 上的扩展：Kafka（KRaft 单节点）+ api/web 覆盖（新镜像与 Kafka/兴趣状态环境变量） |
| `Dockerfile.api` | `cinelab-offline/api:1.3.0-a1` + 从 vendor wheel 安装 `kafka-python==2.2.15` + 学生代码 |
| `Dockerfile.web` | `cinelab-offline/web:1.3.0-a1` + `frontend/dist`（本地 `npm run build` 的产物） |
| `vendor/kafka_python-2.2.15-py2.py3-none-any.whl` | Kafka 生产端依赖的离线安装包（见下） |

## 为什么用 vendor wheel，而不是改依赖清单

课程镜像里没有 Kafka 客户端；本机 Docker Hub 不可达，`deployment/build.ps1` 无法重建课程基础镜像，所以用离线镜像做底座叠加。
`backend/requirements.lock.txt` 由教师维护并被 `deployment.verify` 的摘要校验覆盖，学生不改动它（改了会破坏课程校验），因此把 `kafka-python` 以 wheel 形式随仓库提交，构建时用 `pip install --no-index --find-links` 离线安装。
宿主侧（跑消费端与场景测试）用同一个版本：`D:\Python311\python.exe -m pip install kafka-python==2.2.15`。

## 镜像来源

Kafka 用官方 `apache/kafka:3.9.0`：本机 Docker Hub 不可达，通过国内镜像站 `docker.m.daocloud.io/apache/kafka:3.9.0` 取得（digest `sha256:fbc7d7c428e3755cf36518d4976596002477e4c052d1f80b5b9eafd06d0fff2f`），并以 `pull_policy: never` 固定在本地，不依赖构建时的网络。

## 运行

```powershell
# 构建两个扩展镜像（需要先 cd frontend && npm run build 产出 dist）
docker build -f deployment/lab5/Dockerfile.api -t cinelab-local/api:lab5 .
docker build -f deployment/lab5/Dockerfile.web -t cinelab-local/web:lab5 .

# 起 Kafka，并把 api/web 换成扩展镜像
docker compose -f compose.yaml -f deployment/lab5/compose.lab5.yaml up -d kafka
docker compose -f compose.yaml -f deployment/lab5/compose.lab5.yaml up -d --no-deps api web

# 宿主侧消费端（把事件写成短期兴趣状态）
D:\Python311\python.exe scripts\run_behavior_consumer.py
```

## api 服务新增的环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CINELAB_KAFKA_BOOTSTRAP` | 空（不启用） | 生产端 bootstrap servers，容器内用 `kafka:9092`；未配置时整个生产端处于“未启用”状态 |
| `CINELAB_KAFKA_TOPIC` | `cinelab.behavior.business` | 行为事件主题 |
| `CINELAB_KAFKA_TIMEOUT` | `2.0` | 发送超时（秒）；超时进入冷却，业务写入不受影响 |
| `CINELAB_KAFKA_COOLDOWN` | `5.0` | 发送失败后的冷却秒数（避免每条写入都卡 2 秒） |
| `CINELAB_KAFKA_ENABLED` | `1` | 置 0 可整体关闭生产端（应急开关） |
| `CINELAB_INTEREST_ENABLED` | `1` | 融合侧是否读取短期兴趣 |
| `CINELAB_INTEREST_ALPHA` | `0.5` | 重排强度 `score' = score × (1 + alpha × match)` |
| `CINELAB_INTEREST_MAX_AGE` | `900.0` | 状态过期秒数，过期后不再重排（`stale=true`） |
| `CINELAB_INTEREST_STATE` | `/app/lab5-state/interest.json` | 消费端写出的短期兴趣状态（只读挂载） |
| `CINELAB_INTEREST_EVENTS` | `/app/lab5-state/events.jsonl` | 事件流水（前端“最近事件”表的数据源） |

消费端（宿主脚本）用命令行参数配置：`--bootstrap`（默认 `127.0.0.1:29092`，可被 `CINELAB_KAFKA_BOOTSTRAP` 覆盖）、`--topic`、`--group`、`--state-dir`、`--half-life-minutes`、`--max-messages`、`--idle-timeout`、`--latest`，详见 `scripts/run_behavior_consumer.py`。