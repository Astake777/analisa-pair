import { useEffect, useRef, useState } from 'react'
import {
  createChart, CrosshairMode, LineStyle,
  type AutoscaleInfo, type CandlestickData, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp,
} from 'lightweight-charts'
import type { Bar } from '../feed'
import type { Setup } from '../lib/supabase'
import { fmt, WIB } from '../lib/format'
import { ema } from './ema'
import { css, ZoneBands, type Band } from './zones'

type Props = {
  bars: Bar[]
  shift: number
  setup: Setup | null
  zones: Band[]
  levels: { price: number; label: string; kind: string }[]
  emptyText: string
}
type Api = {
  chart: IChartApi; candles: ISeriesApi<'Candlestick'>
  e20: ISeriesApi<'Line'>; e50: ISeriesApi<'Line'>; e200: ISeriesApi<'Line'>; prim: ZoneBands
}
type Pt = { time: UTCTimestamp; open: number; high: number; low: number; close: number }

const theme = () => ({
  layout: { background: { color: css('--card') }, textColor: css('--muted'), fontFamily: css('--font-num'), fontSize: 11 },
  grid: { vertLines: { color: css('--line') }, horzLines: { color: css('--line') } },
  rightPriceScale: { borderColor: css('--line') },
  timeScale: { borderColor: css('--line'), timeVisible: true, secondsVisible: false },
  crosshair: { mode: CrosshairMode.Normal },
})

type Level = Props['levels'][number]

// Chart hanya memuat pemicu news dan 2 support/resistance terdekat di tiap sisi; daftar lengkap ada di panel Level.
// EMA sudah tampil sebagai garis, jadi level EMA dilewati.
export function keyLevels(levels: Level[], ref: number | undefined, setup: Setup | null): Level[] {
  const pemicu = levels.filter((l) => l.kind.startsWith('pemicu'))
  const taken = [...pemicu.map((l) => l.price), ...(setup ? [setup.entry, setup.sl, ...(setup.tp ?? [])] : [])]
  const sr = levels.filter((l) => (l.kind === 'support' || l.kind === 'resistance') && !/^EMA/i.test(l.label)
    && !taken.some((t) => Math.abs(t - l.price) < 1.5))
  if (ref == null) return pemicu
  const near = (xs: Level[]) => xs.sort((x, y) => Math.abs(x.price - ref) - Math.abs(y.price - ref)).slice(0, 2)
  return [...pemicu, ...near(sr.filter((l) => l.price > ref)), ...near(sr.filter((l) => l.price <= ref))]
}

export default function Chart({ bars, shift, setup, zones, levels, emptyText }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const api = useRef<Api | null>(null)
  const lines = useRef<IPriceLine[]>([])
  const extras = useRef<number[]>([])
  const prev = useRef<{ first?: Bar; len: number; shift: number }>({ len: 0, shift: 0 })
  const [hover, setHover] = useState<Pt | null>(null)
  const [themeRev, setThemeRev] = useState(0)
  const [cd, setCd] = useState<{ top: number; width: number; text: string; up: boolean } | null>(null)
  const live = useRef({ bars, shift })
  live.current = { bars, shift }

  useEffect(() => {
    const chart = createChart(box.current!, { autoSize: true, ...theme() })
    const candles = chart.addCandlestickSeries({
      priceLineVisible: true,
      priceLineStyle: LineStyle.Dotted,
      priceLineWidth: 1,
      autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => {
        const r = orig()
        const ex = extras.current
        if (!r?.priceRange || !ex.length) return r
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...ex), maxValue: Math.max(r.priceRange.maxValue, ...ex) } }
      },
    })
    const line = (style: LineStyle) =>
      chart.addLineSeries({ lineWidth: 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const a: Api = { chart, candles, e20: line(LineStyle.Solid), e50: line(LineStyle.Solid), e200: line(LineStyle.Dashed), prim: new ZoneBands() }
    candles.attachPrimitive(a.prim)
    chart.subscribeCrosshairMove((p) => {
      const d = p.seriesData.get(candles) as CandlestickData | undefined
      setHover(d && 'open' in d ? (d as Pt) : null)
    })
    const recolor = () => {
      chart.applyOptions(theme())
      const up = css('--up'), down = css('--down')
      candles.applyOptions({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false })
      a.e20.applyOptions({ color: css('--ema20') })
      a.e50.applyOptions({ color: css('--ema50') })
      a.e200.applyOptions({ color: css('--ema200') })
      setThemeRev((r) => r + 1)
    }
    recolor()
    api.current = a
    const later = () => requestAnimationFrame(recolor)
    const mq = matchMedia('(prefers-color-scheme: light)')
    mq.addEventListener('change', later)
    const mo = new MutationObserver(later)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      mq.removeEventListener('change', later)
      mo.disconnect()
      chart.remove()
      api.current = null
      lines.current = []
      prev.current = { len: 0, shift: 0 }
    }
  }, [])

  // Full setData only when history, basis or offset changed; live ticks go through series.update.
  useEffect(() => {
    const a = api.current
    if (!a) return
    const p = prev.current
    const n = bars.length
    const data: Pt[] = bars.map((b) => ({
      time: (b.time + WIB) as UTCTimestamp, open: b.open + shift, high: b.high + shift, low: b.low + shift, close: b.close + shift,
    }))
    const full = !n || bars[0] !== p.first || shift !== p.shift || n < p.len || n > p.len + 1
    const emas: [ISeriesApi<'Line'>, number][] = [[a.e20, 20], [a.e50, 50], [a.e200, 200]]
    if (full) {
      a.candles.setData(data)
      for (const [s, k] of emas) s.setData(ema(data, k) as { time: UTCTimestamp; value: number }[])
      if (n && bars[0].time !== p.first?.time) a.chart.timeScale().setVisibleLogicalRange({ from: n - 150, to: n + 4 })
    } else {
      if (n > p.len) a.candles.update(data[n - 2])
      a.candles.update(data[n - 1])
      for (const [s, k] of emas) {
        const pts = ema(data, k) as { time: UTCTimestamp; value: number }[]
        if (n > p.len && pts.length > 1) s.update(pts[pts.length - 2])
        if (pts.length) s.update(pts[pts.length - 1])
      }
    }
    prev.current = { first: bars[0], len: n, shift }
  }, [bars, shift])

  useEffect(() => {
    api.current?.prim.setZones(zones.map((z) => ({ ...z, lo: z.lo + shift, hi: z.hi + shift })))
  }, [zones, shift])

  useEffect(() => {
    const a = api.current
    if (!a) return
    lines.current.forEach((l) => a.candles.removePriceLine(l))
    lines.current = []
    const add = (price: number | undefined, color: string, title: string, style: LineStyle) => {
      if (price == null) return
      lines.current.push(a.candles.createPriceLine({ price: price + shift, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }))
    }
    for (const lv of keyLevels(levels, live.current.bars.at(-1)?.close, setup)) {
      if (lv.kind === 'pemicu-buy') add(lv.price, css('--up'), 'BUY jika tembus', LineStyle.Dashed)
      else if (lv.kind === 'pemicu-sell') add(lv.price, css('--down'), 'SELL jika tembus', LineStyle.Dashed)
      else add(lv.price, css('--muted'), lv.label.split(' (')[0], LineStyle.Dotted)
    }
    if (setup) {
      add(setup.entry, css('--accent'), 'Entry', LineStyle.Solid)
      add(setup.sl, css('--down'), 'SL', LineStyle.Dashed)
      setup.tp?.forEach((tp, i) => add(tp, css('--up'), `TP${i + 1}`, LineStyle.Dashed))
    }
    extras.current = setup ? [setup.entry, setup.sl, ...(setup.tp ?? [])].filter((v) => v != null).map((v) => v + shift) : []
    a.chart.priceScale('right').applyOptions({ autoScale: true })
  }, [setup, levels, shift, themeRev, bars.length > 0])

  // Hitung mundur penutupan candle, diletakkan di bawah label harga terakhir pada sumbu kanan.
  useEffect(() => {
    const tick = () => {
      const a = api.current
      const { bars: bs, shift: sh } = live.current
      const last = bs.at(-1)
      if (!a || !last || bs.length < 2) return setCd(null)
      const tail = bs.slice(-6)
      const step = Math.min(...tail.slice(1).map((x, i) => x.time - tail[i].time))
      const left = last.time + step - Math.floor(Date.now() / 1000)
      const y = a.candles.priceToCoordinate(last.close + sh)
      if (left <= 0 || left > step || y == null) return setCd(null)
      const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), sec = left % 60
      const text = (h ? `${h}:${String(m).padStart(2, '0')}` : String(m).padStart(2, '0')) + ':' + String(sec).padStart(2, '0')
      setCd({ top: y + 9, width: a.chart.priceScale('right').width(), text, up: last.close >= last.open })
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const lastBar = bars.at(-1)
  const b = hover ?? (lastBar && {
    time: (lastBar.time + WIB) as UTCTimestamp, open: lastBar.open + shift, high: lastBar.high + shift, low: lastBar.low + shift, close: lastBar.close + shift,
  })

  return (
    <>
      <div className="readout num">
        {b && (
          <>
            <span>{new Date(b.time * 1000).toISOString().slice(5, 16).replace('T', ' ')} WIB</span>
            <span>O <b>{fmt(b.open)}</b></span>
            <span>H <b>{fmt(b.high)}</b></span>
            <span>L <b>{fmt(b.low)}</b></span>
            <span>C <b>{fmt(b.close)}</b></span>
          </>
        )}
      </div>
      <div className="chart" ref={box} role="img" aria-label="Chart candlestick dengan EMA, zona entry, range Asia, entry, stop loss dan target">
        {cd && (
          <div className={`countdown num ${cd.up ? 'up-bg' : 'down-bg'}`} style={{ top: cd.top, width: cd.width }} aria-label={`Candle tutup dalam ${cd.text}`}>
            {cd.text}
          </div>
        )}
        {!bars.length && (
          <div className="chart-empty" aria-live="polite">
            <div className="spin" aria-hidden="true" />
            <span>{emptyText}</span>
          </div>
        )}
      </div>
    </>
  )
}
