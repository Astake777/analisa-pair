import { useState } from 'react'
import type { Analysis, LogRow, Payload } from '../lib/supabase'
import type { Jejak } from '../lib/nasib'
import { fmt, store, wibTime } from '../lib/format'
import { Kinerja } from './Kinerja'
import { Amd } from './Analysis'
import { RekamJejak, Riwayat } from './Setups'

const TABS = ['Setup', 'Backtest', 'AMD', 'Analisis'] as const
type Tab = (typeof TABS)[number]

type Props = {
  riwayat: Jejak[]; off: number; log: LogRow[] | null; logError: string | null
  amd?: Payload['amd']; analisis: Analysis[] | null
}

// Satu kartu untuk semua yang bersifat "sudah terjadi": setup selesai, rekam jejak watcher, backtest, AMD, analisis lama.
export function Rekap({ riwayat, off, log, logError, amd, analisis }: Props) {
  const [tab, setTab] = useState<Tab>(() => {
    const t = store.get('rekap')
    return (TABS as readonly string[]).includes(t ?? '') ? (t as Tab) : 'Setup'
  })
  const pilih = (t: Tab) => { setTab(t); store.set('rekap', t) }
  return (
    <section className="card" aria-labelledby="rekapTitle">
      <div className="card-head"><h2 id="rekapTitle">Riwayat &amp; kinerja</h2></div>
      <div className="seg rekap-tabs" role="group" aria-label="Bagian riwayat">
        {TABS.map((t) => (
          <button key={t} type="button" aria-pressed={t === tab} onClick={() => pilih(t)}>{t}</button>
        ))}
      </div>
      <div className="rekap-isi">
        {tab === 'Setup' && (
          <>
            <h3>Setup analisis</h3>
            <Riwayat rows={riwayat} off={off} />
            <h3>Watcher sniper (live)</h3>
            <RekamJejak rows={log} error={logError} />
          </>
        )}
        {tab === 'Backtest' && <Kinerja bare />}
        {tab === 'AMD' && (amd ? <Amd amd={amd} off={off} bare /> : <p className="sub">Belum ada data fase AMD di analisis terbaru.</p>)}
        {tab === 'Analisis' && <DaftarAnalisis rows={analisis} off={off} />}
      </div>
    </section>
  )
}

function DaftarAnalisis({ rows, off }: { rows: Analysis[] | null; off: number }) {
  if (rows == null) return <p className="sub">Memuat riwayat analisis.</p>
  if (!rows.length) return <p className="sub">Belum ada riwayat untuk pair dan mode ini.</p>
  return (
    <ul className="log">
      {rows.slice(0, 15).map((h, i) => {
        const s = h.payload.setups?.[0]
        return (
          <li key={h.id ?? i} title={s ? `SL ${fmt(s.sl + off)} · TP ${s.tp?.map((t) => fmt(t + off)).join(' / ') ?? '–'} · keyakinan ${h.keyakinan ?? '–'}` : `keyakinan ${h.keyakinan ?? '–'}`}>
            <span className={s?.side === 'sell' ? 'down' : s?.side === 'buy' ? 'up' : 'sub'}>{s ? s.side.toUpperCase() : '–'}</span>
            <span className="num">{s ? fmt(s.entry + off, 2) : '–'}</span>
            <span className="sub">{wibTime(h.created_at)}</span>
            <span className="sub">{h.status}</span>
          </li>
        )
      })}
    </ul>
  )
}
