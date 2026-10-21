# 学生八表数据字典：student-eight-v3

适用数据版本：`rec-course-v3-train5-1.0.0`；划分版本：`temporal-v1-cold500-train5-v3`。这是原八表设计的可执行补充，不替换教师侧的原始数据、缓存与逐字段溯源模型。SQL见包内 `sql/schema_evaluation.sql`、`sql/schema_business.sql`。

## 1. 用户隔离与模式

历史用户表`history_users`只表示MovieLens匿名ID，保留原userId，无登录能力；`accounts`表示真实注册账号，自增account_id。`ml:123`和`app:123`是不同主体，数值相同也不得合并或直接混训。历史评分只引用ml_user_id，真实评分和收藏只引用account_id；电影均引用原MovieLens movie_id。

两模式分别创建独立数据库，名称必须以`rec_course_`开头并以`_evaluation`或`_business`结束。默认名称还包含包manifest摘要前缀。导入器禁止同库切换模式、覆盖数据或向非空未知数据库写入，不创建/修改现有业务数据库。

- evaluation：5,000个历史ID均保留，其中4,500个warm、500个cold；history_ratings仅583,502条训练评分，cold用户无训练评分。验证、测试、冷启动query保持在文件中。accounts的CHECK禁止写注册账号；评价结果只写evaluation_results，推荐结果只允许历史用户键。
- business：同样5,000个匿名ID；history_ratings为过滤目录下全部798,775条历史评分，供未来业务演示。accounts、account_ratings、account_favorites初始为空，可以承载真实行为；evaluation_results禁止在业务库写评价结果。这份完整评分不能用于离线评价训练。

SQL中的runtime_mode固定CHECK、导入身份标识及模式专属数据文件共同防止混用。导入身份保存在history_ratings的表COMMENT，不增加第九张审计表。账号/连接权限仍应由部署者按各数据库分别授予；不知道或恶意忽略模式的管理员不在这些数据约束的防护范围内。

## 2. movies：8,459部固定电影

| 字段 | SQL类型/空值 | 来源与含义 |
| --- | --- | --- |
| movie_id | BIGINT UNSIGNED PK，非空，>0 | 原MovieLens movieId，永不重排 |
| movielens_title | TEXT，非空 | 原movies.title，完整保留年份括号、字符及引号 |
| genres | TEXT，非空 | 原MovieLens管道分隔类型；不以TMDB分类覆盖 |
| release_year | SMALLINT UNSIGNED，可NULL | 只解析标题末尾合法四位年份括号，不能当完整上映日期 |
| tmdb_id | BIGINT UNSIGNED，可NULL | 原links关联，尚未在线验证，不假设唯一 |
| imdb_id | VARCHAR(32) ASCII，可NULL | 原links字符串，保留前导零；不加tt、不截断 |
| title_zh / original_title | TEXT，可NULL | 本版未采集，全部NULL；MovieLens标题不能冒充TMDB原名 |
| release_date | DATE，可NULL | 未采集，全部NULL，不补成1月1日 |
| overview | LONGTEXT，可NULL | 未采集，全部NULL |
| directors / cast_top10 | JSON，可NULL | 未采集，全部NULL；不生成导演或演员资料 |
| runtime_minutes | INT UNSIGNED，可NULL | 未采集，全部NULL；非空需>0 |
| original_language | VARCHAR(16)，可NULL | 未采集，全部NULL |
| poster_path | TEXT，可NULL | 未采集，全部NULL；将来有路径也不代表下载了图片 |

中文标题缺失时未来显示可回退到movielens_title，但不能把回退值写成可靠中文字段。TMDB链接来自MovieLens原links，不属于本版API响应证据。教师保留原链接冲突清单，学生不管理复杂审计表。

## 3. 历史数据和真实行为

| 表 | 字段 | 约束 |
| --- | --- | --- |
| history_users | ml_user_id BIGINT UNSIGNED；evaluation_role ENUM(warm,cold) | ID主键且>0；role仅标记固定评测角色，不是业务身份 |
| history_ratings | ml_user_id、movie_id、rating DECIMAL(3,1)、timestamp_utc BIGINT UNSIGNED | 双ID联合主键；外键到历史用户/电影；评分0.5–5.0且半星步长；UTC秒不超过253402300799 |
| accounts | account_id BIGINT UNSIGNED自增PK；login_name VARCHAR(128) UNIQUE；password_hash VARCHAR(255)；created_at DATETIME(6)；runtime_mode | 登录名去空白后非空；哈希非空；只在business允许插入。数据包不生成任何账号或密码 |
| account_ratings | account_id、movie_id、rating；created_at、updated_at DATETIME(6) | 联合主键，双外键；同历史评分范围；updated_at由未来业务代码明确更新 |
| account_favorites | account_id、movie_id、created_at | 联合主键和双外键；收藏不是评分，不隐式转成5星 |

所有历史ID和电影ID均不压缩、不偏移。MySQL会先转换DECIMAL再执行CHECK；因此导入器及未来写接口还必须在转换前验证原始数值是精确半星，拒绝4.01、NaN等，避免数据库舍入掩盖非法输入。评分输入不接受bool或浮点非有限数。

## 4. recommendation_results：结果、批次、模型

基础字段：result_id自增PK；movie_id FK；rank_pos正整数；score可NULL；generated_at UTC。ml_user_id和account_id必须**恰有一个非空**，分别有外键；evaluation禁止account_id。

必填模型标识：runtime_mode、dataset_version、split_version、algorithm、model_version、batch_token。这些字符串非空且不含`|`。batch_token应为一次执行生成的UUID文本或其他唯一执行标识；同批所有结果共享同一组元信息。离线和线上任务、不同数据/划分、不同算法/模型版本、不同用户类别必须形成不同batch_id。

数据库生成：

```text
subject_key = ml:<ml_user_id> 或 app:<account_id>
subject_kind = ml 或 app（由非空用户键决定）
batch_id = SHA256(UTF8(runtime_mode|dataset_version|split_version|algorithm|model_version|batch_token|subject_kind))
```

batch_id与subject_key都是生成列，不由接口直接写入。算法名、模型版本等任一变化会生成不同批次ID；同批同主体movie_id不得重复，rank_pos也不得重复，避免nullable联合索引漏约束。batch_token表示执行实例而不是model_version；相同模型不同次运行应使用不同token。已有批次不改模型版本，重新计算应生成新批次。

可选字段：recall_sources JSON（召回来源数组）、recall_ranks JSON（每个来源的名次映射）、fusion_rank正整数NULL。未融合时fusion_rank为NULL；最终展示顺序始终取rank_pos。未来写接口需校验JSON结构、来源名、名次和有限score，数据库仅保证JSON合法和基础约束。本阶段没有生成任何正式推荐结果，也未实现召回或融合。

模型实际工件保存在未来离线训练目录，由模型清单绑定model_version、训练文件SHA-256、特征版本和参数；本轮不增加模型注册表。若需要查某批对应模型，按batch_id读取其重复保存的必填元信息即可，生成列保证不会把不同元信息并为同一个批次（不考虑密码学哈希碰撞）。

## 5. evaluation_results：评价汇总

evaluation_id自增PK；runtime_mode只能evaluation；algorithm、model_version、dataset_version、split_version、candidate_policy必填。positive_threshold默认4.0，k默认10且>0；precision_at_k、recall_at_k、item_coverage可NULL或在[0,1]；eligible_users非负；parameters为可选JSON；evaluated_at为UTC。

本版不填任何评价得分。未来必须写明用户资格及目录协议，不同算法不能靠改变候选范围或删去失败用户提高指标。一个模型的评价与推荐结果通过model_version、dataset_version、split_version、algorithm和mode一致关联，不用业务账号ID连接匿名用户评价。

## 6. 导入顺序和数据库约束

建表依赖：movies→history_users→history_ratings→accounts→account_ratings→account_favorites→recommendation_results→evaluation_results。实际基础数据按movies→history_users→所选模式history_ratings导入；其余五表为空，等待未来真实业务/算法写入。

使用MySQL8.0.16+、InnoDB、utf8mb4_0900_bin、严格模式和UTC，外键不关闭。所有写入为参数化语句，数据事务校验后提交；没有INSERT IGNORE、REPLACE、TRUNCATE或DROP。重复导入相同manifest和模式时验证三张不可变表的内容指纹并返回verified_noop，保留业务行；模式/版本不符直接拒绝。新建库失败会留下标记pending的空表，不伪装为完整导入；恢复应选择新空库或由教师检查处理，不自动删除已有库。

CSV采用UTF-8标准引号转义，空字段映射SQL NULL；官方文本原样保留，元数据空字符串没有另一种业务含义。SQL逐表外键、评分、主键检查见包内sql/verify.sql；一键工具还检查包摘要、分区、时间、用户隔离及数据库全行内容指纹。

MySQL CHECK和utf8mb4行为依据[官方CHECK文档](https://dev.mysql.com/doc/refman/8.0/en/create-table-check-constraints.html)和[官方utf8mb4文档](https://dev.mysql.com/doc/refman/8.0/en/charset-unicode-utf8mb4.html)。实际执行是否通过以教师的MySQL验收报告为准，不由本字典代替。
