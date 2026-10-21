import { defineStore } from 'pinia'
import { ref } from 'vue'
import http, { ApiError, ensureCsrf } from '../api'
import type { Account } from '../types'

export const useAuth = defineStore('auth', () => {
  const account = ref<Account | null>(null)
  const initialized = ref(false)
  const restoreError = ref<unknown>(null)
  let restoration: Promise<void> | undefined
  async function restore() {
    if (initialized.value) return
    if (!restoration) restoration = (async () => {
      try {
        await ensureCsrf()
        account.value = (await http.get<Account>('/auth/me')).data
        restoreError.value = null
      } catch (error) {
        account.value = null
        if (!(error instanceof ApiError && error.status === 401)) restoreError.value = error
      } finally { initialized.value = true }
    })().finally(() => { restoration = undefined })
    return restoration
  }
  async function signIn(login_name: string, password: string) {
    account.value = (await http.post<{ account: Account }>('/auth/login', { login_name, password })).data.account
    restoreError.value = null
  }
  async function signOut() {
    await http.post('/auth/logout')
    account.value = null
  }
  function expire() { account.value = null }
  return { account, initialized, restoreError, restore, signIn, signOut, expire }
})
