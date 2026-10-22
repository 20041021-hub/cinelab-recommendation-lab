# 实验六创新点 A：短期兴趣自适应融合权重

> 把实验五（S3）沉淀下来的**短期兴趣状态**，接到实验四（S1）的**加权 RRF**上：
> 同一套固定权重不再"一刀切"，而是按每个账号当下的兴趣亲和度在线微调。
> 证据脚本：`scripts/lab6_adaptive_check.py` → `reports/lab6/adaptive-weights.json`。

## 1. 为什么需要它

实验四的 `itemcf=6 / als=1` 是在验证集上人工选出来的**全局常数**（见 `docs/lab4-fusion.md`）：
它对所有账号一视同仁，可解释性也止于"实验四调出来的"。

实验五之后系统里已经有一份实时资产：每个账号的短期兴趣状态
（`reports/lab5/state/interest.json`，由 Kafka 行为事件驱动，按 30 分钟半衰期衰减）。
于是可以问一个更细的问题：**对"这个账号"来说，哪一路召回更贴近他此刻的口味？**

## 2. 规则（只调权重，不改公式）

```text
match(电影)   = clamp(该片类型在短期兴趣里的权重之和 / 最强正权重, 0, 1)
affinity(路)  = 该路候选电影 match 值的平均值
w'(路)        = w(路) × (1 + beta × affinity(路))         beta = CINELAB_ADAPTIVE_BETA（默认 0.5）
score(电影)   = Σ_路  w'(路) / (k + rank_路(电影))         k = 60（与实验四一致）
```

- 只在 `interest=1` **且** `adaptive=1` 且该账号的兴趣状态**未过期**时生效；
- 状态过期 / 没有状态 / `adaptive=0` 时**退回固定权重**，并在响应里给出 `reason`（不静默生效）；
- 兴趣本身仍由实验五的规则产生（评分 ≥4.0 加 `(rating-3.0)/2`、≤2.0 减分、收藏 +1.5、取消收藏 −1.5）；
- 每一路都会把 `base_weight`、`affinity`、`adaptive_weight`、`candidates` 回给前端，可逐条追溯。

## 3. 接口

```http
GET /api/v1/me/recommendations/fusion?interest=1&adaptive=1&limit=10
GET /api/v1/demo/history-users/14/recommendations/fusion?adaptive=1&limit=10
GET /api/v1/experiments/fusion/sources          # 默认来源/权重 + 自适应规则说明
```

响应新增 `adaptive` 块：

```json
{
  "adaptive": {
    "requested": true, "enabled": true, "beta": 0.5, "state_version": 59, "stale": false,
    "rule": "w' = w × (1 + beta × affinity)；affinity = 该路候选与兴趣匹配度的均值",
    "sources": {
      "itemcf": {"base_weight": 6.0, "affinity": 0.750981, "adaptive_weight": 8.252944, "candidates": 30},
      "als":    {"base_weight": 1.0, "affinity": 0.835295, "adaptive_weight": 1.417647, "candidates": 30}
    }
  }
}
```

`adaptive=0` 时 `enabled=false`、`reason="未启用（adaptive=0）"`；状态过期时
`reason="短期兴趣状态已过期：N 秒没有新事件（上限 M 秒），这次仍用固定权重"`。

## 4. 对照实验（真实数据，`--fresh-account` 全新账号）

行为输入（账号 `app:43`，五条真实写入）：评分 1→5.0、13→5.0、48→4.5、12（恐怖片）→1.0，收藏 1。
生产端 `sent=25 failed=0 skipped=0`；消费端 `state_version=59`、本轮新增 5 条事件。
兴趣类型：`Animation / Children 4.239048`、`Adventure 3.490993`、`Fantasy 2.493576`、`Comedy 1.496143`。

| 场景 | `adaptive=0` 权重 | `adaptive=1` 权重 | 名次是否变化 |
| --- | --- | --- | --- |
| 默认权重（itemcf=6, als=1） | itemcf 6.0 / als 1.0 | itemcf **8.252944** / als **1.417647** | 否（前 5 名 `364,595,3114,588,1270` 不变） |
| 等权 `&weights=1,1` | 1.0 / 1.0 | **1.375491** / **1.417647**（als 权重反超） | 否（前 5 名 `3114,595,364,588,4886` 不变） |
| 两路 `&algorithms=popular,itemcf&weights=1,1` | 1.0 / 1.0 | popular **1.210785** / itemcf **1.375491** | **是**：`260,356,1270,1196,1198` → `260,1270,356,1196,1210` |

三组共 9 项检查全部为 true（`events_recorded`、`consumer_advanced_state`、`state_not_stale`、
`adaptive_enabled`、`adaptive_disabled_when_flag_0`、`guardrail_disabled_path_explained`、
`weights_changed`、`affinity_differs_across_sources`、`ranking_reacts_to_weights`）。

### 四条依据真实数据的解释

1. **亲和度把"哪一路更懂我"量化了**：同一个账号下 `als 0.835 > itemcf 0.751 > popular 0.422`，
   与直觉一致——ALS 隐向量更容易把动画/家庭片聚在一起，而热门榜里有大量与本人无关的高分片。
2. **权重变了不等于名次变了**：默认权重下 itemcf 占绝对主导，乘 1.375 也不足以翻盘，
   这正是"微调而非重排"的设计意图——自适应不应该颠覆验证集选出的主权重。
3. **只有两路势均力敌时才会改变结果**：`popular+itemcf` 等权场景下亲和度差距（0.42 vs 0.75）
   把名次第 2、3 位互换（`356` ↔ `1270`），说明自适应确实能把"更像我的那一路"顶上来。
4. **不生效时也必须说清楚**：`adaptive=0`、无状态、状态过期三种情况都返回明确的 `reason`
   （过期时会带上"多少秒没有新事件、上限多少秒"），满足手册"不得静默伪造/静默生效"的要求。

## 5. 前端展示

- 推荐实验室 → S1 融合面板：新增 `创新点 A · 自适应权重 β=0.5 · 状态版本 59` 徽标，
  面板内一张小表列出每路的固定权重、亲和度、自适应权重、候选数（截图 `docs/images/lab6-adaptive-weights.png`）。
- 每张卡片保留兴趣加权徽标（`匹配度 100% · 加权 ×1.500`），与实验五的展示一致。
- 「创新点 A · 兴趣自适应融合权重」勾选框与「S3 短期兴趣」勾选框相互独立，
  四种组合（叠加/不叠加 × 自适应/固定）都能直接在页面上对比。

## 6. 复现

```powershell
cd D:\Personal\Desktop\推荐系统\CineLab-Student-Windows-1.3.0-rc1

# 全新账号跑完整对照（注册 → 5 次真实行为 → 消费端更新 → 三组权重对照）
D:\Python311\python.exe scripts\lab6_adaptive_check.py --fresh-account

# 手工对照（同一账号连续两次请求）
curl.exe -s "http://127.0.0.1:8080/api/v1/demo/history-users/14/recommendations/fusion?interest=1&adaptive=0&limit=5"
curl.exe -s "http://127.0.0.1:8080/api/v1/demo/history-users/14/recommendations/fusion?interest=1&adaptive=1&limit=5"
```

## 7. 边界与说明

- 亲和度只按**类型**计算（没有用导演、年份、影片热度），好处是可解释、可复算；
- `beta` 上限 0.5 是刻意的：自适应是"微调"，主权重仍由验证集实验决定；
- 自适应只作用于 S1 融合层，**不改动**任何单算法 Provider（评价表与模型清单不受影响）；
- 状态文件是消费端产物，读不到时融合仍能工作（退回固定权重 + `reason`），不会让推荐接口失败。