import { useState, type FormEvent } from 'react'
import type { Setup, Side } from '../lib/supabase'
import { fmt } from '../lib/format'
import { useAksi, type Mt5 } from './Posisi'



const PIP = 0.1   // 1 pip XAUUSD = $0.10
const KOSONG = { side: 'buy' as Side, entry: '', sl: '20', tp: '100', lot: '' }   // sl/tp dalam pips dari entry

// Posisi dan order langsung dari MT5 lewat jembatan lokal, plus tombol tutup dan pasang limit.
function KontrolMt5({ setup, off, mt5 }: { setup?: Setup | null; off: number; mt5: Mt5 }) {
  const { a, putus, muat } = mt5
  const { kirim, jalankan, pesan } = useAksi(muat)
  const [buka, setBuka] = useState(false)
  const [yakin, setYakin] = useState(false)
  const [f, setF] = useState(KOSONG)
  const AKUN = a?.akun.toUpperCase() ?? ''
  const mati = putus || !a || kirim
  const pctRisiko = a ? +(a.risiko * 100).toFixed(2) : '–'
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
      <div className="card-head">
        <h2 id="orderTitle">Kontrol order MT5</h2>
        {a && <span className={`bot-akun ${a.akun === 'real' ? 'real' : ''}`}>{a.akun === 'real' ? 'AKUN REAL' : AKUN}</span>}
      </div>
      {putus && <p className="setup-state off" role="alert">Kontrol MT5 butuh jembatan MT5 (mulai_trading.bat)</p>}
      {!a && !putus && <p className="sub">Memuat akun MT5.</p>}
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
      {pesan}
    </div>
  )
}

// Tombol order manual ke MT5 (Set limit, Close all) di kartunya sendiri; status EA ada di kartu Mesin eksekusi.
export function KontrolOrder({ setup, off = 0, mt5 }: { setup?: Setup | null; off?: number; mt5: Mt5 }) {
  return (
    <section className="card panel" aria-labelledby="orderTitle">
      <KontrolMt5 setup={setup} off={off} mt5={mt5} />
    </section>
  )
}
