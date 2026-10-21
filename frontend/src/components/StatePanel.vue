<script setup lang="ts">
import { computed, ref } from 'vue'
import { Refresh, Warning, Collection } from '@element-plus/icons-vue'
import { ApiError, ensureCsrf, errorMessage } from '../api'

const props = withDefaults(defineProps<{ loading?: boolean; error?: unknown; empty?: boolean; title?: string; description?: string }>(), { title: '这里还没有内容', description: '试试其他条件，或稍后再来看看。' })
const emit = defineEmits<{ retry: [] }>()
const recover = ref(false)
const recovery = ref('')
const csrfExpired = computed(() => props.error instanceof ApiError && props.error.code === 'CSRF_REJECTED')
async function updateCsrf() {
  recover.value = true
  try { await ensureCsrf(true); recovery.value = '安全凭据已更新，请重新提交刚才的操作。'; emit('retry') }
  catch (e) { recovery.value = errorMessage(e) }
  finally { recover.value = false }
}
</script>

<template>
  <div v-if="loading" class="state-panel" role="status" aria-live="polite"><el-icon class="loading-icon"><Refresh /></el-icon><p>正在读取真实数据…</p></div>
  <div v-else-if="error" class="state-panel error-panel" role="alert">
    <el-icon><Warning /></el-icon><h3>暂时无法完成</h3><p>{{ errorMessage(error) }}</p>
    <small v-if="error instanceof ApiError && error.requestId">请求编号 {{ error.requestId }}</small>
    <el-button v-if="csrfExpired" :loading="recover" @click="updateCsrf">更新安全凭据</el-button>
    <el-button v-else @click="$emit('retry')">重试</el-button>
    <p v-if="recovery" role="status">{{ recovery }}</p>
  </div>
  <div v-else-if="empty" class="state-panel" role="status"><el-icon><Collection /></el-icon><h3>{{ title }}</h3><p>{{ description }}</p><slot name="action" /></div>
  <slot v-else />
</template>
