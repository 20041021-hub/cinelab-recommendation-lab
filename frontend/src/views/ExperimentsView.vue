<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import http, { ApiError } from '../api'
import { useRemote } from '../composables/useRemote'
import type { Algorithm, BehaviorStatus, HistoryUser, Movie, Page } from '../types'
import StatePanel from '../components/StatePanel.vue'
import PageNav from '../components/PageNav.vue'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
import FusionPanel from '../components/FusionPanel.vue'
import AssistantPanel from '../components/AssistantPanel.vue'
import lab3Metrics from '../data/lab3-metrics.json'
import lab4Fusion from '../data/lab4-fusion.json'
const route = useRoute(), router = useRouter(), auth = useAuth()
const source = ref('ml'), algorithm = ref('content'), selected = ref('')
const userPage = ref(1), users = useRemote<Page<HistoryUser>>(), ratings = useRemote<Page<Movie>>()
const ratingPage = ref(1), available = ref<Algorithm[]>([]), registryError = ref<unknown>(null)
const algorithmGroups = computed(() => [
  { label: '核心算法 · 学生实现', items: available.value.filter(a => ['content', 'itemcf', 'als'].includes(a.algorithm)) },
  { label: '配套策略 · 热门与冷启动', items: available.value.filter(a => ['popular', 'cold_start'].includes(a.algorithm)) },
])
const evaluationError = ref<unknown>(null), evaluationPending = ref(false), evaluationMissing = ref(false)
interface MetricRow { algorithm: string; partition: string; model_version: string | null; users: number; empty: number; precision_at_10: number | null; recall_at_10: number | null }
interface MetricFile { generated_at: string | null; validation: MetricRow[]; test: MetricRow[]; cold: MetricRow[] }
const metrics = lab3Metrics as MetricFile
const evaluated = computed(() => [...metrics.validation, ...metrics.test, ...metrics.cold].length > 0)
function formatMetric(value: number | null) { return value === null ? '—' : value.toFixed(4) }
const modelVersion = computed(() => ({ content: 'content-genre-tfidf-v1', popular: 'popular-business-v1', itemcf: 'itemcf-cooccurrence-v1', als: 'als-mllib-v1', cold_start: 'coldstart-popular-content-v1' } as Record<string, string>)[algorithm.value] || '等待算法接入')
const compare = ref(false)
const fusion = ref(false), fusionPreset = ref('selected')
interface FusionStudyRow { config: string; label: string; weights: Record<string, number>; precision_at_10: number; recall_at_10: number; empty_users: number; multi_source_items: number }
interface FusionStudyFile {
  generated_at: string; partition: string; user_count: number; depth: number; k: number; selection_rule: string
  selected: { config: string; label: string; sources: string[]; precision_at_10: number; recall_at_10: number } | null
  baselines: Record<string, { algorithm: string; precision_at_10: number; recall_at_10: number }>
  grid: FusionStudyRow[]
  test: (FusionStudyRow & { partition: string; user_count: number }) | null
}
const fusionMetrics = lab4Fusion as unknown as FusionStudyFile
const fusionPresets = [
  { value: 'selected', label: '默认 · 验证集选定权重' },
  { value: 'content-itemcf', label: 'Content ×1 + ItemCF ×4' },
  { value: 'equal', label: '四路等权 popular+content+itemcf+als' },
  { value: 'als-heavy', label: 'ItemCF ×2 + ALS ×2' },
]
const FUSION_QUERY: Record<string, string> = {
  selected: '',
  'content-itemcf': '?algorithms=content,itemcf&weights=1,4',
  equal: '?algorithms=popular,content,itemcf,als&weights=1,1,1,1',
  'als-heavy': '?algorithms=itemcf,als&weights=2,2',
}
const fusionEndpoint = computed(() => {
  if (!subject.value || !fusion.value) return null
  const base = source.value === 'ml' ? `/demo/history-users/${selected.value}/recommendations/fusion` : '/me/recommendations/fusion'
  const query = FUSION_QUERY[fusionPreset.value] || ''
  const extras = `interest=${interest.value ? 1 : 0}&adaptive=${adaptive.value ? 1 : 0}`
  return query ? `${base}${query}&${extras}` : `${base}?${extras}`
})
const interest = ref(true)
const adaptive = ref(true)
const assistantEndpoint = computed(() => !subject.value ? null : source.value === 'ml' ? `/demo/history-users/${selected.value}/assistant/query` : '/me/assistant/query')
const behavior = useRemote<BehaviorStatus>()
interface InterestRow { movie_id: string; genres: string | null; positive: boolean; occurred_at: string; offset: number }
interface MyInterest {
  subject_key: string
  interest: { top_genres: { genre: string; weight: number }[]; events: number; state_version: number
              updated_at: string; stale: boolean; genres: Record<string, number> } | null
  reason: string | null
  producer: BehaviorStatus['producer']
  consumer: BehaviorStatus['consumer']
  events: InterestRow[]
}
const myInterest = useRemote<MyInterest>()
async function fetchBehavior() {
  await behavior.load(async () => (await http.get<BehaviorStatus>('/experiments/behavior/status')).data)
  if (auth.account) await myInterest.load(async () => (await http.get<MyInterest>('/me/behavior/interest', { params: { events: 8 } })).data)
}
const interestGenres = computed(() => (myInterest.data.value?.interest?.top_genres || []).slice(0, 6))
const panelCount = computed(() => (subject.value ? 1 : 0) + (compare.value && subject.value ? 1 : 0) + (fusion.value && subject.value ? 1 : 0))
const fusionBaselines = computed(() => Object.values(fusionMetrics.baselines || {}))
const fusionGrid = computed(() => fusionMetrics.grid || [])
const subject = computed(() => source.value === 'ml' ? (selected.value ? `ml:${selected.value}` : '') : auth.account?.subject_key || '')
const endpoint = computed(() => !subject.value ? null : source.value === 'ml' ? `/demo/history-users/${selected.value}/recommendations/${algorithm.value}` : `/me/recommendations/${algorithm.value}`)
const compareAlgorithm = computed(() => algorithm.value === 'itemcf' ? 'content' : 'itemcf')
const compareEndpoint = computed(() => !subject.value ? null : source.value === 'ml' ? `/demo/history-users/${selected.value}/recommendations/${compareAlgorithm.value}` : `/me/recommendations/${compareAlgorithm.value}`)
function updateUrl() { void router.replace({ path:'/experiments', query:{ source:source.value, algorithm:algorithm.value, ...(selected.value ? {user:selected.value} : {}) } }) }
async function fetchUsers(page = userPage.value) { userPage.value = page; await users.load(async () => (await http.get<Page<HistoryUser>>('/demo/history-users',{params:{page,per_page:20}})).data) }
function fetchRatings() { if (source.value !== 'ml' || !selected.value) return; return ratings.load(async () => (await http.get<Page<Movie>>(`/demo/history-users/${encodeURIComponent(selected.value)}/ratings`,{params:{page:ratingPage.value,per_page:10}})).data) }
async function fetchRegistry() { try { available.value=(await http.get<{items:Algorithm[]}>('/experiments/algorithms')).data.items; registryError.value=null } catch(e) { registryError.value=e } }
async function fetchEvaluation() { evaluationPending.value=true; evaluationError.value=null; evaluationMissing.value=false; try { await http.get('/experiments/evaluations') } catch(e) { if(e instanceof ApiError && e.status===501 && e.code==='EVALUATION_NOT_IMPLEMENTED') evaluationMissing.value=true; else evaluationError.value=e } finally { evaluationPending.value=false } }
watch(() => route.query, () => { source.value=route.query.source==='app'?'app':'ml'; algorithm.value=typeof route.query.algorithm==='string' && /^[a-z_]+$/.test(route.query.algorithm) ? route.query.algorithm:'content'; selected.value=typeof route.query.user==='string' && /^[1-9][0-9]*$/.test(route.query.user)?route.query.user:'' }, {immediate:true})
watch(() => [selected.value,source.value], () => {ratingPage.value=1; void fetchRatings()})
void fetchUsers(); void fetchRegistry(); void fetchEvaluation(); void fetchRatings(); void fetchBehavior()
watch(() => auth.account?.subject_key, () => { void fetchBehavior() })
</script>
<template>
  <div class="page"><div class="page-heading"><p class="eyebrow">RECOMMENDATION LAB</p><h1>推荐实验室<span class="heading-dot">.</span></h1><p>从一位观众的历史出发，探索不同算法的推荐结果。</p></div><div class="lab-notice"><span class="status-pill">课程实验</span><p>历史用户只读；注册账号与匿名用户相互隔离。内容推荐、热门榜、冷启动、ItemCF 与 ALS 已接入；离线评价由独立进程产出验证集、测试集与冷启动分区指标；实验四的 S1 加权 RRF、实验五的 S3 短期兴趣与创新点 A 的自适应权重通过“显示实验四 · S1 加权融合”一组开关展示；实验六的 S2 自然语言助手在页面底部，它只能通过受控工具读取真实电影数据。</p></div>
    <div class="lab-layout"><aside class="lab-controls"><h2>实验设置</h2><label for="subject-source">用户来源</label><el-select id="subject-source" v-model="source" @change="updateUrl"><el-option label="MovieLens 历史用户（只读）" value="ml" /><el-option label="当前注册账号" value="app" /></el-select><template v-if="source==='ml'"><label for="history-user">选择历史用户</label><el-select id="history-user" v-model="selected" placeholder="请从真实用户列表选择" @change="updateUrl"><el-option v-for="u in users.data.value?.items" :key="u.ml_user_id" :label="`${u.subject_key} · ${u.evaluation_role}`" :value="u.ml_user_id" /></el-select><StatePanel v-if="users.error.value" :error="users.error.value" @retry="fetchUsers()" /><p v-if="users.loading.value" class="muted" role="status">正在加载用户…</p><div class="user-pager"><el-button :disabled="userPage===1 || users.loading.value" @click="fetchUsers(userPage-1)">上一组</el-button><span>{{ userPage }} / {{ Math.ceil((users.data.value?.total || 0)/20) || 1 }}</span><el-button :disabled="!users.data.value || userPage*20>=users.data.value.total || users.loading.value" @click="fetchUsers(userPage+1)">下一组</el-button></div><p class="form-hint">按原始用户ID分页，支持全部课堂历史用户。</p></template><div v-else class="current-subject"><p>{{ auth.account?.login_name || '请先登录注册账号' }}</p><span>{{ auth.account?.subject_key || '尚未登录' }}</span><RouterLink v-if="!auth.account" to="/login" class="text-link">前往登录 →</RouterLink></div><label for="algorithm-select">推荐算法</label><el-select id="algorithm-select" v-model="algorithm" @change="updateUrl"><el-option-group v-for="group in algorithmGroups" :key="group.label" :label="group.label"><el-option v-for="a in group.items" :key="a.algorithm" :label="a.algorithm" :value="a.algorithm" /></el-option-group></el-select><div class="algorithm-scope"><p><strong>本实验已接入</strong>内容推荐（content）、热门（popular）、冷启动（cold_start）、ItemCF（itemcf）、Spark ALS（als）。</p><p><strong>推荐策略</strong>热门按全站正反馈人数排序；冷启动无正反馈时使用热门榜，有 4 分以上评分后切换为内容画像。</p></div><el-checkbox v-model="compare" class="compare-toggle">与 {{ compareAlgorithm }} 对照</el-checkbox><el-checkbox v-model="fusion" class="compare-toggle">显示实验四 · S1 加权融合</el-checkbox><template v-if="fusion"><label for="fusion-preset">融合权重方案</label><el-select id="fusion-preset" v-model="fusionPreset"><el-option v-for="preset in fusionPresets" :key="preset.value" :label="preset.label" :value="preset.value" /></el-select><p class="form-hint">默认权重由验证集对照实验选定；切换方案会改变融合的 algorithms/weights 查询参数，可在同一用户上直接比较。</p><el-checkbox v-model="interest" class="compare-toggle">融合时叠加 S3 短期兴趣</el-checkbox><p class="form-hint">勾选后融合请求带 <code>interest=1</code>：按 Kafka 事件衰减出的类型兴趣重排候选；未启用、无事件或状态过期时返回未加权结果并在面板说明原因。</p><el-checkbox v-model="adaptive" class="compare-toggle">创新点 A · 兴趣自适应融合权重</el-checkbox><p class="form-hint">勾选后融合请求带 <code>adaptive=1</code>：用同一份短期兴趣状态估计每路候选与兴趣的匹配度，把固定权重放缩成 <code>w' = w × (1 + β × affinity)</code> 再融合，面板会显示每路的原始权重、匹配度与生效权重。</p></template><StatePanel v-if="registryError" :error="registryError" @retry="fetchRegistry" /><div class="model-info"><span>当前对象</span><strong>{{ subject || '未选择' }}</strong><span>模型版本</span><strong>{{ modelVersion }}</strong><span>运行模式</span><strong>business · 只读实验入口</strong></div></aside>
    <div class="lab-results"><div class="lab-result-grid" :class="{ 'is-comparing': compare && !!subject, 'is-multi': panelCount > 1 }"><AlgorithmPanel :label="`${algorithm} · 推荐结果`" :endpoint="endpoint" :empty-subject="source==='ml' ? '请选择历史用户' : '登录后体验'" /><AlgorithmPanel v-if="compare && subject" :label="`${compareAlgorithm} · 对照`" :endpoint="compareEndpoint" :empty-subject="source==='ml' ? '请选择历史用户' : '登录后体验'" /><FusionPanel v-if="fusion && subject" :endpoint="fusionEndpoint" :empty-subject="source==='ml' ? '请选择历史用户' : '登录后体验'" /></div><section v-if="source==='ml'" class="history-panel"><div class="section-top"><h2>历史评分</h2><span class="status-pill">{{ subject || '未选择用户' }} · 只读</span></div><StatePanel :loading="ratings.loading.value" :error="ratings.error.value" :empty="!selected || !!ratings.data.value && !ratings.data.value.items.length" title="选择一位历史观众" description="这里将显示真实历史评分，不提供修改入口。" @retry="fetchRatings"><div class="table-scroll"><el-table :data="ratings.data.value?.items || []" aria-label="历史用户只读评分表"><el-table-column label="电影" min-width="200"><template #default="scope"><RouterLink :to="`/movies/${scope.row.movie_id}`" class="text-link">{{ scope.row.movielens_title }}</RouterLink></template></el-table-column><el-table-column prop="movie_id" label="电影ID" width="100" /><el-table-column prop="rating" label="历史评分" width="100" /></el-table></div><PageNav :page="ratingPage" :size="10" :total="ratings.data.value?.total || 0" @change="ratingPage=$event;fetchRatings()" /></StatePanel></section><section class="evaluation-panel"><div class="section-top"><h2>离线评价</h2><span class="small-label">PRECISION@10 / RECALL@10</span></div>
        <div class="evaluation-contract"><span class="status-pill">{{ evaluationMissing ? '业务 API 501 · 契约正确' : evaluationPending ? '检查中' : '契约检查未通过' }}</span><p>评价在独立离线进程执行，业务 API 不连接 evaluation 数据库；下面的指标由 <code>scripts/run_lab3_evaluation.py</code> 生成后随代码提交。</p></div>
        <StatePanel :loading="evaluationPending" :error="evaluationError" @retry="fetchEvaluation">
          <div v-if="!evaluated" class="evaluation-empty"><span class="status-pill">等待离线评价运行</span><p>还没有可展示的评价指标，请先运行离线评价脚本。</p><small>训练仅允许使用固定 train；此页面不连接 evaluation 数据库。</small></div>
          <div v-else class="evaluation-tables">
            <div class="evaluation-block"><div class="evaluation-block-head"><h3>验证集 · 调参与方案选择</h3><span class="status-pill">4,209 位固定评价用户</span></div><p class="form-hint">正反馈阈值 4.0（评分 ≥ 4.0），Top-10；候选已排除该用户在 train 中的评分，无结果的用户仍计入宏平均分母。</p><el-table :data="metrics.validation" aria-label="验证集离线指标"><el-table-column prop="algorithm" label="算法" width="120" /><el-table-column label="Precision@10" width="150"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="140"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column><el-table-column prop="users" label="有效用户" width="110" /><el-table-column prop="empty" label="空结果" width="100" /><el-table-column prop="model_version" label="模型版本" min-width="210" /></el-table></div>
            <div class="evaluation-block"><div class="evaluation-block-head"><h3>测试集 · 方案冻结后只报一次</h3><span class="status-pill">4,198 位固定评价用户</span></div><p class="form-hint">参数在验证集上选定后不再改动；候选额外排除 validation 中的评分。没有可用的评价指标时请先运行离线评价脚本。</p><el-table :data="metrics.test" aria-label="测试集离线指标"><el-table-column prop="algorithm" label="算法" width="120" /><el-table-column label="Precision@10" width="150"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="140"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column><el-table-column prop="users" label="有效用户" width="110" /><el-table-column prop="empty" label="空结果" width="100" /><el-table-column prop="model_version" label="模型版本" min-width="210" /></el-table></div>
            <div class="evaluation-block"><div class="evaluation-block-head"><h3>冷启动分区 · 每位用户 499 人</h3><span class="status-pill">k = 0 / 1 / 5 / 10</span></div><p class="form-hint">support 只用于逐用户推断，query 只作为标签；k = 0 时热门与冷启动策略都不声称个性化。</p><el-table :data="metrics.cold" max-height="340" aria-label="冷启动分区离线指标"><el-table-column prop="partition" label="分区" width="110" /><el-table-column prop="algorithm" label="算法" width="120" /><el-table-column label="Precision@10" width="150"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="140"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column><el-table-column prop="users" label="有效用户" width="110" /><el-table-column prop="empty" label="空结果" width="100" /></el-table></div>
          </div>
        </StatePanel></section><section class="fusion-study"><div class="section-top"><h2>实验四 · S1 融合权重对照</h2><span class="small-label">VALIDATION · 固定评价用户</span></div>
        <div class="evaluation-contract"><span class="status-pill">{{ fusionMetrics.user_count }} 位验证集用户</span><p>同一批真实候选上只改变权重；单路基线来自实验三评价表，测试集在方案冻结后只报告一次。融合入口：<code>GET /api/v1/experiments/fusion/sources</code> 与 <code>/recommendations/fusion</code>。</p></div>
        <div class="evaluation-tables">
          <div class="evaluation-block"><div class="evaluation-block-head"><h3>单路基线 · 实验三口径</h3><span class="status-pill">Top-10 · 正反馈 4.0</span></div>
            <el-table :data="fusionBaselines" aria-label="单路基线指标"><el-table-column prop="algorithm" label="算法" width="120" /><el-table-column label="Precision@10" width="150"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="140"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column></el-table></div>
          <div class="evaluation-block"><div class="evaluation-block-head"><h3>权重对照（按 Precision@10 降序）</h3><span class="status-pill">选定：{{ fusionMetrics.selected?.label || '待发布' }}</span></div>
            <p class="form-hint">{{ fusionMetrics.selection_rule }}</p>
            <el-table :data="fusionGrid" max-height="420" aria-label="加权 RRF 权重对照表"><el-table-column prop="label" label="权重配置" min-width="235" /><el-table-column label="Precision@10" width="140"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="130"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column><el-table-column prop="multi_source_items" label="多路命中" width="110" /><el-table-column prop="empty_users" label="空结果用户" width="120" /></el-table></div>
          <div v-if="fusionMetrics.test" class="evaluation-block"><div class="evaluation-block-head"><h3>测试集 · 方案冻结后单次报告</h3><span class="status-pill">{{ fusionMetrics.test.partition }} · {{ fusionMetrics.test.user_count }} 位用户</span></div>
            <el-table :data="[fusionMetrics.test]" aria-label="融合测试集单次报告"><el-table-column prop="label" label="权重配置" min-width="235" /><el-table-column label="Precision@10" width="140"><template #default="scope">{{ formatMetric(scope.row.precision_at_10) }}</template></el-table-column><el-table-column label="Recall@10" width="130"><template #default="scope">{{ formatMetric(scope.row.recall_at_10) }}</template></el-table-column><el-table-column prop="empty_users" label="空结果用户" width="120" /></el-table></div>
          <p class="form-hint">数据由 <code>scripts/publish_lab4_metrics.py</code> 生成：<code>frontend/src/data/lab4-fusion.json</code>。</p>
        </div></section><section class="behavior-panel"><div class="section-top"><h2>实验五 · S3 行为事件与短期兴趣</h2><span class="small-label">KAFKA → 消费端 → 重排</span></div>
        <div class="evaluation-contract"><span class="status-pill">生产端 {{ behavior.data.value?.producer.sent ?? 0 }} 条</span><span class="status-pill">失败 {{ behavior.data.value?.producer.failed ?? 0 }}</span><span class="status-pill">跳过 {{ behavior.data.value?.producer.skipped ?? 0 }}</span><span class="status-pill">消费端状态版本 {{ behavior.data.value?.consumer.state_version ?? 0 }}</span><p>评分与收藏写入 MySQL 成功后发布行为事件（topic <code>{{ behavior.data.value?.producer.topic || 'cinelab.behavior.business' }}</code>）；消费端按半衰期把事件折算成类型兴趣，写进 <code>{{ behavior.data.value?.consumer.state_path || 'reports/lab5/state/interest.json' }}</code>，融合推荐读取它重排。Kafka 不可用时业务写入照常成功，状态在这里显示失败与跳过计数。</p></div>
        <StatePanel :loading="behavior.loading.value" :error="behavior.error.value" :empty="!myInterest.data.value?.interest && !myInterest.data.value?.reason" title="登录后查看自己的短期兴趣" description="用注册账号评分或收藏几部电影，再运行消费端，就能在这里看到衰减后的类型兴趣。" @retry="fetchBehavior">
          <div class="evaluation-block"><div class="evaluation-block-head"><h3>{{ myInterest.data.value?.subject_key || '当前账号' }} 的短期兴趣</h3><span class="status-pill">{{ myInterest.data.value?.interest ? `事件 ${myInterest.data.value.interest.events} 条` : '暂无状态' }}</span></div>
            <p class="form-hint">{{ myInterest.data.value?.reason || `状态更新于 ${myInterest.data.value?.interest?.updated_at}，半衰期 ${myInterest.data.value?.interest?.stale ? '已过期' : '30 分钟'}。` }}</p>
            <div v-if="interestGenres.length" class="behavior-genres"><span v-for="row in interestGenres" :key="row.genre" class="status-pill">{{ row.genre }} {{ row.weight.toFixed(3) }}</span></div>
            <el-table :data="myInterest.data.value?.events || []" aria-label="最近的行为事件"><el-table-column prop="movie_id" label="电影ID" width="100" /><el-table-column prop="genres" label="类型" min-width="220" /><el-table-column label="方向" width="90"><template #default="scope">{{ scope.row.positive ? '加分' : '减分' }}</template></el-table-column><el-table-column prop="occurred_at" label="发生时间" width="230" /></el-table>
          </div>
        </StatePanel></section>
      <section class="assistant-section"><div class="section-top"><h2>实验六 · S2 自然语言助手</h2><span class="small-label">受控工具调用 · 数据必须来自真实接口</span></div>
        <div class="evaluation-contract"><span class="status-pill">{{ subject || '未选择对象' }}</span><span class="status-pill">工具：search_movies / similar_movies / recommend_movies / my_history</span><p>助手把中文问题交给外部模型（DeepSeek），模型只能通过这四个只读工具取数；片单只由工具输出构成，工具参数逐个校验，越界调用会被拒绝并显示在结果里。外部模型不可用时返回明确的 503 状态，不用假数据兜底。</p></div>
        <AssistantPanel :endpoint="assistantEndpoint" :empty-subject="source==='ml' ? '请选择历史用户' : '登录后体验'" />
      </section></div></div>
  </div>
</template>

<style scoped>
.algorithm-scope { margin-top: 14px; color: var(--muted); font-size: 12px; line-height: 1.8; }
.algorithm-scope p + p { margin-top: 10px; }
.algorithm-scope strong { display: block; color: var(--text); font-weight: 500; }
.compare-toggle { margin-top: 14px; }
.evaluation-contract { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 14px 18px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 12px; line-height: 1.7; }
.evaluation-contract p { margin: 0; }
.evaluation-contract code { padding: 1px 5px; border-radius: 4px; background: #0f1b1b; color: var(--text); font-size: 11px; }
.evaluation-tables { display: grid; gap: 18px; padding: 18px; }
.evaluation-block { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.evaluation-block-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; padding: 13px 16px; border-bottom: 1px solid var(--border); background: #0f1b1b66; }
.evaluation-block-head h3 { font-size: 15px; }
.evaluation-block .form-hint { padding: 10px 16px 0; }
.evaluation-block :deep(.el-table) { margin-top: 6px; }
.lab-result-grid.is-comparing, .lab-result-grid.is-multi { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 14px; align-items: start; }
.fusion-study, .behavior-panel, .assistant-section { margin-top: 0; }
.lab-result-grid { order: 0; }
.fusion-study { order: 1; }
.behavior-panel { order: 2; }
.assistant-section { order: 3; }
.history-panel { order: 4; }
.evaluation-panel { order: 5; }
.evaluation-tables { gap: 14px; padding: 16px; }
.evaluation-block-head { padding: 11px 14px; }
.evaluation-block .form-hint { padding: 8px 14px 0; }
@media (min-width: 1280px) {
  .lab-results { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 16px; }
  .lab-result-grid, .fusion-study, .history-panel, .evaluation-panel { grid-column: 1 / -1; }
  .behavior-panel { grid-column: 1; }
  .assistant-section { grid-column: 2; }
}
.assistant-section .evaluation-contract { border: 1px solid var(--border); border-radius: 8px; }
.assistant-section :deep(.assistant-panel) { padding: 18px; }
.behavior-genres { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 4px; }
@media (max-width: 1100px) { .lab-result-grid.is-comparing, .lab-result-grid.is-multi { grid-template-columns: 1fr; } }
</style>
