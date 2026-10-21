import { onScopeDispose, ref, shallowRef } from 'vue'

export function useRemote<T>() {
  const data = shallowRef<T | null>(null)
  const error = shallowRef<unknown>(null)
  const loading = ref(false)
  let generation = 0
  onScopeDispose(() => { generation++ })
  async function load(task: () => Promise<T>) {
    const current = ++generation
    loading.value = true; error.value = null; data.value = null
    try { const value = await task(); if (current === generation) data.value = value }
    catch (e) { if (current === generation) error.value = e }
    finally { if (current === generation) loading.value = false }
  }
  return { data, error, loading, load }
}
