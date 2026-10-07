"""Level MSNR (Malaysian SnR, dipakai di strategi Alchemist): A/V dari body candle, fresh, RBS/SBR, fib.

A-level (resistance): candle bullish lalu candle bearish; level = close candle bullish (wick diabaikan).
V-level (support): candle bearish lalu candle bullish; level = close candle bearish.
Level diketahui setelah candle kedua tutup. Fresh = belum disentuh wick sejak itu.
Level yang ditembus close berganti peran: A jadi support (RBS), V jadi resistance (SBR); fresh dihitung ulang
sejak candle penembus.
Self-check: python strategi/msnr.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indikator import kolom  # noqa: E402


def levels(rows, step):
    """-> [{t_ok, harga, jenis: 'A'|'V', i}] urut waktu diketahui (t_ok = tutup candle kedua)."""
    t, o, h, l, c = kolom(rows)
    out = []
    for i in range(len(t) - 1):
        if c[i] > o[i] and c[i + 1] < o[i + 1]:
            out.append({"t_ok": t[i + 1] + step, "harga": c[i], "jenis": "A", "i": i})
        elif c[i] < o[i] and c[i + 1] > o[i + 1]:
            out.append({"t_ok": t[i + 1] + step, "harga": c[i], "jenis": "V", "i": i})
    return out


class Papan:
    """Daftar level hidup yang diperbarui candle demi candle (kausal).

    peran: 'resistance' / 'support'; fresh: belum disentuh wick sejak terbentuk atau sejak berganti peran.
    """

    def __init__(self, maks=60):
        self.hidup, self.maks = [], maks

    def tambah(self, lv):
        peran = "resistance" if lv["jenis"] == "A" else "support"
        self.hidup.append({**lv, "peran": peran, "fresh": True, "flip": False})
        self.hidup = self.hidup[-self.maks:]

    def candle(self, hi, lo, close):
        for z in self.hidup:
            p = z["harga"]
            if z["peran"] == "resistance":
                if close > p:
                    z.update(peran="support", fresh=True, flip=True)   # RBS
                elif hi >= p:
                    z["fresh"] = False
            else:
                if close < p:
                    z.update(peran="resistance", fresh=True, flip=True)  # SBR
                elif lo <= p:
                    z["fresh"] = False

    def fresh(self, peran):
        return [z for z in self.hidup if z["fresh"] and z["peran"] == peran]


def fib_zona(lo, hi, side, a=0.618, b=0.786):
    """Zona retracement a..b dari impuls lo->hi (buy: turun dari hi; sell: naik dari lo)."""
    if side > 0:
        return hi - b * (hi - lo), hi - a * (hi - lo)
    return lo + a * (hi - lo), lo + b * (hi - lo)


def pasang(T, side, entry, p, alasan, **extra):
    """Sinyal limit standar sniper: zona `zona` dari tepi pertama (entry) ke arah SL, SL = entry -/+ sl_jarak,
    TP1 = entry +/- max(tp_min, rr x risk). side = +1 buy / -1 sell."""
    r2 = lambda x: round(x, 2)
    entry = r2(entry)
    sl = r2(entry - side * p["sl_jarak"])
    return {"time": T, "side": "buy" if side > 0 else "sell", "entry": entry, "sl": sl,
            "tp": [r2(entry + side * max(p["tp_min"], p["rr"] * p["sl_jarak"]))],
            "zona": sorted([entry, r2(entry - side * p["zona"])]), "alasan": alasan, **extra}


def _selftest():
    rows = [[0, 10, 12, 9, 11, 0],     # bull
            [60, 11, 11.5, 9.5, 10, 0],  # bear -> A di 11
            [120, 10, 10.5, 8, 9, 0],   # bear
            [180, 9, 10.2, 8.8, 10, 0],  # bull -> V di 9
            [240, 10, 10.9, 9.6, 10.5, 0]]
    lv = levels(rows, 60)
    assert [(x["jenis"], x["harga"], x["t_ok"]) for x in lv] == [("A", 11, 120), ("V", 9, 240)], lv
    p = Papan()
    p.tambah(lv[0])
    p.candle(10.9, 9.6, 10.5)
    assert p.fresh("resistance"), "belum disentuh"
    p.candle(11.2, 10, 10.8)
    assert not p.fresh("resistance"), "wick menyentuh -> tidak fresh"
    p.candle(12, 10.8, 11.6)
    assert p.fresh("support") and p.hidup[0]["flip"], "close tembus -> RBS fresh"
    assert fib_zona(100, 110, 1) == (110 - 7.86, 110 - 6.18)
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
