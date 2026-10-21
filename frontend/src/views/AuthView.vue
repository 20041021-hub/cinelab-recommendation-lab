<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { useAuth } from '../stores/auth'
import { safeRedirect } from '../router'
import http, { ApiError, ensureCsrf, errorMessage } from '../api'
const auth = useAuth(), route = useRoute(), router = useRouter()
const register = computed(() => route.path === '/register')
const form = reactive({ login_name: '', password: '' }), formRef = ref<FormInstance>()
const busy = ref(false), error = ref<unknown>(null), recovery = ref('')
const rules: FormRules = {
  login_name: [{ required: true, message: '请输入登录名', trigger: 'blur' }, { min: 3, max: 64, message: '登录名需3—64个字符', trigger: 'blur' }, { pattern: /^[\p{L}\p{N}_.-]+$/u, message: '使用字母、数字、下划线、点或短横线', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }, { min: 12, max: 128, message: '密码需12—128个字符', trigger: 'blur' }],
}
async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  busy.value = true; error.value = null; recovery.value = ''
  try {
    if (register.value) {
      await http.post('/auth/register', form)
      form.password = ''; ElMessage.success('注册成功，请登录')
      await router.replace({ path: '/login', query: route.query })
    } else { await auth.signIn(form.login_name, form.password); form.password = ''; await router.push(safeRedirect(route.query.redirect)) }
  } catch (e) { error.value = e }
  finally { busy.value = false }
}
async function recover() { try { await ensureCsrf(true); error.value = null; recovery.value = '安全凭据已更新，请重新提交。' } catch (e) { error.value = e } }
</script>
<template>
  <div class="page auth-page"><section class="auth-intro"><p class="eyebrow">A SPACE FOR YOUR TASTE</p><h1>记录喜欢，<br /><span>让发现发生。</span></h1><p>为看过的电影评分，把想看的电影收藏。<br />你的片单，从这里开始。</p><div class="auth-decoration" aria-hidden="true"><span>01 / WATCH</span><span>02 / RATE</span><span>03 / DISCOVER</span></div><p class="muted">内容推荐已接入；登录后，评分会转化为你的类型偏好。</p></section>
    <section class="auth-card"><div class="auth-tabs"><RouterLink :to="{path:'/login',query:route.query}" :class="{selected:!register}">登录</RouterLink><RouterLink :to="{path:'/register',query:route.query}" :class="{selected:register}">创建账号</RouterLink></div><h2>{{ register ? '开启你的电影档案' : '欢迎回到片刻' }}</h2><p class="muted">{{ register ? '注册业务账号，历史 MovieLens 用户无需注册。' : '使用你自己的注册账号登录。' }}</p>
      <el-alert v-if="route.query.reason === 'expired'" title="会话已失效，请重新登录" type="warning" :closable="false" />
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit"><el-form-item label="登录名" prop="login_name"><el-input v-model="form.login_name" name="username" autocomplete="username" :maxlength="64" placeholder="3—64个字符" /></el-form-item><el-form-item label="密码" prop="password"><el-input v-model="form.password" type="password" name="password" :autocomplete="register ? 'new-password' : 'current-password'" show-password :maxlength="128" placeholder="12—128个字符" /></el-form-item><div v-if="error" class="form-error" role="alert"><p>{{ errorMessage(error) }}</p><el-button v-if="error instanceof ApiError && error.code === 'CSRF_REJECTED'" text @click="recover">更新安全凭据</el-button></div><p v-if="recovery" role="status">{{ recovery }}</p><el-button type="primary" native-type="submit" class="full-width" :loading="busy">{{ register ? '创建账号' : '登录' }}</el-button></el-form><p class="form-hint">历史匿名用户使用 ml: 标识，注册账号使用 app: 标识，两者独立。</p>
    </section>
  </div>
</template>
