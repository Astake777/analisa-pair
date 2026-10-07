"""AMD (Accumulation - Manipulation - Distribution) per hari WIB.

A: range Asia 07:00 WIB s/d buka London (15m), valid kalau lebar <= lebar_max x ATR(14, 1h).
M: di kill zone London (buka London + kill jam) wick melewati range +/- buf x ATR 15m, lalu close kembali
   di dalam range dalam `balik` candle 15m. Sisi lawan juga tersapu (wick lewat, close kembali) = invalid.
D: displacement 15m (body >= disp x ATR 15m) atau FVG berlawanan arah sweep, plus BOS (close 5m melewati
   swing 5m terakhir di sisi lawan), paling lambat akhir London. Entry di FVG (tengah) atau 50% leg,
   SL = ekstrem sweep +/- buf x ATR 15m, TP1 = sisi lawan range Asia, TP2 = rr2 x R.
Self-check: python strategi/amd.py --selftest
"""
import datetime as dt
import os
import sys
from bisect import bisect_left, bisect_right

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import atr, fvg, kolom, swings, tutup  # noqa: E402
from regime import WIB, jam_sesi  # noqa: E402

PARAMS = {"lebar_max": 1.75, "buf": 0.1, "balik": 3, "disp": 1.5, "kill": 3, "rr2": 2.0, "swing_n": 2}


def _prep(by_tf, p):
    P = {}
    P["t"], P["o"], P["h"], P["l"], P["c"] = kolom(by_tf["15m"])
    P["a"] = atr(P["h"], P["l"], P["c"])
    t1, o1, h1, l1, c1 = kolom(by_tf["1h"])
    P["t1"], P["a1"] = t1, atr(h1, l1, c1)
    P["t5"], _, P["h5"], P["l5"], P["c5"] = kolom(by_tf["5m"])
    sh, sl = swings(P["h5"], P["l5"], p["swing_n"])
    P["sw"] = {"atas": sl, "bawah": sh}  # BOS sweep atas = tembus swing low, dan sebaliknya
    P["conf"] = {k: [i + p["swing_n"] for i, _ in v] for k, v in P["sw"].items()}
    P["fvg"] = {g[0]: g for g in fvg(P["h"], P["l"])}
    return P


def _hari(P, d0, until, p):
    """Status AMD hari WIB yang mulai di d0 (detik UTC), hanya memakai candle yang tutup sebelum `until`."""
    st = {"tanggal": dt.datetime.fromtimestamp(d0 + WIB, dt.timezone.utc).strftime("%Y-%m-%d"),
          "fase": "-", "range": None, "sweep": None, "sinyal": None, "catatan": ""}
    t, h, l, c, o, a15 = P["t"], P["h"], P["l"], P["c"], P["o"], P["a"]
    _, lon, ny, _ = jam_sesi(d0 + 12 * 3600)
    A0, L0, L1 = d0 + 7 * 3600, d0 + lon * 3600, d0 + ny * 3600
    Lk = L0 + p["kill"] * 3600
    tutup_ = lambda k: t[k] + 900 <= until

    def bar(a, b):
        return [k for k in range(bisect_left(t, a), bisect_left(t, b)) if tutup_(k)]

    asia = bar(A0, L0)
    if not asia:
        st["catatan"] = "belum ada candle sesi Asia"
        return st
    lo, hi = min(l[k] for k in asia), max(h[k] for k in asia)
    st["range"] = (lo, hi)
    if until < L0:
        st.update(fase="A", catatan="range Asia sedang terbentuk")
        return st
    a1 = P["a1"][tutup(P["t1"], 3600, L0)]
    if a1 is None or hi - lo > p["lebar_max"] * a1:
        st.update(fase="trend day", catatan=f"range Asia {hi - lo:.1f} > {p['lebar_max']} x ATR 1h, AMD tidak valid")
        return st
    # M: sweep pertama yang close kembali ke dalam range
    man = None
    kill = bar(L0, Lk)
    for k in kill:
        a = a15[k - 1]
        if a is None:
            continue
        up, dn = h[k] > hi + p["buf"] * a, l[k] < lo - p["buf"] * a
        if up and dn:
            st.update(catatan="kedua sisi tersapu di satu candle (invalid)")
            return st
        if not (up or dn):
            continue
        sisi = "atas" if up else "bawah"
        back = [q for q in range(k, min(k + p["balik"], len(t))) if tutup_(q)
                and (c[q] < hi if up else c[q] > lo)]
        if back:
            man = (sisi, k, back[0], a)
            break
    if man is None:
        if kill and until >= Lk:
            luar = c[kill[-1]] > hi or c[kill[-1]] < lo
            st.update(fase="trend day" if luar else "-",
                      catatan="tembus range tanpa kembali" if luar else "tidak ada sweep di kill zone London")
        else:
            st.update(fase="A", catatan="range valid, menunggu sweep di London")
        return st
    sisi, k, q, a = man
    atas = sisi == "atas"
    sgn = 1 if atas else -1
    Tm = t[q] + 900
    ext = max(h[k:q + 1]) if atas else min(l[k:q + 1])
    st.update(fase="M", sweep={"sisi": sisi, "harga": ext, "waktu": t[k]},
              catatan=f"sweep {sisi} range Asia, tunggu displacement + BOS sampai akhir London")
    # D: scan 5m sampai akhir London
    t5, h5, l5, c5 = P["t5"], P["h5"], P["l5"], P["c5"]
    sw, conf = P["sw"][sisi], P["conf"][sisi]
    bos_t = disp_t = None
    gap = None
    r = k + 1
    b0 = bisect_left(t5, t[k])
    for b in range(b0, len(t5)):
        Tb = t5[b] + 300
        if Tb > min(until, L1):
            break
        ext = max(ext, h5[b]) if atas else min(ext, l5[b])
        x = bisect_right(conf, b - 1) - 1
        if bos_t is None and x >= 0 and (c5[b] < sw[x][1] if atas else c5[b] > sw[x][1]):
            bos_t = Tb
        while r < len(t) and t[r] + 900 <= Tb:
            # sisi lawan tersapu = wick lewat lalu close kembali di dalam (close di luar = distribusi)
            if (l[r] < lo - p["buf"] * a and c[r] > lo) if atas else (h[r] > hi + p["buf"] * a and c[r] < hi):
                st.update(fase="-", catatan="kedua sisi range tersapu (invalid)")
                return st
            body = (o[r] - c[r]) * sgn
            g = P["fvg"].get(r)
            if g and g[1] == ("sell" if atas else "buy") and g[0] - 2 >= k:
                gap = g
            if disp_t is None and (body >= p["disp"] * a15[r - 1] or gap):
                disp_t = t[r] + 900
            r += 1
        if bos_t and disp_t and Tb >= Tm:
            if gap:
                entry, zona = (gap[2] + gap[3]) / 2, [gap[2], gap[3]]
            else:
                ujung = min(l5[b0:b + 1]) if atas else max(h5[b0:b + 1])
                entry, zona = (ext + ujung) / 2, None
            sl = ext + sgn * p["buf"] * a
            risk = (sl - entry) * sgn
            if risk <= 0:
                st.update(fase="-", catatan="entry di luar SL (invalid)")
                return st
            tp1 = lo if atas else hi
            tp = sorted({x for x in (tp1, entry - sgn * p["rr2"] * risk) if (entry - x) * sgn > 0},
                        key=lambda x: (entry - x) * sgn)
            st.update(fase="D", sweep={**st["sweep"], "harga": ext},
                      catatan=f"distribusi: displacement/FVG + BOS 5m setelah sweep {sisi}",
                      sinyal={"time": Tb, "side": "sell" if atas else "buy", "entry": entry, "sl": sl, "tp": tp,
                              "zona": zona, "alasan": f"AMD: range Asia {lo:.2f}-{hi:.2f}, sweep {sisi} "
                                                      f"{ext:.2f} di London, {'FVG' if gap else 'displacement'} "
                                                      f"+ BOS 5m"})
            return st
    if until >= L1:
        st.update(fase="-", catatan="tidak ada BOS sampai akhir London (invalid)")
    return st


def _hari_wib(t):
    return t - (t + WIB) % 86400


def signals(by_tf, mode, params=PARAMS):
    """Sama untuk semua mode: AMD selalu memakai 1h/15m/5m."""
    P = _prep(by_tf, params)
    if not P["t"]:
        return []
    out = []
    end = P["t"][-1] + 900
    for d0 in range(_hari_wib(P["t"][0]), end, 86400):
        s = _hari(P, d0, end, params)["sinyal"]
        if s:
            out.append(s)
    return out


def fase_sekarang(by_tf, now, params=PARAMS):
    """-> {tanggal, fase, rangeAsia, sweep, catatan} untuk hari WIB berjalan."""
    st = _hari(_prep(by_tf, params), _hari_wib(now), now, params)
    iso = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sw = st["sweep"]
    return {"tanggal": st["tanggal"], "fase": st["fase"],
            "rangeAsia": {"lo": round(st["range"][0], 2), "hi": round(st["range"][1], 2)} if st["range"] else None,
            "sweep": {"sisi": sw["sisi"], "harga": round(sw["harga"], 2), "waktu": iso(sw["waktu"])} if sw else None,
            "catatan": st["catatan"]}


def _selftest():
    import math
    from strategi import T0, cek_kausal, potong
    rows, t = [], T0
    d0 = T0 + 86400 + 17 * 3600  # Rabu 5 Agu 2026 00:00 WIB

    def garis(a, b, k):
        nonlocal t
        for x in range(k):
            o, c = a + (b - a) * x / k, a + (b - a) * (x + 1) / k
            rows.append([t, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 1])
            t += 300

    while t < d0 + 7 * 3600 - 300:                            # sebelum Asia: volatil
        garis(1000 + 15 * math.sin(len(rows) / 6), 1000 + 15 * math.sin((len(rows) + 1) / 6), 1)
    garis(rows[-1][4], 1000, 1)
    while t < d0 + 14 * 3600:                                 # Asia: sempit 996-1004
        garis(rows[-1][4], 1000 + 4 * math.sin(len(rows) / 6), 1)
    garis(rows[-1][4], 1012, 1), garis(1012, 1006, 1), garis(1006, 1001, 1)   # 15m sweep atas, close kembali
    garis(1001, 997, 3), garis(997, 975, 3), garis(975, 975, 60)       # displacement + FVG + BOS, lalu diam
    by = potong({"5m": rows}, t)
    s = signals(by, "intraday")
    assert len(s) == 1 and s[0]["side"] == "sell", s
    x = s[0]
    st = _hari(_prep(by, PARAMS), d0, t, PARAMS)
    lo, hi = st["range"]
    assert x["sl"] > 1012.5 > hi > x["entry"] > lo and x["tp"][0] == lo and x["zona"], (x, st["range"])
    assert abs((x["entry"] - x["tp"][1]) - 2 * (x["sl"] - x["entry"])) < 1e-9, x
    f = lambda now: fase_sekarang(by, now)
    assert f(d0 + 5 * 3600)["fase"] == "-" and f(d0 + 10 * 3600)["fase"] == "A"
    m = f(d0 + 14 * 3600 + 900)
    assert m["fase"] == "M" and m["sweep"]["sisi"] == "atas" and m["sweep"]["harga"] == 1012.5, m
    assert m["sweep"]["waktu"] == "2026-08-05T07:00:00Z" and m["tanggal"] == "2026-08-05", m
    dd = f(x["time"])
    assert dd["fase"] == "D" and dd["rangeAsia"] == {"lo": round(lo, 2), "hi": round(hi, 2)}, dd
    assert f(x["time"] - 300)["fase"] == "M"
    cek_kausal(signals, by, "intraday", 0.995)
    cek_kausal(signals, by, "intraday", 0.91)
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
