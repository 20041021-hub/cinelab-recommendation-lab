import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.API_TARGET || 'http://127.0.0.1:8000'
  const proxy = { '/api/v1': { target, changeOrigin: false } }
  // Preserve the browser Origin; the backend explicitly trusts the frontend origin.
  return {
    plugins: [vue()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
    build: { chunkSizeWarningLimit: 1100 },
  }
})
