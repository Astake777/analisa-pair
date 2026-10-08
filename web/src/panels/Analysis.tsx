import { useState, type ReactNode } from 'react'
import { FIXTURE, type Analysis, type BacktestRow, type Payload } from '../lib/supabase'
import { dirClass, fmt, signed, store, waktuPendek, wibTime } from '../lib/format'
import type { Jejak } from '../lib/nasib'
import { Riwayat } from './Setups'

export function PrediksiNews({ items }: { items: NonNullable<Payload['prediksiNews']> }) {
  if (!items.length) return <p className="sub">Tidak ada event penting dalam jendela analisis.</p>
  return (
    <>
      {items.map((n, i) => (
        <div className="news-item" key={i}>
          <span className="news-when">{n.waktuWIB}</span>
          <span className="news-title">{n.event}</span>
          <span className={`lean ${dirClass(n.arah)}`}>{n.arah}{n.kekuatan ? ` · ${n.kekuatan}` : ''}</span>
          {n.alasan && <p>{n.alasan}</p>}
          {n.batal && <p className="sub">Batal: {n.batal}</p>}
        </div>
      ))}
    </>
  )
}

const BIAS_ORDER = ['1W', '1D', '4H', '1H', '30m', '15m', '5m']

export function Bias({ a }: { a: Payload }) {
  const b = a.bias ?? {}
  const keys = BIAS_ORDER.filter((k) => b[k])
  return (
    <section className="card" aria-labelledby="biasTitle">
      <div className="card-head"><h2 id="biasTitle">Bias per timeframe</h2></div>
      <div className="tbl-wrap">
        <table>
          <thead><tr><th>TF</th><th>Bias</th><th className="r">RSI</th></tr></thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td>{k}</td>
                <td className={dirClass(/bear|turun|sell|melemah/i.test(b[k].bias) ? 'SELL' : /bull|naik|buy|menguat/i.test(b[k].bias) ? 'BUY' : '')}>
                  {b[k].bias}{b[k].catatan && <div className="sub">{b[k].catatan}</div>}
                </td>
                <td className="r num">{b[k].rsi != null ? fmt(b[k].rsi, 1) : '–'}</td>
              </tr>
            ))}
            {!keys.length && <tr><td colSpan={3} className="sub">Bias belum tersedia.</td></tr>}
          </tbody>
        </table>
      </div>
      {a.makroKonfirmasi && <p className="sub" style={{ margin: '12px 0 0' }}>{a.makroKonfirmasi}</p>}
    </section>
  )
}

export function Levels({ a, off, price }: { a: Payload; off: number; price: number | null }) {
  const lv = [
    ...(a.levels ?? []).filter((l) => l.kind !== 'sekarang'),
    ...(price != null ? [{ price, label: 'Harga sekarang', kind: 'sekarang' }] : []),
  ].sort((x, y) => y.price - x.price)
  const cls = (k: string) => (k === 'sekarang' ? 'now' : k === 'zona-sell' || k === 'pemicu-sell' ? 'zone-sell' : k === 'zona-buy' || k === 'pemicu-buy' ? 'zone-buy' : '')
  return (
    <section className="card" aria-labelledby="lvlTitle">
      <div className="card-head"><h2 id="lvlTitle">Level kunci</h2></div>
      <ul className="levels">
        {lv.map((l, i) => (
          <li key={i} className={cls(l.kind)}><span className="num">{fmt(l.price + off)}</span><span>{l.label}</span></li>
        ))}
      </ul>
    </section>
  )
}

export function Notes({ notes }: { notes: string[] }) {
  return (
    <section className="card" aria-labelledby="noteTitle">
      <div className="card-head"><h2 id="noteTitle">Catatan</h2></div>
      <ul className="list">
        {notes.map((n, i) => <li key={i}>{n}</li>)}
        {!notes.length && <li className="sub">Tidak ada catatan.</li>}
      </ul>
    </section>
  )
}

export function Impact({ v }: { v?: string | null }) {
  if (!v) return null
  return <span className={`imp ${v === 'High' ? 'high' : 'med'}`}>{v === 'High' ? 'High' : 'Medium'}</span>
}

export function Calendar({ events }: { events: NonNullable<Payload['events']> }) {
  return (
    <section className="card" aria-labelledby="calTitle">
      <div className="card-head"><h2 id="calTitle">Kalender USD</h2><span className="sub">High dan medium impact saja. Waktu WIB, A = actual, F = forecast, P = previous</span></div>
      <div className="tbl-wrap">
        <table>
          <thead><tr><th>Waktu</th><th>Impact</th><th>Event</th><th className="r">A</th><th className="r">F</th><th className="r">P</th><th>Arah</th></tr></thead>
          <tbody>
            {events.flatMap((g, gi) => g.items.map((it, i) => (
              <tr key={`${gi}-${i}`}>
                <td className="num nw">{i === 0 ? g.waktuWIB : ''}</td>
                <td><Impact v={it.impact} /></td>
                <td>{it.title}</td>
                <td className="r num">{it.actual ?? '–'}</td>
                <td className="r num">{it.forecast ?? '–'}</td>
                <td className="r num">{it.previous ?? '–'}</td>
                <td>
                  {i === 0 && (g.jenis === 'HASIL'
                    ? <span className={dirClass(g.arah)}>{g.arah} · {g.kekuatan}</span>
                    : g.jenis ? <span className="sub">{g.jenis}</span> : null)}
                </td>
              </tr>
            )))}
            {!events.length && <tr><td colSpan={7} className="sub">Tidak ada event USD high/medium dalam 7 hari.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function Headlines({ items }: { items: NonNullable<Payload['headlines']> }) {
  return (
    <section className="card" aria-labelledby="hlTitle">
      <div className="card-head"><h2 id="hlTitle">Berita</h2></div>
      <ul className="hl">
        {items.map((h, i) => (
          <li key={i}>
            <a href={h.url} target="_blank" rel="noopener noreferrer">{h.title}</a>
            <span className="meta">{(h.published ?? '').slice(0, 10)}{h.bacaan ? ` · dibaca: ${h.bacaan}` : ''}</span>
          </li>
        ))}
        {!items.length && <li className="sub">Tidak ada berita.</li>}
      </ul>
    </section>
  )
}

const pct = (v: number | null) => (v == null ? '–' : `${fmt(Math.abs(v) <= 1 ? v * 100 : v, 1)}%`)

export function Strategi({ s, rows, error }: { s: Payload['strategi']; rows: BacktestRow[] | null; error: string | null }) {
  const r = s?.regime
  return (
    <section className="card" aria-labelledby="stratTitle">
      <div className="card-head">
        <h2 id="stratTitle">Strategi</h2>
        {rows?.[0] && <span className="sub">Backtest {wibTime(rows[0].run_at)}</span>}
      </div>
      {r && (
        <div className="chips">
          {r.tren && <span>Tren <b>{r.tren}</b></span>}
          {r.volatilitas && <span>Volatilitas <b>{r.volatilitas}</b></span>}
          {r.sesi && <span>Sesi <b>{r.sesi}</b></span>}
          {r.jendelaNews != null && <span>Jendela news <b>{r.jendelaNews ? 'ya' : 'tidak'}</b></span>}
        </div>
      )}
      {s?.terpilih && <p className="pick">Dipakai: <b>{s.terpilih}</b>{s.alasan ? `. ${s.alasan}` : ''}</p>}
      {error ? <p className="sub">Hasil backtest tidak bisa dimuat ({error}).</p>
        : rows == null ? <p className="sub">{FIXTURE ? 'Hasil backtest muncul setelah Supabase tersambung.' : 'Memuat hasil backtest'}</p>
        : !rows.length ? <p className="sub">Belum ada hasil backtest untuk pair dan mode ini.</p>
        : (
          <div className="tbl-wrap">
            <table>
              <thead><tr>
                <th>Strategi</th><th>Regime</th><th>Sampel</th><th className="r">Trades</th><th className="r">Winrate</th>
                <th className="r">Expectancy</th><th className="r">PF</th><th className="r">Max DD</th>
              </tr></thead>
              <tbody>
                {rows.map((b, i) => (
                  <tr key={i}>
                    <td>{b.strategy === s?.terpilih ? <b>{b.strategy}</b> : b.strategy}</td>
                    <td>{b.regime}</td>
                    <td>{b.sample === 'oos' ? 'OOS' : 'In'}</td>
                    <td className="r num">{b.trades}</td>
                    <td className="r num">{pct(b.winrate)}</td>
                    <td className="r num">{b.expectancy == null ? '–' : `${signed(Number(b.expectancy), 2)}R`}</td>
                    <td className="r num">{fmt(b.profit_factor, 2)}</td>
                    <td className="r num">{b.max_dd_r == null ? '–' : `${fmt(b.max_dd_r, 1)}R`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </section>
  )
}

const FASE: [string, string][] = [['A', 'Akumulasi'], ['M', 'Manipulasi'], ['D', 'Distribusi']]

// Kartu biasa, atau isi saja (tanpa kartu) saat ditaruh di dalam tab.
export function Bungkus({ bare, judul, id, sisi, children }: { bare: boolean; judul: string; id: string; sisi?: ReactNode; children: ReactNode }) {
  if (bare) return <div className="tab-isi">{sisi && <div className="tab-sisi">{sisi}</div>}{children}</div>
  return (
    <section className="card" aria-labelledby={id}>
      <div className="card-head"><h2 id={id}>{judul}</h2>{sisi}</div>
      {children}
    </section>
  )
}

export function Amd({ amd, off, bare = false }: { amd: NonNullable<Payload['amd']>; off: number; bare?: boolean }) {
  const trend = /trend/i.test(amd.fase ?? '')
  const cur = (amd.fase ?? '').trim().charAt(0).toUpperCase()
  const r = amd.rangeAsia, sw = amd.sweep
  return (
    <Bungkus bare={bare} judul="Fase AMD" id="amdTitle" sisi={amd.tanggal && <span className="sub num">{amd.tanggal}</span>}>
      {trend
        ? <p className="amd-trend"><span className="tag ctx">Trend day</span> <span className="sub">Tidak ada pola akumulasi, manipulasi, distribusi hari ini.</span></p>
        : (
          <div className="amd-steps" role="list" aria-label="Fase hari ini">
            {FASE.map(([k, name]) => (
              <span key={k} role="listitem" aria-current={k === cur ? 'step' : undefined}>{name}</span>
            ))}
          </div>
        )}
      <dl className="kv">
        <dt>Range Asia</dt>
        <dd className="num wide">{r ? `${fmt(r.lo + off)} – ${fmt(r.hi + off)} (${fmt(r.hi - r.lo)})` : '–'}</dd>
        <dt>Sweep</dt>
        <dd className="num wide">
          {sw?.harga != null ? `Sisi ${sw.sisi ?? '?'} di ${fmt(sw.harga + off)}${sw.waktu ? `, ${wibTime(sw.waktu, false).slice(0, 5)} WIB` : ''}` : 'Belum ada'}
        </dd>
      </dl>
      {amd.catatan && <p className="sub" style={{ margin: '10px 0 0' }}>{amd.catatan}</p>}
    </Bungkus>
  )
}

const PILIH_RIWAYAT = [['setup', 'Setup'], ['analisis', 'Analisis']] as const

// Satu kartu riwayat dengan switch: setup yang sudah selesai/gugur, atau analisis yang pernah dipublikasi.
export function RiwayatKartu({ setup, analisis, off }: { setup: Jejak[]; analisis: Analysis[] | null; off: number }) {
  const [v, setV] = useState(() => (store.get('riwayat') === 'analisis' ? 'analisis' : 'setup'))
  const pilih = (
    <div className="seg" role="group" aria-label="Jenis riwayat">
      {PILIH_RIWAYAT.map(([k, t]) => (
        <button key={k} type="button" aria-pressed={v === k} onClick={() => { setV(k); store.set('riwayat', k) }}>{t}</button>
      ))}
    </div>
  )
  return (
    <Bungkus bare={false} judul="Riwayat" id="riwTitle" sisi={pilih}>
      {v === 'setup' ? <Riwayat rows={setup} off={off} /> : <DaftarAnalisis rows={analisis} off={off} />}
    </Bungkus>
  )
}

function DaftarAnalisis({ rows, off }: { rows: Analysis[] | null; off: number }) {
  if (rows == null) return <p className="sub">Memuat riwayat analisis.</p>
  if (!rows.length) return <p className="sub">Belum ada riwayat untuk pair dan mode ini.</p>
  return (
    <ul className="log log2">
      {rows.slice(0, 15).map((h, i) => {
        const s = h.payload.setups?.[0]
        return (
          <li key={h.id ?? i}>
            <span className={s?.side === 'sell' ? 'down' : s?.side === 'buy' ? 'up' : 'sub'}>{s ? s.side.toUpperCase() : '–'}</span>
            <span className="num">{s ? fmt(s.entry + off, 2) : '–'}</span>
            <span className="sub">{h.status}</span>
            <span className="sub ket">{waktuPendek(h.created_at)} WIB{s ? ` · SL ${fmt(s.sl + off)} · TP ${s.tp?.map((t) => fmt(t + off)).join(' / ') ?? '–'}` : ''} · keyakinan {h.keyakinan ?? '–'}</span>
          </li>
        )
      })}
    </ul>
  )
}
