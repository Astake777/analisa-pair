import type { LogRow, Setup } from '../lib/supabase'
import type { Jejak, Nasib } from '../lib/nasib'
import { fmt, wibTime } from '../lib/format'

// Near = live price within 0.5 x (zone height + $6) of the zone midpoint, i.e. inside or within $3 of an edge.
const NEAR_PAD = 6
export const nearZone = (price: number, [a, b]: [number, number]) =>
  Math.abs(price - (a + b) / 2) <= 0.5 * (Math.abs(b - a) + NEAR_PAD)

// XAUUSD: 1 pip = $0.10
const pips = (d: number) => Math.round(Math.abs(d) * 10)
const SUMBER: Record<string, string> = {
  sniper: 'Sniper 1m', utama: 'Strategi utama', 'kontra-tren': 'Kontra-tren',
  alchemist_crt: 'Alchemist CRT', alchemist_london: 'Alchemist London',
}
const BATAL_FRAC = 0.7

// Posisi harga live terhadap zona, dalam kalimat biasa.
function keadaan(s: Setup, price: number | null, off: number, n?: Nasib) {
  if (n?.status === 'berjalan') return { text: `Order terisi${n.t ? ` pukul ${wibTime(new Date(n.t * 1000).toISOString(), false)}` : ''}. Posisi berjalan, kelola dengan SL dan target di bawah.`, kind: 'go' }
  if (price == null) return { text: 'Menunggu harga live.', kind: 'wait' }
  const [lo, hi] = (s.zone ?? [s.entry, s.entry]).map((v) => v + off)
  const sl = s.sl + off
  const sell = s.side === 'sell'
  if (s.hasil) return { text: s.hasil, kind: 'off' }
  if (s.terisi) return { text: 'Order sudah terisi. Kelola posisi dengan SL dan target di bawah.', kind: 'go' }
  if (sell ? price >= sl : price <= sl) return { text: 'Batal: harga sudah melewati stop loss.', kind: 'off' }
  const tp1 = s.tp?.[0]
  // aturan sama dengan pantau.py/validasi.py: batal kalau sudah 70% jalan ke TP1 tanpa entry
  if (tp1 != null) {
    const batal = s.entry + off + (tp1 - s.entry) * BATAL_FRAC
    if (sell ? price <= batal : price >= batal) return { text: 'Batal: harga sudah dekat target tanpa sempat entry.', kind: 'off' }
  }
  if (price >= lo && price <= hi) return { text: 'Harga di zona. Order limit bisa terisi sekarang.', kind: 'go' }
  const jarak = sell ? lo - price : price - hi
  if (jarak > 0) return { text: `Belum aktif. Harga perlu ${sell ? 'naik' : 'turun'} $${fmt(jarak, 2)} (${pips(jarak)} pips) ke zona.`, kind: 'wait' }
  return { text: 'Harga sudah lewat zona, belum kena stop loss. Jangan kejar.', kind: 'wait' }
}

type Props = { aktif: Jejak[]; riwayat: Jejak[]; idx: number; onPick: (i: number) => void; off: number; price: number | null; log: LogRow[] | null }

// Ringkasan forward test nyata satu strategi dari setup_log.
function live(log: LogRow[] | null, strategi?: string) {
  const done = (log ?? []).filter((x) => x.strategi === strategi && x.terisi && (x.hasil === 'TP' || x.hasil === 'SL'))
  if (!done.length) return 'Live: belum ada setup yang selesai.'
  const tp = done.filter((x) => x.hasil === 'TP').length
  const r = done.reduce((a, x) => a + Number(x.r ?? 0), 0)
  return `Live: ${done.length} setup selesai, ${tp} kena TP, total ${r >= 0 ? '+' : ''}${r.toFixed(1)}R.`
}

export function Setups({ aktif, riwayat, idx, onPick, off, price, log }: Props) {
  const setups = aktif.map((j) => j.s)
  return (
    <>
      {!setups.length && (
        <p className="sub">{riwayat.length ? 'Belum ada setup aktif. Setup sebelumnya ada di riwayat di bawah.' : 'Belum ada setup. Sistem mengabari begitu zona yang memenuhi syarat muncul.'}</p>
      )}
      {setups.map((s, i) => {
        const shown = i === idx
        const sell = s.side === 'sell'
        const k = keadaan(s, price, off, aktif[i].n)
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
            {s.valid ? <p className="exp-tag ok"><b>Lulus validasi.</b> {s.catatanValidasi?.replace(/^Lulus validasi: /, '')}</p>
              : s.eksperimen && <p className="exp-tag"><b>Uji coba.</b> {s.eksperimen}</p>}
            {s.sinyalId && <p className="exp-tag">{live(log, s.label)}</p>}
            {setups.length > 1 && (
              <button type="button" className="show-btn" aria-pressed={shown} onClick={() => onPick(i)}>
                {shown ? 'Sedang tampil di chart' : 'Tampilkan di chart'}
              </button>
            )}
          </article>
        )
      })}
      {riwayat.length > 0 && <Riwayat rows={riwayat} off={off} />}
    </>
  )
}

const STATUS: Record<string, [string, string]> = {
  TP1: ['TP1', 'up'], SL: ['Kena SL', 'down'], invalid: ['Invalid', 'sub'], batal: ['Batal', 'sub'], kedaluwarsa: ['Kedaluwarsa', 'sub'],
}

// Setup yang sudah selesai atau gugur, terbaru di atas.
function Riwayat({ rows, off }: { rows: Jejak[]; off: number }) {
  return (
    <div className="riwayat">
      <h3>Riwayat setup</h3>
      <ul className="log">
        {rows.map((j) => {
          const [label, tone] = STATUS[j.n.status] ?? [j.n.status, 'sub']
          return (
            <li key={`${j.s.side}${j.s.entry}${j.t0}`} title={j.n.alasan}>
              <span className={j.s.side === 'sell' ? 'down' : 'up'}>{j.s.side.toUpperCase()}</span>
              <span className="num">{fmt(j.s.entry + off, 2)}</span>
              <span className="sub">{wibTime(new Date((j.n.t ?? j.t0) * 1000).toISOString())} · {SUMBER[j.s.label ?? ''] ?? j.s.label ?? 'Setup'}</span>
              <span className={`num ${tone}`}>{label}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function Tp({ n, price, rr, pip }: { n: number; price: number; rr?: number; pip: number }) {
  return (
    <>
      <dt>Target {n}</dt><dd className="num up">{fmt(price, 2)}</dd><dd className="rr num">+{pip} pips{rr != null ? ` · ${rr}R` : ''}</dd>
    </>
  )
}

const HASIL: Record<string, string> = { TP: 'TP', SL: 'SL' }

export function RekamJejak({ rows, error }: { rows: LogRow[] | null; error: string | null }) {
  return (
    <section className="card" aria-labelledby="logTitle">
      <div className="card-head"><h2 id="logTitle">Rekam jejak live</h2></div>
      {error ? (
        <p className="sub">Tabel rekam jejak belum siap ({error}). Jalankan <code>supabase/migrations/20261008000000_setup_log.sql</code> di SQL Editor Supabase.</p>
      ) : rows == null ? <p className="sub">Memuat rekam jejak.</p>
        : !rows.length ? <p className="sub">Belum ada setup yang dikabarkan watcher. Setiap setup baru tercatat di sini sampai kena TP, SL, atau batal.</p>
        : (
          <ul className="log">
            {rows.slice(0, 10).map((x) => (
              <li key={x.id}>
                <span className={x.side === 'sell' ? 'down' : 'up'}>{x.side.toUpperCase()}</span>
                <span className="num">{fmt(x.entry, 2)}</span>
                <span className="sub">{wibTime(x.dibuat)}</span>
                <span className={`num ${x.hasil === 'TP' ? 'up' : x.hasil === 'SL' ? 'down' : 'sub'}`}>
                  {x.hasil ? (HASIL[x.hasil] ?? 'Batal') : x.terisi ? 'Berjalan' : 'Menunggu'}{x.r != null && x.hasil && HASIL[x.hasil] ? ` ${x.r > 0 ? '+' : ''}${x.r}R` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}
