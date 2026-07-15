import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端（FastAPI）：AG-UI SSE + 确认 + 编辑器 REST
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
