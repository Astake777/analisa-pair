import { useEffect, useState } from 'react'
import { fmt, store } from '../lib/format'

type Strat = {
  nama: string; status: string; sumber: string; earnings: number; totalReturn: number; annualReturn: number | null
  maxDrawdown: number; sharpe: number | null; sortino: number | null; calmar: number | null; winRate: number | null
  expectancy: number; expectancyR: number | null; posisi: number; kurva: [number, number][]; hari: number
}
type Data = { dibuat: string; asumsi: string; mataUang?: string; strategi: Strat[] }

const pct = (v: number | null, d = 2) => (v == null ? '-' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
const num = (v: number | null) => (v == null ? '-' : v.toFixed(2))
const tone = (v: number | null) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '')

function Kurva({ pts }: { pts: [number, number][] }) {
  if (pts.length < 2) return null
  const W = 320, H = 90, pad = 6
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
  const sx = (x: number) => ((x - x0) / (x1 - x0 || 1)) * W
  const sy = (y: number) => pad + (1 - (y - y0) / (y1 - y0 || 1)) * (H - 2 * pad)
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`).join(' ')
  const naik = ys[ys.length - 1] >= ys[0]
  return (
    <svg className="kurva" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`Kurva ekuitas dari ${fmt(ys[0], 0)} ke ${fmt(ys[ys.length - 1], 0)} dollar`}>
      <path d={`${d} L ${W} ${H} L 0 ${H} Z`} fill={naik ? 'var(--zone-buy)' : 'var(--zone-sell)'} />
      <path d={d} fill="none" stroke={naik ? 'var(--up)' : 'var(--down)'} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export function Kinerja() {
  const [data, setData] = useState<Data | null>(null)
  const [err, setErr] = useState(false)
  const [pilih, setPilih] = useState(() => Number(store.get('kinerja') ?? 0) || 0)

  useEffect(() => {
    fetch(`/kinerja.json?t=${Date.now()}`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setData, () => setErr(true))
  }, [])

  const s = data?.strategi[Math.min(pilih, (data?.strategi.length ?? 1) - 1)]
  return (
    <section className="card" aria-labelledby="kinTitle">
      <div className="card-head">
        <h2 id="kinTitle">Hasil backtest</h2>
        {data && data.strategi.length > 1 && (
          <div className="seg" role="group" aria-label="Pilih strategi">
            {data.strategi.map((x, i) => (
              <button key={x.nama} type="button" aria-pressed={x === s} onClick={() => { setPilih(i); store.set('kinerja', String(i)) }}>
                {x.nama}
              </button>
            ))}
          </div>
        )}
      </div>
      {err ? <p className="sub">Belum ada hasil. Jalankan <code>python .claude/skills/analisa-pair/kinerja.py</code> setelah backtest.</p>
        : !data ? <p className="sub">Memuat hasil backtest.</p>
        : !s ? <p className="sub">Belum ada trade out-of-sample.</p>
        : (
          <>
            <Kurva pts={s.kurva} />
            <dl className="kin">
              <div><dt>Earnings</dt><dd className={`num ${tone(s.earnings)}`}>{s.earnings >= 0 ? '+' : '-'}{data.mataUang && data.mataUang !== 'USD' ? `${fmt(Math.abs(s.earnings), 2)} ${data.mataUang}` : `$${fmt(Math.abs(s.earnings), 2)}`}</dd></div>
              <div><dt>Total return</dt><dd className={`num ${tone(s.totalReturn)}`}>{pct(s.totalReturn)}</dd></div>
              <div><dt>Annual return</dt><dd className={`num ${tone(s.annualReturn)}`}>{s.annualReturn == null ? `- (${s.hari} hari)` : pct(s.annualReturn)}</dd></div>
              <div><dt>Max drawdown</dt><dd className="num down">{pct(s.maxDrawdown)}</dd></div>
              <div><dt>Sharpe</dt><dd className={`num ${tone(s.sharpe)}`}>{num(s.sharpe)}</dd></div>
              <div><dt>Win rate</dt><dd className="num">{pct(s.winRate, 1).replace('+', '')}</dd></div>
              <div><dt>Sortino</dt><dd className={`num ${tone(s.sortino)}`}>{num(s.sortino)}</dd></div>
              <div><dt>Calmar</dt><dd className={`num ${tone(s.calmar)}`}>{num(s.calmar)}</dd></div>
              <div><dt>Expectancy</dt><dd className={`num ${tone(s.expectancy)}`}>{pct(s.expectancy)} ({s.expectancyR != null && s.expectancyR > 0 ? '+' : ''}{s.expectancyR}R)</dd></div>
              <div><dt>Posisi selesai</dt><dd className="num">{s.posisi}</dd></div>
            </dl>
            <p className="sub kin-note">{s.nama}, {s.status}. {s.sumber}, {s.hari} hari. {data.asumsi}</p>
          </>
        )}
    </section>
  )
}
