import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { BotStatus, BotTrade, Setup, Side } from '../lib/supabase'
import { age, fmt, kirimMt5, signed, wibTime } from '../lib/format'

type R<T> = { rows: T[] | null; error: string | null }

const MIGRASI = 'supabase/migrations/20261008020000_bot_mt5.sql'

type Posisi = { tiket: number; side: Side; lot: number; buka: number; sl: number; tp: number; profit: number; magic: number; komentar: string; waktu: number }
type Order = { tiket: number; side: Side; jenis: 'limit' | 'stop' | 'lain'; lot: number; harga: number; sl: number; tp: number; magic: number; komentar: string; waktu: number }
type Akun = {
  akun: 'demo' | 'contest' | 'real'; login: number; saldo: number; ekuitas: number; mata_uang: string
  harga: { bid: number; ask: number }; risiko: number; posisi: Posisi[]; order: Order[]
}
type Jawab = { ok: boolean; pesan?: string; lot?: number; rugi_di_sl?: number }
const MAGIC: Record<number, string> = { 770078: 'EA', 770079: 'Web', 0: 'Manual' }
const PIP = 0.1   // 1 pip XAUUSD = $0.10
const KOSONG = { side: 'buy' as Side, entry: '', sl: '20', tp: '100', lot: '' }   // sl/tp dalam pips dari entry

// Posisi dan order langsung dari MT5 lewat jembatan lokal, plus tombol tutup dan pasang limit.
function KontrolMt5({ setup, off }: { setup?: Setup | null; off: number }) {
  const [a, setA] = useState<Akun | null>(null)
  const [putus, setPutus] = useState(false)
  const [buka, setBuka] = useState(false)
  const [f, setF] = useState(KOSONG)
  const [kirim, setKirim] = useState(false)
  const [hasil, setHasil] = useState<{ ok: boolean; teks: string } | null>(null)

  const muat = useCallback(() => fetch('/mt5/akun/posisi').then((r) => (r.ok ? r.json() : Promise.reject()))
    .then((x: Akun) => { setA(x); setPutus(false) }, () => setPutus(true)), [])
  useEffect(() => {
    muat()
    const t = setInterval(muat, 5000)
    return () => clearInterval(t)
  }, [muat])

  const AKUN = a?.akun.toUpperCase() ?? ''
  const mati = putus || !a || kirim
  const pctRisiko = a ? +(a.risiko * 100).toFixed(2) : '–'
  const [yakin, setYakin] = useState(false)
  const jalankan = async (tanya: string | null, path: string, body: unknown, ok: (j: Jawab) => string) => {
    if (tanya && !confirm(tanya)) return
    setKirim(true); setHasil(null)
    try {
      setHasil({ ok: true, teks: ok(await kirimMt5<Jawab>(path, body)) })
    } catch (e) {
      setHasil({ ok: false, teks: (e as Error).message })
    } finally { setKirim(false); muat() }
  }
  const tutupPosisi = (p: Posisi) => jalankan(
    `Tutup posisi ${p.side.toUpperCase()} ${p.lot} lot @ ${fmt(p.buka, 2)} (tiket ${p.tiket}) di akun ${AKUN}?`,
    '/order/close', { tiket: p.tiket }, (j) => j.pesan ?? 'Posisi ditutup.')
  const batalOrder = (o: Order) => jalankan(
    `Batalkan order ${o.side.toUpperCase()} ${o.jenis.toUpperCase()} ${o.lot} lot @ ${fmt(o.harga, 2)} (tiket ${o.tiket}) di akun ${AKUN}?`,
    '/order/close', { tiket: o.tiket }, (j) => j.pesan ?? 'Order dibatalkan.')
  // konfirmasi di kartu (bukan popup) supaya close all tidak terjadi karena salah klik
  const tutupSemua = () => { setYakin(false); jalankan(null, '/order/close-all', {}, (j) => j.pesan ?? 'Selesai.') }
  const pasang = (e: FormEvent) => {
    e.preventDefault()
    const lot = f.lot.trim() ? Number(f.lot) : null
    const body = { side: f.side, entry: Number(f.entry), sl: harga.sl, tp: harga.tp, lot }
    jalankan(
      `Pasang ${f.side.toUpperCase()} LIMIT di akun ${AKUN}?\nEntry ${fmt(body.entry, 2)}, SL ${fmt(body.sl, 2)} (${f.sl} pips), TP ${fmt(body.tp, 2)} (${f.tp} pips), lot ${lot ?? `otomatis (${pctRisiko}% saldo)`}.`,
      '/order/limit', body, (j) => `${j.pesan ?? 'Order terpasang.'} Lot ${j.lot ?? '–'}, rugi di SL ${fmt(j.rugi_di_sl, 2)} ${a?.mata_uang ?? ''}.`)
  }
  // sl/tp diisi dalam pips; harga dihitung dari entry dan arah
  const arah = f.side === 'sell' ? -1 : 1
  const harga = { sl: +(Number(f.entry) - arah * Number(f.sl) * PIP).toFixed(2), tp: +(Number(f.entry) + arah * Number(f.tp) * PIP).toFixed(2) }
  const siap = f.entry !== '' && f.sl !== '' && f.tp !== ''
  // harga setup dalam spot XAU; ditambah selisih broker seperti yang tampil di kartu Setup
  const pips = (d: number) => String(Math.round(Math.abs(d) / PIP))
  const isiSetup = () => setup && setF({
    ...f, side: setup.side, entry: (setup.entry + off).toFixed(2), sl: pips(setup.entry - setup.sl),
    tp: setup.tp?.[0] != null ? pips(setup.tp[0] - setup.entry) : f.tp,
  })
  const ubah = (k: 'entry' | 'sl' | 'tp' | 'lot') => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })

  return (
    <div className="bot-ctl">
      <div className="bot-top">
        <h3>Posisi & order di MT5</h3>
        {a && <span className={`bot-akun ${a.akun === 'real' ? 'real' : ''}`}>{a.akun === 'real' ? 'AKUN REAL' : AKUN}</span>}
      </div>
      {putus && <p className="setup-state off" role="alert">Kontrol MT5 butuh jembatan MT5 (mulai_trading.bat)</p>}
      {!a && !putus && <p className="sub">Memuat posisi MT5.</p>}
      {a && (!a.posisi.length && !a.order.length ? <p className="sub">Tidak ada posisi atau order terbuka.</p> : (
        <ul className="log log2 posisi">
          {a.posisi.map((p) => (
            <li key={`p${p.tiket}`}>
              <span className={p.side === 'sell' ? 'down' : 'up'}>{p.side.toUpperCase()}</span>
              <span className="num">{p.lot} lot @ {fmt(p.buka, 2)}</span>
              <span className={`num ${p.profit > 0 ? 'up' : p.profit < 0 ? 'down' : 'flat'}`}>{signed(p.profit, 2)}</span>
              <button type="button" className="theme-btn" disabled={mati} onClick={() => tutupPosisi(p)}>Tutup</button>
              <span className="sub ket">Posisi · SL {fmt(p.sl, 2)} · TP {fmt(p.tp, 2)} · {MAGIC[p.magic] ?? p.magic}</span>
            </li>
          ))}
          {a.order.map((o) => (
            <li key={`o${o.tiket}`}>
              <span className={o.side === 'sell' ? 'down' : 'up'}>{o.side.toUpperCase()}</span>
              <span className="num">{o.jenis.toUpperCase()} {o.lot} lot @ {fmt(o.harga, 2)}</span>
              <span className="sub">Order</span>
              <button type="button" className="theme-btn" disabled={mati} onClick={() => batalOrder(o)}>Batalkan</button>
              <span className="sub ket">SL {fmt(o.sl, 2)} · TP {fmt(o.tp, 2)} · {MAGIC[o.magic] ?? o.magic}</span>
            </li>
          ))}
        </ul>
      ))}
      <div className="bot-top">
        <button type="button" className="theme-btn" aria-expanded={buka} disabled={putus || !a} onClick={() => setBuka(!buka)}>Set limit</button>
        <button type="button" className="theme-btn bahaya" disabled={!a || mati || (!a.posisi.length && !a.order.length)} aria-expanded={yakin} onClick={() => setYakin(!yakin)}>Close all</button>
      </div>
      {yakin && a && (a.posisi.length > 0 || a.order.length > 0) && (
        <div className="setup-state off yakin" role="alertdialog" aria-label="Konfirmasi close all">
          <p>Tutup <b>{a.posisi.length} posisi</b> dan hapus <b>{a.order.length} order</b> di akun <b>{AKUN}</b>? Tidak bisa dibatalkan.</p>
          <div className="bot-top">
            <button type="button" className="theme-btn" autoFocus onClick={() => setYakin(false)}>Batal</button>
            <button type="button" className="theme-btn bahaya-isi" disabled={mati} onClick={tutupSemua}>Ya, tutup semua</button>
          </div>
        </div>
      )}
      {buka && (
        <form className="form-limit" onSubmit={pasang}>
          <div className="seg" role="group" aria-label="Jenis order">
            {(['buy', 'sell'] as const).map((x) => (
              <button key={x} type="button" aria-pressed={f.side === x} onClick={() => setF({ ...f, side: x })}>{x.toUpperCase()} LIMIT</button>
            ))}
          </div>
          <label>Entry<input type="number" step="0.01" inputMode="decimal" required value={f.entry} onChange={ubah('entry')} /></label>
          <label>Stop loss (pips)<input type="number" step="1" min="1" inputMode="numeric" required value={f.sl} onChange={ubah('sl')} />
            <small>{siap ? `SL di ${fmt(harga.sl, 2)}` : 'isi entry dulu'}</small></label>
          <label>Take profit (pips)<input type="number" step="1" min="1" inputMode="numeric" required value={f.tp} onChange={ubah('tp')} />
            <small>{siap ? `TP di ${fmt(harga.tp, 2)} · RR 1:${(Number(f.tp) / Number(f.sl) || 0).toFixed(1)}` : 'isi entry dulu'}</small></label>
          <label>Lot<input type="number" step="0.01" min="0.01" inputMode="decimal" placeholder={`otomatis ${pctRisiko}% saldo`} value={f.lot} onChange={ubah('lot')} /></label>
          <div className="bot-top">
            <button type="button" className="theme-btn" disabled={!setup} onClick={isiSetup}>Isi dari setup aktif</button>
            <button type="submit" className="theme-btn" disabled={mati}>{kirim ? 'Mengirim' : 'Pasang limit di MT5'}</button>
          </div>
        </form>
      )}
      {hasil && <p className={`setup-state ${hasil.ok ? '' : 'off'}`} role={hasil.ok ? 'status' : 'alert'}>{hasil.teks}</p>}
    </div>
  )
}

export function Bot({ status, trades, setup, off = 0 }: { status: R<BotStatus>; trades: R<BotTrade>; setup?: Setup | null; off?: number }) {
  const s = status.rows?.[0]
  const mati = s ? Date.now() - Date.parse(s.updated_at) > 3 * 60e3 : true
  const selesai = (trades.rows ?? []).filter((t) => t.akun === 'demo' && ['TP', 'SL', 'BE'].includes(t.status))
  const tp = selesai.filter((t) => t.status === 'TP').length
  const be = selesai.filter((t) => t.status === 'BE').length
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
          <span className="sub">{s.server} · {s.simbol ?? '-'} · risiko {(s.risiko * 100).toFixed(0)}% saldo · lot ikut lebar SL</span>
        </div>
        <p className={`setup-state ${mati ? 'off' : g.boleh_order ? 'go' : 'wait'}`} role="status">
          {mati ? `Bot tidak aktif (update terakhir ${age(s.updated_at)}).` : g.boleh_order ? 'Bot aktif, siap pasang order saat setup muncul.' : `Bot aktif, order baru ditahan: ${g.alasan}.`}
        </p>
        <dl className="kv">
          <dt>Saldo</dt><dd className="num wide">{fmt(s.saldo, 2)}</dd>
          <dt>Ekuitas</dt><dd className="num wide">{fmt(s.ekuitas, 2)}</dd>
          <dt>P/L hari ini</dt><dd className={`num wide ${(s.pl_hari_ini ?? 0) >= 0 ? 'up' : 'down'}`}>{fmt(s.pl_hari_ini, 2)}</dd>
          <dt>SL / entry hari ini</dt><dd className="num wide">{s.sl_hari_ini}/2 SL · {s.entry_hari_ini}/3 entry</dd>
          <dt>Spread</dt><dd className="num wide">{g.spread ?? '-'} poin (median {g.spread_median ?? '-'})</dd>
        </dl>
        {(s.terbuka ?? []).map((o) => (
          <p key={o.id} className="bot-open"><b className={o.side === 'sell' ? 'down' : 'up'}>{o.side.toUpperCase()}</b> {o.lot} lot @ {fmt(o.entry, 2)} · SL {fmt(o.sl, 2)} · TP {fmt(o.tp, 2)} · {o.status === 'PENDING' ? 'menunggu terisi' : 'posisi terbuka'}</p>
        ))}
        <p className="exp-tag">Real test demo: {selesai.length} trade selesai, {tp} TP, {be} BE, {totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R, P/L {fmt(pl, 2)}.</p>
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
      <KontrolMt5 setup={setup} off={off} />
    </section>
  )
}
