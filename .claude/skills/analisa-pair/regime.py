"""Label regime per candle: tren, volatilitas, sesi (WIB), dan jendela news. Juga definisi mode.

Pakai:  python regime.py <PAIR> <scalp|intraday|swing>     regime sekarang (JSON)
Self-check: python regime.py --selftest
"""
import calendar
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indikator import adx, atr, ema, kolom, tutup  # noqa: E402

STEP = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
# bias[0] = TF bias utama (untuk regime tren); sim = TF simulasi backtest = TF trigger
MODES = {
    "scalp": {"bias": ["1h", "30m"], "filter": "4h", "entry": "5m", "trigger": "5m", "target": 1.5},
    "intraday": {"bias": ["4h", "1h", "30m"], "filter": None, "entry": "15m", "trigger": "5m", "target": 2.0},
    "swing": {"bias": ["1d", "4h", "1h"], "filter": None, "entry": "1h", "trigger": "15m", "target": 3.0},
}
TFS = ["5m", "15m", "30m", "1h", "4h", "1d"]
WIB = 7 * 3600
ADX_MIN, SLOPE_BARS, VOL_LOOKBACK = 20, 5, 100


def _utc(y, mo, d, h):
    return calendar.timegm((y, mo, d, h, 0, 0))


# musim panas (DST) dalam UTC: [mulai, selesai)
DST_UK = [(_utc(2024, 3, 31, 1), _utc(2024, 10, 27, 1)), (_utc(2025, 3, 30, 1), _utc(2025, 10, 26, 1)),
          (_utc(2026, 3, 29, 1), _utc(2026, 10, 25, 1)), (_utc(2027, 3, 28, 1), _utc(2027, 10, 31, 1))]
DST_US = [(_utc(2024, 3, 10, 7), _utc(2024, 11, 3, 6)), (_utc(2025, 3, 9, 7), _utc(2025, 11, 2, 6)),
          (_utc(2026, 3, 8, 7), _utc(2026, 11, 1, 6)), (_utc(2027, 3, 14, 7), _utc(2027, 11, 7, 6))]


def _panas(t, table):
    return any(a <= t < b for a, b in table)


def jam_sesi(t):
    """Jam WIB (asia, london, ny, akhir ny) pada waktu t; London/NY mundur 1 jam saat DST berakhir."""
    lon = 14 if _panas(t, DST_UK) else 15
    ny = 19 if _panas(t, DST_US) else 20
    return 7, lon, ny, ny + 5


def sesi(t):
    a, lon, ny, end = jam_sesi(t)
    h = (t + WIB) % 86400 / 3600
    if h < a:
        h += 24  # 00:00-07:00 WIB masih bisa ekor sesi NY
    if h < lon:
        return "Asia"
    if h < ny:
        return "London"
    return "NY" if h < end else "sepi"


def jendela_news(t, events, menit=60):
    """events: list detik UTC event USD berdampak tinggi."""
    return any(abs(t - e) <= menit * 60 for e in events)


def tren_seri(rows):
    """ADX >= 20 dan arah kemiringan EMA50 (5 candle) -> trend-naik / trend-turun / range."""
    t, o, h, l, c = kolom(rows)
    a, e = adx(h, l, c)[0], ema(c, 50)
    out = []
    for i in range(len(c)):
        if a[i] is None or i < SLOPE_BARS or e[i - SLOPE_BARS] is None or a[i] < ADX_MIN:
            out.append("range")
        else:
            out.append("trend-naik" if e[i] > e[i - SLOPE_BARS] else "trend-turun")
    return out


def vol_seri(rows, lookback=VOL_LOOKBACK):
    """Persentil ATR(14) dalam lookback candle: <30 rendah, 30-70 normal, >70 tinggi."""
    t, o, h, l, c = kolom(rows)
    a = atr(h, l, c)
    out = []
    for i, v in enumerate(a):
        win = [x for x in a[max(0, i - lookback + 1):i + 1] if x is not None]
        if v is None or len(win) < 2:
            out.append("normal")
            continue
        p = 100 * sum(x < v for x in win) / (len(win) - 1)
        out.append("rendah" if p < 30 else "tinggi" if p > 70 else "normal")
    return out


def regime_sekarang(by_tf, mode, t=None, events=()):
    """-> {tren, volatilitas, sesi, jendelaNews} pada waktu t (default: sekarang), dari candle yang sudah tutup."""
    tf = MODES[mode]["bias"][0]
    rows = by_tf[tf]
    t = t or int(time.time())
    i = tutup([r[0] for r in rows], STEP[tf], t)
    return {"tren": tren_seri(rows)[i], "volatilitas": vol_seri(rows)[i], "sesi": sesi(t),
            "jendelaNews": jendela_news(t, events)}


def _selftest():
    oct7 = _utc(2026, 10, 7, 0)
    assert sesi(oct7) == "Asia"                       # 07:00 WIB
    assert sesi(oct7 + 7 * 3600) == "London"          # 14:00 WIB
    assert sesi(oct7 + 12 * 3600) == "NY"             # 19:00 WIB
    assert sesi(oct7 + 17 * 3600 - 1) == "NY"         # 23:59 WIB
    assert sesi(oct7 - 2 * 3600) == "sepi"            # 05:00 WIB
    oct27 = _utc(2026, 10, 27, 0)                     # UK sudah mundur, US belum
    assert sesi(oct27 + 7 * 3600) == "Asia" and sesi(oct27 + 8 * 3600) == "London"
    assert sesi(oct27 + 12 * 3600) == "NY"
    nov3 = _utc(2026, 11, 3, 0)                       # keduanya mundur
    assert sesi(nov3 + 12 * 3600) == "London" and sesi(nov3 + 13 * 3600) == "NY"
    assert sesi(nov3 + 17 * 3600 + 1800) == "NY" and sesi(nov3 + 18 * 3600) == "sepi"
    assert jendela_news(1000, [1000 + 3600]) and not jendela_news(1000, [1000 + 3601])
    up = [[i * 3600, 100 + i, 101 + i, 99 + i, 100.5 + i, 0] for i in range(120)]
    assert tren_seri(up)[-1] == "trend-naik" and tren_seri(up)[10] == "range"
    dn = [[i * 3600, 300 - i, 301 - i, 299 - i, 299.5 - i, 0] for i in range(120)]
    assert tren_seri(dn)[-1] == "trend-turun"
    flat = [[i * 3600, 100, 101, 99, 100, 0] for i in range(120)]
    assert tren_seri(flat)[-1] == "range"
    # range candle membesar di akhir -> ATR persentil tinggi; konstan -> normal
    grow = [[i * 3600, 100, 100 + (1 if i < 100 else 10), 99, 100, 0] for i in range(110)]
    assert vol_seri(grow)[-1] == "tinggi" and vol_seri(flat)[-1] == "rendah"
    shrink = [[i * 3600, 100, 100 + (10 if i < 100 else 0.5), 99.5, 100, 0] for i in range(130)]
    assert vol_seri(shrink)[-1] == "rendah"
    by = {"1h": up, "30m": up}
    r = regime_sekarang(by, "scalp", up[-1][0] + 3600, [up[-1][0] + 3600 + 1800])
    assert r == {"tren": "trend-naik", "volatilitas": r["volatilitas"], "sesi": sesi(up[-1][0] + 3600),
                 "jendelaNews": True}, r
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    import data
    by = data.bersih(data.load(args[0], TFS))
    print(json.dumps(regime_sekarang(by, args[1]), indent=2))
