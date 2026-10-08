"""Setup bulanan: kandidat trade hari ini berperingkat validasi + status hari (W/L) + status bulan (growth).

Pakai:  python setup_bulanan.py [PAIR] [--json out.json]     (dari root repo, candle broker MT5)
Kandidat: sinyal aktif tiap strategi (belum terisi/kedaluwarsa), zona POI sniper 15m yang masih hidup
(pending: tunggu sweep + CHoCH 1m), dan setup engine setup.py mode scalp + intraday.
Grade dari laporan validasi.py (broker mt5 dulu, lalu umum): A = lolos >= 6/7 kriteria dan expectancy OOS > 0,
B = expectancy OOS > 0, C = belum teruji / expectancy <= 0 (bukan setup kuat).
Aturan hari (WIB, semua magic): 0 loss -> semua kandidat boleh; 1 loss -> hanya #1 dan hanya grade A;
>= 2 loss -> STOP. Menang tidak menghentikan. |P/L| < 0.5% saldo = BE.
Analisis saja: script ini tidak pernah mengirim order.
Self-check: python setup_bulanan.py --selftest
"""
import datetime as dt
import glob
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regime import STEP  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
WIB = 7 * 3600
MAKS_RUGI = 2
BE_FRAC = 0.005        # |P/L| < 0.5% saldo = break-even
ZONA_ATR_D = 1.5       # zona POI ditampilkan kalau dalam 1.5 x ATR harian dari harga
NEWS_MENIT = 60
URUT_GRADE = {"A": 0, "B": 1, "C": 2}
r2 = lambda x: round(x, 2)


# ---- logika murni (diuji _selftest, tanpa MT5) ----
def nilai_grade(lap):
    """lap: {lolos, exp} dari laporan validasi atau None -> 'A' / 'B' / 'C'."""
    if not lap or lap.get("exp") is None or lap["exp"] <= 0:
        return "C"
    return "A" if lap["lolos"] >= 6 else "B"


def kunci(k):
    """Urutan kandidat: grade, expectancy x winrate, skor konfluensi, jarak ke harga."""
    return (URUT_GRADE[k["grade"]], -(k.get("exp") or 0) * (k.get("winrate") or 0), -(k.get("skor") or 0),
            k.get("jarak", 0))


def hasil_trade(pl, saldo):
    return "BE" if abs(pl) < BE_FRAC * saldo else ("W" if pl > 0 else "L")


def aturan_hari(pls, saldo):
    """pls: P/L bersih trade yang tutup hari ini -> {menang, kalah, be, pl, mode, status}."""
    h = [hasil_trade(p, saldo) for p in pls]
    n = {x: h.count(x) for x in ("W", "L", "BE")}
    if n["L"] >= MAKS_RUGI:
        mode, status = "stop", f"STOP hari ini ({n['L']} loss)."
    elif n["L"] == 1:
        mode, status = "terbaik-A", "1 loss: trade berikutnya hanya kandidat #1 dan hanya grade A."
    else:
        mode, status = "semua", "Belum ada loss: semua kandidat boleh (grade C tetap bukan setup kuat)."
    return {"menang": n["W"], "kalah": n["L"], "be": n["BE"], "pl": r2(sum(pls)), "mode": mode, "status": status}


def izinkan(kandidat, mode):
    """Tandai k['boleh'] pada kandidat yang sudah diurutkan."""
    for i, k in enumerate(kandidat):
        k["boleh"] = mode == "semua" or (mode == "terbaik-A" and i == 0 and k["grade"] == "A")
    return kandidat


def trade_berikutnya(kandidat, aturan):
    if aturan["mode"] == "stop":
        return "Tidak ada. STOP hari ini: sudah 2 loss."
    k = next((k for k in kandidat if k["boleh"] and k["grade"] != "C"), None)
    if not k:
        if aturan["mode"] == "terbaik-A":
            return "Tunggu setup peluang tertinggi (grade A); kandidat #1 sekarang bukan grade A."
        return "Belum ada setup teruji hari ini; tunggu. Kandidat grade C bukan setup kuat."
    no = kandidat.index(k) + 1
    tag = "" if k["grade"] == "A" else " (grade B: OOS positif tapi belum lolos >= 6/7 kriteria, lot kecil)"
    return f"#{no} {k['strategi']} {k['side'].upper()} {k['entry_teks']}, trigger: {k['trigger']}{tag}"


def hari_bursa(a, b):
    """Jumlah hari Senin-Jumat dari tanggal a sampai b (inklusif)."""
    return sum((a + dt.timedelta(d)).weekday() < 5 for d in range((b - a).days + 1)) if b >= a else 0


def status_bulan(saldo, pl_bulan, hari):
    """hari: date WIB hari ini -> modal awal, growth, hari lewat/sisa, proyeksi naif (rata-rata harian majemuk)."""
    akhir = (hari.replace(day=28) + dt.timedelta(4)).replace(day=1) - dt.timedelta(1)
    lewat, sisa = hari_bursa(hari.replace(day=1), hari), hari_bursa(hari + dt.timedelta(1), akhir)
    modal = saldo - pl_bulan
    g = pl_bulan / modal if modal > 0 else 0.0
    harian = (1 + g) ** (1 / lewat) - 1 if lewat and g > -1 else 0.0
    proyeksi = (1 + g) * (1 + harian) ** sisa - 1
    return {"modal_awal": r2(modal), "saldo": r2(saldo), "pl": r2(pl_bulan), "growth_pct": r2(g * 100),
            "hari_lewat": lewat, "sisa_hari": sisa, "harian_pct": round(harian * 100, 3),
            "proyeksi_pct": r2(proyeksi * 100), "proyeksi_saldo": r2(modal * (1 + proyeksi))}


def posisi_tutup(deals, buka, ke_utc):
    """deals MT5 -> [(waktu tutup UTC, P/L bersih)] per posisi yang sudah tutup (semua magic, tanpa deal saldo)."""
    per = {}
    for d in deals:
        if d.type in (0, 1):
            per.setdefault(d.position_id, []).append(d)
    out = []
    for pid, ds in per.items():
        keluar = [d for d in ds if d.entry in (1, 3)]
        if keluar and pid not in buka:
            pl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in ds)
            out.append((ke_utc(max(d.time for d in keluar)), pl))
    return out


def zona_hidup(pois, m1, now, p, sinyal):
    """POI sniper yang belum hangus: umur < umur_jam, belum jebol sisi jauh, belum dipakai CHoCH.
    -> [poi + {disentuh}]."""
    t, h, l, c = [r[0] for r in m1], [r[2] for r in m1], [r[3] for r in m1], [r[4] for r in m1]
    dipakai = {(x["side"], tuple(x["poi"])) for x in sinyal}
    out, lihat = [], set()
    for z in pois:
        key = ("buy" if z["side"] > 0 else "sell", (r2(z["lo"]), r2(z["hi"])))
        if z["t_ok"] > now or now > z["t_ok"] + p["umur_jam"] * 3600 or key in dipakai or key in lihat:
            continue
        lihat.add(key)
        idx = [k for k in range(len(t)) if t[k] >= z["t_ok"] and t[k] + 60 <= now]
        buy = z["side"] > 0
        if any((c[k] < z["lo"] - p["pad"]) if buy else (c[k] > z["hi"] + p["pad"]) for k in idx):
            continue
        out.append({**z, "disentuh": any((l[k] <= z["hi"]) if buy else (h[k] >= z["lo"]) for k in idx)})
    return out


def kartu_zona(z, p, price):
    """Zona pending sniper: entry/SL baru pasti setelah sweep; beri pita entry dan aturan SL/TP."""
    buy, pad, sj = z["side"] > 0, p["pad"], p["sl_jarak"]
    band = [z["lo"] - 2 * pad + sj, z["hi"] - pad + sj] if buy else [z["lo"] + pad - sj, z["hi"] + 2 * pad - sj]
    tp = max(p["tp_min"], p["rr"] * sj)
    lo, hi = z["lo"], z["hi"]
    return {"jenis": "zona", "side": "buy" if buy else "sell", "zona": [r2(lo), r2(hi)], "entry": None,
            "entry_band": [r2(x) for x in band], "risk": sj, "skor": z["skor"],
            "jarak": r2(max(lo - price, price - hi, 0)),
            "entry_teks": f"pita {band[0]:,.2f}-{band[1]:,.2f} (POI {lo:,.2f}-{hi:,.2f})",
            "sl_teks": f"ujung sweep {'-' if buy else '+'} {pad:.2f} (risk {sj * 10:.0f} pips)",
            "tp_teks": f"entry {'+' if buy else '-'} {tp:.2f} ({tp * 10:.0f} pips)",
            "rr": r2(tp / sj), "trigger": "tunggu sweep + CHoCH 1m di zona",
            "status": "harga sudah di zona, tunggu CHoCH" if z["disentuh"] else "belum disentuh",
            "alasan": z["alasan"]}


def kartu_level(side, entry, sl, tp, zona, price, **kw):
    risk = abs(entry - sl)
    lo, hi = (zona or [entry, entry])
    return {"jenis": "level", "side": side, "zona": [r2(lo), r2(hi)], "entry": r2(entry), "sl": r2(sl),
            "tp": [r2(x) for x in tp], "risk": r2(risk), "rr": r2(abs(tp[0] - entry) / risk) if tp and risk else None,
            "jarak": r2(max(lo - price, price - hi, 0)), "entry_teks": f"limit {entry:,.2f}",
            "sl_teks": f"{sl:,.2f}", "tp_teks": " / ".join(f"{x:,.2f}" for x in tp), **kw}


# ---- I/O ----
def laporan(nama, sumber=""):
    """Laporan validasi.py terbaru -> {lolos, total, trades, winrate, exp, sumber} atau None."""
    label = f"{nama}-{sumber}" if sumber else nama
    files = sorted(f for f in glob.glob(os.path.join(ROOT, "data", "backtest", "validasi", f"{label}_*.json"))
                   if not f.endswith("_trades.json"))
    if not files:
        return None
    rep = json.load(open(files[-1], encoding="utf-8"))
    m = rep["oos"]
    return {"lolos": sum(rep["cek"].values()), "total": len(rep["cek"]), "trades": m["trades"],
            "winrate": m["winrate"], "exp": m["expectancy"] if m["trades"] else None,
            "sumber": "broker MT5" if sumber else "Binance", "file": os.path.basename(files[-1])}


def nilai_strategi(nama):
    import pantau
    lap = laporan(nama, "mt5") or laporan(nama)
    info = pantau.gabung_info(pantau.info_validasi(nama, "mt5"), pantau.info_validasi(nama))
    g = nilai_grade(lap)
    if lap and lap["exp"] is not None:
        teks = (f"OOS {lap['trades']} tr, menang {lap['winrate'] * 100:.0f}%, {lap['exp']:+.2f}R, "
                f"lolos {lap['lolos']}/{lap['total']} ({lap['sumber']})")
    else:
        teks = "validasi belum punya trade OOS" if lap else "belum divalidasi"
    if g == "C":
        teks += "; PERINGATAN: belum teruji / expectancy <= 0, bukan setup kuat"
    return {"grade": g, "exp": lap and lap["exp"], "winrate": lap and lap["winrate"], "peluang": teks,
            "valid": info["valid"]}


def muat(pair):
    """Candle broker MT5 terbaru; terminal tidak tersedia -> cache MT5 terakhir. -> (by, catatan, ok_mt5)."""
    import data
    try:
        return data.load(pair, ["1m", "5m", "15m", "30m", "1h", "4h", "1d"], refresh=True, source="mt5"), "", True
    except Exception as e:  # terminal mati / paket MetaTrader5 tidak ada
        files = sorted(glob.glob(os.path.join(data.CACHE, f"MT5_{pair}*_1h.json")))
        if not files:
            raise RuntimeError(f"MT5 tidak tersedia ({e}) dan cache MT5 kosong.")
        sym = os.path.basename(files[0])[:-8]
        by = {tf: json.load(open(data._path(sym, tf), encoding="utf-8")) for tf in ("1m", "5m", "15m", "30m", "1h")}
        for tf in ("4h", "1d"):
            by[tf] = data.aggregate([list(r) for r in by["1h"]], STEP[tf])
        akhir = dt.datetime.fromtimestamp(by["1m"][-1][0] + WIB, dt.timezone.utc)
        return by, f"MT5 tidak tersedia ({type(e).__name__}); candle dari cache sampai {akhir:%d %b %H:%M} WIB.", False


def kalender_hari_ini(now):
    """-> (detik UTC event high USD untuk engine, [event high hari ini WIB])."""
    import kalender
    raw = kalender.fetch(24, 1)
    t = lambda e: int(dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
    hari = lambda x: (x + WIB) // 86400
    ev = [t(e) for e in raw if e.get("importance") == 1]
    hi = [{"waktu": dt.datetime.fromtimestamp(t(e) + WIB, dt.timezone.utc).strftime("%H:%M WIB"), "judul": e["title"],
           "forecast": e.get("forecast"), "actual": e.get("actual"), "t": t(e), "lewat": t(e) < now}
          for e in raw if kalender.impact(e) == "High" and hari(t(e)) == hari(now)]
    return ev, sorted(hi, key=lambda x: x["t"])


def kandidat_semua(by, now, price, events, pair):
    import data
    import pantau
    import setup as engine
    from backtest import EXPIRE
    from indikator import atr, kolom
    from pemilih import terbaru
    from regime import MODES
    from strategi import REGISTRY
    from strategi.sniper import arah_bias
    full = data.bersih(by)
    closed_full = {tf: [r for r in rows if r[0] + STEP[tf] <= now] for tf, rows in full.items()}
    closed = {tf: [r for r in rows if r[0] >= now - pantau.HARI_DATA * 86400] for tf, rows in closed_full.items()}
    m1 = by["1m"]
    ada_taker = len(closed["1m"][-1]) > 6 if closed["1m"] else False
    atr_d = atr(*kolom(closed_full["1d"])[2:5])[-1]
    nilai = {}
    out, catatan = [], []

    def grade(nama):
        if nama not in nilai:
            nilai[nama] = nilai_strategi(nama)
        return nilai[nama]

    # 1. sinyal aktif: strategi sniper/alchemist (aturan pantau/bot_mt5) + REGISTRY scalp/intraday
    sumber = [(n, importlib.import_module(f"strategi.{n}"), pantau.MODE, closed) for n in pantau.STRATEGI]
    sumber += [(n, mod, mode, closed_full) for n, mod in REGISTRY.items() for mode in ("scalp", "intraday")]
    sinyal_sniper = []
    for nama, mod, mode, cl in sumber:
        p = dict(mod.PARAMS)
        if p.get("delta") and not ada_taker:
            p["delta"] = False   # candle broker tanpa sisi agresor (sama seperti bot_mt5.sinyal_sekarang)
        exp_s = getattr(mod, "EXPIRE_S", EXPIRE * STEP[MODES[mode]["entry"]])
        try:
            sigs = mod.signals(cl, mode, p)
        except Exception as e:  # satu strategi gagal tidak menggagalkan laporan
            catatan.append(f"strategi {nama} {mode} gagal: {type(e).__name__}: {e}")
            continue
        if nama == "sniper":
            sinyal_sniper = sigs
        for s in sigs:
            if s["time"] < now - exp_s:
                continue
            terisi, hasil = pantau.lacak(s, m1, now, exp_s)
            if terisi or hasil:
                continue
            exp_t = dt.datetime.fromtimestamp(s["time"] + exp_s + WIB, dt.timezone.utc)
            out.append(kartu_level(s["side"], s["entry"], s["sl"], s["tp"], s.get("zona"), price,
                                   strategi=f"{nama}" + ("" if nama in pantau.STRATEGI else f" ({mode})"),
                                   nama=nama, skor=s.get("skor", 0), trigger=f"limit aktif s/d {exp_t:%H:%M} WIB",
                                   status="sinyal aktif, belum terisi", alasan=s.get("alasan", ""), **grade(nama)))

    # 2. zona POI sniper 15m yang masih hidup, dalam 1.5 x ATR harian
    import strategi.sniper as sn
    p = dict(sn.PARAMS)
    if p.get("delta") and not ada_taker:
        p["delta"] = False
    bias = arah_bias(closed, MODES[pantau.MODE]["bias"])(now)
    for z in zona_hidup(sn.poi_list(closed, pantau.MODE, p), closed["1m"], now, p, sinyal_sniper):
        k = kartu_zona(z, p, price)
        if atr_d and k["jarak"] > ZONA_ATR_D * atr_d:
            continue
        if bias != z["side"]:
            k["status"] += "; bias 1H+30m sekarang tidak searah"
        habis = dt.datetime.fromtimestamp(z["t_ok"] + p["umur_jam"] * 3600 + WIB, dt.timezone.utc)
        k["trigger"] += f" (zona berlaku s/d {habis:%H:%M} WIB)"
        out.append({**k, "strategi": "sniper (zona)", "nama": "sniper", **grade("sniper")})

    # 3. engine setup.py scalp + intraday (status apa adanya)
    engine_status = {}
    for mode in ("scalp", "intraday"):
        try:
            e = engine.bangun(full, pair, mode, now, price, events, terbaru(pair, mode), None, "MT5 broker")
        except Exception as x:
            catatan.append(f"engine {mode} gagal: {type(x).__name__}: {x}")
            continue
        engine_status[mode] = f"{e['status']} ({e['strategi']['terpilih']}: {e['strategi']['alasan']})"
        for s in e["setups"]:
            if s["status"].startswith(("NO TRADE", "INVALID")):
                continue
            nama = s.get("strategi") or "kontra-tren"
            g = grade(nama) if s.get("strategi") else {"grade": "C", "exp": None, "winrate": None, "valid": False,
                                                        "peluang": "setup kontra-tren engine, belum teruji"}
            out.append(kartu_level(s["side"], s["entry"], s["sl"], s["tp"], s["zone"], price,
                                   strategi=f"engine {mode}: {nama}", nama=nama, skor=0, trigger=s["trigger"],
                                   status=s["status"], alasan=s.get("alasan", ""), **g))
    out.sort(key=kunci)
    return out, engine_status, catatan, atr_d


def akun_sekarang(now):
    """-> (akun, sym, nama simbol, [(t tutup UTC, pl)] sejak awal bulan WIB, posisi terbuka) atau None."""
    import mt5_link
    akun = mt5_link.sambung()
    mt5 = mt5_link.modul()
    nama = mt5_link.simbol_emas()
    hari = dt.datetime.fromtimestamp(now + WIB, dt.timezone.utc).date()
    awal = int(dt.datetime(hari.year, hari.month, 1, tzinfo=dt.timezone.utc).timestamp()) - WIB
    dari = dt.datetime.fromtimestamp(awal - 7 * 86400, dt.timezone.utc)
    sampai = dt.datetime.fromtimestamp(now + 86400, dt.timezone.utc)
    posisi = mt5.positions_get() or []
    ke_utc = lambda ts: int(ts - mt5_link.offset_server(ts - 3 * 3600))   # jam server -> UTC (pola jembatan_mt5)
    tutup = [x for x in posisi_tutup(mt5.history_deals_get(dari, sampai) or [], {p.identifier for p in posisi}, ke_utc)
             if x[0] >= awal]
    return akun, mt5.symbol_info(nama), nama, tutup, posisi


def laporan_hari(pair, now=None):
    import bot_mt5
    import data
    from regime import sesi
    now = now or int(time.time())
    hari = dt.datetime.fromtimestamp(now + WIB, dt.timezone.utc).date()
    rep = {"pair": pair, "waktu": dt.datetime.fromtimestamp(now + WIB, dt.timezone.utc).strftime("%a %d %b %Y %H:%M WIB"),
           "sesi": sesi(now), "catatan": []}
    by, cat, ok_mt5 = muat(pair)
    if cat:
        rep["catatan"].append(cat)
    price = by["1m"][-1][4]
    rep["harga"] = r2(price)
    try:
        events, rep["news"] = kalender_hari_ini(now)
    except Exception as e:
        events, rep["news"] = [], []
        rep["catatan"].append(f"kalender gagal: {e}")
    rep["jendela_news"] = any(abs(now - x) <= NEWS_MENIT * 60 for x in events)
    kand, rep["engine"], cat, atr_d = kandidat_semua(by, now, price, events, pair)
    rep["catatan"] += cat
    rep["atr_harian"] = atr_d and r2(atr_d)
    akun = None
    if ok_mt5:
        try:
            akun, sym, nama, tutup, posisi = akun_sekarang(now)
        except Exception as e:
            rep["catatan"].append(f"akun MT5 gagal dibaca: {type(e).__name__}: {e}")
    if akun:
        cfg = bot_mt5.konfig(data.env())
        saldo = akun.balance
        hari_ini = [pl for t, pl in tutup if bot_mt5.hari_wib(t) == hari.isoformat()]
        atur = aturan_hari(hari_ini, saldo)
        bln = status_bulan(saldo, sum(pl for _, pl in tutup), hari)
        bln.update(menang=sum(hasil_trade(pl, saldo) == "W" for _, pl in tutup),
                   kalah=sum(hasil_trade(pl, saldo) == "L" for _, pl in tutup),
                   be=sum(hasil_trade(pl, saldo) == "BE" for _, pl in tutup))
        rep["akun"] = {"jenis": {0: "demo", 1: "contest", 2: "real"}.get(akun.trade_mode), "server": akun.server,
                       "mata_uang": akun.currency, "saldo": r2(saldo), "ekuitas": r2(akun.equity),
                       "risiko_pct": cfg["risiko"] * 100, "posisi_terbuka": len(posisi),
                       "floating": r2(sum(p.profit for p in posisi))}
        for k in kand:
            lot, rc = bot_mt5.lot_untuk(sym, 0.0, k["risk"], saldo, cfg["risiko"])
            k["lot"], k["rugi_di_sl"] = lot, rc.get("rugi_di_sl")
    else:
        atur = {**aturan_hari([], 1.0), "status": "Akun tidak tersedia: aturan W/L tidak bisa dicek."}
        atur["menang"] = atur["kalah"] = atur["be"] = atur["pl"] = None
        bln = None
        rep["akun"] = None
    rep["hari"], rep["bulan"] = atur, bln
    rep["kandidat"] = izinkan(kand, atur["mode"])
    rep["berikutnya"] = trade_berikutnya(kand, atur)
    return rep


def cetak(rep):
    f = lambda x: "-" if x is None else f"{x:,.2f}"
    print(f"== {rep['pair']} {rep['waktu']} | sesi {rep['sesi']} | harga {f(rep['harga'])} | ATR D1 {f(rep['atr_harian'])}")
    a, b, h = rep["akun"], rep["bulan"], rep["hari"]
    if a:
        print(f"Akun {a['jenis']} {a['server']}: saldo {f(a['saldo'])} {a['mata_uang']}, ekuitas {f(a['ekuitas'])}, "
              f"risiko {a['risiko_pct']:.0f}%/trade, posisi terbuka {a['posisi_terbuka']} (floating {f(a['floating'])})")
        print(f"BULAN: modal awal {f(b['modal_awal'])} -> saldo {f(b['saldo'])} = {b['growth_pct']:+.2f}% "
              f"(P/L {f(b['pl'])}; {b['menang']}W/{b['kalah']}L/{b['be']}BE), {b['hari_lewat']} hari bursa lewat, "
              f"sisa {b['sisa_hari']} hari setelah hari ini")
        print(f"  PROYEKSI naif (rata-rata {b['harian_pct']:+.3f}%/hari majemuk, bukan janji): "
              f"{b['proyeksi_pct']:+.2f}% -> {f(b['proyeksi_saldo'])} akhir bulan")
        print(f"HARI INI: {h['menang']}W / {h['kalah']}L / {h['be']}BE, P/L {f(h['pl'])}. Aturan: {h['status']}")
    else:
        print("Akun: tidak tersedia. " + h["status"])
    if rep["news"]:
        print("News USD high hari ini: " + "; ".join(f"{n['waktu']} {n['judul']}{' (lewat)' if n['lewat'] else ''} "
                                                    f"(F {n['forecast']}, A {n['actual']})"
                                                    for n in rep["news"]))
    else:
        print("News USD high hari ini: tidak ada (atau kalender gagal).")
    if rep["jendela_news"]:
        print(f"  PERINGATAN: news high dalam {NEWS_MENIT} menit -> TUNGGU NEWS untuk scalp.")
    for mode, s in rep["engine"].items():
        print(f"Engine {mode}: {s}")
    print(f"\nKANDIDAT ({len(rep['kandidat'])}):")
    for i, k in enumerate(rep["kandidat"], 1):
        lot = "-" if k.get("lot") is None else f"{k['lot']} lot (rugi di SL {f(k.get('rugi_di_sl'))})"
        print(f"#{i} [{k['grade']}] {'BOLEH' if k['boleh'] else 'tidak boleh'} | {k['strategi']} {k['side'].upper()} | "
              f"{k['entry_teks']} | SL {k['sl_teks']} | TP {k['tp_teks']} | RR {k['rr']} | {lot}")
        print(f"    peluang: {k['peluang']} | trigger: {k['trigger']} | status: {k['status']} | jarak {f(k['jarak'])}")
    if not rep["kandidat"]:
        print("  tidak ada kandidat hidup (tidak ada sinyal aktif, zona POI, atau setup engine).")
    print(f"\nTRADE BERIKUTNYA: {rep['berikutnya']}")
    for c in rep["catatan"]:
        print(f"catatan: {c}")


def _selftest():
    # grade
    assert nilai_grade({"lolos": 6, "exp": 0.54}) == "A" and nilai_grade({"lolos": 7, "exp": 0.1}) == "A"
    assert nilai_grade({"lolos": 5, "exp": 0.3}) == "B" and nilai_grade({"lolos": 7, "exp": 0.0}) == "C"
    assert nilai_grade(None) == "C" and nilai_grade({"lolos": 6, "exp": None}) == "C"
    k = lambda g, e=0.0, w=0.0, s=0, j=0: {"grade": g, "exp": e, "winrate": w, "skor": s, "jarak": j}
    urut = sorted([k("C", 2, 1), k("B", 0.5, 0.5), k("A", 0.2, 0.4, j=5), k("A", 0.2, 0.4, s=3, j=9), k("A", 0.5, 0.4)],
                  key=kunci)
    assert [(x["grade"], x["exp"], x["skor"]) for x in urut] == [("A", 0.5, 0), ("A", 0.2, 3), ("A", 0.2, 0),
                                                                 ("B", 0.5, 0), ("C", 2, 0)], urut
    # aturan hari: saldo 1000 -> BE kalau |pl| < 5
    assert [hasil_trade(x, 1000) for x in (4.9, -4.9, 5, -5)] == ["BE", "BE", "W", "L"]
    a = aturan_hari([30, -3, 12], 1000)
    assert (a["menang"], a["kalah"], a["be"], a["mode"]) == (2, 0, 1, "semua"), a   # menang tidak menghentikan
    a = aturan_hari([30, -25], 1000)
    assert a["kalah"] == 1 and a["mode"] == "terbaik-A" and a["pl"] == 5, a
    assert aturan_hari([-25, 40, -25], 1000)["mode"] == "stop"
    assert aturan_hari([-25, -4], 1000)["mode"] == "terbaik-A"                       # loss kecil = BE
    kk = lambda g: {**k(g), "strategi": "sniper", "side": "buy", "entry_teks": "limit 1", "trigger": "t"}
    assert [x["boleh"] for x in izinkan([kk("A"), kk("A"), kk("C")], "semua")] == [True, True, True]
    assert [x["boleh"] for x in izinkan([kk("A"), kk("A")], "terbaik-A")] == [True, False]
    c1 = izinkan([kk("B"), kk("A")], "terbaik-A")
    assert not any(x["boleh"] for x in c1)
    assert trade_berikutnya(c1, {"mode": "terbaik-A"}).startswith("Tunggu setup peluang tertinggi")
    assert not any(x["boleh"] for x in izinkan([kk("A")], "stop"))
    assert trade_berikutnya([], {"mode": "stop"}).startswith("Tidak ada. STOP")
    assert trade_berikutnya(izinkan([kk("C")], "semua"), {"mode": "semua"}).startswith("Belum ada setup teruji")
    assert trade_berikutnya(izinkan([kk("C"), kk("B")], "semua"), {"mode": "semua"}).startswith("#2 sniper BUY")
    # bulan: Kamis 8 Okt 2026 -> 6 hari bursa lewat (1,2,5,6,7,8), sisa 16 (9..30 Okt)
    b = status_bulan(1100.0, 100.0, dt.date(2026, 10, 8))
    assert (b["modal_awal"], b["growth_pct"], b["hari_lewat"], b["sisa_hari"]) == (1000.0, 10.0, 6, 16), b
    assert abs(b["proyeksi_pct"] - (1.1 ** (22 / 6) - 1) * 100) < 0.01, b
    b = status_bulan(1000.0, 0.0, dt.date(2026, 2, 27))      # Jumat terakhir Feb: sisa 0
    assert b["sisa_hari"] == 0 and b["proyeksi_pct"] == 0 and b["hari_lewat"] == 20, b
    assert hari_bursa(dt.date(2026, 10, 10), dt.date(2026, 10, 11)) == 0      # Sabtu-Minggu
    # deal -> posisi tutup: posisi 2 masih terbuka, deal saldo (type 2) diabaikan, komisi masuk ikut dihitung
    from types import SimpleNamespace as N
    d = lambda pid, ent, pl, t, typ=0, kom=0.0: N(position_id=pid, entry=ent, profit=pl, commission=kom, swap=0.0,
                                                  time=t, type=typ)
    deals = [d(1, 0, 0, 100, kom=-1.0), d(1, 1, 20, 200, typ=1), d(2, 0, 0, 150), d(3, 0, 0, 0, 2),
             d(4, 0, 0, 300), d(4, 1, -5, 400, typ=1), d(4, 1, -5, 500, typ=1)]
    assert posisi_tutup(deals, {2}, lambda t: t - 10) == [(190, 19.0), (490, -10.0)]
    # zona sniper: buy POI 100-101, jebol kalau close < 99.5; dipakai CHoCH -> hilang
    p = {"umur_jam": 12, "pad": 0.5, "sl_jarak": 3.0, "tp_min": 10.0, "rr": 3.0}
    poi = {"t_ok": 0, "side": 1, "lo": 100.0, "hi": 101.0, "skor": 2, "alasan": "x", "tf": "15m"}
    m = lambda t, hi, lo, c: [t, c, hi, lo, c, 1]
    z = zona_hidup([poi, dict(poi)], [m(0, 103, 102, 102.5), m(60, 102, 100.8, 101)], 180, p, [])
    assert len(z) == 1 and z[0]["disentuh"], z
    assert not zona_hidup([poi], [m(0, 101, 99, 99.4)], 120, p, [])                       # jebol
    assert not zona_hidup([poi], [m(0, 103, 102, 102.5)], 13 * 3600, p, [])               # lewat umur
    assert not zona_hidup([poi], [], 120, p, [{"side": "buy", "poi": [100.0, 101.0]}])     # sudah CHoCH
    kz = kartu_zona({**poi, "disentuh": False}, p, 105.0)
    assert kz["entry_band"] == [102.0, 103.5] and kz["rr"] == 3.33 and kz["jarak"] == 4.0, kz
    ks = kartu_zona({**poi, "side": -1, "disentuh": False}, p, 98.0)
    assert ks["entry_band"] == [97.5, 99.0] and ks["side"] == "sell" and ks["jarak"] == 2.0, ks
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    out = args[args.index("--json") + 1] if "--json" in args else None
    pair = next((a.upper() for a in args if not a.startswith("--") and a != out), "XAUUSD")
    try:
        rep = laporan_hari(pair)
    except RuntimeError as e:
        sys.exit(str(e))
    cetak(rep)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False, default=str)
        print(f"json: {out}")
