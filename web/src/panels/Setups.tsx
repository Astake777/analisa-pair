import type { Setup } from '../lib/supabase'
import { fmt } from '../lib/format'

// Near = live price within 0.5 x (zone height + $6) of the zone midpoint, i.e. inside or within $3 of an edge.
const NEAR_PAD = 6
export const nearZone = (price: number, [a, b]: [number, number]) =>
  Math.abs(price - (a + b) / 2) <= 0.5 * (Math.abs(b - a) + NEAR_PAD)

// XAUUSD: 1 pip = $0.10
const pips = (d: number) => Math.round(Math.abs(d) * 10)
const SUMBER: Record<string, string> = { sniper: 'Sniper 1m', utama: 'Strategi utama', 'kontra-tren': 'Kontra-tren' }

// Posisi harga live terhadap zona, dalam kalimat biasa.
function keadaan(s: Setup, price: number | null, off: number) {
  if (price == null) return { text: 'Menunggu harga live.', kind: 'wait' }
  const [lo, hi] = (s.zone ?? [s.entry, s.entry]).map((v) => v + off)
  const sl = s.sl + off
  const sell = s.side === 'sell'
  if (sell ? price >= sl : price <= sl) return { text: 'Batal: harga sudah melewati stop loss.', kind: 'off' }
  if (price >= lo && price <= hi) return { text: 'Harga di zona. Order limit bisa terisi sekarang.', kind: 'go' }
  const jarak = sell ? lo - price : price - hi
  if (jarak > 0) return { text: `Belum aktif. Harga perlu ${sell ? 'naik' : 'turun'} $${fmt(jarak, 2)} (${pips(jarak)} pips) ke zona.`, kind: 'wait' }
  return { text: 'Harga sudah lewat zona, belum kena stop loss. Jangan kejar.', kind: 'wait' }
}

type Props = { setups: Setup[]; idx: number; onPick: (i: number) => void; off: number; price: number | null }

export function Setups({ setups, idx, onPick, off, price }: Props) {
  if (!setups.length) return <p className="sub">Belum ada setup. Sistem mengabari begitu zona yang memenuhi syarat muncul.</p>
  return (
    <>
      {setups.map((s, i) => {
        const shown = i === idx
        const sell = s.side === 'sell'
        const k = keadaan(s, price, off)
        return (
          <article key={i} className={`setup${shown ? ' on' : ''}${k.kind === 'go' ? ' near' : ''}`}>
            <div className="setup-top">
              <span className={`side ${sell ? 'sell' : 'buy'}`}>{sell ? 'SELL' : 'BUY'} limit</span>
              {s.label && <span className="setup-src">{SUMBER[s.label] ?? s.label}</span>}
            </div>
            <p className={`setup-state ${k.kind}`} role="status">{k.text}</p>
            <dl className="kv">
              <dt>Entry</dt><dd className="num wide strong">{fmt(s.entry + off, 2)}</dd>
              {s.zone && (<><dt>Zona entry</dt><dd className="num wide nw">{fmt(s.zone[0] + off, 2)} – {fmt(s.zone[1] + off, 2)} <span className="rr">· {pips(s.zone[1] - s.zone[0])} pips</span></dd></>)}
              {s.alasan?.entry && <dd className="why">{s.alasan.entry}</dd>}
              <dt>Stop loss</dt><dd className="num down">{fmt(s.sl + off, 2)}</dd><dd className="rr num">−{pips(s.entry - s.sl)} pips</dd>
              {s.alasan?.sl && <dd className="why">{s.alasan.sl}</dd>}
              {(s.tp ?? []).map((tp, j) => (
                <Tp key={j} n={j + 1} price={tp + off} rr={s.rr?.[j]} pip={pips(tp - s.entry)} />
              ))}
              {s.alasan?.tp && <dd className="why">{s.alasan.tp}</dd>}
            </dl>
            {s.langkah?.length ? (
              <ol className="steps">{s.langkah.map((x, j) => <li key={j}>{x}</li>)}</ol>
            ) : s.trigger ? <p><span>Syarat masuk:</span> {s.trigger}</p> : null}
            {s.batal && <p><span>Batal kalau:</span> {s.batal}</p>}
            {s.eksperimen && <p className="exp-tag"><b>Uji coba.</b> {s.eksperimen}</p>}
            {setups.length > 1 && (
              <button type="button" className="show-btn" aria-pressed={shown} onClick={() => onPick(i)}>
                {shown ? 'Sedang tampil di chart' : 'Tampilkan di chart'}
              </button>
            )}
          </article>
        )
      })}
    </>
  )
}

function Tp({ n, price, rr, pip }: { n: number; price: number; rr?: number; pip: number }) {
  return (
    <>
      <dt>Target {n}</dt><dd className="num up">{fmt(price, 2)}</dd><dd className="rr num">+{pip} pips{rr != null ? ` · ${rr}R` : ''}</dd>
    </>
  )
}
