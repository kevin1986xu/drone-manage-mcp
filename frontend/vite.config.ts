import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端：AG-UI SSE + 确认 + 编辑器 REST。
      // 默认演示版 FastAPI(8000)；BACKEND_PORT=8300 可切正式版 BFF（DeerFlow 链路）
      '/api': { target: `http://localhost:${process.env.BACKEND_PORT || 8000}`, changeOrigin: true },
    },
  },
})
