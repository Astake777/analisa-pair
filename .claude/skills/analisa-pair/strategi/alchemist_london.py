"""Alchemist London killzone: sapu high/low sesi Asia saat London buka, struktur 15m patah, limit dekat ujung sapuan.

Range Asia = 00:00-07:00 UTC (07:00-14:00 WIB) dari candle 15m. Killzone = 08:00-10:00 waktu London
(07-09 UTC saat BST, 08-10 UTC saat GMT). Buy: candle 15m di killzone menembus low Asia (sapuan), lalu dalam
`maks_tunggu` candle ada close 15m di atas high tertinggi `lb` candle sebelum candle ujung sapuan (struktur patah).
SL = ujung sapuan - pad, entry = SL + sl_jarak (tepi pertama zona), TP1 >= tp_min dan >= rr x risk.
Sell kebalikannya dengan high Asia. Satu sinyal per hari per sisi.
Self-check: python strategi/alchemist_london.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import kolom  # noqa: E402
from regime import DST_UK, STEP, _panas  # noqa: E402
from strategi import msnr  # noqa: E402
from strategi.sniper import arah_bias  # noqa: E402

PARAMS = {"cerita": None, "senin": True, "lb": 3, "maks_tunggu": 8, "pad": 0.3, "zona": 2.0, "sl_jarak": 3.5,
          "rr": 3.0, "tp_min": 10.0}
GRID = {"cerita": [None, "4h"], "senin": [True, False], "lb": [2, 4], "sl_jarak": [3.0, 3.5]}
SIM = "1m"
EXPIRE_S = 2 * 3600


def signals(by_tf, mode, params=PARAMS):
    p = params
    t, o, h, l, c = kolom(by_tf["15m"])
    bias = arah_bias(by_tf, [p["cerita"]]) if p["cerita"] else (lambda T: 0)
    hari = {}
    for i, ti in enumerate(t):
        hari.setdefault(ti // 86400, []).append(i)
    out = []
    for d, idx in hari.items():
        if not p["senin"] and (d + 3) % 7 == 0:
            continue
        asia = [i for i in idx if t[i] % 86400 < 7 * 3600]
        if len(asia) < 24:
            continue
        a_hi, a_lo = max(h[i] for i in asia), min(l[i] for i in asia)
        mulai = d * 86400 + (7 if _panas(d * 86400 + 43200, DST_UK) else 8) * 3600
        kz = [i for i in idx if mulai <= t[i] < mulai + 7200]
        for side in (1, -1):
            sweep = next((i for i in kz if (l[i] < a_lo if side > 0 else h[i] > a_hi)), None)
            if sweep is None:
                continue
            ujung = sweep
            for j in range(sweep, min(sweep + p["maks_tunggu"], len(t))):
                if (l[j] < l[ujung]) if side > 0 else (h[j] > h[ujung]):
                    ujung = j
                ref = range(max(0, ujung - p["lb"]), ujung)
                if j == ujung or not ref:
                    continue
                garis = max(h[x] for x in ref) if side > 0 else min(l[x] for x in ref)
                if not ((c[j] > garis) if side > 0 else (c[j] < garis)):
                    continue
                T = t[j] + 900
                ext = l[ujung] if side > 0 else h[ujung]
                entry = ext - side * p["pad"] + side * p["sl_jarak"]
                if (c[j] - entry) * side <= 0 or (p["cerita"] and bias(T) != side):
                    break
                out.append(msnr.pasang(T, side, entry, p,
                                       f"London killzone: sapu {'low' if side > 0 else 'high'} Asia "
                                       f"{a_lo if side > 0 else a_hi:.2f} sampai {ext:.2f}, struktur 15m patah",
                                       asia=[round(a_lo, 2), round(a_hi, 2)]))
                break
    out.sort(key=lambda s: s["time"])
    return out


def _sintetis():
    from data import aggregate
    rows, t0, px = [], 1785715200, 1000.0   # Senin 3 Agu 2026 (BST: killzone 07-09 UTC)
    for i in range(1440 * 10):
        m = i % 1440
        if 420 <= m < 440:
            step = -0.12        # sapu low Asia
        elif 440 <= m < 470:
            step = 0.15         # balik naik, struktur patah
        elif m < 420:
            step = 0.02 if (m // 30) % 2 else -0.02
        else:
            step = 0.0
        o, px = px, px + step
        rows.append([t0 + i * 60, o, max(o, px) + 0.02, min(o, px) - 0.02, px, 1])
        if m == 1439:
            px = 1000.0
    by = {"1m": rows}
    for tf in ("5m", "15m", "30m", "1h", "4h", "1d"):
        by[tf] = aggregate([list(r) for r in rows], STEP[tf])
    return by


def _selftest():
    by = _sintetis()
    s = signals(by, "scalp")
    assert s and all(x["side"] == "buy" for x in s), s[:2]
    for x in s:
        assert abs(x["entry"] - x["sl"] - 3.5) < 1e-6 and x["sl"] < x["asia"][0], x
        assert 7 * 3600 <= x["time"] % 86400 <= 10 * 3600, x
    assert len(signals(by, "scalp", {**PARAMS, "senin": False})) < len(s)
    T = by["1m"][int(len(by["1m"]) * 0.7)][0]
    cut = {tf: [r for r in rows if r[0] + STEP[tf] <= T] for tf, rows in by.items()}
    assert [x for x in s if x["time"] <= T] == signals(cut, "scalp"), "tidak kausal"
    print(f"selftest OK ({len(s)} sinyal)")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
