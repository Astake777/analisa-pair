import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'

export default defineConfig(({ mode }) => {
  // Empty prefix loads server-only keys too; they stay in this file and never reach the client.
  const env = loadEnv(mode, '..', '')
  const token = env.OANDA_TOKEN
  const account = env.OANDA_ACCOUNT_ID
  const enabled = Boolean(token && account)
  const host = env.OANDA_ENV === 'live' ? 'fxtrade' : 'fxpractice'
  const headers = { Authorization: `Bearer ${token}` }

  const proxy: Record<string, ProxyOptions> = enabled
    ? {
        '/oanda/api': {
          target: `https://api-${host}.oanda.com`,
          changeOrigin: true,
          headers,
          rewrite: (p) => p.replace(/^\/oanda\/api/, ''),
        },
        '/oanda/stream': {
          target: `https://stream-${host}.oanda.com`,
          changeOrigin: true,
          headers,
          rewrite: () => `/v3/accounts/${encodeURIComponent(account)}/pricing/stream?instruments=XAU_USD`,
        },
      }
    : {}

  return {
    plugins: [react()],
    envDir: '..',
    define: { __OANDA_ENABLED__: JSON.stringify(enabled) },
    server: {
      port: 5180,
      strictPort: true,
      proxy,
      fs: { allow: ['.', '../supabase'] },
    },
    preview: { proxy },
  }
})
