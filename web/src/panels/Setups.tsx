import type { Setup } from '../lib/supabase'
import { fmt } from '../lib/format'

// Near = live price within 0.5 x (zone height + $6) of the zone midpoint, i.e. inside or within $3 of an edge.
const NEAR_PAD = 6
export const nearZone = (price: number, [a, b]: [number, number]) =>
  Math.abs(price - (a + b) / 2) <= 0.5 * (Math.abs(b - a) + NEAR_PAD)

type Props = { setups: Setup[]; idx: number; onPick: (i: number) => void; off: number; price: number | null }

export function Setups({ setups, idx, onPick, off, price }: Props) {
  if (!setups.length) return <p className="sub">Belum ada setup.</p>
  return (
    <>
      {setups.map((s, i) => {
        const shown = i === idx
        const near = shown && price != null && !!s.zone && nearZone(price, s.zone)
        return (
          <article key={i} className={`setup${shown ? ' on' : ''}${near ? ' near' : ''}`}>
            <div className="setup-top">
              <span className={`side ${s.side === 'sell' ? 'sell' : 'buy'}`}>
                {s.side === 'sell' ? 'SELL' : 'BUY'}{s.label ? ` · ${s.label}` : ''}
              </span>
              <span className="st">{s.status}</span>
            </div>
            {s.eksperimen && <p className="exp-tag"><b>Eksperimen</b> {s.eksperimen}</p>}
            {near && <span className="near-tag" role="status">Harga mendekati zona</span>}
            <dl className="kv">
              {s.zone && (<><dt>Zona</dt><dd className="num wide">{fmt(s.zone[0] + off)} – {fmt(s.zone[1] + off)}</dd></>)}
              <dt>Entry</dt><dd className="num wide">{fmt(s.entry + off)}</dd>
              <dt>SL</dt><dd className="num">{fmt(s.sl + off)}</dd><dd className="rr num">{pips(Math.abs(s.entry - s.sl))} pips</dd>
              {(s.tp ?? []).map((tp, j) => (
                <Tp key={j} n={j + 1} price={tp + off} rr={s.rr?.[j]} pip={pips(Math.abs(tp - s.entry))} />
              ))}
            </dl>
            {s.trigger && <p><span>Trigger:</span> {s.trigger}</p>}
            {s.batal && <p><span>Batal:</span> {s.batal}</p>}
            <button type="button" className="show-btn" aria-pressed={shown} onClick={() => onPick(i)}>
              {shown ? 'Tampil di chart' : 'Tampilkan di chart'}
            </button>
          </article>
        )
      })}
    </>
  )
}

// XAUUSD: 1 pip = $0.10
const pips = (d: number) => Math.round(d * 10)

function Tp({ n, price, rr, pip }: { n: number; price: number; rr?: number; pip: number }) {
  return (
    <>
      <dt>TP{n}</dt><dd className="num">{fmt(price)}</dd><dd className="rr num">{pip} pips{rr != null ? ` · ${rr}R` : ''}</dd>
    </>
  )
}
