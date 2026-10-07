"""Sniper: zona pantul kuat (POI) di 15m/1h, entry presisi di 1m setelah sweep + CHoCH, SL tipis, TP >= 100 pips.

POI = candle berlawanan terakhir (OB) sebelum candle displacement (body >= disp x ATR) yang meninggalkan FVG,
searah bias (EMA20 vs EMA50 di semua TF bias mode). Skor konfluensi +1 per: pivot harian di dalam zona,
angka bulat kelipatan $5 di dalam zona, OB menyapu low/high swing sebelumnya, FVG >= 0.5 ATR.
Entry: harga masuk POI, buat ekstrem (sweep), lalu close 1m menembus high/low `lb` candle sebelum ekstrem (CHoCH).
SL = ekstrem -/+ pad. Tepi pertama zona = SL +/- sl_jarak (30-35 pips, 1 pip = $0.10), zona selebar `zona`
(20 pips) dari tepi pertama ke arah SL. Limit di tepi pertama, jadi risk = sl_jarak. TP1 = entry +/- max(tp_min,
rr x risk). Satu POI = satu sinyal; POI hangus kalau close 1m menembus sisi jauh - pad sebelum CHoCH,
atau umurnya lewat.
Self-check: python strategi/sniper.py --selftest
Sweep parameter (in-sample memilih, out-of-sample melapor): python strategi/sniper.py --sweep [mode]
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import atr, ema, kolom, tutup  # noqa: E402
from regime import MODES, STEP  # noqa: E402
from strategi import msnr  # noqa: E402

# validasi.py 8 Okt 2026 (200 hari): kombinasi terbaik seluruh sampel dan lipatan OOS terakhir
PARAMS = {"poi": "15m", "disp": 1.5, "cari_ob": 3, "zona_max": 10.0, "min_skor": 2, "umur_jam": 12,
          "lb": 3, "pad": 0.5, "zona": 2.0, "sl_jarak": 3.0, "rr": 3.0, "tp_min": 10.0, "msnr": True}
SIM = "1m"            # SL beberapa dollar: simulasi di 1m
EXPIRE_S = 3600       # limit berlaku 1 jam setelah CHoCH
GRID = {"msnr": [False, True], "disp": [1.2, 1.5], "min_skor": [1, 2], "lb": [2, 3], "sl_jarak": [3.0, 3.5]}  # POI 5m terbukti rugi (sweep 8 Okt)


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


def pivot_hari(d1, T):
    """Pivot klasik dari candle harian tutup terakhir sebelum T -> [P, R1, S1, R2, S2] atau []."""
    t, o, h, l, c = kolom(d1)
    j = tutup(t, STEP["1d"], T)
    if j < 0:
        return []
    H, L, C = h[j], l[j], c[j]
    p = (H + L + C) / 3
    return [p, 2 * p - L, 2 * p - H, p + (H - L), p - (H - L)]


def poi_list(by_tf, mode, p):
    """-> [{t_ok, side, lo, hi, skor, alasan}] urut waktu diketahui."""
    tf, step = p["poi"], STEP[p["poi"]]
    bias = arah_bias(by_tf, MODES[mode]["bias"])
    t, o, h, l, c = kolom(by_tf[tf])
    a = atr(h, l, c)
    out = []
    papan, lv, nlv, maju = msnr.Papan(), msnr.levels(by_tf[tf], step), 0, 0
    for i in range(4, len(t) - 1):
        if p.get("msnr"):  # papan level MSNR diperbarui sampai candle i+1 (saat POI diketahui)
            while maju <= i + 1:
                while nlv < len(lv) and lv[nlv]["t_ok"] <= t[maju]:
                    papan.tambah(lv[nlv])
                    nlv += 1
                papan.candle(h[maju], l[maju], c[maju])
                maju += 1
        if a[i - 1] is None or abs(c[i] - o[i]) < p["disp"] * a[i - 1]:
            continue
        side = 1 if c[i] > o[i] else -1
        gap = (l[i + 1] - h[i - 1]) if side > 0 else (l[i - 1] - h[i + 1])
        if gap <= 0:
            continue
        T = t[i + 1] + step
        if bias(T) != side:
            continue
        k = next((k for k in range(i - 1, max(-1, i - 1 - p["cari_ob"]), -1)
                  if (c[k] < o[k]) == (side > 0) and c[k] != o[k]), None)
        if k is None or h[k] - l[k] > p["zona_max"]:
            continue
        lo, hi = l[k], h[k]
        alasan, skor = [], 0
        if any(lo - 0.5 <= v <= hi + 0.5 for v in pivot_hari(by_tf["1d"], t[k])):
            skor += 1
            alasan.append("pivot harian")
        if int(hi // 5) * 5 >= lo:
            skor += 1
            alasan.append("angka bulat")
        sebelum = range(max(0, k - 20), k)
        if sebelum and ((lo < min(l[x] for x in sebelum)) if side > 0 else (hi > max(h[x] for x in sebelum))):
            skor += 1
            alasan.append("sapu likuiditas")
        if gap >= 0.5 * a[i - 1]:
            skor += 1
            alasan.append("FVG lebar")
        if p.get("msnr"):
            peran = "support" if side > 0 else "resistance"
            if not any(lo - 0.5 <= z["harga"] <= hi + 0.5 for z in papan.fresh(peran)):
                continue
            skor += 1
            alasan.append("level MSNR fresh")
        if skor >= p["min_skor"]:
            out.append({"t_ok": T, "side": side, "lo": lo, "hi": hi, "skor": skor, "tf": tf,
                        "alasan": ", ".join(alasan) or "tanpa konfluensi tambahan"})
    return out


def signals(by_tf, mode, params=PARAMS, stat=None):
    """stat (dict, opsional) diisi hitungan corong: poi, masuk, choch."""
    p = params
    pois = poi_list(by_tf, mode, p)
    stat = stat if stat is not None else {}
    stat.update(poi=len(pois), masuk=0, choch=0)
    t, o, h, l, c = kolom(by_tf["1m"])
    out, aktif, nxt = [], [], 0
    umur = p["umur_jam"] * 3600
    r2 = lambda x: round(x, 2)
    for j in range(len(t)):
        T = t[j] + 60
        while nxt < len(pois) and pois[nxt]["t_ok"] <= t[j]:
            aktif.append({**pois[nxt], "masuk_i": None, "ext_i": None})
            nxt += 1
        tetap = []
        for z in aktif:
            s, lo, hi = z["side"], z["lo"], z["hi"]
            if t[j] > z["t_ok"] + umur:
                continue
            if (c[j] < lo - p["pad"]) if s > 0 else (c[j] > hi + p["pad"]):
                continue  # zona jebol
            if z["masuk_i"] is None:
                if (l[j] <= hi) if s > 0 else (h[j] >= lo):
                    z["masuk_i"] = z["ext_i"] = j
                    stat["masuk"] += 1
                tetap.append(z)
                continue
            if (l[j] < l[z["ext_i"]]) if s > 0 else (h[j] > h[z["ext_i"]]):
                z["ext_i"] = j
            e = z["ext_i"]
            ref = range(max(0, e - p["lb"]), e)
            if j == e or not ref:
                tetap.append(z)
                continue
            garis = max(h[x] for x in ref) if s > 0 else min(l[x] for x in ref)
            if not ((c[j] > garis) if s > 0 else (c[j] < garis)):
                tetap.append(z)
                continue
            stat["choch"] += 1
            ujung = l[e] if s > 0 else h[e]
            sl = r2(ujung - s * p["pad"])
            entry = r2(sl + s * p["sl_jarak"])
            risk = p["sl_jarak"]
            jauh = r2(entry - s * p["zona"])
            out.append({"time": T, "side": "buy" if s > 0 else "sell", "entry": entry, "sl": sl,
                        "tp": [r2(entry + s * max(p["tp_min"], p["rr"] * risk))],
                        "zona": sorted([entry, jauh]), "poi": [r2(lo), r2(hi)], "skor": z["skor"],
                        "alasan": f"POI {z['tf']} {'demand' if s > 0 else 'supply'} {lo:.2f}-{hi:.2f} "
                                  f"({z['alasan']}), sweep {ujung:.2f} lalu CHoCH 1m; zona {p['zona'] * 10:.0f} pips, "
                                  f"SL {risk * 10:.0f} pips dari tepi pertama"})
            # POI terpakai setelah CHoCH
        aktif = tetap
    return out


def _potong(by_tf, T):
    return {tf: [r for r in rows if r[0] + STEP[tf] <= T] for tf, rows in by_tf.items()}


def _sintetis():
    """1m: tren naik, koreksi, displacement (OB + FVG 15m), kembali ke POI, sweep, CHoCH; siklus 12 jam."""
    from data import aggregate
    rows, px, t0 = [], 1000.0, 1785715200
    for i in range(60 * 24 * 12):
        k = i % 720
        if k < 300:
            step = 0.02
        elif k < 315:
            step = -0.35 if k < 312 else 0.0
        elif k < 330:
            step = 3.0 if k < 318 else 0.05
        elif k < 600:
            step = 0.01
        elif k < 640:
            step = -0.25
        elif k < 645:
            step = -0.3
        elif k < 650:
            step = 0.6
        else:
            step = 0.05
        o, px = px, px + step
        rows.append([t0 + i * 60, o, max(o, px) + 0.05, min(o, px) - 0.05, px, 1])
    by = {"1m": rows}
    for tf in ("5m", "15m", "30m", "1h", "4h", "1d"):
        by[tf] = aggregate([list(r) for r in rows], STEP[tf])
    return by


def _selftest():
    by = _sintetis()
    p = {**PARAMS, "min_skor": 0}
    s = signals(by, "scalp", p)
    assert s and all(x["side"] == "buy" for x in s), len(s)
    for x in s:
        risk = x["entry"] - x["sl"]
        assert abs(risk - p["sl_jarak"]) < 0.011 and x["zona"] == [round(x["entry"] - 2, 2), x["entry"]], x
        assert x["tp"][0] - x["entry"] >= max(10, 3 * risk) - 0.011, x
    assert not signals(by, "scalp", {**p, "min_skor": 5})
    st = {}
    signals(by, "scalp", p, st)
    assert st["poi"] >= st["masuk"] >= st["choch"] >= len(s) > 0, st
    T = by["1m"][int(len(by["1m"]) * 0.7)][0]
    full = [x for x in s if x["time"] <= T]
    assert full == signals(_potong(by, T), "scalp", p), "tidak kausal"
    print(f"selftest OK ({len(s)} sinyal sintetis)")


def sweep(mode="scalp"):
    import data
    from backtest import IS_DAYS, OOS_DAYS, metrik, simulasi
    from regime import TFS
    by = data.bersih(data.load("XAUUSD", TFS + ["1m"], source="binance", spot=False))
    end = by["5m"][-1][0] + 300
    oos0, is0 = end - OOS_DAYS * 86400, end - (IS_DAYS + OOS_DAYS) * 86400
    hasil, keys = [], list(GRID)
    for combo in itertools.product(*GRID.values()):
        p = {**PARAMS, **dict(zip(keys, combo))}
        st = {}
        sig = [s for s in signals(by, mode, p, st) if s["time"] >= is0]
        tr = simulasi(by["1m"], sig, 60, EXPIRE_S // 60)
        ins, oos = metrik([x for x in tr if x["masuk"] < oos0]), metrik([x for x in tr if x["masuk"] >= oos0])
        hasil.append((dict(zip(keys, combo)), ins, oos, tr))
        print(f"corong {dict(zip(keys, combo))}: POI {st['poi']} -> masuk zona {st['masuk']} -> CHoCH {st['choch']} "
              f"-> sinyal 60 hari {len(sig)} -> terisi {len(tr)}", flush=True)
    f = lambda m: "-" if m["trades"] == 0 else \
        f"{m['trades']:>3} tr  win {m['winrate'] * 100:>3.0f}%  exp {m['expectancy']:+.2f}R  PF {m['profit_factor']}"
    for kombi, ins, oos, _ in sorted(hasil, key=lambda x: -(x[1]["expectancy"] if x[1]["trades"] else -9)):
        print(f"{kombi}\n   IS  {f(ins)}\n   OOS {f(oos)}")
    layak = [x for x in hasil if x[1]["trades"] >= 10]
    if layak:
        best = max(layak, key=lambda x: x[1]["expectancy"])
        menang = sorted(x["floating"] for x in best[3] if x["r_net"] > 0)
        print(f"\nTERPILIH (expectancy in-sample tertinggi, >= 10 trade): {best[0]}\n   IS  {f(best[1])}\n   OOS {f(best[2])}")
        if menang:
            print(f"   floating trade menang: median ${menang[len(menang) // 2]:.2f}, terburuk ${menang[-1]:.2f}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    elif sys.argv[1:2] == ["--sweep"]:
        sweep(*(sys.argv[2:3] or ["scalp"]))
