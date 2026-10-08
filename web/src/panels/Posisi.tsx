import { useCallback, useEffect, useState } from 'react'
import type { Side } from '../lib/supabase'
import { fmt, kirimMt5, signed, wibTime } from '../lib/format'

export type Posisi = { tiket: number; side: Side; lot: number; buka: number; sl: number; tp: number; profit: number; magic: number; komentar: string; waktu: number }
export type Order = { tiket: number; side: Side; jenis: 'limit' | 'stop' | 'lain'; lot: number; harga: number; sl: number; tp: number; magic: number; komentar: string; waktu: number }
export type Akun = {
  akun: 'demo' | 'contest' | 'real'; login: number; saldo: number; ekuitas: number; mata_uang: string
  harga: { bid: number; ask: number }; risiko: number; posisi: Posisi[]; order: Order[]
}
export type Mt5 = { a: Akun | null; putus: boolean; muat: () => void }
type Jawab = { ok: boolean; pesan?: string; lot?: number; rugi_di_sl?: number }

const PIP = 0.1
const MAGIC: Record<number, string> = { 770078: 'EA', 770079: 'Web', 0: 'Manual' }
const sumber = (m: number) => MAGIC[m] ?? `magic ${m}`
const jam = (t: number) => wibTime(new Date(t * 1000).toISOString(), false).slice(0, 5)

// Satu sumber data akun MT5 (tiap 5 detik) untuk kartu Posisi dan kartu Bot.
export function useAkunMt5(): Mt5 {
  const [a, setA] = useState<Akun | null>(null)
  const [putus, setPutus] = useState(false)
  const muat = useCallback(() => {
    fetch('/mt5/akun/posisi').then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((x: Akun) => { setA(x); setPutus(false) }, () => setPutus(true))
  }, [])
  useEffect(() => {
    muat()
    const t = setInterval(muat, 5000)
    return () => clearInterval(t)
  }, [muat])
  return { a, putus, muat }
}

// Kirim perintah ke jembatan; pesan hasil hilang sendiri setelah 10 detik.
export function useAksi(muat: () => void) {
  const [kirim, setKirim] = useState(false)
  const [hasil, setHasil] = useState<{ ok: boolean; teks: string } | null>(null)
  useEffect(() => {
    if (!hasil) return
    const t = setTimeout(() => setHasil(null), 10_000)
    return () => clearTimeout(t)
  }, [hasil])
  const jalankan = async (tanya: string | null, path: string, body: unknown, ok: (j: Jawab) => string) => {
    if (tanya && !confirm(tanya)) return
    setKirim(true); setHasil(null)
    try {
      setHasil({ ok: true, teks: ok(await kirimMt5<Jawab>(path, body)) })
    } catch (e) {
      setHasil({ ok: false, teks: (e as Error).message })
    } finally { setKirim(false); muat() }
  }
  const pesan = hasil && (
    <p className={`setup-state hasil ${hasil.ok ? '' : 'off'}`} role={hasil.ok ? 'status' : 'alert'}>
      <span>{hasil.teks}</span>
      <button type="button" className="tutup-x" aria-label="Tutup pesan" onClick={() => setHasil(null)}>×</button>
    </p>
  )
  return { kirim, jalankan, pesan }
}

const Arah = ({ s }: { s: Side }) => <b className={s === 'sell' ? 'down' : 'up'}>{s.toUpperCase()}</b>
const SlTp = ({ sl, tp }: { sl: number; tp: number }) => <>SL {sl ? fmt(sl, 2) : 'tanpa'} · TP {tp ? fmt(tp, 2) : 'tanpa'}</>

// Posisi berjalan dan limit yang menunggu di MT5, langsung dari terminal.
export function PosisiAktif({ mt5 }: { mt5: Mt5 }) {
  const { a, putus, muat } = mt5
  const { kirim, jalankan, pesan } = useAksi(muat)
  const AKUN = a?.akun.toUpperCase() ?? ''
  const mati = putus || !a || kirim
  const floating = a?.posisi.reduce((s, p) => s + p.profit, 0) ?? 0

  let isi
  if (!a && putus) isi = <p className="setup-state off" role="alert">Posisi MT5 butuh jembatan MT5. Jalankan mulai_trading.bat.</p>
  else if (!a) isi = <p className="sub">Memuat posisi dari MT5.</p>
  else if (!a.posisi.length && !a.order.length) {
    isi = (
      <div className="state compact">
        <h2>Tidak ada posisi atau limit aktif</h2>
        <p>Order yang terpasang dari website, EA, atau MT5 langsung muncul di sini.</p>
      </div>
    )
  } else {
    const { bid, ask } = a.harga
    isi = (
      <>
        {a.posisi.length > 0 && (
          <div className="aktif-grup">
            <h3>Posisi berjalan <span className="sub">{a.posisi.length}</span></h3>
            <ul className="aktif-list">
              {a.posisi.map((p) => {
                const pip = ((p.side === 'buy' ? bid - p.buka : p.buka - ask) / PIP)
                return (
                  <li key={p.tiket}>
                    <div className="aktif-baris">
                      <span><Arah s={p.side} /> <span className="num">{p.lot} lot @ {fmt(p.buka, 2)}</span></span>
                      <span className={`num aktif-pl ${p.profit > 0 ? 'up' : p.profit < 0 ? 'down' : ''}`}>{signed(p.profit, 2)}</span>
                    </div>
                    <div className="aktif-baris">
                      <span className="sub">{signed(pip, 0)} pips · <SlTp sl={p.sl} tp={p.tp} /> · {sumber(p.magic)} · sejak {jam(p.waktu)} WIB</span>
                      <button type="button" className="theme-btn" disabled={mati}
                        onClick={() => jalankan(`Tutup posisi ${p.side.toUpperCase()} ${p.lot} lot @ ${fmt(p.buka, 2)} (P/L ${signed(p.profit, 2)}) di akun ${AKUN}?`,
                          '/order/close', { tiket: p.tiket }, (j) => j.pesan ?? 'Posisi ditutup.')}>Tutup</button>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
        {a.order.length > 0 && (
          <div className="aktif-grup">
            <h3>Limit menunggu <span className="sub">{a.order.length}</span></h3>
            <ul className="aktif-list">
              {a.order.map((o) => {
                const jarak = Math.abs((o.side === 'buy' ? ask : bid) - o.harga) / PIP
                return (
                  <li key={o.tiket}>
                    <div className="aktif-baris">
                      <span><Arah s={o.side} /> <span className="num">{o.jenis.toUpperCase()} {o.lot} lot @ {fmt(o.harga, 2)}</span></span>
                      <span className="num sub">{fmt(jarak, 0)} pips lagi</span>
                    </div>
                    <div className="aktif-baris">
                      <span className="sub"><SlTp sl={o.sl} tp={o.tp} /> · {sumber(o.magic)} · dipasang {jam(o.waktu)} WIB</span>
                      <button type="button" className="theme-btn" disabled={mati}
                        onClick={() => jalankan(`Batalkan ${o.side.toUpperCase()} ${o.jenis.toUpperCase()} ${o.lot} lot @ ${fmt(o.harga, 2)} di akun ${AKUN}?`,
                          '/order/close', { tiket: o.tiket }, (j) => j.pesan ?? 'Order dibatalkan.')}>Batalkan</button>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
        {putus && <p className="sub" role="status">Jembatan MT5 terputus, data di atas dari pembaruan terakhir.</p>}
      </>
    )
  }

  return (
    <section className="card" aria-labelledby="aktifTitle">
      <div className="card-head">
        <h2 id="aktifTitle">Posisi & limit aktif</h2>
        {a && (
          <span className="aktif-kepala">
            {a.posisi.length > 0 && <span className={`num ${floating > 0 ? 'up' : floating < 0 ? 'down' : ''}`}>Floating {signed(floating, 2)}</span>}
            <span className={`bot-akun ${a.akun === 'real' ? 'real' : ''}`}>{a.akun === 'real' ? 'AKUN REAL' : AKUN}</span>
          </span>
        )}
      </div>
      {isi}
      {pesan}
    </section>
  )
}
