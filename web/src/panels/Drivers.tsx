import type { Driver, MacroRow, Side } from '../lib/supabase'
import { MACRO } from '../lib/supabase'
import { fmt, signed } from '../lib/format'

export function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1
  const W = 200, H = 44, pad = 4
  const pts = values.map((v, i) => [(i / (values.length - 1)) * W, pad + (1 - (v - min) / span) * (H - 2 * pad)])
  const path = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
  const [ex, ey] = pts[pts.length - 1]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" x2={W} y1={H / 2} y2={H / 2} stroke="var(--line)" strokeWidth="1" />
      <path d={`${path} L ${W} ${H} L 0 ${H} Z`} fill="var(--raised)" opacity="0.6" />
      <path d={path} fill="none" stroke="var(--muted)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <circle cx={ex} cy={ey} r="3" fill="var(--ink-strong)" />
    </svg>
  )
}

const arrow = (n: number) => (n > 0 ? '▲' : n < 0 ? '▼' : '')
const dc = (n: number) => (n > 0 ? 'up' : n < 0 ? 'down' : 'flat')

export function Drivers({ drivers, side }: { drivers: Driver[]; side?: Side }) {
  if (!drivers.length) return <p className="sub">Data driver belum tersedia.</p>
  return (
    <div className="drivers">
      {drivers.map((d) => {
        const isYield = d.sym === '^TNX'
        const val = isYield ? `${fmt(d.last, 3)}%` : fmt(d.last, 2)
        const chg = isYield ? `${signed(d.chg * 100, 1)} bp` : `${signed(d.chgPct, 2)}%`
        let tag = <span className="tag ctx">Konteks</span>
        if (d.relasi !== 'konteks' && side && d.chg !== 0) {
          const pairDown = d.relasi === 'terbalik' ? d.chg > 0 : d.chg < 0
          const pro = (side === 'sell') === pairDown
          tag = <span className={`tag ${pro ? 'pro' : 'con'}`}>{pro ? 'Mendukung' : 'Melawan'} {side.toUpperCase()}</span>
        }
        return (
          <div className="drv" key={d.sym}>
            <div className="drv-top">
              <span className="drv-name">{d.label}</span>
              <span className={`num ${dc(d.chg)}`}>{arrow(d.chg)} {chg}</span>
            </div>
            <span className="drv-val num">{val}</span>
            <Spark values={(d.series ?? []).map((p) => p[1])} />
            {tag}
          </div>
        )
      })}
    </div>
  )
}

const MACRO_META: Record<string, { label: string; pct: boolean }> = {
  DFII10: { label: 'Real yield 10Y', pct: true },
  T10YIE: { label: 'Breakeven 10Y', pct: true },
  COT_GOLD_MM_NET: { label: 'COT managed money net', pct: false },
}

export function Makro({ rows }: { rows: MacroRow[] }) {
  const series = MACRO.map((s) => ({ s, pts: rows.filter((r) => r.series === s) })).filter((x) => x.pts.length)
  if (!series.length) return null
  return (
    <section className="card span2" aria-labelledby="makroTitle">
      <div className="card-head"><h2 id="makroTitle">Makro</h2><span className="sub">Data harian dan mingguan, perubahan dari titik sebelumnya</span></div>
      <div className="drivers">
        {series.map(({ s, pts }) => {
          const m = MACRO_META[s]
          const last = Number(pts[pts.length - 1].value)
          const d = pts.length > 1 ? last - Number(pts[pts.length - 2].value) : 0
          return (
            <div className="drv" key={s}>
              <div className="drv-top">
                <span className="drv-name">{m.label}</span>
                <span className={`num ${dc(d)}`}>{arrow(d)} {m.pct ? `${signed(d * 100, 1)} bp` : `${signed(d, 0)} kontrak`}</span>
              </div>
              <span className="drv-val num">{m.pct ? `${fmt(last, 2)}%` : fmt(last, 0)}</span>
              <Spark values={pts.map((p) => Number(p.value))} />
              <span className="sub">per {pts[pts.length - 1].date}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
