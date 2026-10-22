# 实验六：集成检查、成果说明与答辩提纲

> 覆盖实验六（挑战版）的三件事：**全系统集成检查（31 项）**、**成果说明**、**答辩准备**。
> 证据：`reports/lab6/integration.json`（脱敏副本 `docs/lab6-integration.json`）。

## 1. 集成检查（31 项全部通过）

脚本：`scripts/lab6_integration_check.py`（真人操作视角，全部走 HTTP 接口）。
运行：`D:\Python311\python.exe scripts\lab6_integration_check.py`，失败即 exit 1。

| 组 | 检查项 |
| --- | --- |
| 环境与身份 | 环境启动与数据身份（`rec-course-v3-train5-1.0.0` / `temporal-v1-cold500-train5-v3` / `student-eight-v3` / manifest `fcfb9a19…`）、算法注册表五个都可用 |
| 账号与基础推荐 | 注册/登录与会话、注册账号评分与收藏、当前账号 content / itemcf / als / popular 推荐、零历史账号冷启动 |
| 历史用户 | ml:14 与 ml:55 的 popular / content / itemcf / als / cold_start（各 10 条，且与本人已评分零交集） |
| S3 | 消费端处理行为事件、短期兴趣状态与重排、生产端/消费端状态可见 |
| S1 | 加权融合（含来源与名次、权重与候选数） |
| S2 | 助手状态可见（提供方与模型）、真实调用且 items 只来自工具结果、错误输入有明确状态（不静默编造） |
| 创新点 A | 自适应权重真实改写各路权重、关闭时有明确原因（不静默生效） |
| 指标与前端 | 离线评价表完整（validation/test/cold 共 30 行）、ALS 指标与模型清单一致、前端改造在运行中的网页可见（直接抓运行中的 HTML/JS 分块） |

关键证据（`reports/lab6/integration.json`）：

```text
total=31  failed=[]  passed=true
环境: dataset rec-course-v3-train5-1.0.0 / manifest fcfb9a19… / 注册表五个 registered
S1 : pool=57  itemcf(weight 6.0, candidates 30, contributed 10, best_rank 1) + als(weight 1.0)
S3 : producer sent=21 failed=0 skipped=0；consumer applied=0（无新事件）；interest enabled=true alpha=0.5
S2 : provider=live model=deepseek-chat，工具调用 ok、items 全部带 source_tool
A  : itemcf 1.0→1.375491、popular 1.0→1.210785（关闭时 reason="未启用（adaptive=0）"）
表: reports/lab3/summary.json 30 行；ALS validation 0.041388/0.067163、users 4209、empty 1（与清单一致）
```

## 2. 成果说明

### 2.1 三项扩展与基础系统的关系

```text
基础推荐系统（实验一至三）
  content（类型 TF-IDF）· popular（正反馈支持度）· itemcf（共现）· als（Spark MLlib）· cold_start（组合策略）
        │  统一契约：recommender/contracts.py 的 PROVIDERS + 模型清单 + check_model/check_result
        ├── 实验四 S1：加权 RRF 融合（只吃名次，输出每路的来源、名次与权重）——fusion-rrf-v1
        ├── 实验五 S3：Kafka 行为事件 → 消费端 → 短期兴趣状态 → 推荐重排（interest 块 + 加权徽标）
        └── 实验六 S2：自然语言 → 受控工具（目录 / ItemCF 邻居 / 五算法 Provider / 我的历史）→ 真实结果
                    创新点 A：用 S3 的兴趣亲和度在线微调 S1 的各路权重（adaptive 块）
```

三项扩展都**只做加法**：不改冻结契约、不改八表 schema、不放宽评价协议；
关掉任一扩展（`interest=0`、`adaptive=0`、模型不可用）时，基础推荐与业务写入照常工作。

### 2.2 源码与文件清单

| 部分 | 文件 |
| --- | --- |
| S2 后端 | `backend/app/llm.py`（外部模型客户端）、`backend/app/assistant.py`（工具与护栏）、`backend/app/main.py`（路由注册） |
| 创新点 A | `backend/app/interest.py`（亲和度与 `adaptive_weights`）、`backend/app/fusion.py`（`adaptive` 参数与响应块） |
| S2 前端 | `frontend/src/components/AssistantPanel.vue`、`frontend/src/views/ExperimentsView.vue`、`frontend/src/types.ts` |
| A 前端 | `frontend/src/components/FusionPanel.vue`（自适应权重表与徽标） |
| 部署 | `deployment/lab6/Dockerfile.api`、`Dockerfile.web`、`compose.lab6.yaml`（与 `compose.yaml`、`deployment/lab5/compose.lab5.yaml` 叠加） |
| 测试脚本 | `scripts/lab6_assistant_scenarios.py`（S2 四类场景）、`scripts/lab6_adaptive_check.py`（创新点 A 对照）、`scripts/lab6_integration_check.py`（31 项集成检查） |
| 证据 | `reports/lab6/{assistant-scenarios,adaptive-weights,integration}.json` 与脱敏副本 `docs/lab6-*.json` |
| 界面截图 | `docs/images/lab6-assistant.png`、`lab6-adaptive-weights.png`、`lab6-experiments-full.png` |
| 说明文档 | `docs/lab6-assistant.md`、`docs/lab6-adaptive-weights.md`、本文 |

### 2.3 一键复现（验收时的最短路径）

```powershell
cd D:\Personal\Desktop\推荐系统\CineLab-Student-Windows-1.3.0-rc1
docker compose -f compose.yaml -f deployment/lab5/compose.lab5.yaml -f deployment/lab6/compose.lab6.yaml up -d
D:\Python311\python.exe scripts\lab6_integration_check.py          # 31 项，期望 passed=true
D:\Python311\python.exe scripts\lab6_assistant_scenarios.py        # S2 四类场景
D:\Python311\python.exe scripts\lab6_adaptive_check.py --fresh-account   # 创新点 A 对照
```

网页演示：`http://127.0.0.1:8080/experiments` —— 勾选「S1 加权融合」「S3 短期兴趣」「创新点 A 自适应权重」「S2 自然语言助手」四个开关即可。

### 2.4 本次提交

```text
代码与部署：68299d5  lab6: 接入 S2 自然语言助手与创新点 A 自适应融合权重
文档与截图：fef4592  docs: 实验六 S2 与创新点 A 的说明、集成检查与答辩提纲
仓库：https://github.com/20041021-hub/cinelab-recommendation-lab
```

## 3. 答辩提纲

**Q1：S2 的"受控"体现在哪里？模型会不会胡说？**
工具白名单（四个）、参数取值域（类型必须是目录真实标签、年份 1888—2030、limit ≤10）、
全部只读、`items` 只能由工具输出构成、轮次上限 3；回答里用《》提到工具结果之外的片名会被记入
`unverified_mentions` 并在 `warnings` 里标注（片名对比已做"去年份括号 + 忽略大小写"归一化）。
验收时可以直接问「推荐几部 Anime 类型的电影」——它会如实说目录里没有这个类型。

**Q2：为什么 S2 选择 DeepSeek，Key 怎么保证安全？**
DeepSeek 是 OpenAI 兼容接口，用镜像里已有的 `httpx` 直接调用，**没有新增任何依赖**；
Key 放在 `deployment/private/llm-api-key.txt`（`.gitignore` 的 `**/private/` 规则），容器只读挂载，
接口只报告 `key_configured=true`、不回显 Key；镜像里不含 Key，仓库里也没有。
换提供方只需改 `APP_LLM_BASE_URL` / `APP_LLM_MODEL`。

**Q3：跑一次要花多少钱？**
只在用户点提问时调用，没有后台轮询。实测一次两轮问答约 4 706 tokens（输入 4 175 / 输出 531），
无结果的问题约 1 908 tokens；前端每次都显示本轮用量。课程验收与答辩演示的总量在几万 tokens 量级。

**Q4：创新点 A 和 S1、S3 是什么关系？**
S3 提供"这个账号此刻喜欢什么类型"（带衰减的短期兴趣状态），S1 决定"每一路候选占多大权重"；
A 把两者接起来：`w' = w × (1 + β × 亲和度)`，只调权重、不改 RRF 公式。关闭 A 或状态过期时
退回验证集选出的固定权重，并把原因写进响应。

**Q5：为什么 ALS 的 P@10 低于 ItemCF？**
ALS 优化的是评分预测（RMSE 类目标），Top-N 排序不是它的直接目标；ItemCF 的共现信号与
"一起被打高分"这个评价口径更一致。给 ALS 加候选支持度下限（500）后 P@10 从 0.005393 升到
0.041388，说明长尾候选的噪声是主要原因。手册也明确不要求 ALS 必须超过 ItemCF。

**Q6：Kafka 挂了会怎样？**
生产端有超时与冷却：写入业务数据照常 200，事件进入 `failed/skipped` 计数（`last_error` 可见）；
消费端不跑时推荐仍工作，只是没有兴趣加权（响应里 `interest.reason` 说明状态未就绪）。
证据：`reports/lab5/scenarios-kafka-down.json`。

**Q7：怎么保证"不是静态页面、不是编造的数据"？**
所有推荐都来自 Provider 的真实返回（`model_version` 可见、排除已评分、同分按数值 ID 升序），
助手卡片带 `source_tool`，融合结果带每路来源与名次，兴趣加权带 `state_version` 与 `alpha`；
集成检查里还有一项直接抓**运行中的网页**分块，验证前端改动确实生效。

**Q8：八表 schema 有没有被改？**
没有。Kafka 兴趣状态与评价产物都放在文件里（`reports/lab5/state`、`models/`），
唯一批准过的扩展仍是课程 starter 的 `business.accounts.auth_version`。

## 4. 五分钟演示脚本

1. 打开 `/experiments`，选历史用户 `ml:14` → 依次展示五个算法（重点 ALS 与 ItemCF 的差异）。
2. 勾选「S3 短期兴趣」→ 展示兴趣面板（来源 topic、事件数、状态版本）与卡片上的加权徽标。
3. 勾选「创新点 A」→ 展示每路固定权重 / 亲和度 / 自适应权重；把 `adaptive` 关掉再打开做前后对照。
4. 勾选「S2 自然语言助手」→ 问「推荐几部动画电影」（正常）；再问「只要 2029 年的 Film-Noir」（无结果）；
   最后问「推荐几部 Anime 类型的电影」（错误输入，看它如实说目录里没有这个类型）。
5. 收尾：展示离线评价三张表（validation / test / cold）与集成检查 `passed=true` 的 31 项输出。