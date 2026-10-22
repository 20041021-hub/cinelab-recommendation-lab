<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import http, { ApiError } from '../api'
import StatePanel from './StatePanel.vue'
import MoviePoster from './MoviePoster.vue'
import type { FusionResult, Movie } from '../types'
import fusionStudy from '../data/lab4-fusion.json'
const props = withDefaults(defineProps<{ endpoint: string | null; emptySubject?: string; label?: string }>(), { emptySubject: '请选择历史用户', label: 'S1 加权融合 · 推荐结果' })
const loading = ref(false), error = ref<unknown>(null), unavailable = ref(false)
const result = ref<FusionResult | null>(null), movies = ref<Record<string, Movie>>({})
let run = 0
onScopeDispose(() => { run++ })

interface StudyRow { config: string; weights: Record<string, number>; precision_at_10: number; recall_at_10: number; empty_users: number; multi_source_items: number }
interface FusionFile {
  selected: { config: string; weight: string; k: number; label: string; sources: string[]; precision_at_10: number | null; recall_at_10: number | null } | null
  grid: StudyRow[]
  user_count: number
  test?: { precision_at_10: number | null; recall_at_10: number | null; user_count: number } | null
}
const study = fusionStudy as unknown as FusionFile

async function loadDetails(items: FusionResult['items'], seq: number) {
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
    const response = await http.get<FusionResult>(props.endpoint)
    if (seq !== run) return
    result.value = response.data
    if (response.data.items.length) await loadDetails(response.data.items, seq)
  }
  catch (e) {
    if (seq !== run) return
    if (e instanceof ApiError && (e.status === 501 || e.status === 404)) unavailable.value = true
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
function sourceChips(item: FusionResult['items'][number]) {
  const ranks = item.recall_ranks || {}
  const sources = item.recall_sources?.length ? item.recall_sources : Object.keys(ranks)
  return sources.map(source => ({ source, rank: ranks[source] ?? null }))
}
const configLabel = computed(() => {
  const weights = result.value?.weights || {}
  const parts = Object.entries(weights).map(([name, weight]) => `${name} ×${weight}`)
  return parts.length ? parts.join(' + ') : '加权 RRF'
})
const multiSourceCount = computed(() => (result.value?.items || []).filter(item => (item.recall_sources?.length || 0) > 1).length)
const sourceStats = computed(() => result.value?.sources || [])
const interest = computed(() => result.value?.interest ?? null)
const interestApplied = computed(() => Boolean(interest.value?.enabled && !interest.value?.stale))
const interestGenres = computed(() => (interest.value?.top_genres || []).slice(0, 5))
const boostedCount = computed(() => (result.value?.items || []).filter(item => (item.interest_boost ?? 1) > 1).length)
const adaptive = computed(() => result.value?.adaptive ?? null)
const adaptiveApplied = computed(() => Boolean(adaptive.value?.enabled && !adaptive.value?.stale))
const adaptiveSources = computed(() => adaptive.value?.sources || [])
function boostLabel(item: FusionResult['items'][number]) {
  const boost = item.interest_boost ?? 1
  if (boost <= 1.0001) return null
  const match = item.interest_match ? `匹配度 ${(item.interest_match * 100).toFixed(0)}%` : '类型命中'
  return `${match} · 加权 ×${boost.toFixed(3)}`
}
</script>
<template>
  <section class="fusion-panel" :aria-label="label">
    <div class="section-top"><h3>{{ label }}</h3><span class="small-label">WEIGHTED RRF</span></div>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="!endpoint || unavailable" class="algorithm-empty" role="status">
        <div class="algorithm-symbol"><el-icon aria-hidden="true"><Connection /></el-icon></div>
        <div><span class="status-pill">{{ !endpoint ? emptySubject : '融合入口不可用' }}</span><h3>{{ !endpoint ? '选择观众，启动多路融合' : '融合服务未就绪' }}</h3><p>{{ !endpoint ? '融合结果读取冻结目录，并排除该用户评分或收藏过的电影。' : '请确认后端已加载实验四的融合模块。' }}</p></div>
      </div>
      <div v-else-if="result" class="recommendation-results">
        <div class="recommendation-meta">
          <div><span class="small-label">FUSION CONFIG · {{ result.model_version }}</span><strong>{{ configLabel }} · RRF k={{ result.rrf_k }}</strong></div>
          <div class="recommendation-pills">
            <span class="strategy-pill">{{ result.sources.length }} 路召回</span>
            <span class="strategy-pill">候选池 {{ result.pool_size }}</span>
            <span class="strategy-pill">{{ multiSourceCount }} 部多路命中</span>
            <span v-if="interestApplied" class="interest-pill">短期兴趣已生效 · {{ boostedCount }} 部加权</span>
            <span v-if="adaptiveApplied" class="adaptive-pill">自适应权重已生效 · β={{ adaptive?.beta }}</span>
            <span class="status-pill">{{ result.items.length }} 部推荐</span>
          </div>
        </div>
        <div v-if="interest?.requested" class="fusion-interest-panel">
          <div class="fusion-interest-head">
            <span class="source-name">S3 短期兴趣</span>
            <span class="source-meta">{{ interest.source }}</span>
            <span v-if="interestApplied" class="interest-pill">消费端事件 {{ interest.events }} 条 · 状态版本 {{ interest.state_version }}</span>
            <span v-else class="status-pill">未生效</span>
          </div>
          <p v-if="!interestApplied" class="fusion-source-note">原因：{{ interest.reason || '短期兴趣状态不可用' }}；下面仍是未加权的加权 RRF 结果。</p>
          <template v-else>
            <div class="fusion-interest-genres">
              <span v-for="row in interestGenres" :key="row.genre" class="source-chip">{{ row.genre }} {{ row.weight.toFixed(3) }}</span>
            </div>
            <p class="fusion-source-note">{{ interest.rule }}；alpha={{ interest.alpha }}，半衰期 {{ interest.half_life_minutes }} 分钟，状态更新于 {{ interest.updated_at }}（{{ interest.age_seconds?.toFixed(0) }} 秒前）。</p>
          </template>
        </div>
        <div v-if="adaptive?.requested" class="fusion-adaptive-panel">
          <div class="fusion-interest-head">
            <span class="source-name">创新点 A · 自适应权重</span>
            <span v-if="adaptiveApplied" class="adaptive-pill">β={{ adaptive.beta }} · 状态版本 {{ adaptive.state_version }}</span>
            <span v-else class="status-pill">未生效</span>
          </div>
          <p v-if="!adaptiveApplied" class="fusion-source-note">原因：{{ adaptive?.reason || '短期兴趣状态不可用' }}；这次仍使用固定权重。</p>
          <template v-else>
            <div class="fusion-source-row is-adaptive">
              <span class="source-name">来源</span>
              <span class="source-meta">固定权重</span>
              <span class="source-meta">兴趣匹配度</span>
              <span class="source-meta">生效权重</span>
              <span class="source-meta">候选</span>
            </div>
            <div v-for="row in adaptiveSources" :key="row.algorithm" class="fusion-source-row">
              <span class="source-name">{{ row.algorithm }}</span>
              <span class="source-meta">×{{ row.base_weight }}</span>
              <span class="source-meta">{{ (row.affinity * 100).toFixed(1) }}%</span>
              <span class="source-meta is-strong">×{{ row.adaptive_weight }}</span>
              <span class="source-meta">{{ row.candidates }}</span>
            </div>
            <p class="fusion-source-note">{{ adaptive.rule }}：匹配度是该路候选与短期兴趣类型的平均命中强度，越贴合当前兴趣的召回在融合里权重越高。</p>
          </template>
        </div>
        <div class="fusion-sources-panel">
          <div v-for="stat in sourceStats" :key="stat.algorithm" class="fusion-source-row">
            <span class="source-name">{{ stat.algorithm }}</span>
            <span class="source-meta">权重 ×{{ stat.weight }}</span>
            <span class="source-meta">候选 {{ stat.candidate_count }}</span>
            <span class="source-meta">进入 Top-{{ result.items.length }} {{ stat.contributed }}</span>
            <span class="source-meta">最佳名次 {{ stat.best_rank ?? '—' }}</span>
          </div>
          <p class="fusion-source-note">每路先各自取深度 {{ result.candidate_depth }} 的真实候选（单路仍受模型支持度下限约束），再按 权重 /（k + 名次） 累加融合；已排除 {{ result.filtered_seen }} 部看过的电影。</p>
        </div>
        <p v-if="!result.items.length" class="recommendation-empty">该用户没有任何一路召回给出候选，融合结果为空；无结果的用户仍计入宏平均分母。</p>
        <ol v-else class="recommendation-list">
          <li v-for="item in result.items" :key="item.movie_id" class="recommendation-item">
            <RouterLink :to="`/movies/${item.movie_id}`" class="recommendation-card" :aria-label="`第 ${item.rank} 名：${movieTitle(item.movie_id)}`">
              <div class="recommendation-poster-wrap">
                <MoviePoster :path="movies[item.movie_id]?.poster_path" :title="movieTitle(item.movie_id)" />
                <span class="rank-badge">{{ String(item.rank).padStart(2, '0') }}</span>
              </div>
              <div class="recommendation-copy">
                <p class="small-label">FUSED RANK</p>
                <h4>{{ movieTitle(item.movie_id) }}</h4>
                <p class="recommendation-genres">{{ movieGenres(item.movie_id) }}</p>
                <div class="fusion-sources">
                  <span v-for="chip in sourceChips(item)" :key="chip.source" class="source-chip">{{ chip.source }}<template v-if="chip.rank"> #{{ chip.rank }}</template></span>
                </div>
                <div class="recommendation-score"><span>融合分</span><strong>{{ item.score.toFixed(4) }}</strong></div>
                <p v-if="boostLabel(item)" class="fusion-boost">{{ boostLabel(item) }}</p>
              </div>
            </RouterLink>
          </li>
        </ol>
        <p class="recommendation-footnote">融合分 = Σ 权重 /（{{ result.rrf_k }} + 单路名次），只在单路 Top-{{ result.candidate_depth }} 上累加；同一部电影被多路命中时分数叠加。</p>
        <p v-if="study.selected" class="recommendation-footnote">默认权重在验证集上选定：{{ study.selected.label }}（{{ study.user_count }} 位固定评价用户，P@10={{ study.selected.precision_at_10?.toFixed(4) }}、R@10={{ study.selected.recall_at_10?.toFixed(4) }}）；测试集在方案冻结后只报告一次。</p>
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
.fusion-sources-panel { display: grid; gap: 6px; padding: 14px 20px; border-bottom: 1px dashed var(--border); }
.fusion-source-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 11px; }
.source-name { min-width: 68px; color: var(--text); font-weight: 500; }
.source-meta { color: var(--muted); }
.fusion-source-note { margin-top: 4px; color: var(--muted); font-size: 11px; line-height: 1.7; }
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
.fusion-sources { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
.interest-pill { padding: 4px 9px; border: 1px solid #7a6a3f; border-radius: 999px; background: #2a2418; color: #f0dca8; font-size: 10px; line-height: 1.4; white-space: nowrap; }
.fusion-interest-panel { display: grid; gap: 6px; padding: 12px 20px; border-bottom: 1px dashed var(--border); background: #1a1f16; }
.adaptive-pill { padding: 4px 9px; border: 1px solid #3f6a7a; border-radius: 999px; background: #16262a; color: #a8e0f0; font-size: 10px; line-height: 1.4; white-space: nowrap; }
.fusion-adaptive-panel { display: grid; gap: 6px; padding: 12px 20px; border-bottom: 1px dashed var(--border); background: #141f26; }
.fusion-source-row.is-adaptive { color: var(--muted); font-size: 10px; letter-spacing: .08em; text-transform: uppercase; }
.source-meta.is-strong { color: #a8e0f0; }
.fusion-interest-head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 11px; }
.fusion-interest-genres { display: flex; flex-wrap: wrap; gap: 4px; }
.fusion-boost { margin-top: 3px; color: #d9c184; font-size: 10px; line-height: 1.5; }
.source-chip { padding: 2px 6px; border: 1px solid var(--border); border-radius: 3px; color: var(--muted); font-size: 9px; letter-spacing: .04em; }
.recommendation-score { display: flex; align-items: end; justify-content: space-between; gap: 8px; margin-top: auto; padding-top: 8px; border-top: 1px dashed var(--border); color: var(--muted); font-size: 10px; }
.recommendation-score strong { color: var(--accent); font-size: 17px; font-weight: 500; letter-spacing: -.03em; line-height: 1; }
.recommendation-empty { padding: 26px 20px 8px; color: var(--muted); font-size: 13px; line-height: 1.8; }
.recommendation-footnote { padding: 0 20px 18px; color: var(--muted); font-size: 11px; line-height: 1.7; }
@media (max-width: 540px) {
  .recommendation-list { grid-template-columns: 1fr; padding: 12px; }
  .recommendation-card { grid-template-columns: 82px minmax(0, 1fr); }
}
</style>