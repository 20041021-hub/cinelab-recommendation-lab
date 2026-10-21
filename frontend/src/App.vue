<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { User, Right } from '@element-plus/icons-vue'
import { useAuth } from './stores/auth'
import { ElMessage } from 'element-plus'
import { errorMessage, ApiError, ensureCsrf } from './api'
import { ref } from 'vue'
const auth = useAuth(), route = useRoute(), router = useRouter()
const signingOut = ref(false)
const csrfError = ref(false)
async function logout() {
  signingOut.value = true
  try { await auth.signOut(); ElMessage.success('已退出，原会话已失效'); await router.push('/') }
  catch (e) {
    csrfError.value = e instanceof ApiError && e.code === 'CSRF_REJECTED'
    ElMessage.error(errorMessage(e))
  } finally { signingOut.value = false }
}
async function recover() {
  try { await ensureCsrf(true); csrfError.value = false; ElMessage.success('安全凭据已更新，请重新操作') }
  catch (e) { ElMessage.error(errorMessage(e)) }
}
function expired() {
  const hadAccount = !!auth.account
  auth.expire()
  if (hadAccount && route.meta.requiresAuth) void router.replace({ path: '/login', query: { redirect: route.fullPath, reason: 'expired' } })
}
window.addEventListener('session-expired', expired)
onBeforeUnmount(() => window.removeEventListener('session-expired', expired))
</script>
<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <header class="site-header"><div class="header-inner">
    <RouterLink class="brand" to="/" aria-label="片刻 CineLab 首页"><span class="brand-mark" aria-hidden="true">▶</span><span>片刻<span class="brand-en">CINELAB</span></span></RouterLink>
    <nav aria-label="主导航"><RouterLink to="/" exact-active-class="active">发现</RouterLink><RouterLink to="/movies" active-class="active">电影库</RouterLink><RouterLink to="/experiments" active-class="active">推荐实验室<span class="nav-dot" /></RouterLink></nav>
    <div class="account-nav"><template v-if="auth.account"><RouterLink to="/profile" class="account-link"><el-icon aria-hidden="true"><User /></el-icon><span>{{ auth.account.login_name }}</span></RouterLink><el-button text :loading="signingOut" @click="logout">退出</el-button></template><RouterLink v-else to="/login" class="login-link">登录 / 注册 <el-icon aria-hidden="true"><Right /></el-icon></RouterLink></div>
  </div></header>
  <main id="main" tabindex="-1"><div v-if="auth.restoreError" class="global-notice" role="alert">{{ errorMessage(auth.restoreError) }} <el-button text @click="router.go(0)">重新连接</el-button></div><div v-if="csrfError" class="global-notice"><el-button @click="recover">更新安全凭据</el-button></div><RouterView /></main>
  <footer class="site-footer"><div><RouterLink class="footer-brand" to="/">片刻 CINELAB</RouterLink><p>每一份喜好，都是发现的开始。</p></div><div class="footer-meta"><span>MovieLens 真实电影数据</span><span>推荐算法及应用 · 课程实验</span></div></footer>
</template>
