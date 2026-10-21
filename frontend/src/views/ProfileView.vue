<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { useRemote } from '../composables/useRemote'
import http from '../api'
import type { Movie, Page } from '../types'
import MovieCard from '../components/MovieCard.vue'
import StatePanel from '../components/StatePanel.vue'
import PageNav from '../components/PageNav.vue'
const auth = useAuth(), route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'favorites' ? 'favorites' : 'ratings')
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const { data, error, loading, load } = useRemote<Page<Movie>>()
const removing = ref<string | null>(null), actionError = ref<unknown>(null)
function fetchList() { if (!auth.account) return; return load(async () => (await http.get<Page<Movie>>(`/me/${tab.value}`, { params: { page: page.value, per_page: 12 } })).data) }
function changeTab(value: string | number) { actionError.value = null; void router.push({ path:'/profile', query: { tab: value } }) }
async function remove(movie: Movie) { removing.value = movie.movie_id; actionError.value = null; try { await http.delete(`/me/favorites/${movie.movie_id}`); if (data.value?.items.length === 1 && page.value > 1) await router.replace({query:{tab:'favorites',page:page.value-1}}); else await fetchList() } catch(e) { actionError.value = e } finally { removing.value = null } }
watch(() => [route.fullPath, auth.account?.account_id], fetchList, { immediate:true })
</script>
<template>
  <div class="page"><div class="profile-heading"><div class="avatar" aria-hidden="true">{{ auth.account?.login_name.slice(0,1).toUpperCase() || '·' }}</div><div><p class="eyebrow">YOUR FILM JOURNAL</p><h1>{{ auth.account?.login_name || '我的片单' }}</h1><p class="muted">{{ auth.account?.subject_key }} · 注册业务账号</p></div></div>
    <StatePanel v-if="!auth.account" :error="auth.restoreError" :empty="!auth.restoreError" title="请先登录" @retry="router.go(0)" />
    <template v-else><el-tabs :model-value="tab" @tab-change="changeTab"><el-tab-pane label="我的评分" name="ratings" /><el-tab-pane label="我的收藏" name="favorites" /></el-tabs><StatePanel v-if="actionError" :error="actionError" @retry="actionError=null" /><p class="result-caption">{{ data?.total || 0 }} 条{{ tab === 'ratings' ? '评分' : '收藏' }}记录 · 仅显示当前账号</p><StatePanel :loading="loading" :error="error" :empty="!!data && !data.items.length" :title="tab==='ratings' ? '第一条评分，等你留下' : '你的收藏夹还是空的'" description="浏览电影详情，记录你的评分与收藏。" @retry="fetchList"><template #action><RouterLink to="/movies" class="button primary">浏览电影</RouterLink></template><div class="movie-grid"><div v-for="movie in data?.items" :key="movie.movie_id"><MovieCard :movie="movie" /><div class="record-action"><span v-if="tab==='ratings'">我的评分 <strong>{{ movie.rating }}</strong></span><span v-else>已收藏</span><el-button v-if="tab==='favorites'" text :loading="removing===movie.movie_id" :disabled="!!removing" @click="remove(movie)">取消收藏</el-button><RouterLink v-else :to="`/movies/${movie.movie_id}`" class="text-link">修改评分 →</RouterLink></div></div></div></StatePanel><PageNav :page="page" :total="data?.total || 0" @change="router.push({query:{tab,page:$event}})" /></template>
  </div>
</template>
