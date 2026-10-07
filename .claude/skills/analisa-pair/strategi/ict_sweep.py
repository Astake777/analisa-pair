"""ICT: sweep swing TF bias -> CHoCH di TF entry -> limit di FVG displacement, TP di swing lawan (draw on liquidity).

Sell (buy = harga dicerminkan):
  1. candle sweep TF bias utama: high > swing high terakhir yang belum diambil dan close < swing high itu;
     candle berikutnya tidak melewati high sweep dan tidak close di atas swing high.
  2. dalam choch_bars candle TF entry sejak candle sweep: close < swing low terakhir yang sudah pasti (CHoCH).
  3. entry = limit di FVG bearish dalam leg displacement (fvg_entry 0.5 = tengah FVG), tanpa FVG: 50% leg.
     SL = high sweep (sl="ob": high order block) + sl_atr x ATR TF entry. Opsi ote: entry wajib di 0.618-0.786.
  4. TP = swing low TF bias terdekat yang belum tersentuh di bawah entry; reward < min_r x risk -> lewati.
Self-check: python strategi/ict_sweep.py --selftest
"""
import os
import sys
from bisect import bisect_left, bisect_right

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import atr, fvg, kolom, swings, tutup  # noqa: E402
from regime import MODES, STEP  # noqa: E402

PARAMS = {"swing_n": 2, "choch_bars": 48, "fvg_entry": 0.5, "sl": "sweep", "sl_atr": 0.1,
          "min_r": 1.0, "ote": False, "tp_lookback": 300}


def _terakhir(sw, conf, k):
    """Swing terakhir yang sudah pasti di candle k."""
    x = bisect_right(conf, k) - 1
    return sw[x] if x >= 0 else None


def _cermin(rows):
    """Harga dinegatifkan: sweep bawah jadi sweep atas, sehingga logika sell dipakai untuk buy."""
    return [[r[0], -r[1], -r[3], -r[2], -r[4], r[5]] for r in rows]


def _sell(by_tf, mode, p):
    m = MODES[mode]
    htf, etf = m["bias"][0], m["entry"]
    hs, es, n = STEP[htf], STEP[etf], p["swing_n"]
    ht, ho, hh, hl, hc = kolom(by_tf[htf])
    et, eo, eh, el, ec = kolom(by_tf[etf])
    ea = atr(eh, el, ec)
    sh, sl_ = swings(hh, hl, n)
    conf_sh, low_idx = [i + n for i, _ in sh], {i for i, _ in sl_}
    esl = swings(eh, el, n)[1]
    conf_esl = [i + n for i, _ in esl]
    gaps = [g for g in fvg(eh, el) if g[1] == "sell"]
    gap_i = [g[0] for g in gaps]
    out = []
    for j in range(1, len(ht) - 1):
        s = _terakhir(sh, conf_sh, j - 1)
        if not s or max(hh[s[0] + 1:j], default=-1e18) > s[1]:  # tidak ada swing / sudah diambil
            continue
        lvl = s[1]
        if not (hh[j] > lvl > hc[j] and hh[j + 1] <= hh[j] and hc[j + 1] < lvl):
            continue
        T0 = ht[j + 1] + hs  # sweep pasti saat candle konfirmasi tutup
        a = bisect_left(et, ht[j])
        for mm in range(a, min(a + p["choch_bars"], len(et))):
            low = _terakhir(esl, conf_esl, mm - 1)
            if not low or ec[mm] >= low[1] or ea[mm] is None:
                continue
            T = max(et[mm] + es, T0)
            hi_i = max(range(a, mm + 1), key=lambda x: eh[x])
            leg_hi, leg_lo = eh[hi_i], min(el[hi_i:mm + 1])
            g = gaps[bisect_left(gap_i, hi_i + 2):bisect_right(gap_i, mm)]
            if g:
                _, _, glo, ghi = g[-1]
                entry, zona = glo + p["fvg_entry"] * (ghi - glo), [glo, ghi]
            else:
                entry, zona = leg_hi - 0.5 * (leg_hi - leg_lo), None
            top = hh[j]
            if p["sl"] == "ob":  # order block: candle naik terakhir sebelum displacement
                ob = next((x for x in range(hi_i, a - 1, -1) if ec[x] > eo[x]), None)
                top = eh[ob] if ob is not None else top
            sl = max(top, leg_hi) + p["sl_atr"] * ea[mm]
            risk = sl - entry
            if risk <= 0 or p["ote"] and not 0.618 <= (entry - leg_lo) / (leg_hi - leg_lo) <= 0.786:
                break
            # swing low yang belum tersentuh makin ke belakang makin rendah -> yang pertama ketemu = terdekat
            jc, run, tp = tutup(ht, hs, T), min(leg_lo, entry), None
            for k in range(jc, max(-1, jc - p["tp_lookback"]), -1):
                if k in low_idx and k + n <= jc and hl[k] <= run:  # sama = tersentuh, belum tembus
                    tp = hl[k]
                    break
                run = min(run, hl[k])
            if tp is not None and entry - tp >= p["min_r"] * risk:
                out.append({"time": T, "entry": entry, "sl": sl, "tp": [tp], "zona": zona,
                            "info": (htf, lvl, hh[j], etf, low[1])})
            break
    return out


def signals(by_tf, mode, params=PARAMS):
    out = []
    for side, k, sig in [("sell", 1, s) for s in _sell(by_tf, mode, params)] + \
            [("buy", -1, s) for s in _sell({tf: _cermin(r) for tf, r in by_tf.items()}, mode, params)]:
        htf, lvl, ext, etf, choch = (x * k if isinstance(x, float | int) else x for x in sig.pop("info"))
        z = sig["zona"] and sorted(x * k for x in sig["zona"])
        atas = side == "sell"
        out.append({**sig, "side": side, "entry": sig["entry"] * k, "sl": sig["sl"] * k,
                    "tp": [x * k for x in sig["tp"]], "zona": z,
                    "alasan": f"sweep swing {'high' if atas else 'low'} {htf} {lvl:.2f} ({'high' if atas else 'low'} "
                              f"{ext:.2f}), CHoCH {etf} {'di bawah' if atas else 'di atas'} {choch:.2f}, entry "
                              f"{'FVG' if z else '50% leg'}, TP swing {'low' if atas else 'high'} {htf}"})
    return sorted(out, key=lambda s: s["time"])


def _selftest():
    from strategi import T0, cek_kausal, potong
    rows, t = [], T0

    def garis(a, b, k):  # k candle 5m garis lurus a -> b
        nonlocal t
        for x in range(k):
            o, c = a + (b - a) * x / k, a + (b - a) * (x + 1) / k
            rows.append([t, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 1])
            t += 300

    for a, b in [(1000, 1040), (1040, 1080), (1080, 1100), (1100, 1060), (1060, 1040), (1040, 1070), (1070, 1090)]:
        garis(a, b, 48)
    garis(1090, 1086, 4), garis(1086, 1092, 8)        # 1h: swing low 5m kecil di 1086
    garis(1092, 1108, 6), garis(1108, 1095, 6)        # 1h sweep: high 1108.5 > swing high 1100.5, close 1095
    garis(1095, 1060, 12), garis(1060, 1060, 96)      # 1h konfirmasi + displacement, CHoCH < 1085.5
    by = potong({"5m": rows}, t)
    s = signals(by, "scalp")
    assert len(s) == 1 and s[0]["side"] == "sell", s
    x = s[0]
    assert x["sl"] > 1108.5 > x["entry"] and x["tp"] == [1039.5] and x["zona"], x  # TP = swing low 1h 1040
    assert x["time"] == T0 + (7 * 48 + 36) * 300, x["time"]                        # tutup candle konfirmasi
    b = signals({tf: _cermin(r) for tf, r in by.items()}, "scalp")
    assert len(b) == 1 and b[0]["side"] == "buy" and abs(b[0]["entry"] + x["entry"]) < 1e-9, b
    assert b[0]["sl"] < b[0]["entry"] < b[0]["tp"][0] == -1039.5 and "swing high 1h" in b[0]["alasan"], b
    cek_kausal(signals, by, "scalp", 0.9)
    cek_kausal(signals, by, "scalp", 0.76)
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
