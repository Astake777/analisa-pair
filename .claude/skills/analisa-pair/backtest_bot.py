"""Backtest BOT: aturan bot_mt5.py dijalankan atas riwayat, dalam dollar, di data broker HFM.

Aturan yang ditiru: modal BOT_MODAL, lot dinamis bot_mt5.lot_untuk (BOT_RISK_PERCENTAGE 25-30% saldo / (SL pips x $10),
dibulatkan ke bawah 0.01, lewati kalau lot minimum melebihi risiko), auto BE di BOT_BE_TRIGGER_PIPS (SL ke entry), maks 1 order/posisi, maks MAKS_SL_HARIAN SL dan MAKS_ENTRY_HARIAN entry per hari WIB,
tidak ada order dalam NEWS_MENIT dari news USD high impact, limit harus tembus 0.10, batal kalau harga sudah 70%
ke TP1, kedaluwarsa EXPIRE_S strategi, biaya = spread HFM + slip 0.10.
Data: candle HFM (MT5); M1 sebelum riwayat M1 HFM diisi XAUT Binance yang digeser ke harga HFM (median selisih
close di periode tumpang tindih). Delta orderflow tidak dipakai (candle broker tidak punya sisi agresor).
Ini backtest seluruh sampel dengan PARAMS strategi saat ini (bukan walk-forward); kelulusan strategi tetap dari
validasi.py.
Pakai:  python backtest_bot.py [strategi ...]      bawaan: semua strategi (REGISTRY, EKSTRA, Alchemist)
Keluaran: ringkasan di layar + data/backtest/bot_<waktu>.json (arsip; panel website kini memakai hasil Strategy Tester EA)
Self-check: python backtest_bot.py --selftest
"""
import datetime as dt
import importlib
import json
import os
import statistics
import sys
from bisect import bisect_left

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import metrik, simulasi  # noqa: E402
from bot_mt5 import MAKS_ENTRY_HARIAN, MAKS_SL_HARIAN, NEWS_MENIT, PIP, hari_wib, konfig, lot_untuk  # noqa: E402
from regime import STEP  # noqa: E402
from validasi import BATAL_FRAC, TEMBUS  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
OUT = os.path.join(ROOT, "data", "backtest")
NILAI_TITIK = 100.0    # XAUUSD lot standar: $1 per lot per gerak $0.01 -> $100 per lot per $1
SYM_HFM = type("Sym", (), {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
                            "volume_max": 50.0})


def gabung_1m(hfm, xaut):
    """M1 HFM, didahului M1 XAUT (digeser ke harga HFM) untuk waktu sebelum M1 HFM pertama. -> (rows, geser, n_xaut)"""
    if not hfm:
        return xaut, 0.0, len(xaut)
    t0 = hfm[0][0]
    xi = {r[0]: r[4] for r in xaut if t0 <= r[0] < t0 + 5 * 86400}
    beda = [r[4] - xi[r[0]] for r in hfm if r[0] in xi]
    geser = statistics.median(beda) if beda else 0.0
    awal = [[r[0], r[1] + geser, r[2] + geser, r[3] + geser, r[4] + geser, r[5]] for r in xaut if r[0] < t0]
    return awal + hfm, round(geser, 2), len(awal)


def jalankan(trades, modal, risiko, news, cost):
    """Terapkan aturan bot ke trade kandidat (hasil simulasi per sinyal, urut waktu sinyal). -> (diambil, log lewati)"""
    eq, bebas, harian, diambil, lewati = modal, 0, {}, [], []
    for x in sorted(trades, key=lambda x: x["sinyal"]):
        d = harian.setdefault(hari_wib(x["sinyal"]), {"sl": 0, "entry": 0})
        alasan = None
        if x["sinyal"] < bebas:
            alasan = "masih ada order/posisi"
        elif d["sl"] >= MAKS_SL_HARIAN or d["entry"] >= MAKS_ENTRY_HARIAN:
            alasan = "batas harian"
        elif any(abs(x["sinyal"] - t) <= NEWS_MENIT * 60 for t in news):
            alasan = "jendela news"
        lot, rc = lot_untuk(SYM_HFM, x["entry"], x["sl"], eq, risiko)
        if alasan is None and lot is None:
            alasan = "lot minimum melebihi risiko"
        if alasan:
            lewati.append(alasan)
            continue
        risiko_usd = rc["rugi_di_sl"]
        pl = x["r"] * risiko_usd - cost * lot * NILAI_TITIK
        eq += pl
        d["entry"] += 1
        d["sl"] += x["hasil"] == "SL"
        bebas = x["keluar"] + 60
        diambil.append({**x, "lot": round(lot, 2), "pl": round(pl, 2), "ekuitas": round(eq, 2),
                        "r_net": pl / risiko_usd if risiko_usd else 0})
    return diambil, lewati


def ringkas(diambil, lewati, modal):
    puncak, dd, eq = modal, 0.0, modal
    for x in diambil:
        eq = x["ekuitas"]
        puncak = max(puncak, eq)
        dd = max(dd, (puncak - eq) / puncak)
    m = metrik(diambil)
    alasan = {}
    for a in lewati:
        alasan[a] = alasan.get(a, 0) + 1
    return {**m, "earnings": round(eq - modal, 2), "totalReturn": round(eq / modal - 1, 4), "maxDdPersen": round(-dd, 4),
            "lotRata": round(sum(x["lot"] for x in diambil) / len(diambil), 3) if diambil else None,
            "be": sum(x["hasil"] == "BE" for x in diambil), "dilewati": alasan}


def kandidat(by, nama, mode, cost, be=None):
    mod = importlib.import_module(f"strategi.{nama}")
    p = dict(mod.PARAMS)
    if p.get("delta"):
        p["delta"] = False
    stf = getattr(mod, "SIM", "5m")
    from backtest import EXPIRE
    from regime import MODES
    exp_s = getattr(mod, "EXPIRE_S", EXPIRE * STEP[MODES[mode]["entry"]])
    sig = mod.signals(by, mode, p)
    tr = []
    t = [r[0] for r in by[stf]]
    for s in sig:  # tiap sinyal disimulasikan sendiri; antrean posisi diatur jalankan()
        i = bisect_left(t, s["time"])
        hasil = simulasi(by[stf][i:], [s], STEP[stf], exp_s // STEP[stf], cost=cost, tembus=TEMBUS, batal_frac=BATAL_FRAC,
                          be=be)
        tr += [{**x, "strategi": nama} for x in hasil]
    return sig, tr


def main(nama_list):
    import data
    import kalender
    import mt5_link
    from regime import TFS
    from strategi import EKSTRA, REGISTRY
    e = data.env()
    cfg = konfig(e)
    modal = float(e.get("BOT_MODAL", 100))
    mt5_link.sambung(e)
    s = mt5_link.modul().symbol_info(mt5_link.simbol_emas(e))
    cost = s.spread * s.point + 0.10
    by = data.load("XAUUSD", TFS + ["1m"], source="mt5", spot=False)
    xaut = data.load("XAUUSD", ["1m"], source="binance", spot=False)["1m"]
    by["1m"], geser, n_xaut = gabung_1m(by["1m"], xaut)
    by = data.bersih(by)
    try:
        news = [dt.datetime.fromisoformat(x["date"].replace("Z", "+00:00")).timestamp()
                for x in kalender.fetch(24 * 220, 1) if x.get("importance") == 1]
    except Exception as ex:
        print(f"peringatan: kalender gagal ({ex}); jendela news tidak diterapkan", file=sys.stderr)
        news = []
    f = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %b %Y")
    print(f"data HFM {f(by['1m'][0][0])} - {f(by['1m'][-1][0])}; {n_xaut} candle M1 awal dari XAUT digeser {geser:+.2f}; "
          f"biaya {cost:.2f}/trade; modal ${modal:,.0f}; risiko {cfg['risiko'] * 100:.0f}%/trade; auto BE {cfg['be_pips']:g} pips; {len(news)} news high impact")
    import pantau
    semua = nama_list or list(dict.fromkeys(list(REGISTRY) + list(EKSTRA) + pantau.STRATEGI))
    hasil, gabung = {}, []
    for nama in semua:
        mode = "scalp"
        sig, tr = kandidat(by, nama, mode, cost, cfg["be_pips"] * PIP or None)
        diambil, lewati = jalankan(tr, modal, cfg["risiko"], news, cost)
        hasil[nama] = {"sinyal": len(sig), **ringkas(diambil, lewati, modal),
                       "kurva": [[x["keluar"], x["ekuitas"]] for x in diambil]}
        gabung += tr
        m = hasil[nama]
        print(f"{nama:<17} {m['sinyal']:>4} sinyal {m['trades']:>4} trade  menang {round((m['winrate'] or 0) * 100):>3}%  "
              f"{m['expectancy'] if m['expectancy'] is not None else '-':>6}R  ${m['earnings']:>9,.2f} ({m['totalReturn'] * 100:+.1f}%)  "
              f"DD {m['maxDdPersen'] * 100:.1f}%  BE {m['be']}  lewati {m['dilewati']}")
    diambil, lewati = jalankan(gabung, modal, cfg["risiko"], news, cost)
    hasil["gabungan"] = {"sinyal": len(gabung), **ringkas(diambil, lewati, modal),
                         "kurva": [[x["keluar"], x["ekuitas"]] for x in diambil]}
    m = hasil["gabungan"]
    print(f"{'GABUNGAN semua':<17} {m['trades']:>4} trade  menang {round((m['winrate'] or 0) * 100)}%  ${m['earnings']:,.2f} "
          f"({m['totalReturn'] * 100:+.1f}%)  DD {m['maxDdPersen'] * 100:.1f}%")
    rep = {"dibuat": dt.datetime.now(dt.timezone.utc).isoformat(), "modal": modal, "risiko": cfg["risiko"], "bePips": cfg["be_pips"], "biaya": cost,
           "geserXaut": geser, "nXaut": n_xaut, "dari": by["1m"][0][0], "sampai": by["1m"][-1][0], "hasil": hasil}
    path = os.path.join(OUT, f"bot_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M}.json")
    json.dump(rep, open(path, "w", encoding="utf-8"))
    print(path)


def _selftest():
    D = 86400
    tr = lambda t, r, hasil: {"sinyal": t, "masuk": t, "keluar": t + 600, "entry": 100.0, "sl": 97.0, "r": r, "hasil": hasil}
    # modal 100, risiko 25% = $25: SL 30 pips x $10 -> lot 0.08 (rugi di SL $24)
    x, lw = jalankan([tr(D, 3.0, "TP")], 100, 0.25, [], 0.46)
    assert x[0]["lot"] == 0.08 and abs(x[0]["pl"] - (3 * 24 - 0.46 * 8)) < 1e-6, x
    # posisi tumpang tindih dilewati; batas 2 SL per hari
    x, lw = jalankan([tr(D, -1, "SL"), tr(D + 60, 3, "TP"), tr(D + 1000, -1, "SL"), tr(D + 2000, 3, "TP")], 100, 0.25, [], 0)
    assert len(x) == 2 and lw == ["masih ada order/posisi", "batas harian"], (x, lw)
    # jendela news dan lot minimum
    assert jalankan([tr(D, 3, "TP")], 100, 0.25, [D + 300], 0)[1] == ["jendela news"]
    assert jalankan([tr(D, 3, "TP")], 10, 0.25, [], 0)[1] == ["lot minimum melebihi risiko"]
    rows, g, n = gabung_1m([[1000, 0, 0, 0, 12, 0], [1060, 0, 0, 0, 13, 0]],
                           [[880, 0, 0, 0, 8, 0], [940, 0, 0, 0, 9, 0], [1000, 0, 0, 0, 10, 0], [1060, 0, 0, 0, 11, 0]])
    assert g == 2.0 and n == 2 and rows[0] == [880, 2.0, 2.0, 2.0, 10.0, 0] and len(rows) == 4, (rows, g)
    m = ringkas(x, [], 100)
    assert m["trades"] == 2 and m["dilewati"] == {}
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(sys.argv[1:])
