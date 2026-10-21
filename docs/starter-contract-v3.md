# 后续Starter Kit接口与数据约束（仅契约）

本文件不实现接口、推荐算法、前后端或部署。后续开发必须显式绑定dataset_version=`rec-course-v3-train5-1.0.0`、split_version=`temporal-v1-cold500-train5-v3`、schema_version=`student-eight-v3`及manifest SHA-256。

## 1. 电影与用户

- 电影查询以原movie_id作为主键，分页稳定排序；显示标题可回退movielens_title，title_zh为空不是已证实“没有中文译名”。genres为管道分隔原分类。导演、简介、语言、时长、海报等可NULL，不生成补造资料或假图片URL。
- 原TMDB/IMDb关联仅为MovieLens links字段；tmdb_id不能作为唯一业务主键。imdb_id始终为保留前导零的字符串。
- 真实请求身份来自认证后的account_id，不能靠客户端提交ml_user_id授权。历史用户体验入口必须显式只读匿名命名空间`ml:<id>`；`app:<id>`只指真实账号。公开JSON建议把BIGINT ID序列化为十进制字符串，避免JavaScript数值精度丢失。

## 2. 评分、收藏与结果写入

将来评分接口需验证电影存在、认证账号有效、评分是精确0.5至5.0半星步长，拒绝bool、NaN、Infinity、4.01等；只更新account_ratings和updated_at，禁止改history_ratings。建议请求携带十进制字符串rating，服务器先用Decimal验证，不能依赖MySQL舍入。收藏只写account_favorites，不当作评分或曝光。多次收藏采用业务幂等处理，数据库唯一键兜底。

推荐接口需返回algorithm、model_version、dataset_version、split_version、batch_id、generated_at及去重items；每项含movie_id、rank_pos、可NULL score和可选recall_sources/recall_ranks/fusion_rank。无个性化种子时须明确回退来源，不能把热门清单标成个性化结果。score在不同算法之间不假定同标度。

写结果时提供唯一batch_token和固定元信息，数据库自动生成batch_id。不要提交生成列。不同模型、版本、用户类别和运行模式自动分为不同批次；同批同主体最终排名和电影唯一。应用应事务性写完一个主体清单，失败不发布半个清单。JSON召回字段须校验预期结构与正整数名次；数据库不替代业务层全部语义检查。

模型文件未来需要侧车manifest，记录训练文件SHA-256、特征快照、参数、工具版本和model_version；本轮没有模型工件。模式不同，即使模型名相同也不能共享结果缓存或数据库连接配置。

## 3. 离线评价入口

固定输入：evaluation/train.csv拟合；validation仅调参；test在配置冻结后报告一次；本版不将validation并入训练重训。所有共现、热度、支持度及ALS因子仅从train获取；business/history_ratings.csv、cold-history和测试标签不可读入拟合流程。

统一电影目录为8,459个训练支持度≥5的ID。验证候选C=目录−训练已评分；测试候选C=目录−训练已评分−验证已评分；正例为对应留出rating≥4且位于C。只评价训练存在、|C|≥10、|正例|≥1的固定用户：validation4,209人，test4,198人。算法不能输出结果也不得删掉该用户，Precision@10分母保持10。排序并列按movie_id升序。

500个cold用户完全不参与拟合或调参；k=0/1/5/10支持/query沿用原来的时间边界。目录过滤后部分支持不足名义k，不能从未来补分；数据文件已提供retained_support及资格字段。各k均499名有效用户，后续比较须同时报告群体和支持数。k=0属于无交互起点，不声称个性化。

本划分不是全体用户共同时间截点的在线回放；当前没有TMDB可用的历史特征快照。tags不在本包默认特征中。后续若改变候选、用户资格、正例阈值、过滤规则或重训方案，必须创建新split_version及重新统计，禁止覆盖本版指标。

## 4. 模式和验收要求

业务服务只连接`..._business`库，离线评价只连接`..._evaluation`库并核对导入标识中的mode/manifest。启动时若数据版本、模型训练摘要或schema不匹配应拒绝运行，不能自动切换到全量评分。未来数据库角色建议按模式分配最小权限；不会把教师root凭据放入Starter Kit。

后续Starter Kit开始前须由教师验收本数据包及实际MySQL报告。本阶段不建立HTTP路由、不连接Kafka/Redis/LLM、不生成假推荐结果，不提前实现内容模型、ItemCF或ALS服务。
