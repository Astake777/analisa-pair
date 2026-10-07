import { useEffect, useMemo, useRef, useState } from 'react'
import Chart from './chart/Chart'
import type { Band } from './chart/zones'
import { TFS, useLiveFeed, type TF } from './feed'
import { useLiveDrivers } from './feed/yahoo'
import { age, fmt, marketOpen, signed, statusKind, store, wibTime } from './lib/format'
import { FIXTURE, useAnalyses, useSetupLog, useBacktest, useMacro, useNewsOutlook, type Mode } from './lib/supabase'
import { Amd, Bias, Calendar, Headlines, History, Levels, Notes, PrediksiNews, Strategi } from './panels/Analysis'
import { ASET, Drivers, Makro } from './panels/Drivers'
import { Menjelang, Outlook } from './panels/Outlook'
import { RekamJejak, Setups } from './panels/Setups'

const PAIR = 'XAUUSD'
const MODES: Mode[] = ['scalp', 'intraday', 'swing']
const NO_LEVELS: { price: number; label: string; kind: string }[] = []
const hasNotif = typeof Notification !== 'undefined'

// Dua bunyi pendek; tanpa file audio.
function bunyi() {
  try {
    const ctx = new AudioContext()
    ;[0, 0.25].forEach((t) => {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.frequency.value = 880
      g.gain.setValueAtTime(0.15, ctx.currentTime + t)
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.2)
      o.connect(g).connect(ctx.destination)
      o.start(ctx.currentTime + t)
      o.stop(ctx.currentTime + t + 0.2)
    })
  } catch { /* browser memblokir audio sebelum ada interaksi */ }
}

const ICON = {
  sun: <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>,
  moon: <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 9.8A5.8 5.8 0 0 1 6.2 2.5a5.8 5.8 0 1 0 7.3 7.3z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg>,
  wait: <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 4.5V8l2.5 1.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>,
  go: <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M5 8.2l2 2 4-4.4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  none: <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M5 8h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>,
}

export default function App() {
  const [mode, setMode] = useState<Mode>(() => {
    const m = store.get('mode') as Mode
    return MODES.includes(m) ? m : 'intraday'
  })
  const [tf, setTf] = useState<TF>('M15')
  const [offText, setOffText] = useState(() => store.get(`offset:${PAIR}`) ?? '0')
  const off = Number(offText) || 0
  const [pick, setPick] = useState({ key: '', idx: 0 })
  const [now, setNow] = useState(() => Date.now())
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const t = store.get('theme')
    return t === 'light' || t === 'dark' ? t : matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    store.set('theme', theme)
  }, [theme])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(t)
  }, [])

  const feed = useLiveFeed(tf)
  const an = useAnalyses(PAIR, mode)
  const bt = useBacktest(PAIR, mode)
  const news = useNewsOutlook(PAIR)
  const macro = useMacro()
  const log = useSetupLog(PAIR)
  const drv = useLiveDrivers(ASET)

  const [izin, setIzin] = useState(() => (hasNotif ? Notification.permission : 'denied'))
  const [kabar, setKabar] = useState<string | null>(null)
  const dilihat = useRef<Partial<Record<Mode, Set<string>>>>({})

  // Setup sniper baru di baris analisis terbaru -> banner, bunyi, notifikasi browser. Muatan pertama tiap mode hanya dicatat.
  useEffect(() => {
    const sn = (an.rows?.[0]?.payload.setups ?? []).filter((x) => x.label === 'sniper')
    if (!an.rows) return
    const kunci = (x: (typeof sn)[number]) => `${x.side}-${x.entry}`
    const seen = dilihat.current[mode]
    if (!seen) {
      dilihat.current[mode] = new Set(sn.map(kunci))
      return
    }
    const baru = sn.find((x) => !seen.has(kunci(x)))
    sn.forEach((x) => seen.add(kunci(x)))
    if (!baru) return
    const text = `${baru.side.toUpperCase()} limit ${fmt(baru.entry, 2)}, SL ${fmt(baru.sl, 2)}, TP ${fmt(baru.tp?.[0], 2)}`
    setKabar(text)
    bunyi()
    if (hasNotif && Notification.permission === 'granted') new Notification('Setup sniper XAUUSD', { body: text })
  }, [an.rows, mode])

  const latest = an.rows?.[0] ?? null
  const a = latest?.payload ?? null
  const akey = latest ? `${latest.id ?? ''}${latest.created_at}` : ''
  const idx = pick.key === akey ? pick.idx : 0
  const setup = a?.setups?.[idx] ?? null

  const zones = useMemo<Band[]>(() => {
    const z: Band[] = [...(a?.zones ?? [])]
    const r = a?.amd?.rangeAsia
    if (r) z.push({ lo: r.lo, hi: r.hi, side: 'range', label: 'Range Asia' })
    return z
  }, [a])

  const spot = feed.last ?? a?.price ?? null
  const price = spot == null ? null : spot + off
  const lastBar = feed.bars.at(-1)
  const base = lastBar && feed.bars.findLast((b) => b.time <= lastBar.time - 86400)
  const chg = lastBar && base ? lastBar.close - base.close : null

  const feedWarn =
    feed.status === 'error' ? 'Feed harga gagal tersambung, mencoba lagi'
    : feed.status === 'reconnecting' ? 'Feed harga terputus, menyambung ulang'
    : feed.status === 'live' && feed.lastTick && marketOpen(new Date(now)) && now - feed.lastTick > 30000
      ? 'Feed tertunda: tidak ada tick lebih dari 30 detik'
      : null

  const anStale = latest ? now - Date.parse(latest.created_at) > 2 * 3600e3 : false
  const anAge = latest ? age(latest.created_at) : null
  const kind = statusKind(a?.status)
  const modeName = mode.charAt(0).toUpperCase() + mode.slice(1)

  const pickMode = (m: Mode) => {
    setMode(m)
    store.set('mode', m)
  }

  return (
    <div className="wrap">
      <header className="bar">
        <div className="brand">Analisa <b>Pair</b></div>
        <div className="tabs" role="group" aria-label="Pilih mode">
          {MODES.map((m) => (
            <button key={m} type="button" className="tab" aria-pressed={m === mode} onClick={() => pickMode(m)}>{m.toUpperCase()}</button>
          ))}
        </div>
        <div className="spacer" />
        <label className="offset">Selisih ke harga broker
          <input
            type="number" step="0.1" inputMode="decimal" value={offText}
            onChange={(e) => { setOffText(e.target.value); store.set(`offset:${PAIR}`, e.target.value) }}
          />
        </label>
        {izin === 'default' && (
          <button type="button" className="theme-btn" onClick={() => Notification.requestPermission().then(setIzin)}>
            Aktifkan notifikasi setup
          </button>
        )}
        <button
          type="button" className="theme-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          aria-label={theme === 'dark' ? 'Ganti ke tema terang' : 'Ganti ke tema gelap'}
        >
          {theme === 'dark' ? ICON.sun : ICON.moon}
          {theme === 'dark' ? 'Terang' : 'Gelap'}
        </button>
        <div className={`fresh${anStale ? ' stale' : ''}`} aria-live="polite">
          <span className="dot" />
          <span>{anAge ? (anStale ? `Analisis ${anAge}, jalankan ulang /analisa-pair` : `Analisis ${anAge}`) : an.rows ? 'Belum ada analisis' : 'Menunggu data'}</span>
        </div>
      </header>

      {FIXTURE && (
        <div className="banner" role="note">
          <b>Contoh data: Supabase belum tersambung</b>
          <span className="sub">
            Analisis di bawah adalah contoh bentuk, bukan analisis nyata. Isi <code>VITE_SUPABASE_URL</code> dan <code>VITE_SUPABASE_ANON_KEY</code> di <code>.env</code> lalu jalankan ulang <code>npm run dev</code>. Harga live tetap asli.
          </span>
        </div>
      )}

      {kabar && (
        <div className="kabar" role="alert">
          <span><b>Setup sniper baru:</b> {kabar}</span>
          <button type="button" className="theme-btn" onClick={() => setKabar(null)}>Tutup</button>
        </div>
      )}

      <section className="hero">
        <div>
          <div className="pair">{PAIR} · {modeName}</div>
          <div>
            <span className="price num">{fmt(price, 2)}</span>
            {chg != null && base && (
              <span className={`chg num ${chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat'}`}>
                {chg > 0 ? '▲' : chg < 0 ? '▼' : ''} {signed(chg)} ({signed((chg / base.close) * 100, 2)}%) {tf === 'W1' ? 'minggu ini' : tf === 'D1' ? 'sejak 07:00 WIB' : '24j'}
              </span>
            )}
          </div>
          <div className="live-row">
            <span className={`badge${feed.status === 'live' ? '' : ' off'}`}><span className="dot" aria-hidden="true" />{feed.label || 'Menghubungkan feed'}</span>
            <span className="sub num" aria-live="off">{feed.lastTick ? `Tick ${wibTime(new Date(feed.lastTick).toISOString(), false)}` : 'Menunggu tick'}</span>
            {feedWarn && <span className="warn" role="status">{feedWarn}</span>}
          </div>
          {latest && <div className="sub">Dianalisis {wibTime(latest.created_at)}</div>}
        </div>
        {a && (
          <div className="status-block">
            <span className={`pill ${kind}`}>{ICON[kind]}{a.status}</span>
            <span className="sub">Keyakinan: {a.keyakinan || '–'}</span>
          </div>
        )}
      </section>

      <div className="main">
        <div className="left">
          <Menjelang rows={news.rows} now={now} />
          <section className="card" aria-labelledby="chartTitle">
            <div className="card-head">
              <h2 id="chartTitle">Chart dan zona entry</h2>
              <div className="seg" role="group" aria-label="Timeframe">
                {TFS.map((t) => (
                  <button key={t} type="button" aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>
                ))}
              </div>
            </div>
            <Chart
              bars={feed.bars} shift={off} setup={setup} zones={zones} levels={a?.levels ?? NO_LEVELS}
              emptyText={feed.status === 'error' ? 'Feed harga belum tersambung' : 'Memuat harga live'}
            />
            <div className="legend">
              <span><i className="sw" style={{ background: 'var(--ema20)' }} />EMA 20</span>
              <span><i className="sw" style={{ background: 'var(--ema50)' }} />EMA 50</span>
              {feed.bars.length >= 200 && <span><i className="sw dash" />EMA 200</span>}
              {zones.some((z) => z.side === 'sell') && <span><i className="sw box" style={{ background: 'var(--zone-sell)', outline: '1px solid var(--down)' }} />Zona sell</span>}
              {zones.some((z) => z.side === 'buy') && <span><i className="sw box" style={{ background: 'var(--zone-buy)', outline: '1px solid var(--up)' }} />Zona buy</span>}
              {a?.amd?.rangeAsia && <span><i className="sw box" style={{ background: 'var(--zone-range)', outline: '1px solid var(--muted)' }} />Range Asia</span>}
              {setup && (
                <>
                  <span><i className="sw" style={{ background: 'var(--accent)' }} />Entry</span>
                  <span><i className="sw" style={{ background: 'var(--down)' }} />Stop loss</span>
                  <span><i className="sw" style={{ background: 'var(--up)' }} />Target</span>
                </>
              )}
            </div>
          </section>
          {a && (
            <section className="card" aria-labelledby="newsTitle">
              <div className="card-head"><h2 id="newsTitle">Prediksi news</h2></div>
              <PrediksiNews items={a.prediksiNews ?? []} />
            </section>
          )}
        </div>

        <aside className="rail">
          <section className="card" aria-labelledby="setupTitle">
            <div className="card-head"><h2 id="setupTitle">Setup</h2></div>
            {an.error ? (
              <div className="state compact" role="alert">
                <h2>Data tidak bisa dimuat</h2>
                <p>Koneksi ke data analisis gagal ({an.error}). Muat ulang halaman untuk mencoba lagi.</p>
              </div>
            ) : !an.rows ? (
              <div className="state compact" aria-live="polite">
                <div className="spin" aria-hidden="true" />
                <h2>Menghubungkan ke data analisis</h2>
              </div>
            ) : !a ? (
              <div className="state compact">
                <h2>Belum ada analisis {modeName}</h2>
                <p>Jalankan <code>/analisa-pair {PAIR} {mode}</code> di Claude Code. Hasilnya langsung muncul di sini.</p>
              </div>
            ) : (
              <Setups setups={a.setups ?? []} idx={idx} onPick={(i) => setPick({ key: akey, idx: i })} off={off} price={feed.last} log={log.rows} />
            )}
          </section>
          <RekamJejak rows={log.rows} error={log.error} />
          {a?.amd && <Amd amd={a.amd} off={off} />}
        </aside>
      </div>

      <div className="lower">
        {a && (
          <>
            <Bias a={a} />
            <Levels a={a} off={off} price={spot} />
            <Notes notes={a.notes ?? []} />
            <Calendar events={a.events ?? []} />
            <Headlines items={a.headlines ?? []} />
            <Strategi s={a.strategi} rows={bt.rows} error={bt.error} />
          </>
        )}
        {macro.rows && <Makro rows={macro.rows} />}
        <section className="card full" aria-labelledby="drvTitle">
          <div className="card-head">
            <h2 id="drvTitle">Aset pendukung</h2>
            <span className="sub">Yahoo, diperbarui tiap 20 detik dan bisa tertunda beberapa menit · perubahan 24 jam{setup ? ` · dibanding ${setup.side.toUpperCase()} di chart` : ''}</span>
          </div>
          <Drivers items={drv} side={setup?.side} />
        </section>
        <Outlook rows={news.rows} error={news.error} />
        {an.rows && an.rows.length > 0 && <History rows={an.rows} off={off} />}
      </div>

      <p className="foot">
        Harga live dari {feed.source === 'oanda' ? 'OANDA XAU_USD (mid)' : 'XAUT Binance yang dikoreksi ke spot gold-api'}. Level dan zona dalam harga spot XAU.
        Isi selisih ke harga broker di atas supaya chart dan level cocok dengan platformmu. Ini analisis teknikal, bukan nasihat keuangan.
      </p>
    </div>
  )
}
