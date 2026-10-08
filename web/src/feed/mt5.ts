import { applyTick, backoff, throttle, TF_SEC, type Bar, type Start } from './index'

type Tick = { bid: number; ask: number; last: number; time: number }

// Harga broker HFM dari jembatan MT5 lokal (port 5181, lewat proxy /mt5).
export const startMt5: Start = (tf, emit, fail) => {
  let dead = false, tries = 0, timer = 0
  let bars: Bar[] = [], last: number | null = null, lastTick = 0
  const flush = throttle(() => { if (!dead) emit({ bars: [...bars], last, lastTick, status: 'live', label: 'MT5 HFM' }) })

  async function candles() {
    const r = await fetch(`/mt5/candles?tf=${tf}&n=1000`)
    if (!r.ok) throw new Error(`candles HTTP ${r.status}`)
    const b = (await r.json()) as Bar[]
    if (!b.length) throw new Error('candles kosong')
    return b
  }

  async function poll() {
    try {
      const r = await fetch('/mt5/tick')
      if (!r.ok) throw new Error(`tick HTTP ${r.status}`)
      const t = (await r.json()) as Tick
      if (dead) return
      if (!Number.isFinite(t.last)) throw new Error('tick tidak valid')
      last = t.last
      applyTick(bars, last, t.time, TF_SEC[tf])
      lastTick = Date.now()
      tries = 0
      flush()
      timer = window.setTimeout(poll, 500)
    } catch {
      if (dead) return
      // Satu kali gagal belum dianggap putus.
      if (tries > 0) emit({ status: tries > 2 ? 'error' : 'reconnecting' })
      timer = window.setTimeout(poll, backoff(tries++))
    }
  }

  // Candle diambil ulang tiap 60 detik supaya bar yang sedang terbentuk sama dengan MT5.
  const resync = window.setInterval(async () => {
    try {
      const b = await candles()
      if (dead) return
      bars = b
      flush()
    } catch { /* tetap pakai bar lama */ }
  }, 60000)

  candles().then((b) => {
    if (dead) return
    bars = b
    last = b[b.length - 1].close
    emit({ bars: [...bars], last, label: 'MT5 HFM', status: 'connecting' })
    poll()
  }, () => {
    // Jembatan mati atau MT5 tidak tersedia: pindah ke sumber berikutnya.
    if (!dead) fail()
  })

  return () => {
    dead = true
    clearTimeout(timer)
    clearInterval(resync)
    flush.cancel()
  }
}
