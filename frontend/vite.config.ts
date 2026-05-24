import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:10011',
      '/auth': 'http://localhost:10011',
      '/setup': 'http://localhost:10011',
      '/settings': 'http://localhost:10011',
      '/health': 'http://localhost:10011',
      '/admin': 'http://localhost:10011'
    }
  }
})
