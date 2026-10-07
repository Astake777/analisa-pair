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

// Data Yahoo bisa tertunda beberapa menit; cukup untuk arah yield/dollar, bukan untuk tick.
export function useLiveDrivers(drivers: Driver[], ms = 20000) {
  const [live, setLive] = useState<Record<string, Driver>>({})
  const syms = drivers.map((d) => d.sym).join(',')
  useEffect(() => {
    let alive = true
    const run = () => drivers.forEach(async (d) => {
      try {
        const r = await fetch(`/yahoo/v8/finance/chart/${encodeURIComponent(d.sym)}?interval=5m&range=2d`)
        const nd = r.ok ? toDriver(d, (await r.json()) as Chart) : null
        if (alive && nd) setLive((p) => ({ ...p, [d.sym]: nd }))
      } catch { /* tetap pakai nilai terakhir */ }
    })
    run()
    const id = setInterval(run, ms)
    return () => { alive = false; clearInterval(id) }
    // syms mewakili daftar driver
  }, [syms, ms])
  const out = drivers.map((d) => live[d.sym] ?? d)
  return { drivers: out, live: drivers.length > 0 && drivers.every((d) => live[d.sym]) }
}
