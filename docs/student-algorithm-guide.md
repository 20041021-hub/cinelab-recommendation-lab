# 学生算法接入：从已有工程开始

当前五个算法均未实现；本说明不提供推荐逻辑。先完成内容推荐、ItemCF、Spark ALS三个核心算法，再按课程要求处理热门/冷启动策略。无需重新开发认证、页面或数据库迁移。

## 1. 先运行自检

Windows先按[详细指南](Windows实验环境安装与启动指南.md)初始化并启动系统。在项目根目录打开允许执行课程脚本的PowerShell，载入原项目保护函数，再在当前API容器执行检查（无需宿主Python）：

```powershell
. .\deployment\offline\runtime.ps1
Read-CourseRelease
Open-CourseRuntime -RequireSaved
Assert-CourseEngine
Assert-CourseOwnership
```

```powershell
Invoke-CourseCompose exec -T api python -m recommender.check selfcheck
Invoke-CourseCompose exec -T api python -m recommender.check schemas
Invoke-CourseCompose exec -T api python -m recommender.check evaluation-inputs
Invoke-CourseCompose exec -T api python -m recommender.check task --partition validation
```

当前selfcheck应显示五个not_implemented、对应501、生成0条结果。schemas输出精确JSON Schema；evaluation-inputs只验证真实冻结文件，task只描述评价任务，不是评价成绩。失败退出码1，成功0。

## 2. 在哪里写、如何注册

在`recommender/student/`自行添加content.py、itemcf.py、als.py等；不要把训练代码放进HTTP路由，不要在模块导入时自动训练或写数据库。

沿用`recommender/contracts.py`：实例实现同步`recommend(request: RecommendationRequest) -> RecommendationResult`。已实现实例还需有`model_manifest_path`属性，指向真实模型侧车清单。该属性用于身份校验，没有新增插件系统。

**完成算法、真实模型和清单后**，在contracts.py末尾显式注册（以下为未来操作，本轮没有这些学生文件）：

```python
from recommender.student.itemcf import ItemCFProvider
PROVIDERS[Algorithm.itemcf] = ItemCFProvider(
    model_manifest_path=Path("models/business/itemcf-v1/manifest.json")
)
```

只注册已完成的算法。未注册继续501；注册但缺少清单会明确失败，不能靠空壳冒充完成。保存后按详细指南运行build.ps1 -Target backend重建并启动；现有前端无需改API路径。

## 3. 输入与允许的数据

Request只有以下字段，**不自动携带评分列表**：

| 字段 | 含义 |
|---|---|
| subject_kind、subject_id | ml历史匿名用户或app真实账号及其原始数值ID |
| limit | Top-N，1—50；评价固定10 |
| runtime_mode | business或evaluation |
| dataset_version、split_version | 固定课堂包和划分版本 |

ml:123与app:123互不相干；HTTP的app身份来自已验收鉴权，不能靠查询参数选择其他账号。

业务推断需要用户历史时可调用`recommender.business.read_history(request)`。它复用后端已有环境配置和最小权限business连接，返回subject_key、真实ratings及单独的favorite_movie_ids；收藏不自动当作高评分。ml只读取history_ratings，app只读取该account的account_ratings/account_favorites。评价模式调用它会被拒绝。不要给学生代码配置root或evaluation数据库账号。

离线拟合只能调用`FrozenData().training()`读取evaluation/train.csv；静态特征用`FrozenData().rows("movies.csv")`，目录用`.catalog()`。validation/test和cold支持/query按[评价说明](evaluation-environment.md)分开使用，不能进入拟合。业务模型与评价模型使用不同目录和进程；business模型清单绑定business历史快照，evaluation模型清单只能绑定固定train。离线工具没有数据库连接。

业务模型准备时可在独立业务脚本中使用封存的business/history_ratings.csv，并核对manifest所列摘要；该文件不属于评价读取器白名单。当前不提供任何拟合脚本。电影CSV的genres仍为管道分隔原分类，空资料保留NULL，不能补造简介等特征。

## 4. 返回已有DTO

返回真正的RecommendationResult，不返回任意字典。外层包含algorithm、model_version、dataset_version、split_version、runtime_mode、generated_at（有时区）、可选batch_id和items。每项包含movie_id字符串、连续rank、相同algorithm/model_version、有限score或NULL，及可选recall_sources/recall_ranks。

- movie_id必须在8,459部批准目录，去重，rank按列表顺序从1连续递增，不超过limit；同分按movie_id数值升序。
- 无候选时返回合法元信息与items=[]；不能补随机电影、复制候选或伪造分数。尚未实现则不注册，保持501。
- model_version填写你实际生成的版本（例如自行确定的itemcf-v1），必须与清单及每一项一致；数据版本不可自行改写。
- 本轮接口只校验并返回JSON，不写recommendation_results；不需要新增表、模型管理服务或批次系统。batch_id可NULL。

外层入口会检查方法签名、输入、模型工件及结果；错误返回已有统一错误信封：模型/数据绑定失败503，Provider输出非法500。没有虚构的成功回退。

## 5. 学生实现后的命令

这些命令只有在你已完成对应文件/工件后才执行，不代表教师已提供模型：

```powershell
Invoke-CourseCompose exec -T api python -m recommender.check selfcheck `
  --module recommender.student.itemcf --algorithm itemcf `
  --subject-kind ml --subject-id 14 --mode business --limit 10
Invoke-CourseCompose exec -T api python -m recommender.check model `
  models/business/itemcf-v1/manifest.json --mode business
```

如果使用app，必须显式指定真实account_id；自检不会把ml用户当账号。测试算法输出时应选真实允许的用户，CLI成功只证明接口和元信息有效，不证明推荐效果好。

评价是独立进程：创建你自己的评价入口，实例化**evaluation清单**的Provider并赋给同一个PROVIDERS入口，再用`--module 你的评价入口 --mode evaluation`自检。不要让API加载评价Provider，不能仅改HTTP参数切换运行模式。

现有路由保持：`/api/v1/me/recommendations/{algorithm}`与`/api/v1/demo/history-users/{ml_user_id}/recommendations/{algorithm}`。首页/推荐实验室已经支持真实501空状态和合法结果；返回空items时显示真实无结果。不要改前端来伪造完成状态。
