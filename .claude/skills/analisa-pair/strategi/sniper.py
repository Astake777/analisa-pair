"""Sniper: limit di order block 5m yang memicu displacement searah bias, SL tipis di balik OB, target RR besar.

Zona = candle berlawanan terakhir sebelum candle displacement (body >= disp x ATR 5m), dan close displacement
harus menembus high/low OB. Entry di `masuk` x lebar OB dari tepi dekat (0 = tepi dekat, 0.5 = tengah),
SL = tepi jauh +/- pad. Risk wajib <= max_sl (3.5 = 35 pips, 1 pip = $0.10) dan >= min_sl.
Bias: EMA20 vs EMA50 di semua TF bias mode harus sepakat.
Self-check: python strategi/sniper.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import atr, ema, kolom, tutup  # noqa: E402
from regime import MODES, STEP  # noqa: E402

PARAMS = {"disp": 1.5, "zona_max": 3.0, "pad": 0.4, "max_sl": 3.5, "min_sl": 1.0, "rr": 3.0, "masuk": 0.5,
          "cari_ob": 3}
SIM = "1m"            # SL beberapa dollar: simulasi di 1m supaya SL/TP dalam satu candle 5m tidak ditebak
EXPIRE_S = 2 * 3600   # limit berlaku 2 jam


def arah_bias(by_tf, tfs):
    """-> fungsi T -> +1/-1/0: EMA20 vs EMA50 candle tutup terakhir di semua tfs sepakat."""
    seri = []
    for tf in tfs:
        t, *_, c = kolom(by_tf[tf])
        seri.append((t, STEP[tf], [0 if a is None or b is None else (1 if a > b else -1)
                                   for a, b in zip(ema(c, 20), ema(c, 50))]))

    def at(T):
        s = set()
        for t, step, arr in seri:
            j = tutup(t, step, T)
            s.add(arr[j] if j >= 0 else 0)
        return s.pop() if len(s) == 1 else 0
    return at


def signals(by_tf, mode, params=PARAMS):
    p = params
    bias = arah_bias(by_tf, MODES[mode]["bias"])
    t, o, h, l, c = kolom(by_tf["5m"])
    a = atr(h, l, c)
    out = []
    for i in range(1, len(t)):
        if a[i - 1] is None or abs(c[i] - o[i]) < p["disp"] * a[i - 1]:
            continue
        side = 1 if c[i] > o[i] else -1
        T = t[i] + STEP["5m"]
        if bias(T) != side:
            continue
        k = next((k for k in range(i - 1, max(-1, i - 1 - p["cari_ob"]), -1)
                  if (c[k] < o[k]) == (side > 0) and c[k] != o[k]), None)
        if k is None or h[k] - l[k] > p["zona_max"]:
            continue
        lo, hi = l[k], h[k]
        if (c[i] <= hi) if side > 0 else (c[i] >= lo):
            continue
        w = hi - lo
        if side > 0:
            entry, sl = hi - p["masuk"] * w, lo - p["pad"]
        else:
            entry, sl = lo + p["masuk"] * w, hi + p["pad"]
        r2 = lambda x: round(x, 2)
        entry, sl = r2(entry), r2(sl)
        risk = abs(entry - sl)
        if not p["min_sl"] <= risk <= p["max_sl"]:
            continue
        out.append({"time": T, "side": "buy" if side > 0 else "sell", "entry": entry, "sl": sl,
                    "tp": [r2(entry + side * p["rr"] * risk)], "zona": [r2(lo), r2(hi)],
                    "alasan": f"OB 5m {'bullish' if side > 0 else 'bearish'} sebelum displacement "
                              f"{abs(c[i] - o[i]) / a[i - 1]:.1f}x ATR, bias {'+'.join(MODES[mode]['bias'])} searah, "
                              f"SL {risk * 10:.0f} pips"})
    return out


def _selftest():
    from strategi import cek_kausal, contoh

    def naik(i):
        # tren naik; tiap 40 candle: koreksi kecil lalu lonjakan +6 (displacement)
        k = i % 40
        return 1000 + 0.05 * i + (-2.0 if k == 37 else 6.0 if k >= 38 else 0.0) + (0.6 if k % 2 else 0)
    up = contoh(naik)
    s = signals(up, "scalp")
    assert s and all(x["side"] == "buy" for x in s), len(s)
    for x in s:
        risk = x["entry"] - x["sl"]
        assert 1.0 <= risk <= 3.5 + 1e-9 and x["zona"][0] <= x["entry"] <= x["zona"][1], x
        assert abs(x["tp"][0] - x["entry"] - 3 * risk) < 0.02, x
    turun = contoh(lambda i: 2 * 1000 - naik(i) + 1000)
    assert all(x["side"] == "sell" for x in signals(turun, "scalp"))
    assert not signals(up, "scalp", {**PARAMS, "max_sl": 0.5})  # SL tidak muat -> tidak ada sinyal
    cek_kausal(signals, up, "scalp")
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
