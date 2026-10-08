import type { Setup } from './supabase'

type Bar = { time: number; open: number; high: number; low: number; close: number }
export type Nasib = {
  status: 'menunggu' | 'berjalan' | 'TP1' | 'SL' | 'invalid' | 'batal' | 'kedaluwarsa' | 'dibatalkan'
  alasan: string; t: number | null   // t = waktu kejadian (detik UTC)
}
export type Jejak = { s: Setup; t0: number; n: Nasib; terbaru: boolean }
export const SELESAI = new Set(['TP1', 'SL', 'invalid', 'batal', 'kedaluwarsa', 'dibatalkan'])
const BATAL_FRAC = 0.7
const UMUR = 24 * 3600

// Jalur harga sejak setup dibuat (t0, detik UTC) -> nasib setup. Diperlakukan sebagai limit di entry.
export function nasib(s: Setup, t0: number, bars: Bar[], now = Date.now() / 1000, off = 0): Nasib {
  if (s.hasil) return { status: /TP/.test(s.hasil) ? 'TP1' : /SL/.test(s.hasil) ? 'SL' : 'batal', alasan: s.hasil, t: null }
  const buy = s.side !== 'sell'
  const e = s.entry + off, sl = s.sl + off, tp = s.tp?.[0] != null ? s.tp[0] + off : null
  const batal = tp != null ? e + (tp - e) * BATAL_FRAC : null
  let isi: number | null = s.terisi ? t0 : null
  for (const b of bars) {
    if (b.time < t0 - 60) continue
    if (isi == null) {
      const tembusSL = buy ? b.close < sl : b.close > sl
      if (tembusSL) return { status: 'invalid', alasan: `zona jebol: close ${buy ? 'di bawah' : 'di atas'} SL sebelum entry sah`, t: b.time }
      if (buy ? b.low <= e : b.high >= e) isi = b.time
      else if (batal != null && (buy ? b.high >= batal : b.low <= batal)) return { status: 'batal', alasan: 'harga 70% ke TP1 tanpa entry', t: b.time }
      else if (b.time - t0 > UMUR) return { status: 'kedaluwarsa', alasan: 'lewat 24 jam tanpa entry', t: b.time }
      else continue
    }
    if (buy ? b.low <= sl : b.high >= sl) return { status: 'SL', alasan: 'kena stop loss', t: b.time }
    if (tp != null && (buy ? b.high >= tp : b.low <= tp)) return { status: 'TP1', alasan: 'kena target 1', t: b.time }
  }
  if (isi != null) return { status: 'berjalan', alasan: 'order terisi, posisi berjalan', t: isi }
  if (now - t0 > UMUR) return { status: 'kedaluwarsa', alasan: 'lewat 24 jam tanpa entry', t: null }
  return { status: 'menunggu', alasan: '', t: null }
}

// Kunci dedupe setup, sama dengan kunci pembatalan di jembatan MT5.
export const kunci = (s: Setup) => `${s.side}:${Math.round(s.entry * 10)}`
