"""Baseline: ikut tren EMA20/50/200 di TF bias, masuk saat pullback ke EMA20 TF entry dan momentum TF trigger pulih.

Self-check: python strategi/tren_pullback.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import atr, ema, kolom, macd, rsi, tutup  # noqa: E402
from regime import MODES, STEP  # noqa: E402

PARAMS = {"pullback": 5, "rsi_buy": 40, "rsi_sell": 60, "macd_flip": True, "sl_atr": 0.25}


def susunan(c):
    """+1 kalau EMA20>EMA50>EMA200, -1 kalau terbalik, 0 campur. Riwayat < 200 candle: EMA20 vs EMA50 saja."""
    out = []
    for a, b, d in zip(ema(c, 20), ema(c, 50), ema(c, 200)):
        if a is None or b is None:
            out.append(0)
        elif d is None:
            out.append(1 if a > b else -1)
        else:
            out.append(1 if a > b > d else -1 if a < b < d else 0)
    return out


def _filter(c):
    return [0 if a is None or b is None else (1 if a > b else -1) for a, b in zip(ema(c, 20), ema(c, 50))]


def signals(by_tf, mode, params=PARAMS):
    m, p = MODES[mode], params
    arah = []  # semua harus sepakat (dan bukan 0)
    for tf in m["bias"]:
        t, *_, c = kolom(by_tf[tf])
        arah.append((t, STEP[tf], susunan(c)))
    if m["filter"]:
        t, *_, c = kolom(by_tf[m["filter"]])
        arah.append((t, STEP[m["filter"]], _filter(c)))
    et, eo, eh, el, ec = kolom(by_tf[m["entry"]])
    es = STEP[m["entry"]]
    e20, e50, ea = ema(ec, 20), ema(ec, 50), atr(eh, el, ec)
    tt, to, th, tl, tc = kolom(by_tf[m["trigger"]])
    ts = STEP[m["trigger"]]
    r, hist = rsi(tc), macd(tc)[2]
    n = p["pullback"]
    span = n * es // ts  # candle trigger yang menutupi jendela pullback
    out = []
    for i in range(1, len(tt)):
        if r[i - 1] is None or hist[i - 1] is None:
            continue
        T = tt[i] + ts
        sides = set()
        for st, s, arr in arah:
            j = tutup(st, s, T)
            sides.add(arr[j] if j >= 0 else 0)
        if len(sides) != 1 or 0 in sides:
            continue
        side = sides.pop()
        j = tutup(et, es, T)
        if j < n or e20[j - n + 1] is None or e50[j] is None or ea[j] is None:
            continue
        win = range(j - n + 1, j + 1)
        if side > 0:
            rsi_ok = r[i - 1] < p["rsi_buy"] <= r[i]
            mom = rsi_ok or (p["macd_flip"] and hist[i - 1] <= 0 < hist[i])
            ok = mom and any(el[x] <= e20[x] for x in win) and ec[j] > e50[j]
        else:
            rsi_ok = r[i - 1] > p["rsi_sell"] >= r[i]
            mom = rsi_ok or (p["macd_flip"] and hist[i - 1] >= 0 > hist[i])
            ok = mom and any(eh[x] >= e20[x] for x in win) and ec[j] < e50[j]
        if not ok:
            continue
        lo = max(0, i - span + 1)
        entry = tc[i]
        sl = min(tl[lo:i + 1]) - p["sl_atr"] * ea[j] if side > 0 else max(th[lo:i + 1]) + p["sl_atr"] * ea[j]
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        sisi = "buy" if side > 0 else "sell"
        out.append({"time": T, "side": sisi, "entry": entry, "sl": sl, "tp": [entry + side * m["target"] * risk],
                    "alasan": f"EMA20/50/200 {'naik' if side > 0 else 'turun'} di {'+'.join(m['bias'])}, "
                              f"pullback ke EMA20 {m['entry']}, {'RSI' if rsi_ok else 'MACD hist'} "
                              f"{m['trigger']} berbalik"})
    return out


def _selftest():
    import math
    from strategi import cek_kausal, contoh
    up = contoh(lambda i: 1000 + 0.05 * i + 2 * math.sin(i / 8))
    s = signals(up, "scalp")
    assert s and all(x["side"] == "buy" for x in s), len(s)
    for x in s:
        assert x["sl"] < x["entry"] < x["tp"][0] and abs((x["tp"][0] - x["entry"]) - 1.5 * (x["entry"] - x["sl"])) < 1e-6
    dn = contoh(lambda i: 3000 - 0.05 * i + 2 * math.sin(i / 8))
    s = signals(dn, "intraday")
    assert s and all(x["side"] == "sell" and x["tp"][0] < x["entry"] < x["sl"] for x in s), len(s)
    assert not signals(contoh(lambda i: 1000 + 2 * math.sin(i / 8)), "scalp")  # tanpa tren: diam
    cek_kausal(signals, up, "scalp")
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
