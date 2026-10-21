import axios, { AxiosError } from 'axios'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
    public details: unknown = null, public requestId?: string, public retryAfter?: number) { super(message) }
}

const http = axios.create({ baseURL: '/api/v1', withCredentials: true, timeout: 15000 })
let csrfToken = ''
let pendingCsrf: Promise<void> | undefined
export function resetCsrf() { csrfToken = '' }

// One shared initialization per page; the nonce stays in memory, never localStorage.
export function ensureCsrf(force = false): Promise<void> {
  if (force) resetCsrf()
  if (csrfToken) return Promise.resolve()
  if (!pendingCsrf) {
    pendingCsrf = http.get('/auth/csrf').then(({ data }) => { csrfToken = data.csrf_token })
      .finally(() => { pendingCsrf = undefined })
  }
  return pendingCsrf
}

http.interceptors.request.use(async config => {
  if (['post', 'put', 'patch', 'delete'].includes(config.method || '')) {
    await ensureCsrf()
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  return config
})
http.interceptors.response.use(response => {
  if (response.data?.csrf_token) csrfToken = response.data.csrf_token
  return response
}, (error: AxiosError<{ error?: { code?: string; message?: string; details?: unknown }; request_id?: string }>) => {
  const status = error.response?.status || 0
  const body = error.response?.data
  const code = body?.error?.code || (status ? `HTTP_${status}` : 'NETWORK_ERROR')
  if (status === 403 && code === 'CSRF_REJECTED') resetCsrf()
  if (status === 401 && !error.config?.url?.endsWith('/auth/login')) {
    window.dispatchEvent(new Event('session-expired'))
  }
  // A rejected mutation is never silently retried. Let the user refresh CSRF explicitly.
  return Promise.reject(new ApiError(status, code, body?.error?.message || '暂时无法连接服务，请稍后重试',
    body?.error?.details, body?.request_id, Number(error.response?.headers['retry-after']) || undefined))
})

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '操作未完成，请稍后重试'
  if (error.status === 401) return error.code === 'INVALID_CREDENTIALS' ? '登录名或密码错误' : '请登录后继续；原会话可能已失效'
  if (error.status === 403) return error.code === 'CSRF_REJECTED' ? '安全校验已过期或会话已变化，请更新安全凭据后重试' : '请求来源或权限校验未通过，请检查访问地址'
  if (error.status === 409) return '这个登录名已被使用，请换一个'
  if (error.status === 422) return '输入格式不正确，请检查字段、评分范围或访问地址'
  if (error.status === 429) return `登录尝试过于频繁，请在 ${error.retryAfter || 60} 秒后重试`
  if (error.status === 501) return '尚未实现，等待算法接入'
  if (error.status === 503) return '服务尚未就绪，请稍后重试或联系教师检查后端'
  return error.message
}

export default http
