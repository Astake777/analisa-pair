import { applyTick, backoff, throttle, TF_SEC, type Bar, type Start, type TF } from './index'

const IV: Record<TF, string> = { M1: '1m', M5: '5m', M15: '15m', M30: '30m', H1: '1h', H4: '4h' }
type Kline = { t: number; o: string; h: string; l: string; c: string }

// XAUT trades at a small premium/discount to spot. basis = gold-api spot minus XAUT, smoothed over ~5 polls.
export const startBinance: Start = (tf, emit) => {
  const iv = IV[tf]
  let dead = false, tries = 0, timer = 0
  let ws: WebSocket | null = null
  let raw: Bar[] = [], disp: Bar[] = []
  let basis: number | null = null, lastRaw: number | null = null, lastTick = 0

  const shift = (b: Bar): Bar =>
    basis == null ? b : { time: b.time, open: b.open + basis, high: b.high + basis, low: b.low + basis, close: b.close + basis }
  const sync = () => { for (let i = Math.max(0, disp.length - 1); i < raw.length; i++) disp[i] = shift(raw[i]) }
  const snapshot = () => ({
    bars: [...disp],
    last: lastRaw == null ? null : lastRaw + (basis ?? 0),
    lastTick,
    label: basis == null ? 'XAUT (menunggu spot)' : 'XAUT + koreksi spot',
  })
  const flush = throttle(() => {
    if (!dead) emit({ ...snapshot(), status: ws?.readyState === WebSocket.OPEN ? 'live' : 'connecting' })
  })

  async function poll() {
    try {
      const r = await fetch('https://api.gold-api.com/price/XAU')
      const { price } = (await r.json()) as { price: number }
      if (dead || lastRaw == null || !Number.isFinite(price)) return
      const s = price - lastRaw
      basis = basis == null ? s : basis + (s - basis) / 3
      disp = raw.map(shift)
      flush()
    } catch { /* keep last basis */ }
  }
  const pollTimer = window.setInterval(poll, 10000)

  function reconnect() {
    if (dead) return
    emit({ status: tries > 2 ? 'error' : 'reconnecting' })
    timer = window.setTimeout(connect, backoff(tries++))
  }

  async function connect() {
    try {
      const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=XAUTUSDT&interval=${iv}&limit=1000`)
      if (!r.ok) throw new Error(`klines HTTP ${r.status}`)
      const rows = (await r.json()) as [number, string, string, string, string][]
      raw = rows.map((k) => ({ time: k[0] / 1000, open: +k[1], high: +k[2], low: +k[3], close: +k[4] }))
      lastRaw = raw.at(-1)?.close ?? null
      disp = raw.map(shift)
      if (dead) return
      emit({ ...snapshot(), status: 'connecting' })
      if (basis == null) poll()
    } catch {
      return reconnect()
    }

    ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=xautusdt@kline_${iv}/xautusdt@trade`)
    ws.onmessage = (ev) => {
      tries = 0
      const { stream, data } = JSON.parse(ev.data as string) as { stream: string; data: { p?: string; T?: number; k?: Kline } }
      if (stream.endsWith('@trade') && data.p && data.T) {
        lastRaw = +data.p
        applyTick(raw, lastRaw, data.T / 1000, TF_SEC[tf])
      } else if (data.k) {
        const k = data.k
        const b = { time: k.t / 1000, open: +k.o, high: +k.h, low: +k.l, close: +k.c }
        const i = raw.length - 1
        if (raw[i]?.time === b.time) raw[i] = b
        else if (!raw[i] || b.time > raw[i].time) raw.push(b)
        lastRaw = b.close
      }
      lastTick = Date.now()
      sync()
      flush()
    }
    ws.onclose = () => reconnect()
  }

  connect()
  return () => {
    dead = true
    clearTimeout(timer)
    clearInterval(pollTimer)
    flush.cancel()
    if (ws) { ws.onclose = null; ws.close() }
  }
}
