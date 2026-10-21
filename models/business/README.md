此目录保存学生生成的真实 business 模型及其版本/摘要清单。
当前包含实验一的 `content-v1/`（类型 TF-IDF 内容推荐）。
Compose 将此目录只读挂载到 `/app/models/business`，沿用阶段 4D 的
`model_manifest_path`；不得放入 evaluation 模型用于业务推荐。
