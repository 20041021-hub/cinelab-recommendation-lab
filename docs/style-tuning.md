# 页面排版优化（减少留白、收紧节奏）

目标：在不改动任何功能、数据、接口与文案含义的前提下，收敛页面留白、优化排布逻辑。
改动只涉及前端样式与少量模板结构（推荐卡片、片库分页），后端、模型、评价指标、Kafka 事件与助手工具链一律未动。

## 1. 改动清单

| 位置 | 改动 | 效果 |
| --- | --- | --- |
| `style.css`（末尾覆盖块，注明“排版优化”） | 收紧 `.page` / `.page-heading` / `.section-heading` / `.filter-bar` / `.result-caption` 内边距与间距 | 每页顶部与分区间空隙变小 |
| 片库网格 `.movie-grid.is-dense` | 电影库与我的片单改为 5 列（≤1100px 回到 4 列，≤780px 3 列，≤540px 2 列），海报比例 3/3.8 → 3/3.35 | 每张卡片 570px → 320px，且行满不留空位 |
| `MoviesView.vue` / `ProfileView.vue` | 每页 12 部 → 15 部（与 5 列对齐），文案与分页 `size` 同步 | 每页正好 3 行，最后一排不再出现空格 |
| `AlgorithmPanel.vue` | 推荐卡片改为紧凑横排：海报 88px → 50px，标题单行省略，分数与策略标签同行；占位海报只留小图标 | 单卡 190px → 93px；10 条推荐 2004px → 652px |
| `ExperimentsView.vue` | 结果区改为“结果 → 实验四权重对照 → 实验五行为 + 实验六助手（并排）→ 历史评分 → 离线评价”；左侧实验设置吸顶；表格行高收紧 | 页面 10178px → 5705px；阅读顺序按“结论 → 参数 → 证据”排列 |
| 冷启动长表 / 权重对照表 | `max-height` 340 / 420，表内滚动 | 离线评价 2563px → 1395px，权重对照 2399px → 1295px |
| `HomeView.vue` | hero 内边距、hero 插画高度、推荐区与冷启动提示的间距收紧 | 首页 2658px → 2186px |

## 2. 前后度量（1440×1000 视口，Chrome，同一账号）

| 页面 | 优化前高度 | 优化后高度 | 变化 |
| --- | --- | --- | --- |
| 首页 `/` | 2658px | 2186px | −17.8% |
| 电影库 `/movies` | 2151px | 1702px | −20.9% |
| 推荐实验室 `/experiments` | 10178px | 5705px | −43.9% |
| 冷启动 `/cold-start` | 1440px | 1123px | −22.0% |
| 我的片单 `/profile` | 1618px | 1000px | −38.2% |
| 电影详情 `/movies/1` | 1124px | 1082px | −3.7% |

组件级：推荐卡片 190px → 93px；单片算法面板 2004px → 652px；离线评价 2563px → 1395px；实验四对照 2399px → 1295px；历史评分 926px → 798px。
1024px 视口复查：首页 2686px、电影库 1977px、推荐实验室 6258px，布局正常（侧栏 260px、推荐 2 列、片库 4 列）。

## 3. 回归验证

- `python scripts/lab6_integration_check.py`：**31 项检查，失败 0 项**（数据身份、五个算法注册、业务读写、S1 融合、S3 事件与兴趣、S2 助手真实调用与护栏、创新点 A 自适应权重、离线评价表与 ALS 清单一致性、前端改造在运行页面可见）。
- 前端构建：`npm run build`（vue-tsc + vite）通过；web 镜像 `cinelab-local/web:lab6` 重新构建并替换容器。
- 浏览器控制台仅剩两条预期信息：未登录时的 401（会话恢复探测）与 `/experiments/evaluations` 的 501（课程契约要求保留）。

## 4. 截图

| 文件 | 内容 |
| --- | --- |
| `docs/images/style-tuning-home.png` | 首页：hero、两列紧凑推荐、单行片库 |
| `docs/images/style-tuning-movies.png` | 电影库：5 列 × 3 行（每页 15 部） |
| `docs/images/style-tuning-experiments.png` | 推荐实验室：吸顶设置、并排的行为/助手区、滚动表格 |

## 5. 如何回退

样式改动是纯增量：删除 `frontend/src/style.css` 末尾“排版优化”覆盖块，并还原 `AlgorithmPanel.vue` 卡片样式、`ExperimentsView.vue` 的 `order`/栅格规则与两个 `max-height` 属性、`MoviesView.vue`/`ProfileView.vue` 的 `is-dense` 与分页大小即可；也可直接 `git revert` 本次提交。