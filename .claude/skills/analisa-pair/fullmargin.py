"""Analisa full margin (all-in): sniper 1 shot 0 floating di jendela volatil. Analisis saja, tidak pernah kirim order.

Pakai:  python .claude/skills/analisa-pair/fullmargin.py XAUUSD [--json out.json]
1. Ukuran all-in dari akun MT5 live: lot maks dari free margin, $/pip, jarak SL yang menghabiskan saldo,
   jarak ke stop-out broker (margin_so_so).
2. Jendela volatil 7 hari: news USD high impact (importance 1), London open, NY open (jam WIB sadar DST),
   dinilai dari candle M5 MT5: median range 30 menit pertama dan % kejadian gerak >= 100 pips dalam 2 jam.
3. Statistik "0 floating" sniper (tanpa delta) di riwayat M1 MT5: trade TP dengan floating <= 5 pips,
   win rate dan expectancy kalau SL dipaksa 10/15/20 pips (entry dan TP tetap), per jenis jendela.
4. Kandidat sekarang: sinyal sniper aktif / zona POI belum tersentuh, dicek terhadap batas SL all-in,
   plus rencana news high impact terdekat. Tidak ada yang muat -> TIDAK ADA SHOT.
MT5 mati -> ukuran "tidak tersedia", statistik tetap dari cache candle.
Self-check: python fullmargin.py --selftest
"""
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
from bisect import bisect_left

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import simulasi  # noqa: E402
from indikator import atr, kolom  # noqa: E402
from regime import STEP, WIB, jam_sesi, sesi  # noqa: E402
from validasi import BATAL_FRAC, TEMBUS  # noqa: E402

PIP = 0.10
SPREAD, SLIP = 0.36, 0.10          # biaya histori HFM per trade
SL_PAKSA = [10, 15, 20]            # pips
NOL_FLOAT = 0.5                    # floating <= $0.5 (5 pips) = "0 floating"
GERAK = 10.0                       # 100 pips
NEWS_MENIT = 60
HORIZON = 14 * 1440                # candle 1m maks per simulasi trade
DEKAT_ZONA = 30.0                  # zona POI ditampilkan kalau <= $30 dari harga
HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def wib(t):
    d = dt.datetime.fromtimestamp(t + WIB, dt.timezone.utc)
    return f"{HARI[d.weekday()]} {d:%d %b %H:%M} WIB"


def pips(d):
    return round(abs(d) / PIP, 1)


# ---------- 1. ukuran all-in ----------
def ukuran_allin(saldo, margin_bebas, margin_lot, tick_size, tick_value, step, vmin, vmax, so_mode, so, spread=0.0):
    """Lot maks dari free margin dan jarak bahayanya (pips). so_mode 0 = persen margin, 1 = uang."""
    lot = min(math.floor(margin_bebas / margin_lot / step + 1e-9) * step, vmax)
    lot = round(lot, 2)
    if lot < vmin:
        return {"lot": 0.0, "usdPip": None, "slHabis": None, "stopOut": None, "batasSl": None,
                "marginLot": margin_lot, "spreadPips": pips(spread)}
    usd_pip = lot * PIP / tick_size * tick_value
    margin = lot * margin_lot
    ekuitas_so = margin * so / 100 if so_mode == 0 else so
    habis, stopout = saldo / usd_pip, (saldo - ekuitas_so) / usd_pip
    r1 = lambda x: round(x, 1)
    return {"lot": lot, "usdPip": round(usd_pip, 2), "slHabis": r1(habis), "stopOut": r1(stopout),
            "ekuitasStopOut": round(ekuitas_so, 2), "marginPakai": round(margin, 2), "marginLot": round(margin_lot, 2),
            "spreadPips": pips(spread), "batasSl": r1(min(habis, stopout) - pips(spread))}


def ukuran_live(mt5, nama):
    ai, si, tick = mt5.account_info(), mt5.symbol_info(nama), mt5.symbol_info_tick(nama)
    m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, nama, 1.0, tick.ask)
    if not m:
        raise RuntimeError(f"order_calc_margin gagal: {mt5.last_error()}")
    u = ukuran_allin(ai.balance, ai.margin_free, m, si.trade_tick_size, si.trade_tick_value, si.volume_step,
                     si.volume_min, si.volume_max, ai.margin_so_mode, ai.margin_so_so, tick.ask - tick.bid)
    return {**u, "saldo": ai.balance, "ekuitas": ai.equity, "marginBebas": ai.margin_free, "leverage": ai.leverage,
            "soPersen": ai.margin_so_so, "soMode": "persen" if ai.margin_so_mode == 0 else "uang",
            "bid": tick.bid, "ask": tick.ask, "akun": {0: "demo", 2: "real"}.get(ai.trade_mode, ai.trade_mode)}


# ---------- 2. jendela volatil ----------
def gerak(idx, T):
    """Dari open candle M5 di T: (range 30 menit, gerak maks dari open dalam 2 jam) dalam $, atau None."""
    if T not in idx:
        return None
    o0 = idx[T][1]
    b30 = [idx[T + i * 300] for i in range(6) if T + i * 300 in idx]
    b120 = [idx[T + i * 300] for i in range(24) if T + i * 300 in idx]
    if len(b30) < 4 or len(b120) < 16:
        return None
    return (max(r[2] for r in b30) - min(r[3] for r in b30),
            max(max(r[2] for r in b120) - o0, o0 - min(r[3] for r in b120)))


def ringkas_gerak(g):
    g = [x for x in g if x]
    if not g:
        return {"n": 0, "range30": None, "p100": None}
    return {"n": len(g), "range30": pips(statistics.median(x[0] for x in g)),
            "p100": round(sum(x[1] >= GERAK for x in g) / len(g), 2)}


def hari_jam(t):
    return ((t + WIB) // 86400 + 3) % 7, (t + WIB) % 86400 // 3600


def statistik_slot(idx):
    """{(hari WIB 0=Sen, jam WIB): ringkas_gerak} dari setiap awal jam."""
    kel = {}
    for T in idx:
        if T % 3600 == 0:
            kel.setdefault(hari_jam(T), []).append(gerak(idx, T))
    return {k: ringkas_gerak(v) for k, v in kel.items()}


def kunci(st):
    return (st.get("p100") or 0, st.get("range30") or 0)


def jendela(now, slot, news_depan, news_lalu, idx, hari=7):
    """Jendela 7 hari ke depan, urut waktu, masing-masing dengan statistik. news_*: [(t, [judul])]."""
    out, d0 = [], (now + WIB) // 86400 * 86400 - WIB
    for k in range(hari + 1):
        D = d0 + k * 86400
        _, lon, ny, _ = jam_sesi(D + 12 * 3600)
        for nama, h in (("London open", lon), ("NY open", ny)):
            t = D + h * 3600
            if t >= now and t < now + hari * 86400 and not libur(t):
                out.append({"t": t, "jenis": nama, "alasan": f"pembukaan {nama.split()[0]}",
                            "stat": slot.get(hari_jam(t), ringkas_gerak([]))})
    semua = ringkas_gerak([gerak(idx, t) for t, _ in news_lalu])
    for t, judul in news_depan:
        sama = ringkas_gerak([gerak(idx, u) for u, j in news_lalu if set(j) & set(judul)])
        st = sama if sama["n"] >= 3 else semua
        out.append({"t": t, "jenis": "News", "alasan": ", ".join(judul),
                    "stat": {**st, "dasar": "rilis judul sama" if st is sama else "semua news high impact"},
                    "jamSama": slot.get(hari_jam(t - t % 3600), ringkas_gerak([]))})
    out.sort(key=lambda w: w["t"])
    urut = sorted(out, key=lambda w: kunci(w["stat"]), reverse=True)
    for i, w in enumerate(urut):
        w["peringkat"] = i + 1
    return out


def libur(t):
    import data
    return data.libur(t)


# ---------- 3. statistik 0 floating ----------
def paksa_sl(s, p):
    k = 1 if s["side"] == "buy" else -1
    return {**s, "sl": round(s["entry"] - k * p * PIP, 2)}


def sim_tiap(m1, sigs, cost, expire):
    """Tiap sinyal disimulasikan sendiri (seperti backtest_bot.kandidat)."""
    t, out = [r[0] for r in m1], []
    for s in sigs:
        i = bisect_left(t, s["time"])
        out += simulasi(m1[i:i + HORIZON], [s], 60, expire, cost=cost, tembus=TEMBUS, batal_frac=BATAL_FRAC)
    return out


def tipe_waktu(t, news):
    if any(abs(t - n) <= NEWS_MENIT * 60 for n in news):
        return "news"
    _, lon, ny, _ = jam_sesi(t)
    return "open" if hari_jam(t)[1] in (lon, ny) else "lain"


def statistik_trade(tr):
    n = len(tr)
    if not n:
        return {"n": 0, "menang": 0, "winrate": None, "expR": None, "expPips": None, "nolFloat": None}
    return {"n": n, "menang": sum(x["r_net"] > 0 for x in tr),
            "winrate": round(sum(x["r_net"] > 0 for x in tr) / n, 2),
            "expR": round(sum(x["r_net"] for x in tr) / n, 2),
            "expPips": round(sum(x["r_net"] * abs(x["entry"] - x["sl"]) for x in tr) / n / PIP, 1),
            "nolFloat": round(sum(x["hasil"] == "TP" and x["floating"] <= NOL_FLOAT for x in tr) / n, 2)}


def histori(by, sigs, news, expire):
    """{sl: {semua/news/open/lain/<sesi>: statistik}}; sl 'asli' = SL sniper (30 pips)."""
    cost = SPREAD + SLIP
    out = {}
    for nama, ss in [("asli", sigs)] + [(p, [paksa_sl(s, p) for s in sigs]) for p in SL_PAKSA]:
        tr = sim_tiap(by["1m"], ss, cost, expire)
        kel = {"semua": tr}
        for x in tr:
            kel.setdefault(tipe_waktu(x["masuk"], news), []).append(x)
            kel.setdefault(sesi(x["masuk"]), []).append(x)
        out[nama] = {k: statistik_trade(v) for k, v in kel.items()}
    return out


def sl_terbaik(hist, batas, sl_asli):
    """SL (pips) dengan expectancy histori tertinggi yang muat di batas all-in -> (pips, statistik) atau (None, None)."""
    opsi = [(sl_asli, hist["asli"]["semua"])] + [(p, hist[p]["semua"]) for p in SL_PAKSA]
    opsi = [(p, s) for p, s in opsi if s["n"] and (batas is None or p <= batas)]
    return max(opsi, key=lambda x: x[1]["expR"]) if opsi else (None, None)


# ---------- 4. kandidat ----------
def zona_poi(by, now, p, harga):
    from strategi.sniper import poi_list
    t, o, h, l, c = kolom(by["1m"])
    out = []
    for z in poi_list(by, "scalp", p):
        if z["t_ok"] < now - p["umur_jam"] * 3600:
            continue
        i = bisect_left(t, z["t_ok"])
        buy = z["side"] > 0
        if any((l[k] <= z["hi"]) if buy else (h[k] >= z["lo"]) for k in range(i, len(t))):
            continue   # sudah tersentuh (masuk zona / jebol)
        jarak = harga - z["hi"] if buy else z["lo"] - harga
        if 0 <= jarak <= DEKAT_ZONA:
            out.append({**z, "jarak": round(jarak, 2), "kedaluwarsa": z["t_ok"] + p["umur_jam"] * 3600})
    return out


def muat():
    """-> (by_tf, ukuran atau None, catatan[]). Candle MT5; kalau MT5 tidak tersedia, cache MT5_XAUUSD."""
    import data
    catat, uk = [], None
    try:
        import mt5_link
        mt5_link.sambung()
        uk = ukuran_live(mt5_link.modul(), mt5_link.simbol_emas())
    except Exception as x:
        catat.append(f"MT5 tidak tersedia ({x}); ukuran all-in tidak tersedia")
    try:
        if uk is None:
            raise RuntimeError("MT5 mati")
        by = data.load("XAUUSD", TFS, source="mt5", spot=False)
    except Exception:
        sym = "MT5_XAUUSD"
        by = {tf: data._cached(data._path(sym, tf), tf, False)[0] for tf in TFS if tf not in ("4h", "1d")}
        if not by.get("1m"):
            raise SystemExit("cache candle MT5 kosong (data/cache/MT5_XAUUSD_*.json); nyalakan MT5 dulu")
        for tf in ("4h", "1d"):
            by[tf] = data.aggregate([list(r) for r in by["1h"]], STEP[tf])
        catat.append(f"candle dari cache sampai {wib(by['1m'][-1][0])}")
    return data.bersih(by), uk, catat


def ambil_news(now):
    """-> (news lalu [(t, [judul])], news depan, catatan). importance 1 = high impact."""
    import kalender
    try:   # feed memotong di 2000 event: ambil per 30 hari (fetch(back, days) dengan days negatif = rentang lampau)
        raw = {(e["date"], e["title"]): e for k in range(220, 0, -30)
               for e in kalender.fetch(24 * k, min(8, 30 - k))}.values()
    except Exception as x:
        return [], [], [f"kalender gagal ({x}); jendela news tidak dihitung"]
    kel = {}
    for e in raw:
        if e.get("importance") == 1:
            t = int(dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
            kel.setdefault(t, []).append(e)
    lalu = [(t, [e["title"] for e in v]) for t, v in sorted(kel.items()) if t < now]
    depan = [(t, [e["title"] for e in v], v) for t, v in sorted(kel.items()) if now <= t < now + 7 * 86400]
    return lalu, depan, []


def analisa(pair="XAUUSD"):
    import pantau
    from strategi import sniper
    by, uk, catat = muat()
    now = int(time.time())
    closed = {tf: [r for r in rows if r[0] + STEP[tf] <= now] for tf, rows in by.items()}
    harga = uk["bid"] if uk else closed["1m"][-1][4]
    batas = uk["batasSl"] if uk else None
    p = {**sniper.PARAMS, "delta": False}
    sigs = sniper.signals(closed, "scalp", p)
    lalu, depan, c2 = ambil_news(now)
    catat += c2
    idx = {r[0]: r for r in closed["5m"]}
    slot = statistik_slot(idx)
    jd = jendela(now, slot, [(t, j) for t, j, _ in depan], lalu, idx)
    hist = histori(closed, sigs, [t for t, _ in lalu], sniper.EXPIRE_S // 60)
    sl_asli = round(p["sl_jarak"] / PIP)
    sl_pilih, st_pilih = sl_terbaik(hist, batas, sl_asli)

    kand = []
    for s in sigs:
        if s["time"] < now - sniper.EXPIRE_S:
            continue
        terisi, hasil = pantau.lacak(s, closed["1m"], now, sniper.EXPIRE_S)
        if hasil or terisi:
            continue
        x = {"side": s["side"], "entry": s["entry"], "slAsli": s["sl"], "tp": s["tp"][0], "alasan": s["alasan"],
             "slPips": sl_pilih, "sl": paksa_sl(s, sl_pilih)["sl"] if sl_pilih else None,
             "tpPips": pips(s["tp"][0] - s["entry"]), "histori": st_pilih,
             "kedaluwarsa": s["time"] + sniper.EXPIRE_S}
        x["muat"] = None if batas is None else sl_pilih is not None   # None = ukuran akun tidak diketahui
        # filter news analisa-pair (scalp): news high impact dalam 60 menit -> tunggu, bukan shot
        x["newsDekat"] = next((wib(t) for t, _, _ in depan if t - now <= NEWS_MENIT * 60), None)
        kand.append(x)
    zona = zona_poi(closed, now, p, harga)
    a15 = atr(*kolom(closed["15m"])[2:])[-1]
    rencana = None
    if depan:
        t, judul, items = depan[0]
        w = next(w for w in jd if w["jenis"] == "News" and w["t"] == t)
        stop_news = pips(1.5 * a15) if a15 else None
        rencana = {"t": t, "waktu": wib(t), "event": [{k: e.get(k) for k in ("title", "forecast", "previous")} for e in items],
                   "stat": w["stat"], "stopIdealPips": stop_news,
                   "stopMuat": None if batas is None else (stop_news or 0) <= batas,
                   "flat": wib(t - 15 * 60), "pemicuMulai": wib(t + 3 * 60)}
    status = "ADA SHOT" if any(k["muat"] is True and not k["newsDekat"] for k in kand) else "TIDAK ADA SHOT"
    return {"pair": pair, "dibuat": now, "waktu": wib(now), "harga": harga, "status": status, "ukuran": uk,
            "jendela": jd, "histori": hist, "slAsli": sl_asli, "slPilih": sl_pilih, "kandidat": kand, "zona": zona,
            "rencanaNews": rencana, "nSinyal": len(sigs), "dataDari": closed["1m"][0][0], "dataSampai": closed["1m"][-1][0],
            "catatan": catat}


# ---------- 5. keluaran ----------
def teks(r):
    pc = lambda x: "-" if x is None else f"{x * 100:.0f}%"
    L = [f"FULL MARGIN {r['pair']} - {r['waktu']} | harga {r['harga']:,.2f}", f"STATUS: {r['status']}", ""]
    u = r["ukuran"]
    if u and u["lot"]:
        L += [f"Ukuran all-in (akun {u['akun']} ${u['saldo']:,.2f}, leverage akun 1:{u['leverage']}): "
              f"margin 1 lot ${u['marginLot']:,.2f} -> lot maks {u['lot']:.2f}, ${u['usdPip']:.2f}/pip",
              f"  SL habis-saldo {u['slHabis']} pips | stop-out ({u['soPersen']:g}% {u['soMode']}) {u['stopOut']} pips | "
              f"spread {u['spreadPips']} pips | batas SL pakai {u['batasSl']} pips"]
    elif u:
        L.append(f"Ukuran all-in: free margin ${u['marginBebas']:,.2f} tidak cukup untuk lot minimum")
    else:
        L.append("Ukuran all-in: tidak tersedia (MT5 tidak terhubung)")
    L += ["", "Jendela volatil 7 hari (peringkat dari p>=100 pips dalam 2 jam, lalu range 30 menit):"]
    top = sorted(r["jendela"], key=lambda w: w["peringkat"])[:6]
    for w in sorted(top, key=lambda w: w["t"]):
        s = w["stat"]
        L.append(f"  #{w['peringkat']:<2} {wib(w['t'])}  {w['jenis']:<11} {w['alasan'][:60]}  "
                 f"range30 {s['range30']} pips, >=100 pips {pc(s['p100'])} (n {s['n']}{', ' + s['dasar'] if 'dasar' in s else ''})")
    h = r["histori"]
    L += ["", f"Histori sniper tanpa delta, M1 HFM {wib(r['dataDari'])[4:10]} - {wib(r['dataSampai'])[4:10]} "
              f"({r['nSinyal']} sinyal, biaya {SPREAD + SLIP:.2f}/trade):",
          f"  {'SL':<9}{'semua':<26}{'news +-60m':<26}{'jam open L/NY':<26}{'lain':<26}"]
    for k in ["asli"] + SL_PAKSA:
        nama = f"{r['slAsli']}p asli" if k == "asli" else f"{k}p"
        sel = []
        for g in ("semua", "news", "open", "lain"):
            s = h[k].get(g, {"n": 0})
            sel.append(f"{s['menang']}/{s['n']} {pc(s['winrate'])} {s['expR']:+.2f}R" if s["n"] else "-")
        L.append(f"  {nama:<9}" + "".join(f"{x:<26}" for x in sel))
    nf = h["asli"]
    L.append("  0 floating (TP, floating <= 5 pips): " + ", ".join(
        f"{g} {pc(nf[g]['nolFloat'])} (n {nf[g]['n']})" for g in ("semua", "news", "open", "lain", "Asia", "London", "NY")
        if g in nf))
    if r["slPilih"]:
        s = h["asli" if r["slPilih"] == r["slAsli"] else r["slPilih"]]["semua"]
        L.append(f"  SL terbaik yang muat: {r['slPilih']} pips -> menang {s['menang']}/{s['n']}, {s['expR']:+.2f}R "
                 f"({s['expPips']:+.1f} pips/trade). Sampel kecil, bukan kepastian.")
    else:
        L.append("  Tidak ada ukuran SL yang muat di batas all-in dengan histori.")
    L += ["", "Kandidat shot:"]
    for k in r["kandidat"]:
        st = k["histori"] or {}
        L.append(f"  {k['side'].upper()} limit {k['entry']:,.2f} | SL {k['sl'] if k['sl'] else '-'} ({k['slPips']} pips) | "
                 f"TP {k['tp']:,.2f} ({k['tpPips']} pips) | { {True: 'MUAT', False: 'TIDAK MUAT', None: 'batas all-in tidak diketahui'}[k['muat']] } | histori menang "
                 f"{st.get('menang')}/{st.get('n')}, 0-floating {pc(st.get('nolFloat'))} | batal {wib(k['kedaluwarsa'])}")
        L.append(f"    {k['alasan']}")
        if k["newsDekat"]:
            L.append(f"    news high impact {k['newsDekat']} dalam 60 menit -> tunggu news, bukan shot")
    if not r["kandidat"]:
        L.append("  tidak ada sinyal sniper aktif")
    for z in r["zona"]:
        L.append(f"  Zona POI {'demand' if z['side'] > 0 else 'supply'} {z['lo']:,.2f}-{z['hi']:,.2f} ({z['jarak']:.2f} dari harga, "
                 f"{z['alasan']}): tunggu masuk zona -> sweep -> CHoCH 1m, berlaku sampai {wib(z['kedaluwarsa'])}")
    n = r["rencanaNews"]
    if n:
        ev = "; ".join(f"{e['title']} (F {e['forecast']}, P {e['previous']})" for e in n["event"])
        s = n["stat"]
        L += ["", f"News terdekat: {n['waktu']} - {ev}",
              f"  histori: range30 {s['range30']} pips, >=100 pips dalam 2 jam {pc(s['p100'])} (n {s['n']}, {s['dasar']})",
              f"  flat sejak {n['flat']}; tandai high/low candle 1m pertama; entry mulai {n['pemicuMulai']} hanya tembus + "
              f"retest dan US10Y searah; stop ideal 1.5xATR15m = {n['stopIdealPips']} pips "
              + {True: "(muat batas all-in)", None: "(batas all-in tidak diketahui)",
                 False: "(MELEBIHI batas all-in -> lewati atau kecilkan lot)"}[n["stopMuat"]]]
    if r["catatan"]:
        L += ["", "Catatan: " + "; ".join(r["catatan"])]
    L.append("PERINGATAN: all-in = satu kali kena SL atau stop-out menghabiskan hampir seluruh saldo.")
    return "\n".join(L)


def _selftest():
    # $100, leverage 1:2000, harga 4120 -> margin 1 lot 206
    u = ukuran_allin(100, 100, 4120 * 100 / 2000, 0.01, 1.0, 0.01, 0.01, 50, 0, 20)
    assert u["lot"] == 0.48 and u["usdPip"] == 4.8, u
    assert abs(u["slHabis"] - 20.8) < 0.05 and abs(u["stopOut"] - 16.7) < 0.05, u   # (100 - 98.88*0.2)/4.8
    assert u["batasSl"] == u["stopOut"]
    u = ukuran_allin(100, 100, 206, 0.01, 1.0, 0.01, 0.01, 50, 1, 10, spread=0.4)      # stop-out mode uang $10
    assert u["stopOut"] == 18.8 and u["batasSl"] == 14.8, u                             # (100-10)/4.8 - 4 pips spread
    u = ukuran_allin(100, 100, 824.93, 0.01, 1.0, 0.01, 0.01, 50, 0, 20)                # margin HFM emas nyata
    assert u["lot"] == 0.12 and u["usdPip"] == 1.2 and abs(u["slHabis"] - 83.3) < 0.05, u
    assert ukuran_allin(5, 5, 824.93, 0.01, 1.0, 0.01, 0.01, 50, 0, 20)["lot"] == 0.0

    # jendela: Agustus 2026 (DST: London 14, NY 19 WIB); Selasa 19:00 WIB bergerak $15, jam lain $0.5
    t0 = 1785715200 - WIB          # Senin 3 Agu 2026 00:00 WIB
    idx = {}
    for i in range(21 * 288):
        T = t0 + i * 300
        hari, jam = hari_jam(T)
        naik = 15 / 24 if (hari, jam) in ((1, 19), (1, 20)) else 0.0
        o = idx[T - 300][4] if T - 300 in idx else 1000.0
        idx[T] = [T, o, o + naik + 0.1, o - 0.1, o + naik, 1]
    slot = statistik_slot(idx)
    assert slot[(1, 19)]["p100"] == 1.0 and slot[(1, 19)]["n"] == 3, slot[(1, 19)]
    assert slot[(2, 14)]["p100"] == 0.0 and slot[(2, 14)]["range30"] == 2.0, slot[(2, 14)]
    now = t0 + 21 * 86400          # Senin minggu ke-4
    jd = jendela(now, slot, [], [], idx)
    assert [w["jenis"] for w in jd[:2]] == ["London open", "NY open"] and len(jd) == 10, [w["jenis"] for w in jd]
    top = min(jd, key=lambda w: w["peringkat"])
    assert top["jenis"] == "NY open" and hari_jam(top["t"]) == (1, 19), top
    # news: statistik dari rilis judul sama kalau >= 3, selain itu semua news
    lalu = [(t0 + d * 86400 + 19 * 3600, ["CPI"]) for d in (1, 8, 15)]   # Selasa 19:00 WIB
    w = [x for x in jendela(now, slot, [(now + 86400 + 12 * 3600, ["CPI"])], lalu, idx) if x["jenis"] == "News"][0]
    assert w["stat"]["dasar"] == "rilis judul sama" and w["stat"]["p100"] == 1.0 and w["peringkat"] <= 2, w

    # SL paksa, tipe waktu, statistik trade, pilihan SL
    s = {"time": 0, "side": "sell", "entry": 100.0, "sl": 103.0, "tp": [90.0]}
    assert paksa_sl(s, 15)["sl"] == 101.5 and paksa_sl({**s, "side": "buy"}, 10)["sl"] == 99.0
    news_t = t0 + 86400 + 12 * 3600
    assert tipe_waktu(news_t + 3600, [news_t]) == "news" and tipe_waktu(t0 + 14 * 3600 + 600, []) == "open"
    assert tipe_waktu(t0 + 3 * 3600, []) == "lain"
    tr = [{"r_net": 3.0, "entry": 100, "sl": 99, "hasil": "TP", "floating": 0.3},
          {"r_net": -1.1, "entry": 100, "sl": 99, "hasil": "SL", "floating": 1.0}]
    st = statistik_trade(tr)
    assert st["winrate"] == 0.5 and st["nolFloat"] == 0.5 and st["expPips"] == 9.5, st
    hist = {"asli": {"semua": {**st, "expR": 0.2}}, 10: {"semua": {**st, "expR": 0.5}},
            15: {"semua": {**st, "expR": 0.9}}, 20: {"semua": {**st, "expR": 0.1}}}
    assert sl_terbaik(hist, 16.7, 30)[0] == 15 and sl_terbaik(hist, 12, 30)[0] == 10
    assert sl_terbaik(hist, None, 30)[0] == 15 and sl_terbaik(hist, 5, 30) == (None, None)
    # simulasi: buy 100, SL paksa 10 pips (99), TP 110; harga turun 0.5 lalu naik -> TP, floating 0.5
    m1 = [[i * 60, 100.5, 100.6, 100.4, 100.5, 0] for i in range(3)]
    m1 += [[180, 100.5, 100.5, 99.5, 99.6, 0], [240, 99.6, 111, 99.6, 110.5, 0]]
    x = sim_tiap(m1, [paksa_sl({"time": 60, "side": "buy", "entry": 100.0, "sl": 97.0, "tp": [110.0]}, 10)], 0.46, 60)
    assert x and x[0]["hasil"] == "TP" and x[0]["floating"] == 0.5 and x[0]["sl"] == 99.0, x
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    pair = next((a for a in args if not a.startswith("--") and not a.endswith(".json")), "XAUUSD").upper()
    if pair != "XAUUSD":
        sys.exit("fullmargin: hanya XAUUSD (simbol emas MT5)")
    hasil = analisa(pair)
    print(teks(hasil))
    if "--json" in args:
        with open(args[args.index("--json") + 1], "w", encoding="utf-8") as f:
            json.dump(hasil, f, indent=1, default=str)
