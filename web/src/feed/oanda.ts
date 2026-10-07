import { applyTick, backoff, throttle, TF_SEC, type Bar, type Start } from './index'

type Candle = { time: string; mid: { o: string; h: string; l: string; c: string } }
type Msg = { type: string; time?: string; bids?: { price: string }[]; asks?: { price: string }[] }

// OANDA sends nanosecond fractions; trim to milliseconds so every engine parses it.
const sec = (t: string) => Date.parse(t.replace(/(\.\d{3})\d+/, '$1')) / 1000

export const startOanda: Start = (tf, emit, fail) => {
  let dead = false, tries = 0, timer = 0, streamed = false, hadHistory = false
  let ctrl: AbortController | null = null
  let bars: Bar[] = [], last: number | null = null, lastTick = 0
  const flush = throttle(() => { if (!dead) emit({ bars: [...bars], last, lastTick, status: 'live' }) })

  async function connect() {
    ctrl = new AbortController()
    try {
      const r = await fetch(`/oanda/api/v3/instruments/XAU_USD/candles?granularity=${tf === 'D1' ? 'D' : tf === 'W1' ? 'W' : tf}&count=1500&price=M`, { signal: ctrl.signal })
      if (!r.ok) throw new Error(`candles HTTP ${r.status}`)
      const { candles } = (await r.json()) as { candles: Candle[] }
      bars = candles.map((c) => ({ time: sec(c.time), open: +c.mid.o, high: +c.mid.h, low: +c.mid.l, close: +c.mid.c }))
      last = bars.at(-1)?.close ?? null
      hadHistory = true
      emit({ bars: [...bars], last, label: 'OANDA', status: 'connecting' })

      const s = await fetch('/oanda/stream', { signal: ctrl.signal })
      if (!s.ok || !s.body) throw new Error(`stream HTTP ${s.status}`)
      const reader = s.body.pipeThrough(new TextDecoderStream()).getReader()
      let buf = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += value
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.trim()) continue
          const m = JSON.parse(line) as Msg
          tries = 0
          streamed = true
          if (m.type === 'PRICE' && m.bids?.[0] && m.asks?.[0] && m.time) {
            last = (Number(m.bids[0].price) + Number(m.asks[0].price)) / 2
            applyTick(bars, last, sec(m.time), TF_SEC[tf])
            lastTick = Date.now()
          }
          flush()
        }
      }
      throw new Error('stream ditutup')
    } catch {
      if (dead) return
      // Never got data from OANDA at all: hand over to the Binance fallback.
      if (!hadHistory || (!streamed && tries >= 3)) return fail()
      emit({ status: 'reconnecting' })
      timer = window.setTimeout(connect, backoff(tries++))
    }
  }

  connect()
  return () => {
    dead = true
    ctrl?.abort()
    clearTimeout(timer)
    flush.cancel()
  }
}
