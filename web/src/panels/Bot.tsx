import type { BotStatus, BotTrade } from '../lib/supabase'
import { age, fmt, wibTime } from '../lib/format'

type R<T> = { rows: T[] | null; error: string | null }

const MIGRASI = 'supabase/migrations/20261008020000_bot_mt5.sql'

export function Bot({ status, trades }: { status: R<BotStatus>; trades: R<BotTrade> }) {
  const s = status.rows?.[0]
  const mati = s ? Date.now() - Date.parse(s.updated_at) > 3 * 60e3 : true
  const selesai = (trades.rows ?? []).filter((t) => t.akun === 'demo' && (t.status === 'TP' || t.status === 'SL'))
  const tp = selesai.filter((t) => t.status === 'TP').length
  const totalR = selesai.reduce((a, t) => a + Number(t.r ?? 0), 0)
  const pl = selesai.reduce((a, t) => a + Number(t.pl ?? 0), 0)

  let isi
  if (status.error) {
    isi = <p className="sub">Tabel bot belum ada ({status.error}). Jalankan <code>{MIGRASI}</code> di SQL Editor Supabase.</p>
  } else if (!status.rows) {
    isi = <p className="sub">Memuat status bot.</p>
  } else if (!s) {
    isi = <p className="sub">Bot belum pernah berjalan. Isi MT5_LOGIN, MT5_PASSWORD, MT5_SERVER akun demo di <code>.env</code>, lalu minta Claude menjalankan bot.</p>
  } else {
    const g = s.pengaman ?? {}
    isi = (
      <>
        <div className="bot-top">
          <span className={`bot-akun ${s.akun === 'real' ? 'real' : ''}`}>{s.akun === 'real' ? 'AKUN REAL' : 'DEMO'}</span>
          <span className="sub">{s.server} · {s.simbol ?? '-'} · risiko {(s.risiko * 100).toFixed(1)}%</span>
        </div>
        <p className={`setup-state ${mati ? 'off' : g.boleh_order ? 'go' : 'wait'}`} role="status">
          {mati ? `Bot tidak aktif (update terakhir ${age(s.updated_at)}).` : g.boleh_order ? 'Bot aktif, siap pasang order saat setup muncul.' : `Bot aktif, order baru ditahan: ${g.alasan}.`}
        </p>
        <dl className="kv">
          <dt>Ekuitas</dt><dd className="num wide">{fmt(s.ekuitas, 2)}</dd>
          <dt>P/L hari ini</dt><dd className={`num wide ${(s.pl_hari_ini ?? 0) >= 0 ? 'up' : 'down'}`}>{fmt(s.pl_hari_ini, 2)}</dd>
          <dt>SL / entry hari ini</dt><dd className="num wide">{s.sl_hari_ini}/2 SL · {s.entry_hari_ini}/3 entry</dd>
          <dt>Spread</dt><dd className="num wide">{g.spread ?? '-'} poin (median {g.spread_median ?? '-'})</dd>
        </dl>
        {(s.terbuka ?? []).map((o) => (
          <p key={o.id} className="bot-open"><b className={o.side === 'sell' ? 'down' : 'up'}>{o.side.toUpperCase()}</b> {o.lot} lot @ {fmt(o.entry, 2)} · SL {fmt(o.sl, 2)} · TP {fmt(o.tp, 2)} · {o.status === 'PENDING' ? 'menunggu terisi' : 'posisi terbuka'}</p>
        ))}
        <p className="exp-tag">Real test demo: {selesai.length} trade selesai, {tp} TP, {totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R, P/L {fmt(pl, 2)}.</p>
        {s.syarat_live && (
          <ul className="cek">
            {Object.entries(s.syarat_live).map(([k, v]) => <li key={k} className={v ? 'up' : 'sub'}>{v ? 'Lulus' : 'Belum'}: {k}</li>)}
          </ul>
        )}
      </>
    )
  }
  return (
    <section className="card" aria-labelledby="botTitle">
      <div className="card-head"><h2 id="botTitle">Bot MT5</h2>{s && <span className="sub">{wibTime(s.updated_at)}</span>}</div>
      {isi}
    </section>
  )
}
