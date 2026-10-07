import { createClient } from '@supabase/supabase-js'
import { useEffect, useState } from 'react'

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined
const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined
export const sb = url && key ? createClient(url, key) : null
export const FIXTURE = !sb

export type Mode = 'scalp' | 'intraday' | 'swing'
export type Side = 'sell' | 'buy'
export type Setup = {
  side: Side; label?: string; entry: number; zone?: [number, number]; sl: number; risk?: number
  tp?: number[]; rr?: number[]; status?: string; trigger?: string; batal?: string; eksperimen?: string
  langkah?: string[]; alasan?: { entry?: string; sl?: string; tp?: string }
}
export type Driver = {
  sym: string; label: string; relasi: string; last: number; chg: number; chgPct: number; series?: [number, number][]
}
export type CalEvent = {
  waktuUTC?: string; waktuWIB: string; jenis?: string | null; arah?: string | null; kekuatan?: string | null
  items: { title: string; impact?: 'High' | 'Medium' | null; actual?: number | null; forecast?: number | null; previous?: number | null }[]
}
export type Payload = {
  updatedAt: string; pair: string; mode?: string; gaya?: string; status: string; keyakinan?: string; price?: number
  priceSymbol?: string; contoh?: string; makroKonfirmasi?: string
  bias?: Record<string, { bias: string; rsi?: number | null; catatan?: string }>
  levels?: { price: number; label: string; kind: string }[]
  zones?: { lo: number; hi: number; side: Side; label: string }[]
  setups?: Setup[]
  prediksiNews?: { event: string; waktuWIB: string; arah: string; kekuatan?: string; alasan?: string; batal?: string }[]
  headlines?: { title: string; url: string; published?: string; bacaan?: string }[]
  notes?: string[]
  drivers?: Driver[]
  events?: CalEvent[]
  strategi?: {
    regime?: { tren?: string; volatilitas?: string; sesi?: string; jendelaNews?: boolean }
    terpilih?: string; alasan?: string
  }
  amd?: {
    tanggal?: string; fase?: string; catatan?: string
    rangeAsia?: { lo: number; hi: number } | null
    sweep?: { sisi?: string; harga?: number; waktu?: string } | null
  }
  basis?: { sumber?: string; nilai?: number }
}
export type Analysis = {
  id?: string; pair: string; mode: string; created_at: string; status: string
  keyakinan: string | null; price: number | null; payload: Payload
}
export type NewsRow = {
  id: string; event_time: string; title: string; importance: number | null
  forecast: number | null; previous: number | null; actual: number | null
  dampak: { panas?: string; dingin?: string } | null
  lean: { arah?: string; kekuatan?: string; alasan?: string } | null
  hasil: Record<string, unknown> | null
}
export type BacktestRow = {
  run_id: string; run_at: string; strategy: string; regime: string; sample: 'in' | 'oos'; trades: number
  winrate: number | null; expectancy: number | null; profit_factor: number | null; max_dd_r: number | null
}
export type MacroRow = { series: string; date: string; value: number }

type Rows<T> = { rows: T[] | null; error: string | null }

async function q<T>(p: PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T[]> {
  const { data, error } = await p
  if (error) throw new Error(error.message)
  return (data ?? []) as T[]
}

// Loads rows for `key`, reloads whenever Realtime reports a change on `table` (optionally filtered).
function useRows<T>(key: string, load: (() => Promise<T[]>) | null, table?: string, filter?: string): Rows<T> {
  const [state, setState] = useState<Rows<T> & { key: string }>({ key: '', rows: null, error: null })
  useEffect(() => {
    if (!load) return
    let alive = true
    const run = () =>
      load().then(
        (rows) => alive && setState({ key, rows, error: null }),
        (e: Error) => alive && setState({ key, rows: null, error: e.message || String(e) }),
      )
    run()
    const ch = sb && table
      ? sb.channel(`${table}:${key}`)
          .on('postgres_changes', { event: '*', schema: 'public', table, filter }, run)
          .subscribe()
      : null
    return () => {
      alive = false
      if (ch && sb) sb.removeChannel(ch)
    }
    // load is recreated each render; key identifies the query
  }, [key])
  return state.key === key ? state : { rows: null, error: null }
}

export function useAnalyses(pair: string, mode: Mode) {
  return useRows<Analysis>(
    `${pair}:${mode}:${FIXTURE ? 'fixture' : 'db'}`,
    sb
      ? () => q(sb.from('analyses').select('*').eq('pair', pair).eq('mode', mode).order('created_at', { ascending: false }).limit(20))
      : async () => {
          const p = (await import('../../../supabase/payload.example.json')).default as unknown as Payload
          return [{ pair, mode, created_at: p.updatedAt, status: p.status, keyakinan: p.keyakinan ?? null, price: p.price ?? null, payload: p }]
        },
    'analyses',
    `pair=eq.${pair}`,
  )
}

// 00:00 WIB hari ini dalam ISO UTC.
const startOfTodayWib = () => {
  const d = 86400e3, wib = 7 * 3600e3
  return new Date(Math.floor((Date.now() + wib) / d) * d - wib).toISOString()
}

export function useNewsOutlook(pair: string) {
  return useRows<NewsRow>(
    `news:${pair}`,
    sb && (() => q(sb.from('news_outlook').select('*').eq('pair', pair)
      .gte('event_time', startOfTodayWib())
      .order('event_time', { ascending: true }))),
    'news_outlook',
    `pair=eq.${pair}`,
  )
}

export function useBacktest(pair: string, mode: Mode) {
  return useRows<BacktestRow>(
    `bt:${pair}:${mode}`,
    sb && (async () => {
      const last = await q<BacktestRow>(sb.from('backtest_results').select('run_id').eq('pair', pair).eq('mode', mode).order('run_at', { ascending: false }).limit(1))
      if (!last.length) return []
      return q(sb.from('backtest_results').select('*').eq('run_id', last[0].run_id).order('strategy').order('regime').order('sample'))
    }),
    'backtest_results',
    `pair=eq.${pair}`,
  )
}

export const MACRO = ['DFII10', 'T10YIE', 'COT_GOLD_MM_NET'] as const

export function useMacro() {
  return useRows<MacroRow>(
    'macro',
    sb && (async () => {
      const per = await Promise.all(MACRO.map((s) =>
        q<MacroRow>(sb.from('macro_series').select('*').eq('series', s).order('date', { ascending: false }).limit(60))))
      return per.flatMap((r) => r.reverse())
    }),
  )
}
