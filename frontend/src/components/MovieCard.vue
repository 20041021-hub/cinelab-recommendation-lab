<script setup lang="ts">
import type { Movie } from '../types'
import MoviePoster from './MoviePoster.vue'
defineProps<{ movie: Movie }>()
</script>
<template>
  <article class="movie-card">
    <RouterLink :to="`/movies/${movie.movie_id}`" :aria-label="`查看 ${movie.title_zh || movie.movielens_title} 详情`">
      <div class="poster-wrap"><MoviePoster :path="movie.poster_path" :movie-id="movie.movie_id" :title="movie.movielens_title" /><span class="movie-id">#{{ movie.movie_id }}</span><span v-if="movie.rating" class="my-rating">我的评分 {{ movie.rating }}</span></div>
      <h3>{{ movie.title_zh || movie.movielens_title }}</h3>
      <p>{{ movie.release_year || '年份未收录' }}<span aria-hidden="true"> · </span>{{ movie.genres.slice(0, 2).join(' / ') || '类型未收录' }}</p>
    </RouterLink>
  </article>
</template>
