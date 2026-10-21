<script setup lang="ts">
defineProps<{ page: number; total: number; size?: number }>()
defineEmits<{ change: [page: number] }>()
</script>
<template>
  <div v-if="total > (size || 12)" class="pagination" aria-label="分页">
    <span class="muted">共 {{ total.toLocaleString() }} 条</span>
    <el-pagination :current-page="page" :page-size="size || 12" :total="total" :pager-count="5" layout="prev, pager, next" background @current-change="$emit('change', $event)" />
  </div>
</template>
