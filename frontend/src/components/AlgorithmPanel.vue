<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import http, { ApiError } from '../api'
import StatePanel from './StatePanel.vue'
import MoviePoster from './MoviePoster.vue'
import type { AlgorithmResult, Movie } from '../types'
const props = withDefaults(defineProps<{ endpoint: string | null; label: string; emptySubject?: string }>(), { emptySubject: '登录后体验' })
const loading = ref(false), error = ref<unknown>(null), unavailable = ref(false)
const result = ref<AlgorithmResult | null>(null), movies = ref<Record<string, Movie>>({})
let run = 0
onScopeDispose(() => { run++ })

async function loadDetails(items: AlgorithmResult['items'], seq: number) {
  const details: Record<string, Movie> = {}
  await Promise.all(items.map(async item => {
    try { details[item.movie_id] = (await http.get<Movie>(`/movies/${item.movie_id}`)).data } catch { /* keep the ID fallback */ }
  }))
  if (seq === run) movies.value = details
}

async function load() {
  const seq = ++run
  error.value = null; result.value = null; unavailable.value = false; loading.value = false; movies.value = {}
  if (!props.endpoint) return
  loading.value = true
  try {
    const response = await http.get<AlgorithmResult>(props.endpoint)
    if (seq !== run) return
    result.value = response.data
    if (response.data.items.length) await loadDetails(response.data.items, seq)
  }
  catch (e) {
    if (seq !== run) return
    if (e instanceof ApiError && e.status === 501 && e.code === 'ALGORITHM_NOT_IMPLEMENTED') unavailable.value = true
    else error.value = e
  } finally { if (seq === run) loading.value = false }
}
watch(() => props.endpoint, load, { immediate: true })

function movieTitle(movieId: string) {
  const movie = movies.value[movieId]
  return movie ? movie.title_zh || movie.movielens_title : `电影 #${movieId}`
}
function movieGenres(movieId: string) {
  const genres = movies.value[movieId]?.genres
  return genres?.length ? genres.slice(0, 3).join(' / ') : '类型未收录'
}
const strategy = computed(() => {
  const algorithm = result.value?.algorithm
  const sources = result.value?.items[0]?.recall_sources || []
  if (algorithm === 'cold_start') return sources.includes('cold_start_content') ? '冷启动 · 内容画像' : '冷启动 · 正反馈热度'
  return ({ content: '个性化 · 类型画像', popular: '非个性化 · 正反馈热度', itemcf: '个性化 · 行为共现', als: '个性化 · 隐向量内积' } as Record<string, string>)[algorithm || ''] || '推荐策略'
})
const cardLabel = computed(() => ({ content: 'CONTENT MATCH', popular: 'POPULARITY', itemcf: 'ITEMCF', als: 'MATRIX FACTOR', cold_start: 'COLD START' } as Record<string, string>)[result.value?.algorithm || ''] || 'RECOMMENDED')
const scoreLabel = computed(() => {
  const algorithm = result.value?.algorithm
  const sources = result.value?.items[0]?.recall_sources || []
  if (algorithm === 'itemcf') return '推荐分'
  if (algorithm === 'als') return '预测分'
  if (algorithm === 'content') return '匹配度'
  if (algorithm === 'cold_start' && sources.includes('cold_start_content')) return '匹配度'
  return '热度'
})
const emptyText = computed(() => {
  const algorithm = result.value?.algorithm
  if (algorithm === 'content') return '你的历史里还没有 4 分以上的电影，暂时无法生成类型画像。'
  if (algorithm === 'itemcf') return '你的历史里还没有 4 分以上的电影，无法生成 ItemCF 推荐。'
  if (algorithm === 'als') return '模型里没有你的隐向量，且你的历史不足以折叠出用户向量。'
  return '暂时没有可推荐的新电影。'
})
const footnote = computed(() => {
  const algorithm = result.value?.algorithm
  const sources = result.value?.items[0]?.recall_sources || []
  if (algorithm === 'content') return '依据类型 TF-IDF 与你评分为 4 分以上的历史计算，已排除看过的电影。'
  if (algorithm === 'popular') return '按全站 4 分以上正反馈人数排序；已排除你评分或收藏过的电影，本身不是个性化结果。'
  if (algorithm === 'itemcf') return '基于物品共现相似度，从你评分为 4 分以上的电影扩展；已排除看过的电影。'
  if (algorithm === 'als') return 'Spark MLlib ALS 隐向量内积；候选池已按训练支持度下限过滤冷门电影，并排除看过的电影。'
  if (algorithm === 'cold_start' && sources.includes('cold_start_content')) return '检测到你的正反馈，冷启动已从热门榜切换为类型画像。'
  if (algorithm === 'cold_start') return '没有 4 分以上评分时展示全站正反馈热门榜；这不是个性化结果。'
  return `数据版本 ${result.value?.dataset_version || ''}`
})
function formatScore(score: number | null) {
  if (score === null) return '—'
  if (result.value?.algorithm === 'itemcf') return score.toFixed(3)
  if (result.value?.algorithm === 'als') return score.toFixed(3)
  return `${(score * 100).toFixed(1)}%`
}
</script>
<template>
  <section class="algorithm-panel" :aria-label="label">
    <div class="section-top"><h3>{{ label }}</h3><span class="small-label">RECOMMENDATION</span></div>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="!endpoint || unavailable" class="algorithm-empty" role="status">
        <div class="algorithm-symbol"><el-icon aria-hidden="true"><Connection /></el-icon></div>
        <div><span class="status-pill">{{ !endpoint ? emptySubject : '尚未实现' }}</span><h3>{{ !endpoint ? '选择观众，开始探索' : '下一部好电影，等待算法发现' }}</h3><p>{{ !endpoint ? '先选择历史用户或登录注册账号，推荐结果会显示在这里。' : '等待算法接入；当前没有模型版本或推荐结果。' }}</p></div>
        <RouterLink v-if="!endpoint && emptySubject === '登录后体验'" class="text-link" to="/login">前往登录 →</RouterLink>
      </div>
      <div v-else-if="result" class="recommendation-results">
        <div class="recommendation-meta">
          <div><span class="small-label">MODEL VERSION</span><strong>{{ result.model_version }}</strong></div>
          <div class="recommendation-pills"><span class="strategy-pill">{{ strategy }}</span><span class="status-pill">{{ result.items.length }} 部推荐</span></div>
        </div>
        <p v-if="!result.items.length" class="recommendation-empty">{{ emptyText }}</p>
        <ol v-else class="recommendation-list">
          <li v-for="item in result.items" :key="item.movie_id" class="recommendation-item">
            <RouterLink :to="`/movies/${item.movie_id}`" class="recommendation-card" :aria-label="`第 ${item.rank} 名：${movieTitle(item.movie_id)}`">
              <div class="recommendation-poster-wrap">
                <MoviePoster :path="movies[item.movie_id]?.poster_path" :title="movieTitle(item.movie_id)" />
                <span class="rank-badge">{{ String(item.rank).padStart(2, '0') }}</span>
              </div>
              <div class="recommendation-copy">
                <p class="small-label">{{ cardLabel }}</p>
                <h4>{{ movieTitle(item.movie_id) }}</h4>
                <p class="recommendation-genres">{{ movieGenres(item.movie_id) }}</p>
                <div class="recommendation-score"><span>{{ scoreLabel }}</span><strong>{{ formatScore(item.score) }}</strong></div>
              </div>
            </RouterLink>
          </li>
        </ol>
        <p class="recommendation-footnote">{{ footnote }}</p>
      </div>
    </StatePanel>
  </section>
</template>

<style scoped>
.recommendation-results { padding: 0; }
.recommendation-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 15px 20px; border-bottom: 1px solid var(--border); background: #0f1b1b66; }
.recommendation-meta strong { display: block; margin-top: 4px; color: var(--text); font-size: 12px; font-weight: 500; overflow-wrap: anywhere; }
.recommendation-pills { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 6px; }
.strategy-pill { padding: 4px 9px; border: 1px solid #4d6f68; border-radius: 999px; color: #d8e8df; font-size: 10px; line-height: 1.4; white-space: nowrap; }
.recommendation-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; margin: 0; padding: 16px; list-style: none; }
.recommendation-item { min-width: 0; }
.recommendation-card { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 13px; height: 100%; padding: 9px; border: 1px solid var(--border); border-radius: 8px; background: linear-gradient(145deg, #1d2c2b, #172322); transition: border-color .18s, transform .18s, background .18s; }
.recommendation-card:hover { border-color: var(--accent); background: linear-gradient(145deg, #223432, #192725); transform: translateY(-2px); }
.recommendation-poster-wrap { position: relative; min-width: 0; }
.recommendation-poster-wrap :deep(.movie-poster) { aspect-ratio: 2 / 3; border-radius: 5px; }
.rank-badge { position: absolute; top: 7px; left: 7px; z-index: 1; padding: 2px 6px; border-radius: 3px; background: var(--accent); color: var(--ink); font-size: 10px; font-weight: 700; letter-spacing: .08em; line-height: 1.5; }
.recommendation-copy { display: flex; flex-direction: column; min-width: 0; padding: 3px 1px; }
.recommendation-copy .small-label { font-size: 8px; letter-spacing: .16em; }
.recommendation-copy h4 { display: -webkit-box; overflow: hidden; margin: 6px 0 4px; font-size: 14px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.recommendation-genres { overflow: hidden; color: var(--muted); font-size: 10px; line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }
.recommendation-score { display: flex; align-items: end; justify-content: space-between; gap: 8px; margin-top: auto; padding-top: 8px; border-top: 1px dashed var(--border); color: var(--muted); font-size: 10px; }
.recommendation-score strong { color: var(--accent); font-size: 17px; font-weight: 500; letter-spacing: -.03em; line-height: 1; }
.recommendation-empty { padding: 26px 20px 8px; color: var(--muted); font-size: 13px; line-height: 1.8; }
.recommendation-footnote { padding: 0 20px 18px; color: var(--muted); font-size: 11px; line-height: 1.7; }
@media (max-width: 540px) {
  .recommendation-list { grid-template-columns: 1fr; padding: 12px; }
  .recommendation-card { grid-template-columns: 82px minmax(0, 1fr); }
}
</style>
