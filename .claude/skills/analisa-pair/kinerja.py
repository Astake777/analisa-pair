"""Kinerja backtest untuk panel website: equity curve dan metrik dari trade OUT-OF-SAMPLE saja.

Asumsi akun: modal MODAL dollar, risiko per trade = BOT_RISK_PERCENTAGE di .env (25-30% saldo), majemuk; R bersih biaya.
Sumber: EA MT5 = SniperBot_tester.csv dari Strategy Tester (saldo nyata); sniper = trade OOS walk-forward terbaru validasi.py; scalp/intraday = sample OOS tren_pullback backtest.py terbaru.
Pakai:  python kinerja.py            menulis web/public/kinerja.json
Self-check: python kinerja.py --selftest
"""
import csv
import datetime as dt
import glob
import json
import math
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
BT = os.path.join(ROOT, "data", "backtest")
OUT = os.path.join(ROOT, "web", "public", "kinerja.json")
MODAL, RISIKO, MATA_UANG = 10_000.0, 0.01, "USC"   # bawaan: akun cent 10.000 USC = $100
EA_CSV = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files", "SniperBot_tester.csv")
MIN_HARI_TAHUNAN = 90   # periode lebih pendek tidak disetahunkan (angkanya menyesatkan)


def metrik(trades, t0, t1):
    """trades: [{masuk, keluar, r_net}] urut waktu; t0..t1 = rentang OOS (detik)."""
    eq, kurva, puncak, dd = MODAL, [[t0, MODAL]], MODAL, 0.0
    ret = []
    for x in sorted(trades, key=lambda x: x["keluar"]):
        r = RISIKO * x["r_net"]
        ret.append(r)
        eq *= 1 + r
        puncak = max(puncak, eq)
        dd = max(dd, (puncak - eq) / puncak)
        kurva.append([x["keluar"], round(eq, 2)])
    n = len(ret)
    tahun = max((t1 - t0) / (365 * 86400), 1e-9)
    total = eq / MODAL - 1
    hari = (t1 - t0) / 86400
    annual = ((eq / MODAL) ** (1 / tahun) - 1 if eq > 0 else -1) if hari >= MIN_HARI_TAHUNAN else None
    rata = sum(ret) / n if n else 0
    sd = math.sqrt(sum((r - rata) ** 2 for r in ret) / (n - 1)) if n > 1 else 0
    turun = math.sqrt(sum(min(r, 0) ** 2 for r in ret) / n) if n else 0
    per_tahun = n / tahun
    r2 = lambda v, d=4: None if v is None else round(v, d)
    return {
        "earnings": round(eq - MODAL, 2), "totalReturn": r2(total), "annualReturn": r2(annual),
        "maxDrawdown": r2(-dd), "sharpe": r2(rata / sd * math.sqrt(per_tahun), 2) if sd else None,
        "sortino": r2(rata / turun * math.sqrt(per_tahun), 2) if turun else None,
        "calmar": r2(annual / dd, 2) if dd and annual is not None else None,
        "winRate": r2(sum(x["r_net"] > 0 for x in trades) / n) if n else None,
        "expectancy": r2(rata), "expectancyR": r2(sum(x["r_net"] for x in trades) / n, 2) if n else None,
        "posisi": n, "kurva": kurva, "dari": t0, "sampai": t1, "hari": round(hari),
    }


def terbaru(pola):
    files = sorted(glob.glob(pola))
    return files[-1] if files else None


def sniper():
    f = terbaru(os.path.join(BT, "validasi", "sniper_*_trades.json"))
    if not f:
        return None
    rep = json.load(open(f.replace("_trades.json", ".json"), encoding="utf-8"))
    tr = json.load(open(f, encoding="utf-8"))
    t0 = min(x["oos_mulai"] for x in rep["lipatan"])
    return {"nama": "Sniper", "status": "valid" if rep["valid"] else "uji coba",
            "sumber": "OOS walk-forward validasi.py (parameter dipilih hanya dari data sebelumnya)",
            **metrik(tr, t0, rep["data"][1])}


def tren_pullback(mode):
    f = terbaru(os.path.join(BT, f"XAUUSD_{mode}_*_trades.json"))
    if not f:
        return None
    tr = [x for x in json.load(open(f, encoding="utf-8")) if x["strategy"] == "tren_pullback" and x["sample"] == "oos"]
    if not tr:
        return None
    from backtest import OOS_DAYS
    t1 = max(x["keluar"] for x in tr)
    return {"nama": mode.capitalize(), "status": f"tren pullback, strategi utama mode {mode}",
            "sumber": f"OOS {OOS_DAYS} hari terakhir backtest.py", **metrik(tr, t1 - OOS_DAYS * 86400, t1)}


def ea_mt5():
    """EA SniperBot di Strategy Tester MT5 (EA_CSV): kurva saldo dollar nyata dari baris TRADE."""
    if not os.path.exists(EA_CSV):
        return None
    raw = open(EA_CSV, "rb").read()   # FILE_CSV MQL5 tanpa FILE_ANSI = UTF-16 ber-BOM
    rows = list(csv.DictReader((raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")).splitlines()))
    tr = sorted((r for r in rows if r["jenis"] == "TRADE"), key=lambda r: int(r["waktu_utc"]))
    if not tr:
        return None
    modal = float(tr[0]["saldo"]) - float(tr[0]["pl"])   # deposit awal = saldo sebelum trade pertama
    t0 = min([int(r["waktu_utc"]) for r in rows] + [int(tr[0]["masuk_utc"])])
    t1 = int(tr[-1]["waktu_utc"])
    tt, prev = [], modal
    for r in tr:   # r_net setara: majemuk RISIKO * r_net = pl / saldo sebelum -> kurva sama dengan saldo nyata
        tt.append({"masuk": int(r["masuk_utc"]), "keluar": int(r["waktu_utc"]), "r_net": float(r["pl"]) / prev / RISIKO})
        prev = float(r["saldo"])
    global MODAL
    lama, MODAL = MODAL, modal
    m = metrik(tt, t0, t1)
    MODAL = lama
    m["kurva"] = [[t0, modal]] + [[int(r["waktu_utc"]), float(r["saldo"])] for r in tr]
    m["winRate"] = round(sum(r["hasil"] != "BE" and float(r["pl"]) > 0 for r in tr) / len(tr), 4)   # BE bukan menang
    m["be"] = sum(r["hasil"] == "BE" for r in tr)
    return {"nama": "EA MT5", "status": "Strategy Tester MT5, data broker real ticks",
            "sumber": "EA SniperBot di Strategy Tester HFM (real ticks), lot dinamis + auto BE, tanpa delta orderflow; "
                      "seluruh periode tester (bukan OOS)", **m}


def _selftest():
    D = 86400
    tr = [{"masuk": i * D, "keluar": i * D + 3600, "r_net": 3.0 if i % 2 else -1.0} for i in range(10)]
    m = metrik(tr, 0, 365 * D)
    eq = MODAL * (1.03 ** 5) * (0.99 ** 5)
    assert abs(m["earnings"] - round(eq - MODAL, 2)) < 0.01 and m["posisi"] == 10 and m["winRate"] == 0.5, m
    assert abs(m["annualReturn"] - m["totalReturn"]) < 1e-3, m          # rentang tepat 1 tahun
    assert m["maxDrawdown"] == round(-0.01, 4) and m["expectancyR"] == 1.0, m
    assert len(m["kurva"]) == 11 and m["sharpe"] > 0 and m["sortino"] > m["sharpe"], m
    assert metrik([], 0, D)["posisi"] == 0
    assert metrik(tr, 0, 30 * D)["annualReturn"] is None and metrik(tr, 0, 30 * D)["calmar"] is None
    # EA MT5: CSV sintetis, majemuk r_net harus mereproduksi saldo terakhir CSV
    import tempfile
    global EA_CSV
    asli, EA_CSV = EA_CSV, os.path.join(tempfile.mkdtemp(), "SniperBot_tester.csv")
    assert ea_mt5() is None   # file belum ada -> tab tidak tampil
    with open(EA_CSV, "w", encoding="utf-8", newline="") as f:
        f.write("""jenis,waktu_utc,masuk_utc,side,entry,sl,tp,lot,harga_keluar,pl,hasil,saldo,alasan
SINYAL,1000,,buy,100,97,110,0.10,,,ORDER,,zona OB
SINYAL,1500,,sell,105,108,95,,,,LEWATI,,spread lebar
TRADE,2000,1100,buy,100,97,110,0.10,110,30,TP,130,
TRADE,3000,2500,sell,105,108,95,0.13,108,-13,SL,117,
TRADE,4000,3500,buy,101,98,111,0.12,101.01,0.02,BE,117.02,
TRADE,5000,4500,buy,102,99,112,0.12,103,5,DITUTUP,122.02,
""")
    e = ea_mt5()
    teks = open(EA_CSV, encoding="utf-8").read()
    open(EA_CSV, "w", encoding="utf-16").write(teks)   # versi UTF-16 (FileOpen MQL5 tanpa FILE_ANSI) hasilnya sama
    assert ea_mt5() == e
    EA_CSV = asli
    assert MODAL == 10_000.0, MODAL   # MODAL global dipulihkan
    assert abs(100 + e["earnings"] - 122.02) < 0.01 and e["kurva"][0] == [1000, 100.0] and e["kurva"][-1] == [5000, 122.02], e
    assert e["posisi"] == 4 and e["be"] == 1 and e["winRate"] == 0.5 and e["nama"] == "EA MT5", e
    assert abs(e["maxDrawdown"] - round(-13 / 130, 4)) < 1e-9, e
    print("selftest OK")


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    global RISIKO, MODAL, MATA_UANG
    import bot_mt5
    import data
    RISIKO = bot_mt5.konfig(data.env())["risiko"]   # sama dengan risiko bot (BOT_RISK_PERCENTAGE)
    e = data.env()
    MODAL = float(e.get("BOT_MODAL", MODAL))
    MATA_UANG = e.get("BOT_MATA_UANG", MATA_UANG)
    out = {"dibuat": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mataUang": MATA_UANG, "modal": MODAL,
           "asumsi": f"Modal {MODAL:,.0f} {MATA_UANG}{' (akun cent, = $' + format(MODAL / 100, ',.0f') + ')' if MATA_UANG == 'USC' else ''}, risiko {RISIKO * 100:.1f}% saldo per trade (BOT_RISK_PERCENTAGE, lot dinamis dari lebar SL), majemuk, biaya spread+slip dihitung. "
                     "Hanya trade out-of-sample.",
           "strategi": [x for x in (ea_mt5(), sniper(), tren_pullback("scalp"), tren_pullback("intraday")) if x]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"))
    for s in out["strategi"]:
        print(f"{s['nama']}: {s['posisi']} posisi, earnings {s['earnings']:,.2f} {MATA_UANG}, total {s['totalReturn'] * 100:.2f}%, "
              f"annual {'-' if s['annualReturn'] is None else round(s['annualReturn'] * 100, 2)}% ({s['hari']} hari), DD {s['maxDrawdown'] * 100:.2f}%, win {s['winRate'] and s['winRate'] * 100:.1f}%, "
              f"sharpe {s['sharpe']}, sortino {s['sortino']}, calmar {s['calmar']}")
    print(OUT)


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main()
