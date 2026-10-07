"""Alchemist CRT 1H + 15m (sesi NY): range candle 1H jam 13:00 London, arah dari candle 14:00, entry limit 15m.

Candle R = 1H yang buka 13:00 waktu London (12:00 UTC saat BST, 13:00 UTC saat GMT; 19:00/20:00 WIB).
Candle D = jam berikutnya. Arah buy kalau close D di atas high R (arah="hl") atau di atas close R (arah="close");
sell kebalikannya. Entry: level MSNR 15m fresh yang berperan support (buy) / resistance (sell) di dalam range R,
terbentuk sampai D tutup, paling dekat ke harga; tanpa level, tidak ada sinyal. Limit di level itu
(tepi pertama zona), SL sl_jarak di baliknya, TP1 >= tp_min dan >= rr x risk. Berlaku `EXPIRE_S` sejak D tutup.
Opsi: cerita = TF yang EMA20/50-nya harus searah; senin = False untuk melewatkan hari Senin.
Self-check: python strategi/alchemist_crt.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import kolom, tutup  # noqa: E402
from regime import DST_UK, STEP, _panas  # noqa: E402
from strategi import msnr  # noqa: E402
from strategi.sniper import arah_bias  # noqa: E402

PARAMS = {"arah": "hl", "cerita": None, "senin": True, "zona": 2.0, "sl_jarak": 3.5, "rr": 3.0, "tp_min": 10.0,
          "jarak_min": 0.5}
GRID = {"arah": ["hl", "close"], "cerita": [None, "4h"], "senin": [True, False], "sl_jarak": [3.0, 3.5]}
SIM = "1m"
EXPIRE_S = 2 * 3600   # jam ketiga (15:00 London) + 1 jam


def signals(by_tf, mode, params=PARAMS):
    p = params
    t, o, h, l, c = kolom(by_tf["1h"])
    m15 = by_tf["15m"]
    t15 = [r[0] for r in m15]
    bias = arah_bias(by_tf, [p["cerita"]]) if p["cerita"] else (lambda T: 0)
    out = []
    for i in range(len(t) - 1):
        jam = 12 if _panas(t[i], DST_UK) else 13
        if (t[i] // 3600) % 24 != jam or t[i + 1] != t[i] + 3600:
            continue
        if not p["senin"] and (t[i] // 86400 + 3) % 7 == 0:   # 1 Jan 1970 = Kamis
            continue
        d = i + 1
        ref_hi, ref_lo = (h[i], l[i]) if p["arah"] == "hl" else (c[i], c[i])
        side = 1 if c[d] > ref_hi else -1 if c[d] < ref_lo else 0
        T = t[d] + 3600
        if not side or (p["cerita"] and bias(T) != side):
            continue
        a = tutup(t15, 900, t[i] - 900 * 16)       # 4 jam sebelum R sebagai riwayat level
        b = tutup(t15, 900, T)
        if a < 0 or b < 0:
            continue
        win = m15[a:b + 1]
        papan = msnr.Papan()
        lv = msnr.levels(win, 900)
        k = 0
        for r in win:
            while k < len(lv) and lv[k]["t_ok"] <= r[0]:
                papan.tambah(lv[k])
                k += 1
            papan.candle(r[2], r[3], r[4])
        while k < len(lv):
            papan.tambah(lv[k])
            k += 1
        harga = c[d]
        cocok = [z["harga"] for z in papan.fresh("support" if side > 0 else "resistance")
                 if l[i] <= z["harga"] <= h[i] and (harga - z["harga"]) * side >= p["jarak_min"]]
        if not cocok:
            continue
        lvl = max(cocok) if side > 0 else min(cocok)
        out.append(msnr.pasang(T, side, lvl, p,
                               f"CRT 1H: range {l[i]:.2f}-{h[i]:.2f}, candle berikutnya close "
                               f"{'di atas' if side > 0 else 'di bawah'} range; limit di level MSNR 15m fresh "
                               f"{lvl:.2f} di dalam range", range=[round(l[i], 2), round(h[i], 2)]))
    return out


def _sintetis():
    """1m datar 1000 dengan pola harian: jam R naik-turun membentuk V-level 15m, jam D close di atas high R."""
    from data import aggregate
    rows, t0 = [], 1785715200   # Senin 3 Agu 2026 00:00 UTC (BST: R = 12:00 UTC)
    px = 1000.0
    for i in range(60 * 24 * 10):
        m = i % 1440
        if 720 <= m < 735:
            step = -0.1       # R: turun (candle 15m bearish)
        elif 735 <= m < 750:
            step = 0.12       # R: naik (bullish) -> V-level di close bearish
        elif 750 <= m < 780:
            step = 0.0
        elif 780 <= m < 840:
            step = 0.12       # D: naik keluar range
        elif 840 <= m < 900:
            step = -0.15      # jam ketiga: kembali ke range
        else:
            step = 0.0 if m < 720 else -0.0005
        o, px = px, px + step
        rows.append([t0 + i * 60, o, max(o, px) + 0.02, min(o, px) - 0.02, px, 1])
    by = {"1m": rows}
    for tf in ("5m", "15m", "30m", "1h", "4h", "1d"):
        by[tf] = aggregate([list(r) for r in rows], STEP[tf])
    return by


def _selftest():
    by = _sintetis()
    s = signals(by, "scalp")
    assert s and all(x["side"] == "buy" for x in s), s[:2]
    for x in s:
        assert abs(x["entry"] - x["sl"] - 3.5) < 1e-6 and x["zona"] == [round(x["entry"] - 2, 2), x["entry"]], x
        assert x["range"][0] <= x["entry"] <= x["range"][1], x
        assert (x["time"] // 3600) % 24 == 14, x               # BST: D tutup 14:00 UTC
    assert not signals(by, "scalp", {**PARAMS, "jarak_min": 50})
    senin = signals(by, "scalp", {**PARAMS, "senin": False})
    assert len(senin) < len(s) and all((x["time"] // 86400 + 3) % 7 for x in senin)
    T = by["1m"][int(len(by["1m"]) * 0.7)][0]
    cut = {tf: [r for r in rows if r[0] + STEP[tf] <= T] for tf, rows in by.items()}
    assert [x for x in s if x["time"] <= T] == signals(cut, "scalp"), "tidak kausal"
    print(f"selftest OK ({len(s)} sinyal)")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
