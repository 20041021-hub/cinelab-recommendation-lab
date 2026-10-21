<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import http from '../api'
import { useRemote } from '../composables/useRemote'
import type { Movie, Page } from '../types'
import MovieCard from '../components/MovieCard.vue'
import StatePanel from '../components/StatePanel.vue'
import PageNav from '../components/PageNav.vue'
const route = useRoute(), router = useRouter()
const q = ref(''), genre = ref(''), genres = ref<string[]>([])
const genreError = ref<unknown>(null)
const { data, error, loading, load } = useRemote<Page<Movie>>()
const page = computed(() => Math.max(1, Math.min(100000, Number(route.query.page) || 1)))
async function fetchGenres() { try { genres.value = (await http.get<{items:string[]}>('/genres')).data.items; genreError.value = null } catch (e) { genreError.value = e } }
function fetchMovies() { return load(async () => (await http.get<Page<Movie>>('/movies', { params: { page: page.value, per_page: 12, ...(q.value ? { q: q.value } : {}), ...(genre.value ? { genre: genre.value } : {}) } })).data) }
function search(nextPage = 1) { void router.push({ path: '/movies', query: { ...(q.value ? { q: q.value } : {}), ...(genre.value ? { genre: genre.value } : {}), ...(nextPage > 1 ? { page: nextPage } : {}) } }); if (nextPage === page.value && q.value === (route.query.q || '') && genre.value === (route.query.genre || '')) void fetchMovies() }
watch(() => route.fullPath, () => { q.value = typeof route.query.q === 'string' ? route.query.q : ''; genre.value = typeof route.query.genre === 'string' ? route.query.genre : ''; void fetchMovies() }, { immediate: true })
void fetchGenres()
</script>
<template>
  <div class="page"><div class="page-heading"><p class="eyebrow">EXPLORE THE ARCHIVE</p><h1>电影库<span class="heading-dot">.</span></h1><p>片名、年代、类型，总有一种方式让你遇见好电影。</p></div>
    <form class="filter-bar" @submit.prevent="search()"><div class="search-field"><label for="movie-search">搜索片名</label><el-input id="movie-search" v-model="q" :maxlength="100" placeholder="输入中文或原始片名" :prefix-icon="Search" clearable /></div><div class="genre-field"><label for="genre-select">电影类型</label><el-select id="genre-select" v-model="genre" placeholder="全部类型" clearable><el-option v-for="value in genres" :key="value" :label="value" :value="value" /></el-select></div><el-button type="primary" native-type="submit" :loading="loading">搜索电影</el-button></form>
    <StatePanel v-if="genreError" :error="genreError" @retry="fetchGenres" />
    <div class="result-caption"><span>{{ data ? `${data.total.toLocaleString()} 部电影` : '电影档案' }}</span><span>原始编号升序 · 每页 12 部</span></div>
    <StatePanel :loading="loading" :error="error" :empty="!!data && !data.items.length" title="没有找到匹配的电影" description="尝试简短片名，或清除类型筛选。" @retry="fetchMovies"><div class="movie-grid"><MovieCard v-for="movie in data?.items" :key="movie.movie_id" :movie="movie" /></div></StatePanel>
    <PageNav :page="page" :total="data?.total || 0" @change="search" />
  </div>
</template>
