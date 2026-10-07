"""Orderflow dan volume: delta, CVD, anchored VWAP, volume profile (POC/VAH/VAL) dari candle Binance XAUT.

Volume XAUT adalah proksi volume emas (bukan COMEX); kolom ke-7 candle Binance = volume taker buy, jadi
delta = 2 x taker buy - volume (positif = agresor beli dominan).
Pakai:  python orderflow.py [PAIR]     ringkasan sekarang (JSON)
Self-check: python orderflow.py --selftest
"""
import json
import os
import sys
from bisect import bisect_left, bisect_right

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indikator import kolom, swings, tutup  # noqa: E402
from regime import DST_UK, DST_US, _panas  # noqa: E402

VALUE_AREA = 0.70


def delta(r):
    return 2 * r[6] - r[5] if len(r) > 6 else None


class Vwap:
    """Kumulatif harga x volume supaya VWAP dari jangkar mana pun ke waktu T dihitung O(log n)."""

    def __init__(self, rows, step):
        self.t, self.step = [r[0] for r in rows], step
        self.pv, self.v = [0.0], [0.0]
        for r in rows:
            tp = (r[2] + r[3] + r[4]) / 3
            self.pv.append(self.pv[-1] + tp * r[5])
            self.v.append(self.v[-1] + r[5])

    def nilai(self, anchor, T):
        """VWAP candle yang buka >= anchor dan sudah tutup sebelum/pada T; None kalau volumenya nol."""
        a = bisect_left(self.t, anchor)
        b = tutup(self.t, self.step, T) + 1
        if b <= a or self.v[b] - self.v[a] <= 0:
            return None
        return (self.pv[b] - self.pv[a]) / (self.v[b] - self.v[a])


def profil(rows, t0, t1, bin_=0.5):
    """Volume profile candle yang buka di [t0, t1): volume dibagi rata ke rentang high-low tiap candle.
    -> {poc, vah, val} atau None."""
    lo_i = bisect_left([r[0] for r in rows], t0)
    hi_i = bisect_left([r[0] for r in rows], t1)
    vol = {}
    for r in rows[lo_i:hi_i]:
        a, b = int(r[3] // bin_), int(r[2] // bin_)
        n = b - a + 1
        for k in range(a, b + 1):
            vol[k] = vol.get(k, 0) + r[5] / n
    if not vol or sum(vol.values()) <= 0:
        return None
    poc = max(vol, key=vol.get)
    total, isi, lo, hi = sum(vol.values()), vol[poc], poc, poc
    while isi < VALUE_AREA * total:
        bawah, atas = vol.get(lo - 1, -1), vol.get(hi + 1, -1)
        if bawah < 0 and atas < 0:
            break
        if atas >= bawah:
            hi += 1
            isi += atas
        else:
            lo -= 1
            isi += bawah
    f = lambda k: round((k + 0.5) * bin_, 2)
    return {"poc": f(poc), "vah": round((hi + 1) * bin_, 2), "val": round(lo * bin_, 2)}


def jangkar(T, h1=None):
    """Titik jangkar AVWAP (detik UTC) yang sudah lewat pada T."""
    hari = T - T % 86400
    minggu = hari - ((T // 86400 + 3) % 7) * 86400              # Senin 00:00 UTC
    lon = hari + (7 if _panas(T, DST_UK) else 8) * 3600
    ny = hari + (13 if _panas(T, DST_US) else 14) * 3600 + 1800   # NY buka 09:30 waktu NY
    out = {"hari": hari, "minggu": minggu}
    if lon <= T:
        out["london"] = lon
    if ny <= T:
        out["ny"] = ny
    if h1:
        t, o, h, l, c = kolom(h1)
        k = tutup(t, 3600, T)
        sh, sl = swings(h[:k + 1], l[:k + 1])
        # fraktal di i baru pasti setelah candle i+2 tutup
        sh = [x for x in sh if x[0] + 2 <= k]
        sl = [x for x in sl if x[0] + 2 <= k]
        if sh:
            out["swingHigh"] = t[sh[-1][0]]
        if sl:
            out["swingLow"] = t[sl[-1][0]]
    return out


def ringkas(by_tf, now):
    """Ringkasan orderflow sekarang untuk payload analisis (candle 5m dan 1m yang sudah tutup)."""
    m5 = [r for r in by_tf["5m"] if r[0] + 300 <= now]
    m1 = [r for r in by_tf.get("1m", []) if r[0] + 60 <= now]
    vw = Vwap(m5, 300)
    j = jangkar(now, by_tf.get("1h"))
    avwap = {k: round(v, 2) for k, v in ((k, vw.nilai(a, now)) for k, a in j.items()) if v is not None}
    hari = now - now % 86400
    d1 = [delta(r) for r in m1 if r[0] >= now - 3600]
    dh = [delta(r) for r in m5 if r[0] >= hari]
    harga = m1[-1][4] if m1 else m5[-1][4]
    return {"avwap": avwap, "profilKemarin": profil(m5, hari - 86400, hari), "profilHariIni": profil(m5, hari, now),
            "delta1j": None if None in d1 or not d1 else round(sum(d1), 2),
            "cvdHariIni": None if None in dh or not dh else round(sum(dh), 2), "harga": round(harga, 2),
            "catatan": "Volume dari Binance XAUT (proksi, bukan COMEX)."}


def _selftest():
    rows = [[i * 300, 100, 101, 99, 100, 10, 7] for i in range(12)] + \
           [[3600 + i * 300, 110, 111, 109, 110, 30, 10] for i in range(12)]
    assert delta(rows[0]) == 4 and delta(rows[-1]) == -10 and delta([0, 1, 1, 1, 1, 5]) is None
    vw = Vwap(rows, 300)
    assert abs(vw.nilai(0, 3600) - 100) < 1e-9, vw.nilai(0, 3600)          # jam pertama saja
    assert abs(vw.nilai(0, 7200) - (100 * 120 + 110 * 360) / 480) < 1e-9     # bobot volume
    assert vw.nilai(3600, 3600) is None                                      # belum ada candle tutup
    p = profil(rows, 0, 7200, 1.0)
    assert p["poc"] in (109.5, 110.5) and p["val"] <= 109 and p["vah"] >= 111, p
    T = 4 * 86400 + 10 * 3600   # Senin 5 Jan 1970 10:00 UTC
    j = jangkar(T)
    assert j["minggu"] == 4 * 86400 and j["hari"] == 4 * 86400 and "london" in j and "ny" not in j, j
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        import time
        import data
        pair = (sys.argv[1:] or ["XAUUSD"])[0]
        by = data.load(pair, ["1m", "5m", "1h"], refresh=True)
        print(json.dumps(ringkas(by, int(time.time())), indent=2))
