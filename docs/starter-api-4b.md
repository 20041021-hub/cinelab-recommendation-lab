# 阶段4B API与阶段4C接入约束

基础前缀 `/api/v1`。响应 ID（movie_id、account_id、ml_user_id、tmdb_id）使用十进制字符串，避免 JavaScript 整数精度问题；评分使用一位小数字符串；业务 datetime 为 UTC ISO8601 `Z`。原始 MovieLens ID 不重编。未知资料返回 NULL，前端自行显示占位，不补造简介、海报或评价。

## 通用约定

分页 `page=1`、`per_page=20`，per_page 1—100；响应 `{items,total,page,per_page}`。搜索 `q` 最多100字符，类型 `genre` 只能使用 `/genres` 返回值；未知参数、非正整数ID、不合法评分为422，不存在的电影/历史用户为404。`%`、`_` 在搜索中作为普通字符处理。

错误响应：

```json
{"error":{"code":"MOVIE_NOT_FOUND","message":"电影不存在","details":null},"request_id":"请求追踪ID"}
```

所有响应携带 `X-Request-ID`、`Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`。验证错误仅暴露字段位置和错误类别，不回显密码、令牌或数据库语句。

## 身份和CSRF流程

1. 同源请求 `GET /auth/csrf`，保留服务器设置的 HttpOnly CSRF Cookie，并把 JSON 中的 `csrf_token` 保存在前端内存。
2. 每个 POST/PUT/PATCH/DELETE 请求发送准确的 Origin 和 `X-CSRF-Token`，浏览器携带 Cookie；这些请求不接受查询参数。即便注册、登录也需要 CSRF。Cookie SameSite=Strict，Path=/api/v1；HTTPS 使用 Secure。
3. 注册体和登录体均为 `{login_name,password}`。登录名3—64字符，允许 Unicode 字母数字、下划线、点、短横线；密码12—128字符。注册不自动登录。
4. 登录成功返回 `{account,csrf_token}`，更新内存 nonce；JWT 只放在 HttpOnly Cookie，不通过 JSON 返回，也不使用 localStorage。后续 GET /auth/me 获取当前账号。
5. 页面刷新后重新 GET /auth/csrf，恢复内存 nonce；登录/退出旋转 nonce，旧登录态 nonce 不可跨会话使用。多个标签页需要重新获取 nonce。
6. 退出 POST /auth/logout，清 Cookie 并更新 nonce，数据库 auth_version 加1，使该账号所有旧 JWT 立即失效；API重启后仍然失效。过期、撤销或篡改的 JWT 返回401。缺失/不匹配 CSRF 或错误 Origin 返回403。登录失败限速429并返回 Retry-After。

阶段4C优先通过开发代理统一前后端 Origin；当前无跨源 CORS 配置。浏览器不会允许脚本随意设置 Origin，使用浏览器实际请求源。HTTPX 测试通过显式 Origin 模拟同源 HTTP 请求；本轮没有浏览器和 HTTPS 验收。

## 已实现接口

| 方法 | 路径（除health外均加前缀） | 行为与权限 |
|---|---|---|
| GET | /health/live、/health/ready | 存活200；就绪验证数据库、数据包版本及认证迁移，错误503 |
| GET | /auth/csrf | 返回nonce并设置签名CSRF Cookie |
| POST | /auth/register | 注册201；重名409；仅接受登录名及密码 |
| POST | /auth/login | 验证Argon2哈希，设置短时JWT Cookie，返回当前账号及nonce |
| POST | /auth/logout | 需登录；撤销该账号全部旧会话 |
| GET | /auth/me | 需登录；只返回account_id、subject_key、login_name |
| GET | /genres | 真实数据包中的电影分类列表 |
| GET | /movies | q、genre、page、per_page；按movie_id稳定排序 |
| GET | /movies/{movie_id} | 电影全部已有字段，缺失资料为NULL |
| PUT | /me/ratings/{movie_id} | 需登录；体 `{rating:4.5}` 或 `{rating:"4.5"}`，0.5—5.0，严格半星；重复请求修改同一条评分 |
| GET | /me/ratings | 需登录；按更新时间降序分页的本人评分历史 |
| GET | /me/ratings/{movie_id} | 需登录；未评分返回rating=null |
| PUT | /me/favorites/{movie_id} | 需登录；空体或 `{}`，幂等收藏，返回200 |
| DELETE | /me/favorites/{movie_id} | 需登录；幂等取消，返回204 |
| GET | /me/favorites | 需登录；本人收藏分页列表 |
| GET | /demo/history-users | 历史匿名用户分页列表，附evaluation_role和ml:命名空间 |
| GET | /demo/history-users/{ml_user_id}/ratings | 只读历史评分分页，返回read_only=true |

业务接口只接受登录身份，不能提交 account_id、ml_user_id 或 runtime_mode 代替身份。`ml:123` 与 `app:123` 即使数字相同也完全独立；历史用户不能登录，没有历史评分写接口。本人列表和评分接口从登录令牌及数据库推导身份，没有任意账号写入路由。

## 学生算法边界

| 方法 | 路径 | 当前结果 |
|---|---|---|
| GET | /experiments/algorithms | 五种算法及not_implemented状态 |
| GET | /me/recommendations/{algorithm}?limit=10 | 当前注册账号，需登录；501 |
| GET | /demo/history-users/{ml_user_id}/recommendations/{algorithm}?limit=10 | 只读历史用户实验入口；501 |
| GET | /experiments/evaluations | 501 EVALUATION_NOT_IMPLEMENTED，metrics=null |

algorithm仅允许popular、content、itemcf、als、cold_start；limit为1—50。五种算法均返回统一501错误 `ALGORITHM_NOT_IMPLEMENTED`，details包含status、algorithm、subject_key、dataset_version、split_version、runtime_mode=business、model_version=null、batch_id=null、items=[]。前端显示“待实现”，不能用随机电影或固定榜单填充首页。

学生在 `recommender/contracts.py` 的空 PROVIDERS 注册表中接入自己的简单 provider。协议是 `recommend(RecommendationRequest) -> RecommendationResult`；DTO约束统一输出：algorithm、model_version、dataset_version、split_version、runtime_mode、batch_id、generated_at及items。item包含movie_id、rank、algorithm、model_version、score和可选recall_sources/recall_ranks。未知分数允许NULL，不返回虚构0分；目前没有成功模型输出、模型文件或结果写入功能。

训练与离线评价必须另外使用固定evaluation/train，validation调参、test最终评价，独立配置及进程；不能向业务API传mode切换数据库。未来模型元数据要绑定已批准数据/划分版本，并按原契约检查批次和版本一致性。S1 RRF、S2 LLM、S3 Kafka仅保留原教学任务，本轮无依赖、服务或额外业务表。

## 数据库和前端接续

八表仍遵循 student-eight-v3；唯一批准扩展是business.accounts.auth_version，独立迁移版本starter-auth-v1。应用不连接evaluation；数据库用户无evaluation权限。history_ratings保存完整课堂历史评分，只读业务展示；不能用于离线评价训练。

前端接续需要：同源Cookie/CSRF封装；401返回登录；403重新获取nonce或提示会话变化；422展示参数错误；429按Retry-After提示；503就绪失败提示；501显示待实现。没有海报/简介时保留空状态；收藏列表、评分历史来自本人接口；历史用户演示始终标记只读。该文档不代表阶段4C已实施。
