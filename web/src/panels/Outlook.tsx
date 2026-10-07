import { FIXTURE, type NewsRow } from '../lib/supabase'
import { dirClass, fmt, wibTime } from '../lib/format'

const num = (v: number | null) => (v == null ? '–' : fmt(Number(v), 2).replace(/\.?0+$/, ''))

function Hasil({ h }: { h: Record<string, unknown> }) {
  const arah = typeof h.arah === 'string' ? h.arah : typeof h.usd === 'string' ? h.usd : ''
  const kuat = typeof h.kekuatan === 'string' ? h.kekuatan : ''
  return <span className={dirClass(arah)}>Hasil: {arah || 'sudah rilis'}{kuat ? ` · ${kuat}` : ''}</span>
}

export function Outlook({ rows, error }: { rows: NewsRow[] | null; error: string | null }) {
  const body = FIXTURE ? <p className="sub">Outlook muncul setelah Supabase tersambung.</p>
    : error ? <p className="sub">Outlook tidak bisa dimuat ({error}).</p>
    : rows == null ? <div className="state compact"><div className="spin" aria-hidden="true" /><p>Memuat outlook</p></div>
    : !rows.length ? <p className="sub">Belum ada event terjadwal dalam 45 hari ke depan.</p>
    : (
      <div className="tbl-wrap tall">
        <table>
          <thead><tr>
            <th>Waktu</th><th>Event</th><th className="r">F</th><th className="r">P</th><th className="r">A</th><th>Dampak</th><th>Lean / hasil</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="num nw">{wibTime(r.event_time)}</td>
                <td>{r.title}</td>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  return (
    <section className="card full" aria-labelledby="outTitle">
      <div className="card-head"><h2 id="outTitle">Outlook news 45 hari</h2><span className="sub">Waktu WIB. F = forecast, P = previous, A = actual</span></div>
      {body}
    </section>
  )
}
