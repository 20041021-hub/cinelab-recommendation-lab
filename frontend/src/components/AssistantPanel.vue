<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import http, { ApiError } from '../api'
import type { AssistantResult } from '../types'
import MoviePoster from './MoviePoster.vue'
import StatePanel from './StatePanel.vue'

const props = withDefaults(defineProps<{
  endpoint: string | null
  emptySubject?: string
  presets?: string[]
}>(), {
  emptySubject: '登录后体验',
  presets: () => ['推荐几部动画电影', '和《玩具总动员》相似的电影', '看看我最近评过什么'],
})

const question = ref('')
const result = ref<AssistantResult | null>(null)
const error = ref<unknown>(null)
const loading = ref(false)
let run = 0

onScopeDispose(() => { run++ })
watch(() => props.endpoint, () => { result.value = null; error.value = null })

async function ask(text?: string) {
  const asked = (text ?? question.value).trim()
  if (!asked || !props.endpoint) return
  question.value = asked
  const token = ++run
  loading.value = true
  error.value = null
  try {
    const response = await http.get<AssistantResult>(props.endpoint, { params: { q: asked, limit: 5 } })
    if (token === run) result.value = response.data
  } catch (caught) {
    if (token === run) {
      error.value = caught
      result.value = null
    }
  } finally {
    if (token === run) loading.value = false
  }
}

const unavailableReason = computed(() => {
  const caught = error.value
  if (!(caught instanceof ApiError)) return ''
  const details = (caught.details || {}) as { reason?: string }
  const reasons: Record<string, string> = {
    missing_key: '还没有配置外部模型的 API Key',
    network_error: '连不上外部模型服务',
    timeout: '外部模型在超时时间内没有响应',
    http_error: '外部模型返回了错误状态',
    bad_response: '外部模型的响应格式不符',
  }
  return reasons[details.reason || ''] || caught.message
})

function toolLabel(row: AssistantResult['tool_calls'][number]) {
  return row.ok ? `${row.tool} · ${row.matched} 条` : `${row.tool} · 被拒绝`
}
function itemNote(item: AssistantResult['items'][number]) {
  if (item.similarity !== undefined) return `相似度 ${item.similarity.toFixed(3)}`
  if (item.my_rating) return `我的评分 ${item.my_rating}`
  if (item.score !== null && item.score !== undefined) return `得分 ${Number(item.score).toFixed(4)}`
  return item.kind === 'favorite' ? '我的收藏' : ''
}
const methods = computed(() => result.value?.methods || [])
function percent(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(2)}%`
}
function userCount(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  return Number(value).toLocaleString('zh-CN')
}
</script>

<template>
  <div class="assistant-panel">
    <div class="assistant-ask">
      <el-input
        v-model="question"
        maxlength="300"
        show-word-limit
        placeholder="用一句中文描述你想找的电影，例如：推荐几部 90 年代的动画片"
        aria-label="自然语言提问"
        @keyup.enter="ask()"
      />
      <el-button type="primary" :loading="loading" :disabled="!endpoint || !question.trim()" @click="ask()">提问</el-button>
    </div>
    <div class="assistant-presets">
      <span class="small-label">示例问题</span>
      <el-button v-for="preset in presets" :key="preset" size="small" :disabled="!endpoint || loading" @click="ask(preset)">{{ preset }}</el-button>
    </div>

    <p v-if="!endpoint" class="muted" role="status">{{ emptySubject }}</p>

    <StatePanel v-else :loading="loading" :error="null" title="向助手提问" description="提问后，助手只能通过受控工具读取真实电影数据。">
      <div v-if="error" class="assistant-error" role="alert">
        <span class="status-pill">{{ error instanceof ApiError ? error.code : '请求失败' }}</span>
        <p v-if="unavailableReason">外部模型当前不可用：{{ unavailableReason }}。系统没有用假数据兜底，请稍后重试。</p>
        <p v-else>{{ error instanceof ApiError ? error.message : '请求失败' }}</p>
        <small v-if="error instanceof ApiError && error.status === 503">配好 Key（或在有 Key 的环境）后重试；此提示本身就是"外部服务不可用"的明确状态。</small>
      </div>

      <div v-else-if="result" class="assistant-result">
        <div class="assistant-meta">
          <span class="status-pill">{{ result.status === 'ready' ? '已给出片单' : '无结果' }}</span>
          <span class="small-label">{{ result.provider }} · {{ result.model }} · {{ result.rounds }} 轮工具调用<template v-if="result.usage && result.usage.total_tokens"> · {{ result.usage.total_tokens }} tokens（输入 {{ result.usage.prompt_tokens }} / 输出 {{ result.usage.completion_tokens }}）</template></span>
        </div>
        <p class="assistant-answer">{{ result.answer || '（模型没有给出文字回答）' }}</p>

        <div v-if="methods.length" class="assistant-methods">
          <span class="small-label">推荐所用算法 · 准确率（实验三离线评价）</span>
          <div v-for="method in methods" :key="`${method.tool}-${method.algorithm}`" class="assistant-method">
            <span class="status-pill">{{ method.algorithm_label }}</span>
            <span class="assistant-metric">线上模型 <b>{{ method.model_version || '—' }}</b></span>
            <span class="assistant-metric">验证集 Precision@10 <b>{{ percent(method.offline_metrics.precision_at_10) }}</b></span>
            <span class="assistant-metric">Recall@10 <b>{{ percent(method.offline_metrics.recall_at_10) }}</b></span>
            <span class="assistant-metric">{{ userCount(method.offline_metrics.users) }} 位固定用户</span>
          </div>
          <p class="assistant-metric-note">
            准确率取自实验三的离线评价（{{ methods[0].offline_metrics.protocol }}），是<strong>算法级历史成绩</strong>；
            上面的片单由线上业务模型 {{ methods.map(method => method.model_version || '—').join(' / ') }} 现场生成，两者不是同一次预测。
          </p>
        </div>

        <div class="assistant-tools">
          <span class="small-label">工具调用证据</span>
          <span v-for="(row, index) in result.tool_calls" :key="index" class="status-pill" :class="{ 'is-rejected': !row.ok }">
            {{ toolLabel(row) }}
          </span>
        </div>
        <ul v-if="result.tool_calls.some(row => !row.ok)" class="assistant-rejections">
          <li v-for="(row, index) in result.tool_calls.filter(row => !row.ok)" :key="index">{{ row.tool }}：{{ row.error }}</li>
        </ul>

        <div v-if="result.warnings.length" class="assistant-warnings" role="status">
          <p v-for="(warning, index) in result.warnings" :key="index">！{{ warning }}</p>
        </div>
        <div v-if="result.unverified_mentions.length" class="assistant-warnings" role="alert">
          <p>回答里提到了工具未返回的片名：{{ result.unverified_mentions.join('、') }}</p>
        </div>

        <ol v-if="result.items.length" class="assistant-items">
          <li v-for="item in result.items" :key="item.movie_id" class="assistant-card">
            <MoviePoster :path="item.poster_path" :title="item.title" />
            <div class="assistant-card-body">
              <RouterLink :to="`/movies/${item.movie_id}`" class="text-link">{{ item.title }}</RouterLink>
              <p class="assistant-card-genres">{{ item.genres.slice(0, 3).join(' · ') || '未标注类型' }}<span v-if="item.year"> · {{ item.year }}</span></p>
              <p class="assistant-card-note"><span class="status-pill">{{ item.source_tool || '工具结果' }}</span> {{ itemNote(item) }}</p>
            </div>
          </li>
        </ol>
        <p v-else class="assistant-empty">这次提问没有可展示的电影：工具确实没有返回结果（不是被过滤掉的），可以换一种问法。</p>

        <p class="assistant-footnote">
          护栏：{{ result.guardrails.join('、') }}。片单只由工具输出构成；工具参数逐个校验，越界调用会被拒绝并记录在上面。
        </p>
      </div>

      <div v-else class="assistant-idle">
        <p>输入问题或点一个示例问题开始。助手不会凭空编造电影：它必须先调用受控工具（真实目录、共现模型、已注册 Provider、你的真实评分）。</p>
      </div>
    </StatePanel>
  </div>
</template>

<style scoped>
.assistant-panel { display: grid; gap: 14px; }
.assistant-ask { display: flex; gap: 10px; align-items: center; }
.assistant-ask :deep(.el-input) { flex: 1; }
.assistant-presets { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.assistant-error { display: grid; gap: 8px; padding: 16px 18px; border: 1px solid #7a3b3b; border-radius: 8px; background: #2a1717aa; }
.assistant-error p { margin: 0; }
.assistant-error small { color: var(--muted); }
.assistant-result { display: grid; gap: 12px; }
.assistant-meta, .assistant-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.assistant-answer { margin: 0; padding: 14px 18px; border-left: 2px solid var(--accent, #7aa2a2); background: #0f1b1b66; white-space: pre-wrap; line-height: 1.9; }
.assistant-tools .status-pill.is-rejected { border-color: #7a3b3b; color: #e0a0a0; }
.assistant-methods { display: grid; gap: 8px; padding: 12px 14px; border: 1px dashed var(--border); border-radius: 8px; background: #14201f66; }
.assistant-method { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; font-size: 12px; color: var(--muted); }
.assistant-metric b { color: var(--accent, #d9c184); font-weight: 600; }
.assistant-metric-note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.8; }
.assistant-metric-note strong { color: #d9c184; font-weight: 600; }
.assistant-rejections { margin: 0; padding-left: 18px; color: #e0a0a0; font-size: 12px; line-height: 1.8; }
.assistant-warnings { display: grid; gap: 4px; padding: 10px 14px; border: 1px dashed var(--border); border-radius: 8px; color: var(--muted); font-size: 12px; }
.assistant-warnings p { margin: 0; }
.assistant-items { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.assistant-card { display: flex; gap: 12px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
.assistant-card :deep(.movie-poster) { width: 54px; flex: none; }
.assistant-card-body { display: grid; gap: 4px; align-content: start; }
.assistant-card-genres { margin: 0; color: var(--muted); font-size: 12px; }
.assistant-card-note { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0; color: var(--muted); font-size: 12px; }
.assistant-empty, .assistant-idle { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.8; }
.assistant-footnote { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.8; }
</style>