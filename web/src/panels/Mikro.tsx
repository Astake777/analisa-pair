import { useEffect, useState } from 'react'
import { fmt } from '../lib/format'

type N = number | null
type Profil = { poc: N; vah: N; val: N } | null
type Sesi = { hi: N; lo: N; jalan: boolean } | null
type Data = {
  waktu: N | string; harga: N
  spread: { poin: N; pips: N; median: N }
  volatilitas: { m5: N; normal: N; rasio: N }
  range: { hari: N; adr: N; persen: N; hi: N; lo: N }
  vwap: { nilai: N; jarak: N }
  profil: { hari: Profil; kemarin: Profil }
  volume: { jam: N; normal: N; rasio: N }
  sesi: Record<'Asia' | 'London' | 'NY', Sesi>
}

// XAUUSD: 1 pip = $0.10
const pip = (d: N) => (d == null ? '–' : fmt(Math.abs(d) * 10, 0))
const ada = (n: N): n is number => n != null && Number.isFinite(n)

function Tile({ nama, nilai, ket, tag, waspada }: { nama: string; nilai: string; ket?: string; tag?: string; waspada?: boolean }) {
  return (
    <div className="drv">
      <span className="drv-name">{nama}</span>
      <span className="drv-val num">{nilai}</span>
      {ket && <span className="sub">{ket}</span>}
      {tag && <span className={`tag ${waspada ? 'con' : 'ctx'}`}>{tag}</span>}
    </div>
  )
}

function useMikro() {
  const [d, setD] = useState<Data | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let hidup = true
    const ambil = () => fetch('/mt5/mikro').then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((x: Data) => { if (hidup) { setD(x); setErr(false) } }, () => { if (hidup) setErr(true) })
    ambil()
    const t = setInterval(ambil, 30000)
    return () => { hidup = false; clearInterval(t) }
  }, [])
  return { d, err }
}

export function Mikro() {
  const { d, err } = useMikro()
  let isi
  if (!d && err) isi = <p className="sub" role="alert">Data mikro butuh jembatan MT5. Jalankan <code>mulai_trading.bat</code>.</p>
  else if (!d) isi = <p className="sub drv-state" role="status"><span className="spin sm" aria-hidden="true" />Memuat data mikro</p>
  else {
    const { spread: sp, volatilitas: vo, range: rg, vwap, profil, volume: vm } = d
    const lebar = ada(sp.poin) && ada(sp.median) && sp.poin > 1.5 * sp.median
    const tingkat = (r: N, a: string, b: string, c: string) => (!ada(r) ? undefined : r < 0.7 ? a : r > 1.5 ? c : b)
    const pct = ada(rg.hari) && ada(rg.adr) && rg.adr > 0 ? (rg.hari / rg.adr) * 100 : rg.persen
    const sisa = ada(rg.hari) && ada(rg.adr) ? rg.adr - rg.hari : null
    const jarak = ada(d.harga) && ada(vwap.nilai) ? d.harga - vwap.nilai : vwap.jarak
    const ph = profil.hari, pk = profil.kemarin
    isi = (
      <>
        <div className="drivers aset">
          <Tile
            nama="Spread" nilai={`${fmt(sp.poin, 0)} poin`} ket={`${fmt(sp.pips, 1)} pips · median ${fmt(sp.median, 0)} poin`}
            tag={ada(sp.poin) && ada(sp.median) ? (lebar ? 'Spread lebar' : 'Spread normal') : undefined} waspada={lebar}
          />
          <Tile
            nama="Volatilitas 1 jam" nilai={`${pip(vo.m5)} pips`} ket={`rata-rata candle M5 · normal ${pip(vo.normal)} pips · ${fmt(vo.rasio, 2)}×`}
            tag={tingkat(vo.rasio, 'Tenang', 'Normal', 'Volatil tinggi')} waspada={ada(vo.rasio) && vo.rasio > 1.5}
          />
          <Tile
            nama="Range hari ini" nilai={`${pip(rg.hari)} pips`} ket={`${fmt(pct, 0)}% ADR ${pip(rg.adr)} pips · ${fmt(rg.lo, 2)} – ${fmt(rg.hi, 2)}`}
            tag={sisa == null ? undefined : sisa > 0 ? `Sisa ±${pip(sisa)} pips ke ADR` : `Lewat ADR ${pip(sisa)} pips`} waspada={sisa != null && sisa <= 0}
          />
          <Tile
            nama="VWAP hari ini" nilai={fmt(vwap.nilai, 2)}
            tag={ada(jarak) ? `Harga ${jarak >= 0 ? 'di atas' : 'di bawah'} VWAP ${pip(jarak)} pips` : undefined}
          />
          <Tile
            nama="Volume profile hari ini" nilai={`POC ${fmt(ph?.poc, 2)}`}
            ket={`VAH ${fmt(ph?.vah, 2)} · VAL ${fmt(ph?.val, 2)}`} tag={`POC kemarin ${fmt(pk?.poc, 2)}`}
          />
          <Tile
            nama="Volume relatif" nilai={`${fmt(vm.rasio, 2)}×`} ket={`tick jam ini ${fmt(vm.jam, 0)} · normal ${fmt(vm.normal, 0)}`}
            tag={tingkat(vm.rasio, 'Sepi', 'Normal', 'Ramai')}
          />
          <div className="drv">
            <span className="drv-name">Sesi high – low</span>
            {(['Asia', 'London', 'NY'] as const).map((n) => {
              const s = d.sesi?.[n]
              return (
                <div className="drv-top" key={n}>
                  <span>{n}{s?.jalan && <span className="tag ctx sesi-jalan">berjalan</span>}</span>
                  <span className="num">{s ? `${fmt(s.hi, 2)} – ${fmt(s.lo, 2)}` : '–'}</span>
                </div>
              )
            })}
          </div>
        </div>
        <p className="exp-tag">Delta orderflow tidak tersedia: candle broker tidak punya sisi agresor.</p>
        {err && <p className="exp-tag" role="status">Pembaruan terakhir gagal, menampilkan data sebelumnya.</p>}
      </>
    )
  }
  return (
    <section className="card" aria-labelledby="mikroTitle">
      <div className="card-head"><h2 id="mikroTitle">Mikro</h2><span className="sub">Broker MT5 HFM, tick volume (bukan volume futures), tiap 30 detik</span></div>
      {isi}
    </section>
  )
}
