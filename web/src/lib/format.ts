export const WIB = 7 * 3600

export const fmt = (n: number | null | undefined, d = 1) =>
  n == null || Number.isNaN(Number(n))
    ? '–'
    : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

export const signed = (n: number, d = 1) => `${n > 0 ? '+' : ''}${fmt(n, d)}`

export function wibTime(iso: string | null | undefined, withDate = true) {
  const ms = Date.parse(iso ?? '')
  if (Number.isNaN(ms)) return '–'
  const s = new Date(ms + WIB * 1000).toISOString()
  return (withDate ? s.slice(0, 16).replace('T', ' ') : s.slice(11, 19)) + ' WIB'
}

export function age(iso: string | null | undefined) {
  const ms = Date.now() - Date.parse(iso ?? '')
  if (!Number.isFinite(ms)) return null
  const m = Math.max(0, Math.round(ms / 60000))
  return m < 60 ? `${m} menit lalu` : m < 1440 ? `${Math.round(m / 60)} jam lalu` : `${Math.round(m / 1440)} hari lalu`
}

export const statusKind = (s = ''): 'none' | 'go' | 'wait' =>
  /NO TRADE|INVALID/i.test(s) ? 'none' : /AKTIF|SIAP/i.test(s) ? 'go' : 'wait'

export const dirClass = (a: string | null | undefined = '') =>
  /SELL/i.test(a ?? '') ? 'down' : /BUY/i.test(a ?? '') ? 'up' : 'flat'

export const store = {
  get(k: string) {
    try { return localStorage.getItem(k) } catch { return null }
  },
  set(k: string, v: string) {
    try { localStorage.setItem(k, v) } catch { /* private mode */ }
  },
}

// Gold trades Sun 22:00 UTC to Fri 21:00 UTC.
export function marketOpen(now = new Date()) {
  const d = now.getUTCDay(), h = now.getUTCHours()
  if (d === 6) return false
  if (d === 0) return h >= 22
  if (d === 5) return h < 21
  return true
}
