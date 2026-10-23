<script setup lang="ts">
import { onMounted } from 'vue'
import { ArrowRight, Search, Reading } from '@element-plus/icons-vue'
import http from '../api'
import { useAuth } from '../stores/auth'
import { useRemote } from '../composables/useRemote'
import type { Movie, Page } from '../types'
import MovieCard from '../components/MovieCard.vue'
import StatePanel from '../components/StatePanel.vue'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
const auth = useAuth()
const { data, error, loading, load } = useRemote<Page<Movie>>()
const fetchMovies = () => load(async () => (await http.get<Page<Movie>>('/movies', { params: { per_page: 4 } })).data)
onMounted(fetchMovies)
</script>
<template>
  <div class="page home-page">
    <section class="hero">
      <div class="hero-copy"><p class="eyebrow"><span /> A MOMENT FOR CINEMA</p><h1>好电影，值得<span>再靠近一点。</span></h1><p class="hero-description">从一部电影开始，发现自己的观影偏好。<br />探索真实片库，让每一次评分留下回响。</p><div class="hero-actions"><RouterLink class="button primary" to="/movies">探索电影库 <el-icon aria-hidden="true"><ArrowRight /></el-icon></RouterLink><RouterLink class="button subtle" to="/experiments">进入推荐实验室</RouterLink></div><div class="hero-footnote"><span class="line" /> 浏览 · 记录 · 发现</div></div>
      <div class="hero-art" aria-hidden="true"><div class="orbital orbital-one" /><div class="orbital orbital-two" /><div class="film-ticket"><div class="ticket-top"><span>CINELAB</span><span>ADMIT ONE</span></div><div class="ticket-reel"><span /><span /><span /><span /><i /></div><div class="ticket-caption">THE NEXT<br /><em>discovery.</em></div><div class="ticket-bottom"><span>留一点时间，给电影。</span><span>▶</span></div></div><span class="art-label">YOUR TASTE. YOUR NEXT FILM.</span></div>
    </section>
    <div class="intro-strip"><p><el-icon aria-hidden="true"><Reading /></el-icon><strong>真实片库</strong><span>保留原始 MovieLens 电影标识</span></p><p><el-icon aria-hidden="true"><Search /></el-icon><strong>自由探索</strong><span>按片名与类型发现电影</span></p><RouterLink to="/movies">开始浏览 <span aria-hidden="true">↗</span></RouterLink></div>
    <section class="recommendation-section"><div class="section-heading"><div><p class="eyebrow">MADE FOR YOUR TASTE</p><h2>你的下一部<span class="heading-dot">.</span></h2><p class="muted">评分留下口味线索；没有评分时，也可以从全站热门榜开始。</p></div><RouterLink to="/experiments" class="text-link">查看实验室 →</RouterLink></div><div class="recommendation-columns"><AlgorithmPanel label="为你推荐" :endpoint="auth.account ? '/me/recommendations/content' : null" /><AlgorithmPanel label="热门推荐" :endpoint="auth.account ? '/me/recommendations/popular' : null" /></div><div class="cold-start-callout"><div><p class="eyebrow">NEW TO CINELAB?</p><h3>第一次来，也能从热门开始</h3><p>没有评分时先看全站正反馈热门榜；当你的评分达到 4 分，冷启动会自动切换成你的类型画像。</p></div><RouterLink class="button subtle" to="/cold-start">进入冷启动 →</RouterLink></div></section>
    <section class="browse-section"><div class="section-heading"><div><p class="eyebrow">THE FILM ARCHIVE</p><h2>从片库出发<span class="heading-dot">.</span></h2><p class="muted">按原始电影编号展示，探索属于你的下一部。</p></div><RouterLink to="/movies" class="text-link">查看全部 <el-icon aria-hidden="true"><ArrowRight /></el-icon></RouterLink></div><StatePanel :loading="loading" :error="error" :empty="!!data && !data.items.length" @retry="fetchMovies"><div class="movie-grid"><MovieCard v-for="movie in data?.items" :key="movie.movie_id" :movie="movie" /></div></StatePanel></section>
  </div>
</template>

<style scoped>
.hero { min-height: 0; padding: 22px 34px; grid-template-columns: 1.5fr .65fr; align-items: center; }
.hero h1 { font-size: clamp(30px, 2.7vw, 38px); line-height: 1.3; margin: 12px 0; }
.hero .eyebrow { margin-bottom: 8px; }
.hero-description { font-size: 14px; line-height: 1.75; }
.hero-actions { margin-top: 18px; }
.hero-footnote { display: none; }
.hero-art { height: 188px; min-height: 0; transform: none; }
.film-ticket { transform: scale(.62) rotate(9deg); }
.orbital { width: 260px; height: 230px; }
.orbital-two { width: 280px; height: 190px; }
.art-label { display: none; }
.recommendation-section { margin-top: 24px; }
.cold-start-callout { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-top: 14px; padding: 18px 22px; border: 1px solid var(--border); border-radius: 10px; background: linear-gradient(120deg, #1b2547, #131a30 65%); }
.cold-start-callout h3 { margin: 5px 0 7px; font-size: 19px; font-weight: 500; }
.cold-start-callout p:not(.eyebrow) { max-width: 650px; color: var(--muted); font-size: 12px; line-height: 1.8; }
.cold-start-callout .button { flex: none; }
@media (max-width: 780px) {
  .hero { grid-template-columns: 1fr 160px; padding: 24px; gap: 12px; }
  .hero-art { height: 190px; }
  .film-ticket { transform: scale(.52) rotate(9deg); }
}
@media (max-width: 540px) {
  .hero { grid-template-columns: 1fr; padding: 22px 20px; }
  .hero h1 { font-size: 30px; }
  .hero-art { display: none; }
  .hero-actions { gap: 10px; }
  .hero-actions .button { padding: 10px 12px; }
  .recommendation-section { margin-top: 26px; }
  .cold-start-callout { align-items: flex-start; flex-direction: column; gap: 14px; padding: 18px; }
}
</style>
