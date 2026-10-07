import { useEffect, useState } from 'react'
import type { Driver } from '../lib/supabase'

type Chart = { chart: { result: { meta: { regularMarketPrice?: number }; timestamp?: number[]; indicators: { quote: { close: (number | null)[] }[] } }[] } }

// Sama dengan snapshot.driver di Python: perubahan dari titik terakhir yang sudah >= 24 jam sebelumnya.
export function toDriver(base: Driver, j: Chart): Driver | null {
  const r = j.chart.result?.[0]
  if (!r?.timestamp) return null
  const series = r.timestamp.map((t, i) => [t, r.indicators.quote[0].close[i]] as [number, number | null])
    .filter((p): p is [number, number] => p[1] != null)
  if (series.length < 2) return null
  const [lastT, lastC] = series[series.length - 1]
  const last = r.meta.regularMarketPrice ?? lastC
  const prev = [...series].reverse().find(([t]) => t <= lastT - 86400)?.[1] ?? series[0][1]
  return { ...base, last, chg: last - prev, chgPct: ((last - prev) / prev) * 100, series: series.filter(([t]) => t > lastT - 86400) }
}

export type Aset = { sym: string; label: string; relasi: string }
// data: undefined = masih memuat, null = Yahoo gagal dan belum pernah ada data.
export type AsetLive = Aset & { data?: Driver | null }

// Data Yahoo bisa tertunda beberapa menit; cukup untuk arah yield/dollar, bukan untuk tick.
export function useLiveDrivers(list: Aset[], ms = 20000) {
  const [live, setLive] = useState<Record<string, Driver | null>>({})
  const syms = list.map((d) => d.sym).join(',')
  useEffect(() => {
    let alive = true
    const run = () => list.forEach(async (d) => {
      let nd: Driver | null = null
      try {
        const r = await fetch(`/yahoo/v8/finance/chart/${encodeURIComponent(d.sym)}?interval=5m&range=2d`)
        nd = r.ok ? toDriver({ ...d, last: 0, chg: 0, chgPct: 0 }, (await r.json()) as Chart) : null
      } catch { /* dianggap gagal di bawah */ }
      // Gagal sesudah pernah berhasil: tetap pakai nilai terakhir.
      if (alive) setLive((p) => (nd ? { ...p, [d.sym]: nd } : p[d.sym] ? p : { ...p, [d.sym]: null }))
    })
    run()
    const id = setInterval(run, ms)
    return () => { alive = false; clearInterval(id) }
    // syms mewakili daftar aset
  }, [syms, ms])
  return list.map((d): AsetLive => ({ ...d, data: live[d.sym] }))
}
