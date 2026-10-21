# 冻结评价环境与结果格式（阶段4D）

本轮没有指标计算器、模型训练器或评价成绩。原协议以docs/starter-contract-v3.md为准，本文件仅把它整理为可执行的数据和格式检查入口。

## 固定身份与文件

- dataset_version：rec-course-v3-train5-1.0.0
- split_version：temporal-v1-cold500-train5-v3
- schema_version：student-eight-v3
- manifest SHA-256：fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e
- train SHA-256：c07a4c82155b216751129ddf5716717e54bbe2cb772cdf3a44f24e1f3971c342

路径均相对data/packages/rec-course-v3-train5-1.0.0：evaluation/train.csv（583,502条）、validation.csv（70,366条）、test.csv（67,792条），cold-k0/1/5/10各自support/query/users.csv；另读冻结的evaluation-eligibility.csv、per-user-counts.csv、training-catalog.csv、history_users.csv及movies.csv。每个文件绑定原manifest的SHA-256、字段和行数；不会重新划分或覆盖正式文件。

离线读取器没有通用路径入口：business/history_ratings.csv、cold-history.csv、任意外部路径和越界链接均拒绝。`.training()`只返回train；没有读取evaluation数据库的配置分支，当前实现选择冻结文件方案。

```bash
backend/.venv/bin/python -m recommender.check evaluation-inputs
backend/.venv/bin/python -m recommender.check task --partition validation
backend/.venv/bin/python -m recommender.check task --partition test
backend/.venv/bin/python -m recommender.check task --partition cold-k5
backend/.venv/bin/python -m recommender.check schemas
```

任务描述含固定文件摘要、目录、候选规则、用户数/集合摘要和确定性的task_sha256。用户摘要计算方法：userId数值升序，每行十进制ID加LF，再做SHA-256。它是任务身份，不是指标结果。

## 所有算法共用的规则

正反馈为显式评分≥4.0；固定Top10，不做负采样。统一目录是训练支持度≥5的8,459部电影。

| 分区 | 候选目录 | 固定有效用户 |
|---|---|---:|
| validation | 目录−该用户全部训练已评分电影 | 4,209 |
| test | 目录−训练已评分−验证已评分电影 | 4,198 |
| cold-k0/1/5/10 | 目录−该用户对应support已评分电影 | 各499 |

留出分区中≥4.0且在候选中的电影才是相关目标。Precision@10分母始终10，Recall@10分母为该用户相关目标数；对固定有效用户宏平均。算法少于10条仍保留该用户，空结果须提交items=[]，不能删除困难用户。并列按movie_id数值升序。

普通评价只包含训练存在、候选≥10且至少一个相关目标的固定集合，以eligibility文件为准。validation只调参；test在超参数冻结后作最终报告，本版不把validation并回train重训。500个cold用户不参与拟合/调参；support仅用于各用户推断/适配，query仅作标签。过滤后实际支持可能小于名义k，按users文件的retained_support报告，不能从未来补足。k=0不声称个性化。

该协议不是全局共同时间点的在线回放；没有历史TMDB特征快照，也不把tags作为默认特征。改变目录、资格、候选、正例阈值或拟合规则需要另立版本，不能覆盖本版。

## 模型清单与隔离

在独立models/business/<版本>/或models/evaluation/<版本>/保存真实工件及manifest.json。当前工程不提供已训练模型。清单字段精确定义由schemas命令给出：

| 字段 | 要求 |
|---|---|
| algorithm、model_version、runtime_mode | 五个原标识之一、真实自定义版本、明确模式 |
| dataset_version、split_version、schema_version、manifest_sha256 | 上述固定值 |
| training_files | evaluation仅允许evaluation/train.csv及其准确SHA；business绑定business/history_ratings.csv及准确SHA |
| feature_files | 可选movies.csv/training-catalog.csv及准确SHA |
| artifacts | 至少一个真实工件，相对清单目录的文件路径→SHA-256；不允许越界或清单自身 |
| parameters、tool_versions | 真实参数与实际训练工具版本；不冒充已运行 |

评价模式不允许business训练清单、validation/test拟合、旧数据/旧划分、错误工件摘要。业务API只连接business库，检查业务清单时不读取评价评分文件。清单只能验证声明和文件一致性，不能从数学上证明学生没在别处偷读标签；Python开发环境也不是操作系统文件访问沙箱，仍须审查训练代码。

## 预测与评价结果格式

未来学生将逐用户真实预测导出JSONL，每行仅含`ml_user_id`（字符串）与`result`（已有RecommendationResult的JSON形式）。result.runtime_mode=evaluation，limit=10，对应任务的每个有效用户必须恰好一行；空items合法。此工具只检查用户集合、DTO、目录及候选排除，不与正例求交，也不计算命中数。

未来学生计算完指标后导出EvaluationResult，字段见schemas：status、runtime_mode、algorithm、model_version、task_sha256、model_manifest_sha256、三项数据版本、partition、user_count、precision_at_10、recall_at_10。not_run状态两个指标必须NULL；completed才可填实际计算的有限[0,1]数值。格式验证不证明指标计算正确。

以下命令依赖学生未来真实工件/结果，本轮不生成这些成果文件：

```bash
backend/.venv/bin/python -m recommender.check model models/evaluation/itemcf-v1/manifest.json --mode evaluation
backend/.venv/bin/python -m recommender.check predictions predictions.jsonl \
  --model models/evaluation/itemcf-v1/manifest.json --partition validation
backend/.venv/bin/python -m recommender.check evaluation-result evaluation-result.json \
  --model models/evaluation/itemcf-v1/manifest.json
```

各命令仅验证，不训练、不排序、不计算指标、不写数据库。不要把验证通过当作算法完成或效果达标。本轮协议测试中的临时工件明确标记PROTOCOL ONLY、NOT A MODEL，不是教学推荐成果。
