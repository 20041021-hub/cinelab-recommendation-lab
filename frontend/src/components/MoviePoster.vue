<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Film } from '@element-plus/icons-vue'
const props = defineProps<{ path?: string | null; title: string; movieId?: string | number | null }>()
const failed = ref(false)
watch(() => [props.path, props.movieId], () => { failed.value = false })
// 课程数据包里 poster_path 全为空（没有随包授权的海报文件）；
// 所以把 movie_id 交给后端：后端按 tmdb_id 从公开剧照页取回真实海报并缓存到 posters/，
// 取不到（或抓取失败）时才退回后端生成的占位海报。
const remote = computed(() => props.path && /^\/[a-zA-Z0-9._-]+\.(jpg|png|webp)$/.test(props.path) ? `https://image.tmdb.org/t/p/w342${props.path}` : null)
const generated = computed(() => (props.movieId ? `/api/v1/movies/${props.movieId}/poster` : null))
const src = computed(() => remote.value || generated.value)
</script>
<template>
  <div class="movie-poster">
    <img v-if="src && !failed" :src="src" :alt="`${title}海报`" loading="lazy" @error="failed = true" />
    <div v-else class="poster-placeholder" role="img" :aria-label="title + '：暂无海报'">
      <span class="frame-corner top" aria-hidden="true" /><el-icon aria-hidden="true"><Film /></el-icon><span>暂无海报</span><small>CINELAB ARCHIVE</small><span class="frame-corner bottom" aria-hidden="true" />
    </div>
  </div>
</template>