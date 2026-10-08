"""Kinerja backtest untuk panel website: equity curve dan metrik dari trade OUT-OF-SAMPLE saja.

Asumsi akun: modal MODAL dollar, risiko per trade = BOT_RISK_PERCENTAGE di .env (25-30% saldo), majemuk; R bersih biaya.
Sumber: sniper = trade OOS walk-forward terbaru validasi.py; scalp/intraday = sample OOS tren_pullback backtest.py terbaru.
Pakai:  python kinerja.py            menulis web/public/kinerja.json
Self-check: python kinerja.py --selftest
"""
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


def bot_hfm():
    """Sniper dengan aturan bot di data HFM dari backtest_bot.py terbaru (seluruh sampel, dalam dollar)."""
    f = terbaru(os.path.join(BT, "bot_*.json"))
    if not f:
        return None
    rep = json.load(open(f, encoding="utf-8"))
    h = rep["hasil"].get("sniper")
    if not h or not h["kurva"]:
        return None
    modal, kurva = rep["modal"], [[rep["dari"], rep["modal"]]] + h["kurva"]
    tr, prev = [], modal
    for t, eq in h["kurva"]:   # r_net setara: perubahan ekuitas dibagi risiko yang dipakai
        tr.append({"masuk": t, "keluar": t, "r_net": (eq / prev - 1) / rep["risiko"]})
        prev = eq
    global RISIKO, MODAL
    lama = RISIKO, MODAL
    RISIKO, MODAL = rep["risiko"], modal
    m = metrik(tr, rep["dari"], rep["sampai"])
    RISIKO, MODAL = lama
    m["kurva"] = kurva
    return {"nama": "Bot HFM", "status": "aturan bot, data broker",
            "sumber": "backtest_bot.py: data HFM, lot dibulatkan 0.01, batas harian, jendela news, seluruh sampel (bukan OOS)",
            **m}


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
           "strategi": [x for x in (bot_hfm(), sniper(), tren_pullback("scalp"), tren_pullback("intraday")) if x]}
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
