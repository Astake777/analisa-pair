import { readFileSync } from 'node:fs'
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

  // Yahoo tidak mengirim header CORS; indeks pendukung (US10Y, DXY, dst.) diambil lewat proxy ini.
  const yahoo: ProxyOptions = {
    target: 'https://query1.finance.yahoo.com',
    changeOrigin: true,
    headers: { 'User-Agent': 'Mozilla/5.0' },
    rewrite: (p) => p.replace(/^\/yahoo/, ''),
  }
  // Jembatan MT5 lokal (jembatan_mt5.py) untuk harga broker HFM. Token POST disisipkan di sini, browser tidak pernah memegangnya.
  let jembatan = ''
  try { jembatan = readFileSync(new URL('../data/jembatan_token.txt', import.meta.url), 'utf8').trim() } catch { /* token belum dibuat */ }
  const mt5: ProxyOptions = {
    target: 'http://127.0.0.1:5181',
    ...(jembatan ? { headers: { 'X-Jembatan-Token': jembatan } } : {}),
    rewrite: (p) => p.replace(/^\/mt5/, ''),
  }
  const proxy: Record<string, ProxyOptions> = enabled
    ? {
        '/yahoo': yahoo,
        '/mt5': mt5,
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
    : { '/yahoo': yahoo, '/mt5': mt5 }

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
