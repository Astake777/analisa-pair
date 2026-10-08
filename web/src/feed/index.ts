import { useEffect, useState } from 'react'
import { startBinance } from './binance'
import { startMt5 } from './mt5'
import { startOanda } from './oanda'

declare const __OANDA_ENABLED__: boolean

export type TF = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D1' | 'W1'
export const TFS: TF[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1']
export const TF_SEC: Record<TF, number> = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400, W1: 604800 }

export type Bar = { time: number; open: number; high: number; low: number; close: number }
export type FeedStatus = 'connecting' | 'live' | 'reconnecting' | 'error'
export type FeedState = { bars: Bar[]; last: number | null; lastTick: number; status: FeedStatus; label: string }
export type Emit = (s: Partial<FeedState>) => void
export type Start = (tf: TF, emit: Emit, fail: () => void) => () => void

// Mutates `bars` in place. New bars keep the source's own bucket alignment (OANDA H4 is not aligned to 00:00 UTC).
export function applyTick(bars: Bar[], price: number, t: number, tfSec: number) {
  const b = bars[bars.length - 1]
  if (!b) return
  if (t < b.time + tfSec) {
    bars[bars.length - 1] = { ...b, high: Math.max(b.high, price), low: Math.min(b.low, price), close: price }
  } else {
    const time = b.time + Math.floor((t - b.time) / tfSec) * tfSec
    bars.push({ time, open: price, high: price, low: price, close: price })
  }
}

export const backoff = (n: number) => Math.min(30000, 1000 * 2 ** n)

export function throttle(fn: () => void, ms = 250) {
  let t = 0
  const run = () => {
    if (!t) t = window.setTimeout(() => { t = 0; fn() }, ms)
  }
  run.cancel = () => clearTimeout(t)
  return run
}

export type Source = 'mt5' | 'oanda' | 'binance'
// Urutan cadangan: MT5 lokal dulu, lalu OANDA (kalau dikonfigurasi), terakhir Binance.
const ORDER: Source[] = __OANDA_ENABLED__ ? ['mt5', 'oanda', 'binance'] : ['mt5', 'binance']

const INITIAL: FeedState = { bars: [], last: null, lastTick: 0, status: 'connecting', label: '' }

export function useLiveFeed(tf: TF) {
  const [source, setSource] = useState<Source>('mt5')
  const [state, setState] = useState<FeedState & { key: string }>({ ...INITIAL, key: '' })
  const key = `${source}:${tf}`

  useEffect(() => {
    const start = { mt5: startMt5, oanda: startOanda, binance: startBinance }[source]
    const next = ORDER[ORDER.indexOf(source) + 1] ?? 'binance'
    return start(tf, (s) => setState((p) => ({ ...(p.key === key ? p : { ...INITIAL, last: p.last, lastTick: p.lastTick }), ...s, key })), () => setSource(next))
  }, [tf, source, key])

  const s = state.key === key ? state : { ...INITIAL, last: state.last, lastTick: state.lastTick }
  return { bars: s.bars, last: s.last, lastTick: s.lastTick, status: s.status, label: s.label, source }
}
