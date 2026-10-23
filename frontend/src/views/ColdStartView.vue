<script setup lang="ts">
import { ref } from 'vue'
import { useAuth } from '../stores/auth'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
const auth = useAuth()
const refreshKey = ref(0)
</script>
<template>
  <div class="page cold-start-page">
    <div class="page-heading">
      <p class="eyebrow">START HERE</p>
      <h1>第一次来<span class="heading-dot">.</span></h1>
      <p>没有历史评分时，先从一个诚实的全站热门榜开始；一次真实的评分，会让推荐切换到你的类型画像。</p>
    </div>
    <div class="cold-start-grid">
      <aside class="cold-start-notes">
        <p class="small-label">HOW IT WORKS</p>
        <ol>
          <li><strong>没有正反馈</strong><span>返回按全站 4 分以上正反馈人数排序的热门榜，并明确标注“非个性化”。</span></li>
          <li><strong>你评了一部电影</strong><span>评分达到 4 分，冷启动检测到真实正反馈，切换到实验一的类型 TF-IDF 画像。</span></li>
          <li><strong>随时刷新</strong><span>评分或收藏后回到这里刷新，结果来源会随行为变化。</span></li>
        </ol>
        <div class="cold-start-actions">
          <RouterLink class="button primary" to="/movies">去找一部电影评分</RouterLink>
          <el-button :disabled="!auth.account" @click="refreshKey++">刷新推荐</el-button>
        </div>
        <p class="form-hint">历史 MovieLens 用户请在推荐实验室使用只读入口；这里只针对当前注册账号。</p>
      </aside>
      <AlgorithmPanel
        :key="refreshKey"
        label="新用户冷启动"
        :endpoint="auth.account ? '/me/recommendations/cold_start' : null"
        empty-subject="登录后体验"
      />
    </div>
  </div>
</template>
<style scoped>
.cold-start-grid { display: grid; grid-template-columns: minmax(220px, .72fr) minmax(0, 1.6fr); gap: 22px; align-items: start; }
.cold-start-notes { padding: 22px; border: 1px solid var(--border); border-radius: 10px; background: #f6f8fc; }
.cold-start-notes ol { display: grid; gap: 18px; margin: 18px 0; padding: 0; list-style: none; }
.cold-start-notes li { display: grid; gap: 4px; padding-left: 16px; border-left: 1px solid #d9e0ee; }
.cold-start-notes li strong { color: var(--text); font-size: 13px; font-weight: 500; }
.cold-start-notes li span { color: var(--muted); font-size: 12px; line-height: 1.75; }
.cold-start-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
@media (max-width: 780px) { .cold-start-grid { grid-template-columns: 1fr; } }
</style>
