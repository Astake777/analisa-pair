"""Strategi entry. Tiap modul: PARAMS dan signals(by_tf, mode, params) -> [{time, side, entry, sl, tp, alasan}].

time = detik UTC saat sinyal diketahui (tutup candle pemicu). Opsional: zona [lo, hi] (FVG).
Self-check per modul: python strategi/<modul>.py --selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategi import amd, ict_sweep, sniper, tren_pullback  # noqa: E402

REGISTRY = {
    "tren_pullback": tren_pullback,
    "ict_sweep": ict_sweep,
    "amd": amd,
    # "alchemist": alchemist,  # menunggu PDF strategi dari user
}
# Dinilai terpisah dari pemilih strategi utama: backtest.py --strategi sniper, hasil di data/backtest/sniper/
EKSTRA = {"sniper": sniper}
T0 = 1785715200  # Senin 3 Agu 2026 00:00 UTC


def contoh(f, n=12000, t0=T0, step=300, spread=0.3):
    """Data sintetis untuk selftest: close 5m = f(i), lalu diagregasi ke semua TF."""
    rows, prev = [], f(0)
    for i in range(n):
        c = f(i)
        rows.append([t0 + i * step, prev, max(prev, c) + spread, min(prev, c) - spread, c, 1])
        prev = c
    return potong({"5m": rows}, t0 + n * step)


def potong(by_tf, T):
    """Candle 5m sampai waktu T, diagregasi ulang (candle TF besar terakhir boleh belum tutup)."""
    from data import aggregate
    base = [r for r in by_tf["5m"] if r[0] + 300 <= T]
    out = {"5m": base}
    for tf, sec in (("15m", 900), ("30m", 1800), ("1h", 3600), ("4h", 14400), ("1d", 86400)):
        out[tf] = aggregate([list(r) for r in base], sec)
    return out


def cek_kausal(fn, by_tf, mode, frac=0.7):
    """Sinyal sampai T dari data penuh harus sama dengan sinyal dari data yang dipotong di T."""
    t = by_tf["5m"]
    T = t[int(len(t) * frac)][0]
    full = [s for s in fn(by_tf, mode) if s["time"] <= T]
    cut = fn(potong(by_tf, T), mode)
    assert full == cut, (len(full), len(cut))
