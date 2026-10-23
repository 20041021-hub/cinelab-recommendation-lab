<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Film } from '@element-plus/icons-vue'
const props = defineProps<{ path?: string | null; title: string; movieId?: string | number | null }>()
const failed = ref(false)
watch(() => [props.path, props.movieId], () => { failed.value = false })
// 课程数据包里 poster_path 全为空（没有授权海报），外部图床在本机也不可达；
// 因此把 movie_id 交给后端，用真实元数据确定性地生成一张本地海报顶上。
const remote = computed(() => props.path && /^\/[a-zA-Z0-9._-]+\.(jpg|png|webp)$/.test(props.path) ? `https://image.tmdb.org/t/p/w342${props.path}` : null)
const generated = computed(() => (props.movieId ? `/api/v1/movies/${props.movieId}/poster.svg` : null))
const src = computed(() => remote.value || generated.value)
</script>
<template>
  <div class="movie-poster" :class="{ 'is-generated': !remote && !!generated }">
    <img v-if="src && !failed" :src="src" :alt="`${title}${remote ? '海报' : '：本地生成海报'}`" loading="lazy" @error="failed = true" />
    <div v-else class="poster-placeholder" role="img" :aria-label="title + '：暂无海报'">
      <span class="frame-corner top" aria-hidden="true" /><el-icon aria-hidden="true"><Film /></el-icon><span>暂无海报</span><small>CINELAB ARCHIVE</small><span class="frame-corner bottom" aria-hidden="true" />
    </div>
  </div>
</template>