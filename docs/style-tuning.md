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

## 6. 全站海报与配色（第二轮视觉改造）

### 6.1 海报（第一版：本地生成）

课程数据包不含授权海报文件（`movies.poster_path` 全为 NULL，外部图床在本机网络不可达）。因此新增一个**只读**接口：

- `GET /api/v1/movies/{movie_id}/poster.svg`（`backend/app/posters.py`）——按 `movie_id` 哈希确定性地选配色与构图，用真实元数据（片名 / 年份 / 类型）渲染 360×540 的 SVG；带 `ETag` 与 7 天缓存；只读 `movies` 表，不写任何表、不进入推荐代码路径。
- 图上固定标注 `CINELAB · LOCAL POSTER` 与 `本地生成`，避免被误当成官方授权海报；片名尾部年份在标题区去掉、只保留在元信息行。
- 前端 `MoviePoster.vue` 新增 `movie-id` 参数：`poster_path` 合法时仍优先用 TMDB 图（本机不可达时自动回退），否则使用本地生成海报；首页、片库、详情、算法推荐、S1 融合、S2 助手六处调用点全部接入。
- 这一版在第三轮被**真实剧照缓存**取代为默认路径（生成的 SVG 退化为抓取失败时的兜底），见 §7.1。

### 6.2 配色（第二轮：午夜靛蓝，第三轮已换成白底）

`frontend/src/theme.css` 从原来的“墨绿 + 金”整体换成“午夜靛蓝 + 薄荷 + 藤紫”：

| 变量 | 旧值 | 新值 |
| --- | --- | --- |
| `--bg` | `#121b1b` | `#0b1020` |
| `--surface` | `#192424` | `#151c33` |
| `--accent` | `#dfb76c` | `#5eead4` |
| `--accent-2` | — | `#a78bfa` |
| `--border` | `#31413e` | `#2c3763` |

Element Plus 主题变量（`--el-color-primary` 及明暗档、表格头/悬停底色、边框与文字色）同步替换；`style.css` 与 6 个组件里 69 处硬编码的旧色值一次性迁移，页面配色不再依赖历史色。

### 6.3 排布微调

- 实验五“行为事件”由 4 列宽表格改为窄栏内的紧凑列表（`#电影ID · 类型 · 加/减分 · 时间`），消除了横向溢出与截断。
- 冷启动分区表、融合权重对照表的标题补上“N 行 · 表内滚动”，滚动是有意为之而不是排版事故；海报右上角的“我的评分”徽标不再遮挡海报上的片名与年份。

### 6.4 回归与截图

- 集成检查 `python scripts/lab6_integration_check.py`：**31 项，失败 0 项**（含“前端改造在运行中的网页可见”这一项）。
- `npm run build`（vue-tsc + vite）通过；`cinelab-local/api:lab6`、`cinelab-local/web:lab6` 重建并替换容器，四个容器 healthy。

| 文件 | 内容 |
| --- | --- |
| `docs/images/poster-palette-home.png` | 首页：新配色 hero、两列推荐、本地生成海报的片库卡片 |
| `docs/images/poster-palette-movies.png` | 电影库：15 部/页，每部一张确定性生成的海报 |
| `docs/images/poster-palette-detail.png` | 详情页：海报 + 元数据 + 评分控件 |
| `docs/images/poster-palette-experiments.png` | 推荐实验室：融合、行为紧凑列表、助手、评价表 |
| `docs/images/poster-palette-coldstart.png` | 冷启动页：热门回退与类型画像切换 |

### 6.5 边界

海报是展示层增强：不修改冻结数据、不改变任何接口返回的字段含义、不参与离线评价；关闭它只需让 `MoviePoster.vue` 不再传 `movie-id`（即回退到原来的占位框）。

## 7. 真实剧照海报与白底浅色主题（第三轮视觉改造）

### 7.1 真实剧照：来源、缓存与兜底

先做了一次**图源可达性实测**（本机网络，2026-10-23）：`image.tmdb.org`、`upload.wikimedia.org` 连接被关闭；`is1-ssl.mzstatic.com`（iTunes 图床）403 且 iTunes 搜索返回 0 条；`api.themoviedb.org`、`omdbapi.com` 401（可达但需要 Key）；豆瓣图床 418。可达且不需要 Key 的是 **Letterboxd**：`https://letterboxd.com/tmdb/{tmdb_id}/` 返回 200，资料页里带 `https://a.ltrbxd.com/resized/film-poster/...jpg` 真实剧照，图片直接 200 返回 `image/jpeg`。

后端因此改成两级：

- `GET /api/v1/movies/{movie_id}/poster`：**缓存优先**——命中 `posters/<movie_id>.jpg` 直接返回（`Cache-Control: public, max-age=604800` + `ETag`）；未命中且开启抓取时，按该片 `tmdb_id` 取 Letterboxd 资料页、解析海报地址并下载，落盘后返回；抓不到就转发到下面的生成海报（`Content-Type: image/svg+xml`）。所有失败都只影响这一个请求，不抛错到前端。
- `GET /api/v1/movies/{movie_id}/poster.svg`：本地生成的 SVG（见 §6.1）。
- `GET /api/v1/movies/{movie_id}/poster/status`、`GET /api/v1/experiments/poster-cache`：缓存与抓取状态。

实现要点（`backend/app/posters.py`）：只读 `movies` 表取 `tmdb_id`；`CINELAB_POSTER_DIR`（默认 `/app/posters`）、`CINELAB_POSTER_FETCH`（默认开）、`CINELAB_POSTER_TIMEOUT`（默认 8s）；单图上限 4 MB、必须 `image/*`；落盘用临时文件 + `os.replace` 原子替换；失败记录在 `posters/_misses.json`（同一部片 7 天内最多重试 3 次）；不写任何数据库表、不进入推荐代码路径。

预热脚本 `scripts/fetch_posters.py`（宿主运行，纯标准库）：分页读取目录、逐个请求 `poster/status?fetch=1`，默认 3 并发、每次间隔 0.4s，遇到 429/5xx 退避重试，结果写入 `reports/lab6/posters.json`。命令：

```powershell
D:\Python311\python.exe scripts\fetch_posters.py --workers 4 --delay 0.25
```

抓回来的图片放在仓库根目录 `posters/`，已在 `.gitignore` 中排除（第三方剧照不随仓库分发）；`deployment/lab6/compose.lab6.yaml` 把该目录可写挂载进容器 `/app/posters`。因此老师克隆仓库后**不需要预置图片**：第一次打开某部电影时后端会按需抓取并缓存，抓不到才显示生成的占位海报。

### 7.2 白底浅色主题

`frontend/src/theme.css` 全量换成浅色（`color-scheme: light`）：

| 变量 | 第二轮（靛蓝） | 第三轮（白底） |
| --- | --- | --- |
| `--bg` | `#0b1020` | `#ffffff` |
| `--surface` | `#151c33` | `#ffffff` |
| `--surface-2` | `#1c2542` | `#f6f8fc` |
| `--text` | `#eef2ff` | `#131a2e` |
| `--muted` | `#a6b0d4` | `#5b6785` |
| `--border` | `#2c3763` | `#dfe5f1` |
| `--accent` | `#5eead4` | `#0f9d8a` |
| `--accent-2` | `#a78bfa` | `#6d5dfc` |
| `--accent-3` | `#f0abfc` | `#b45309` |
| `--danger` | `#fb7185` | `#d64545` |

配套改动：`frontend/src/main.ts` 去掉 Element Plus 深色样式表导入与 `document.documentElement.classList.add('dark')`；`style.css` 与 6 个组件里 66 处硬编码色值按“深底 → 浅底、亮字 → 深字、霓虹强调 → 同色系深色”的映射一次性迁移（`AlgorithmPanel.vue` 8、`AssistantPanel.vue` 10、`FusionPanel.vue` 16、`ColdStartView.vue` 2、`ExperimentsView.vue` 1、`HomeView.vue` 2、`style.css` 15，另加 `theme.css` 若干），并逐个修正迁移后不协调的地方：hero 渐变收尾、`film-ticket` 票据由“米白纸 + 黑字”改为“青绿票据 + 白字”、占位海报底色、`.form-error` 底色与文字、评价表 `<code>` 底色。所有“浅字”只保留在强调色底（票据、主按钮、评分徽标）上。

### 7.3 回归验证

- 页面实测（Chrome 1440×1100，构建后）：`document.body` 背景 `rgb(255,255,255)`；首页 1623px、电影库 1702px、详情 1100px、推荐实验室 5207px。
- 海报实测：`GET /api/v1/movies/1/poster` 返回真实剧照（Toy Story，163 KB JPEG）；`/api/v1/experiments/poster-cache` 显示缓存数量与字节数；预热脚本冒烟 6 部 6 成功、0 走兜底。
- `npm run build`（vue-tsc + vite）通过；`cinelab-local/api:lab6` 与 `cinelab-local/web:lab6` 重建并替换容器，四个容器 healthy。

### 7.4 截图

| 文件 | 内容 |
| --- | --- |
| `docs/images/poster-real-home.png` | 首页白底 + 真实剧照（hero、片库卡片） |
| `docs/images/poster-real-movies.png` | 电影库：15 部/页，全部真实剧照 |
| `docs/images/poster-real-detail.png` | 详情页：Toy Story 真实剧照与元数据 |
| `docs/images/poster-real-experiments.png` | 推荐实验室：白底下的推荐卡片、评价表与助手面板 |

### 7.5 边界与回退

- 剧照仅作**本机课程演示缓存**，仓库不提交图片（`posters/` 已忽略）；缓存里没有的片子按需抓取，抓不到则显示生成的占位海报，页面不会出现破图。
- 海报与配色都不影响任何接口字段与离线评价结果；关掉抓取只需设 `CINELAB_POSTER_FETCH=0`。
- 回退：`git revert` 本轮提交即可（海报链路回到“只生成”，配色回到靛蓝主题）。