import { useState } from 'react'
import { FIXTURE, type NewsRow, type Skenario } from '../lib/supabase'
import { dirClass, fmt, wibTime } from '../lib/format'
import { Impact } from './Analysis'

const BULAN = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
const bulan = (iso: string) => {
  const d = new Date(Date.parse(iso) + 7 * 3600e3)
  return `${BULAN[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}
const num = (v: number | null) => (v == null ? '–' : fmt(Number(v), 2).replace(/\.?0+$/, ''))

function Hasil({ h }: { h: Record<string, unknown> }) {
  const arah = typeof h.arah === 'string' ? h.arah : typeof h.usd === 'string' ? h.usd : ''
  const kuat = typeof h.kekuatan === 'string' ? h.kekuatan : ''
  return <span className={dirClass(arah)}>Hasil: {arah || 'sudah rilis'}{kuat ? ` · ${kuat}` : ''}</span>
}

const NAMA: Record<string, string> = { panas: 'Panas', sesuai: 'Sesuai', dingin: 'Dingin' }
const pct = (p: number | null | undefined) => (p == null ? '–' : `${Math.round(p * 100)}%`)
const usd = (v: number | null | undefined) => (v == null ? '–' : `±$${fmt(v, 1)}`)
const nama = (k: Skenario) => NAMA[k.nama] ?? k.nama

function Detail({ r }: { r: NewsRow }) {
  const sk = r.skenario ?? [], p = r.pendukung ?? {}
  const ind = p.indikator ?? [], pm = p.polymarket ?? [], aset = p.aset ?? []
  return (
    <div className="det-body">
      {sk.length > 0 && (
        <div className="skn-grid">
          {sk.map((k, i) => (
            <div className="skn" key={i}>
              <div className="skn-top"><b>{nama(k)}</b><span className="num">Peluang {pct(k.peluang)}</span></div>
              <p>{k.syarat}</p>
              <p>Emas <b className={dirClass(k.arahEmas)}>{k.arahEmas}</b>, gerak khas <span className="num">{usd(k.gerak15m)}</span> (15m) dan <span className="num">{usd(k.gerak1h)}</span> (1j)</p>
              {k.konfirmasi && <p><span className="sub">Konfirmasi:</span> {k.konfirmasi}</p>}
              {k.batal && <p><span className="sub">Batal:</span> {k.batal}</p>}
            </div>
          ))}
        </div>
      )}
      {ind.length > 0 && (
        <>
          <h3>Indikator pendukung</h3>
          <ul className="det-list">
            {ind.map((x, i) => (
              <li key={i}><b>{x.nama}</b> <span className="num">{x.nilai}</span> <span className="tag ctx">{x.arah}</span> {x.catatan && <span className="sub">{x.catatan}</span>}</li>
            ))}
          </ul>
        </>
      )}
      {pm.length > 0 && (
        <>
          <h3>Polymarket</h3>
          <ul className="det-list">
            {pm.map((m, i) => (
              <li key={i}>
                {/^https?:\/\//.test(m.url) ? <a href={m.url} target="_blank" rel="noopener noreferrer">{m.judul}</a> : m.judul}
                {' '}<span className="sub num">volume ${fmt(m.volume, 0)}</span>
                <div className="chips">{(m.outcomes ?? []).map((o, j) => <span key={j}>{o.label} <b className="num">{pct(o.peluang)}</b></span>)}</div>
              </li>
            ))}
          </ul>
        </>
      )}
      {aset.length > 0 && <p className="sub">Pantau juga: {aset.map((x) => `${x.label} (${x.relasi})`).join(', ')}</p>}
    </div>
  )
}

// Strip di atas chart saat event high impact dengan skenario tinggal < 2 jam.
export function Menjelang({ rows, now }: { rows: NewsRow[] | null; now: number }) {
  const r = rows?.find((x) => {
    const dt = Date.parse(x.event_time) - now
    return x.importance === 1 && x.skenario?.length && dt > 0 && dt < 2 * 3600e3
  })
  if (!r?.skenario) return null
  const left = Math.ceil((Date.parse(r.event_time) - now) / 60000)
  return (
    <section className="soon" aria-labelledby="soonTitle">
      <div className="soon-head">
        <h2 id="soonTitle">Menjelang news</h2>
        <b>{r.title}</b>
        <span className="sub num">{wibTime(r.event_time)} · {left >= 60 ? `${Math.floor(left / 60)} jam ${left % 60} menit lagi` : `${left} menit lagi`}</span>
      </div>
      <ul>
        {r.skenario.map((k, i) => (
          <li key={i}>
            <b>{nama(k)}</b> <span className="num">{pct(k.peluang)}</span>: {k.syarat} → <b className={dirClass(k.arahEmas)}>emas {k.arahEmas}</b>
            {k.gerak15m != null && <span className="sub num"> · {usd(k.gerak15m)} dalam 15m</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function Outlook({ rows, error }: { rows: NewsRow[] | null; error: string | null }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const body = FIXTURE ? <p className="sub">Outlook muncul setelah Supabase tersambung.</p>
    : error ? <p className="sub">Outlook tidak bisa dimuat ({error}).</p>
    : rows == null ? <div className="state compact"><div className="spin" aria-hidden="true" /><p>Memuat outlook</p></div>
    : !rows.length ? <p className="sub">Belum ada event high/medium mulai hari ini sampai 45 hari ke depan.</p>
    : (
      <div className="tbl-wrap tall">
        <table>
          <thead><tr>
            <th>Waktu</th><th>Impact</th><th>Event</th><th className="r">F</th><th className="r">P</th><th className="r">A</th><th>Dampak</th><th>Lean / hasil</th>
          </tr></thead>
          <tbody>
            {rows.flatMap((r, i) => { const has = !!(r.skenario?.length || r.pendukung); return [
              ...(i === 0 || bulan(r.event_time) !== bulan(rows[i - 1].event_time)
                ? [<tr key={`m-${r.id}`} className="month"><th colSpan={8} scope="rowgroup">{bulan(r.event_time)}</th></tr>]
                : []),
              <tr key={r.id}>
                <td className="num nw">{wibTime(r.event_time)}</td>
                <td><Impact v={r.importance === 1 ? 'High' : r.importance === 0 ? 'Medium' : null} /></td>
                <td>
                  {r.title}
                  {has && (
                    <button
                      type="button" className="exp-btn" aria-expanded={!!open[r.id]} aria-controls={`det-${r.id}`}
                      onClick={() => setOpen((o) => ({ ...o, [r.id]: !o[r.id] }))}
                    >
                      {open[r.id] ? 'Tutup skenario' : 'Lihat skenario'}
                    </button>
                  )}
                </td>
                <td className="r num">{num(r.forecast)}</td>
                <td className="r num">{num(r.previous)}</td>
                <td className="r num">{num(r.actual)}</td>
                <td className="nw">
                  {r.dampak?.panas || r.dampak?.dingin ? (
                    <>
                      Panas → <span className={dirClass(r.dampak.panas)}>{r.dampak.panas ?? '–'}</span>,{' '}
                      Dingin → <span className={dirClass(r.dampak.dingin)}>{r.dampak.dingin ?? '–'}</span>
                    </>
                  ) : <span className="sub">–</span>}
                </td>
                <td>
                  {r.hasil ? <Hasil h={r.hasil} />
                    : r.lean?.arah ? <span className={`lean ${dirClass(r.lean.arah)}`} title={r.lean.alasan}>{r.lean.arah}{r.lean.kekuatan ? ` · ${r.lean.kekuatan}` : ''}</span>
                    : <span className="sub">–</span>}
                </td>
              </tr>,
              ...(has && open[r.id] ? [<tr key={`d-${r.id}`} id={`det-${r.id}`} className="det"><td colSpan={8}><Detail r={r} /></td></tr>] : []),
            ] })}
          </tbody>
        </table>
      </div>
    )
  return (
    <section className="card full" aria-labelledby="outTitle">
      <div className="card-head"><h2 id="outTitle">Outlook news hari ini dan ke depan</h2><span className="sub">Waktu WIB. F = forecast, P = previous, A = actual</span></div>
      {body}
    </section>
  )
}
