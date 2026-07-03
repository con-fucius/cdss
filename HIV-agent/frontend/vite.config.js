import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import process from 'node:process'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            const normalized = id.replaceAll('\\', '/')
            if (normalized.includes('react-syntax-highlighter') || normalized.includes('refractor')) {
              return 'syntax'
            }
            if (normalized.includes('framer-motion') || normalized.includes('motion-dom') || normalized.includes('lucide-react')) {
              return 'ui'
            }
            return 'vendor'
          },
        },
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/health': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/metrics': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/phase7': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/diseases': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/guidelines': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/feedback': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/sessions': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/admin': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/memory': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/evidence': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/terminology': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/chat': {
          target: backendTarget,
          changeOrigin: true,
          ws: true,
        },
        '/query-build': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/context-options': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/pageindex': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/init-status': {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
