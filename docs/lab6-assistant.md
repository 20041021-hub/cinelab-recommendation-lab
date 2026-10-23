# 实验六扩展 S2：自然语言助手（LLM 受控工具调用）

> 挑战版要求：在实验四（S1 加权 RRF）、实验五（S3 Kafka 事件驱动）之外，再实现一项标准扩展。
> 本实验实现 **S2 · LLM 工具调用**，并把三项扩展与基础推荐系统串成一条链路。
> 全部数据都来自业务库与真实模型工件，模型只负责"听懂问题 + 选工具 + 组织语言"。

## 1. 目标与选题理由

| 项 | 内容 |
| --- | --- |
| 实验四 | S1 加权 RRF（名次融合，来源可追溯） |
| 实验五 | S3 Kafka 事件驱动（前端行为 → 事件 → 短期兴趣 → 推荐改变） |
| 实验六 | **S2 自然语言助手** + 创新点 A（短期兴趣自适应融合权重） |

选择 S2 的理由：它是三项扩展里唯一"把人的自然语言变成受控查询"的一环，能直接复用已有的
目录、ItemCF 工件与推荐 Provider；同时它天然带来"模型可能编造"的风险，正好用来演示
**护栏与状态反馈**——手册明确要求"已实现功能必须有明确错误或无结果状态、不能静默伪造数据"。

## 2. 链路结构

```
浏览器（推荐实验室 → 自然语言助手面板）
   └─ GET /api/v1/me/assistant/query?q=…&limit=5      （带会话 Cookie）
        └─ backend/app/assistant.py   意图循环（最多 3 轮）
             ├─ backend/app/llm.py    httpx → DeepSeek /chat/completions（OpenAI 兼容）
             │      tools = search_movies / similar_movies / recommend_movies / my_history
             └─ 工具执行（全部只读）
                  ├─ business.movies（真实目录，类型/年份，8459 部）
                  ├─ models/business/itemcf-v1/item-similarities.json（共现邻居）
                  ├─ recommender.contracts.recommend(...)（真 Provider：content/popular/itemcf/als/cold_start）
                  └─ recommender.business.read_history(...)（当前账号的真实评分与收藏）
```

模型返回的 `tool_calls` 由后端执行，结果以 `role="tool"` 消息回灌；模型的最终回答只负责组织语言，
**返回给前端的 `items` 完全来自工具输出**（每张卡片都带 `source_tool`）。

## 3. 受控工具契约

| 工具 | 参数 | 数据来源 | 上限 |
| --- | --- | --- | --- |
| `search_movies` | `genres[]`、`year_min`、`year_max`、`limit` | `business.movies` 真实目录 | 类型必须是目录里真实存在的标签；年份 1888—2030；limit ≤ 10 |
| `similar_movies` | `movie_id`、`limit` | ItemCF 共现工件（带相似度与共现次数） | 电影必须在目录内 |
| `recommend_movies` | `algorithm`（五种）、`limit` | 真实 Provider（`recommend()` 契约校验） | limit ≤ 10 |
| `my_history` | `limit` | 当前账号的评分与收藏 | 只能查"自己"（会话身份，URL 里没有身份参数） |

## 4. 护栏（不依赖模型自觉）

1. **工具白名单**：`HANDLERS` 只认识四个工具，模型提到别的名字直接判失败。
2. **参数取值域校验**：类型必须在 `get_genres()` 返回的真实词汇表内，年份、limit 逐项校验；
   越界请求以 `ok=false` + 中文原因回给模型（例：`目录里没有这些类型：Anime（可用类型见 /api/v1/genres）`）。
3. **只读**：四个工具全部只有 SELECT / 读工件，写路径（评分、收藏）不经过助手。
4. **items 只由工具输出构成**：回答里的电影卡片来自工具结果；若回答用《》提到了工具结果之外的片名，
   会被记入 `unverified_mentions` 并在 `warnings` 里标注（片名对比做归一化：去掉结尾 `(年份)`、压缩空白、忽略大小写）。
5. **轮次上限 3**：`MAX_TOOL_ROUNDS=3`，防止模型无限调用工具。

状态只有两种：`ready`（有工具结果）与 `no_result`（工具没返回任何电影）；
再加 `warnings`（工具被拒、没有结果、疑似未验证片名）与 `guardrails` 字段，
前端把 `reason` / `warnings` 原样展示——**没有结果就如实说没有结果**。

### 4.1 回答里说明"用了哪个算法、准确率多少"

用推荐类工具（`recommend_movies`、`similar_movies`）时，工具证据 `evidence` 里会带上：

```json
{"algorithm": "itemcf", "algorithm_label": "ItemCF 协同过滤",
 "model_version": "itemcf-cooccurrence-v1",
 "offline_metrics": {"partition": "validation", "users": 4209,
                     "precision_at_10": 0.055263, "recall_at_10": 0.09783,
                     "model_version": "itemcf-cooccurrence-eval-v1",
                     "protocol": "冻结验证集 · 4,209 位固定用户 · 正反馈≥4.0 · Top-10 · 宏平均"}}
```

- 这份数字是**实验三离线评价的只读快照**：`backend/app/lab3_metrics.json`，由
  `scripts/publish_lab3_metrics.py` 从 `reports/lab3/summary.json` 生成（同一份数字也进
  `frontend/src/data/lab3-metrics.json` 与 `docs/lab3-evaluation-table.md`）。API 不加载任何
  evaluation 模型或评价 Provider，读到的只是指标。
- 系统提示第 6 条规定：用到推荐工具时，回答里必须写清算法名与
  `precision_at_10` / `recall_at_10`（按百分比），并说明这是固定验证集上的**算法级历史评价**，
  不是对本次提问的准确率承诺。
- 证据随工具消息回给模型，响应里另有结构化的 `methods` 字段
  （`tool` / `algorithm` / `algorithm_label` / `model_version` / `offline_metrics`），
  前端"推荐所用算法 · 准确率"区块直接渲染它，因此**模型措辞变了也不会丢这条信息**。
- 卡片排序：推荐类工具的输出排最前（用户问的是推荐），`my_history` 只作为解释材料排在最后。

## 5. 外部模型配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `APP_LLM_MODEL` | `deepseek-chat` | 模型名 |
| `APP_LLM_KEY_FILE` | `/app/llm-key/llm-api-key.txt` | 只读挂载的 Key 文件（不进镜像、不进仓库） |
| `APP_LLM_API_KEY` | 空 | 优先于 Key 文件（仅用于临时覆盖） |
| `APP_LLM_TIMEOUT` | `30.0` | 单次调用超时 |
| `APP_LLM_MODE` | 空 | `stub` 时走本地假模型，用于离线自检 |

`GET /api/v1/experiments/assistant/status` 只报告 `key_configured` / `mode` / `model` / `base_url`，
**不回显任何 Key 内容**。外部模型不可用时接口返回 503 `ASSISTANT_UNAVAILABLE`，`reason` 为
`missing_key | timeout | network_error | http_error | bad_response`，业务写入与推荐接口不受影响。

## 6. 场景证据（`reports/lab6/assistant-scenarios.json`，4/4 通过）

| 场景 | 问法 | 结果 |
| --- | --- | --- |
| 正常 | 「推荐几部动画电影」 | 200 `ready`，2 轮，`search_movies` 命中 10 部，5 张卡片全部 `source_tool=search_movies`（Toy Story 1995 / Shrek 2001 / Lion King 1994 …） |
| 无结果 | 「只要 2029 年上映的 Film-Noir 电影，别的都不要」 | 200 `no_result`，`matched=0`，回答「没有找到符合条件的电影」+ `warnings` 说明无结果，`items=[]` |
| 错误输入 | 「推荐几部 Anime 类型的电影」 | 工具被拒 `目录里没有这些类型：Anime`；短问题 422、`limit=999` 422、未知历史用户 404；`items=[]` 不伪造 |
| 外部不可用 | 同上（把 `APP_LLM_BASE_URL` 指向 `http://127.0.0.1:9`，或把 Key 文件指向不存在的路径） | 503 `ASSISTANT_UNAVAILABLE`，`reason=network_error` / `missing_key`；同期业务写入仍 200，恢复后 `status=ready` |

真实调用流水（录制时的账号 `app:36`）：

```text
rounds=2  tools=search_movies(ok, matched=10)  items=5  status=ready
answer ≈ 「我从目录里找到了这些动画电影，推荐给你：…」
```

正常场景另有 4 项"算法 + 准确率"检查（问法「用 ItemCF 给我推荐 5 部电影，并说明这个算法的准确率」）：
`algorithm_reported`（`methods[0].algorithm=itemcf`、`model_version=itemcf-cooccurrence-v1`）、
`accuracy_reported`（`precision_at_10=0.055263`、`recall_at_10=0.09783`、`users=4209`）、
`accuracy_in_answer`（回答文本里出现准确率表述）、`methods_match_tool_evidence`。
截图见 `docs/images/lab6-assistant-accuracy.png`。

## 7. Token 用量（会不会很贵）

系统只在**用户点一次提问**时调用外部模型，没有后台轮询、没有启动预热，也没有定时任务。实测两次：

| 问题 | 轮次 | 输入 tokens | 输出 tokens | 合计 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 「给我推荐几部动画片」（含 3 次工具调用） | 2 | 4 175 | 531 | **4 706** | 5.1 s |
| 「帮我找一部 2029 年上映的默片」（无结果） | 2 | 1 743 | 165 | **1 908** | 3.1 s |

前端助手面板会直接显示本次用量（`live · deepseek-chat · 2 轮工具调用 · 4 706 tokens（输入 4 175 / 输出 531）`），
所以"花了多少"永远可见。按当前量级，一次提问约 2—5 千 tokens，课程验收与答辩演示的总消耗在几万 tokens 量级。

## 8. 复现与自检

```powershell
cd D:\Personal\Desktop\推荐系统\CineLab-Student-Windows-1.3.0-rc1

# 1) 组件状态（应 mode=live、key_configured=true、tools=4）
curl.exe -s http://127.0.0.1:8080/api/v1/experiments/assistant/status

# 2) 四类场景（会临时覆盖环境变量并 recreate api，最后自动恢复）
D:\Python311\python.exe scripts\lab6_assistant_scenarios.py

# 3) 历史用户公开入口（无需登录）
curl.exe -s "http://127.0.0.1:8080/api/v1/demo/history-users/14/assistant/query?q=%E6%8E%A8%E8%8D%90%E5%87%A0%E9%83%A8%E5%96%9C%E5%89%A7&limit=5"
```

网页入口：`http://127.0.0.1:8080/experiments` → 勾选「显示实验六 · S2 自然语言助手」→ 点预设问题。

## 9. 边界与已知限制

- 助手**不写数据**：不会替用户评分或收藏，任何写操作仍需用户自己在界面完成。
- `stub` 模式只给自动化测试用（关键词 → 固定工具调用），演示与验收走 `live`。
- 模型偶尔会提到工具结果之外的片名（来自它自己的知识），这时只做标注、不当作事实卡片展示；
  演示时可以在回答下方看到 `unverified_mentions`。
- 一次问答最多 3 轮工具调用，复杂需求会被拆成多次提问——这是有意的成本与可控性取舍。