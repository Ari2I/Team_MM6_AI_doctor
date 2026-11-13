import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/applications': { target: 'http://127.0.0.1:8000', timeout: 300000, proxyTimeout: 300000 },
      '/health': { target: 'http://127.0.0.1:8000', timeout: 300000, proxyTimeout: 300000 },
    },
  },
})
