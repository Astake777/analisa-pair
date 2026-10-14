import { useEffect, useState } from 'react'
import type { BotStatus, BotTrade, Payload } from '../lib/supabase'
import type { Jejak } from '../lib/nasib'
import { fmt, signed } from '../lib/format'
import type { Mt5 } from './Posisi'

type R<T> = { rows: T[] | null; error: string | null }
type Aturan = { max_sl: number; max_entry: number; max_spread: number; news_menit: number; sl_pips: number; tp_min_pips: number; rr: number; expire_menit: number; poi_tf: string; magic: number }
export type EaStatus = {
  ada: boolean; umur?: number; algo_trading?: boolean; dijeda?: boolean; risk_persen?: number; be_pips?: number
  boleh_order?: boolean; alasan?: string; sl_hari_ini?: number; entry_hari_ini?: number; poi_aktif?: number
  log_terakhir?: string; aturan?: Aturan
}
export type Ea = { ea: EaStatus | null; putus: boolean }

const PIP = 0.1
const nada = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')

// Status EA SniperBot dari file yang ditulis EA tiap 30 detik (lewat jembatan MT5).
export function useEaStatus(): Ea {
  const [ea, setEa] = useState<EaStatus | null>(null)
  const [putus, setPutus] = useState(false)
  useEffect(() => {
    const ambil = () => fetch('/mt5/ea/status').then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((x: EaStatus) => { setEa(x); setPutus(false) }, () => setPutus(true))
    ambil()
    const t = setInterval(ambil, 5000)
    return () => clearInterval(t)
  }, [])
  return { ea, putus }
}

// Strip angka akun di bawah header: yang dicek pertama kali saat membuka terminal.
export function KpiStrip({ mt5, bot }: { mt5: Mt5; bot: R<BotStatus> }) {
  const { a, putus } = mt5
  const s = bot.rows?.[0]
  const floating = a?.posisi.reduce((x, p) => x + p.profit, 0) ?? null
  const mati = putus && !a
  const sel = (label: string, nilai: React.ReactNode, ket: React.ReactNode, cls = '') => (
    <div className="kpi">
      <dt>{label}</dt>
      <dd className={`num kpi-val ${cls}`}>{nilai}</dd>
      <dd className="kpi-ket">{ket}</dd>
    </div>
  )
  return (
    <dl className="kpi-strip" aria-label="Akun MT5">
      {sel('Saldo', a ? fmt(a.saldo, 2) : '–', mati ? 'jembatan MT5 mati' : a ? `${a.mata_uang} · ${a.akun.toUpperCase()}` : 'memuat')}
      {sel('Ekuitas', a ? fmt(a.ekuitas, 2) : '–', a ? `${signed(a.ekuitas - a.saldo, 2)} dari saldo` : '')}
      {sel('Floating P/L', floating == null ? '–' : signed(floating, 2), a ? (a.posisi.length ? `${a.posisi.length} posisi terbuka` : 'tidak ada posisi') : '', nada(floating))}
      {sel('Bid / Ask', a ? `${fmt(a.harga.bid, 2)} / ${fmt(a.harga.ask, 2)}` : '–', a ? `spread ${((a.harga.ask - a.harga.bid) / PIP).toFixed(1)} pips` : '', 'panjang')}
      {sel('P/L EA hari ini', s ? signed(s.pl_hari_ini ?? 0, 2) : '–', s ? `${s.sl_hari_ini} SL · ${s.entry_hari_ini} entry` : bot.error ? 'tabel bot belum ada' : '', nada(s?.pl_hari_ini))}
    </dl>
  )
}

// Keadaan EA dan gerbang order: apakah EA hidup, dan kalau ada setup, apakah boleh pasang order sekarang.
export function Mesin({ ea: { ea, putus }, mt5 }: { ea: Ea; mt5: Mt5 }) {
  const hidup = !!ea?.ada && (ea.umur ?? 1e9) < 120
  const at = ea?.aturan
  let gerbang: [string, string, string] // [kelas, judul, keterangan]
  if (putus && !ea) gerbang = ['off', 'Jembatan mati', 'Jalankan mulai_trading.bat untuk menyalakan MT5 dan jembatan.']
  else if (!ea) gerbang = ['wait', 'Memuat', 'Membaca status EA.']
  else if (!ea.ada) gerbang = ['off', 'EA belum terpasang', 'Pasang SniperBot di chart XAUUSD M1 (mulai_trading.bat).']
  else if (!hidup) gerbang = ['off', 'EA tidak menjawab', `Status terakhir ${Math.round((ea.umur ?? 0) / 60)} menit lalu. Cek MT5 terbuka.`]
  else if (!ea.algo_trading) gerbang = ['off', 'Algo Trading mati', 'Nyalakan tombol Algo Trading di MT5.']
  else if (ea.dijeda) gerbang = ['wait', 'Dijeda', 'Order baru ditahan sampai EA dilanjutkan (ea_kontrol.py resume).']
  else if (ea.boleh_order) gerbang = ['go', 'Siap order', 'EA memasang limit saat zona sniper terkonfirmasi.']
  else gerbang = ['wait', 'Ditahan', ea.alasan || 'Syarat order belum terpenuhi.']
  return (
    <section className="card panel" aria-labelledby="mesinTitle">
      <div className="card-head">
        <h2 id="mesinTitle">Mesin eksekusi</h2>
        {at && <span className="tag-garis num">EA {at.magic}</span>}
      </div>
      <p className="ea-hidup">
        <span className={`titik ${hidup ? 'on' : 'off'}`} aria-hidden="true" />
        <span>
          <b>{hidup ? 'EA tersambung' : 'EA tidak tersambung'}</b>
          <span className="sub">{ea?.ada ? `SniperBot · status ${ea.umur ?? '–'} dtk lalu` : 'SniperBot'}</span>
        </span>
      </p>
      <div className={`gerbang ${gerbang[0]}`} role="status">
        <span className="gerbang-label">Gerbang order</span>
        <b>{gerbang[1]}</b>
        <span className="sub">{gerbang[2]}</span>
      </div>
      {ea?.ada && (
        <dl className="baris-dl">
          <dt>Entry hari ini</dt><dd className="num">{ea.entry_hari_ini ?? 0}{at ? ` / ${at.max_entry}` : ''}</dd>
          <dt>SL hari ini</dt><dd className={`num ${ea.sl_hari_ini ? 'down' : ''}`}>{ea.sl_hari_ini ?? 0}{at ? ` / ${at.max_sl}` : ''}</dd>
          <dt>Zona POI aktif</dt><dd className="num">{ea.poi_aktif ?? 0}{at ? ` · ${at.poi_tf}` : ''}</dd>
          <dt>Akun</dt><dd className="num">{mt5.a ? `${mt5.a.akun.toUpperCase()} ${mt5.a.login}` : '–'}</dd>
        </dl>
      )}
      {ea?.log_terakhir && <p className="ea-log sub">{ea.log_terakhir}</p>}
    </section>
  )
}

// Aturan risiko yang benar-benar dipakai EA (dibaca dari status EA, bukan disalin).
export function KontrolRisiko({ ea: { ea }, mt5, bot }: { ea: Ea; mt5: Mt5; bot: { status: R<BotStatus>; trades: R<BotTrade> } }) {
  const at = ea?.aturan
  const saldo = mt5.a?.saldo
  const s = bot.status.rows?.[0]
  const selesai = (bot.trades.rows ?? []).filter((t) => t.akun === 'demo' && ['TP', 'SL', 'BE'].includes(t.status))
  const totalR = selesai.reduce((x, t) => x + Number(t.r ?? 0), 0)
  const pl = selesai.reduce((x, t) => x + Number(t.pl ?? 0), 0)
  return (
    <section className="card panel" aria-labelledby="risikoTitle">
      <div className="card-head"><h2 id="risikoTitle">Kontrol risiko</h2></div>
      {!ea?.ada || !at ? <p className="sub">Aturan risiko tampil setelah EA SniperBot menulis status.</p> : (
        <dl className="baris-dl">
          <dt>Risiko / trade</dt>
          <dd className="num">{ea.risk_persen?.toFixed(1)}%{saldo != null && ea.risk_persen != null ? <small> ≈ {fmt((saldo * ea.risk_persen) / 100, 2)}</small> : null}</dd>
          <dt>Stop awal</dt><dd className="num">{at.sl_pips} pips</dd>
          <dt>TP minimum</dt><dd className="num">{at.tp_min_pips} pips · 1:{at.rr}</dd>
          <dt>Break-even</dt><dd className="num">+{ea.be_pips} pips</dd>
          <dt>Spread maks</dt><dd className="num">{at.max_spread / 10} pips</dd>
          <dt>Batas harian</dt><dd className="num">{at.max_sl} SL · {at.max_entry} entry</dd>
          <dt>Jeda news USD</dt><dd className="num">±{at.news_menit} menit</dd>
          <dt>Limit kedaluwarsa</dt><dd className="num">{at.expire_menit} menit</dd>
        </dl>
      )}
      <details className="syarat">
        <summary>Syarat pindah ke akun real</summary>
        <p className="sub">Real test demo: {selesai.length} trade selesai, {totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R, P/L {fmt(pl, 2)}.</p>
        {s?.syarat_live ? (
          <ul className="cek">
            {Object.entries(s.syarat_live).map(([k, v]) => <li key={k} className={v ? 'up' : 'sub'}>{v ? 'Lulus' : 'Belum'}: {k}</li>)}
          </ul>
        ) : <p className="sub">{bot.status.error ? `Tabel bot belum ada (${bot.status.error}).` : 'Belum ada data dari jembatan.'}</p>}
      </details>
    </section>
  )
}

// Ringkasan analisis terbaru di bawah chart: arah, status, strategi beserta angka OOS-nya, setup yang sedang aktif.
export function SetupSemasa({ a, aktif, off }: { a: Payload | null; aktif: Jejak[]; off: number }) {
  if (!a) return null
  const b1 = a.bias?.['1H']?.bias, lain = ['30m', '4H', '5m'].filter((k) => a.bias?.[k]).slice(0, 2)
  const st = a.strategi, k = st?.kandidat?.find((x) => x.strategy === st.terpilih && x.regime === 'semua') ?? st?.kandidat?.[0]
  const s = aktif[0]?.s
  const arah = (v?: string) => (/bear/i.test(v ?? '') ? 'down' : /bull/i.test(v ?? '') ? 'up' : '')
  return (
    <dl className="semasa" aria-label="Setup semasa">
      <div>
        <dt>Bias 1H</dt>
        <dd className={`semasa-val ${arah(b1)}`}>{b1 ?? '–'}</dd>
        <dd className="sub">{lain.map((x) => `${x} ${a.bias?.[x]?.bias}`).join(' · ') || ' '}</dd>
      </div>
      <div>
        <dt>Status</dt>
        <dd className="semasa-val">{a.status}</dd>
        <dd className="sub">Keyakinan {a.keyakinan || '–'}</dd>
      </div>
      <div>
        <dt>Strategi</dt>
        <dd className="semasa-val">{st?.terpilih ?? '–'}</dd>
        <dd className="sub num">{k && k.expectancy != null ? `OOS ${k.expectancy > 0 ? '+' : ''}${k.expectancy.toFixed(2)}R · ${k.trades} trade · WR ${Math.round((k.winrate ?? 0) * 100)}%` : 'belum ada data OOS'}</dd>
      </div>
      <div>
        <dt>Setup aktif</dt>
        <dd className={`semasa-val num ${s ? (s.side === 'sell' ? 'down' : 'up') : ''}`}>{s ? `${s.side.toUpperCase()} ${fmt(s.entry + off, 2)}` : 'Tidak ada'}</dd>
        <dd className="sub num">{s ? `SL ${fmt(s.sl + off, 2)} · TP ${fmt((s.tp?.[0] ?? 0) + off, 2)}${aktif.length > 1 ? ` · +${aktif.length - 1} lagi` : ''}` : 'menunggu zona'}</dd>
      </div>
    </dl>
  )
}
