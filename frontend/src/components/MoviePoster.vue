<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Film } from '@element-plus/icons-vue'
const props = defineProps<{ path?: string | null; title: string }>()
const failed = ref(false)
watch(() => props.path, () => { failed.value = false })
const src = computed(() => props.path && /^\/[a-zA-Z0-9._-]+\.(jpg|png|webp)$/.test(props.path) ? `https://image.tmdb.org/t/p/w342${props.path}` : null)
</script>
<template>
  <div class="movie-poster">
    <img v-if="src && !failed" :src="src" :alt="title + '海报'" loading="lazy" @error="failed = true" />
    <div v-else class="poster-placeholder" role="img" :aria-label="title + '：暂无海报'">
      <span class="frame-corner top" aria-hidden="true" /><el-icon aria-hidden="true"><Film /></el-icon><span>暂无海报</span><small>CINELAB ARCHIVE</small><span class="frame-corner bottom" aria-hidden="true" />
    </div>
  </div>
</template>
