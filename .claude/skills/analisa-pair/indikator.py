"""Indikator teknikal murni di atas list. Keluaran sejajar input, None selama warm-up.

Pakai:  python indikator.py <PAIR> <tf>     nilai indikator candle terakhir yang sudah tutup
Self-check: python indikator.py --selftest
"""
import sys
from bisect import bisect_right


def kolom(rows):
    """[[t,o,h,l,c,v], ...] -> (t, o, h, l, c)."""
    if not rows:
        return [], [], [], [], []
    t, o, h, l, c = (list(x) for x in list(zip(*rows))[:5])
    return t, o, h, l, c


def tutup(starts, step, t):
    """Indeks candle terakhir yang sudah tutup pada waktu t (start + step <= t); -1 kalau belum ada."""
    return bisect_right(starts, t - step) - 1


def sma(x, n):
    return [None if i < n - 1 else sum(x[i - n + 1:i + 1]) / n for i in range(len(x))]


def _smooth(x, n, alpha):
    """EMA dengan seed SMA n nilai pertama yang bukan None."""
    out = [None] * len(x)
    s = next((i for i, v in enumerate(x) if v is not None), len(x))
    if len(x) - s < n:
        return out
    e = sum(x[s:s + n]) / n
    out[s + n - 1] = e
    for i in range(s + n, len(x)):
        e += alpha * (x[i] - e)
        out[i] = e
    return out


def ema(x, n):
    return _smooth(x, n, 2 / (n + 1))


def rma(x, n):
    """Smoothing Wilder."""
    return _smooth(x, n, 1 / n)


def rsi(c, n=14):
    d = [None] + [c[i] - c[i - 1] for i in range(1, len(c))]
    g = rma([None if v is None else max(v, 0) for v in d], n)
    lo = rma([None if v is None else max(-v, 0) for v in d], n)
    out = []
    for a, b in zip(g, lo):
        if a is None:
            out.append(None)
        elif b == 0:
            out.append(50.0 if a == 0 else 100.0)
        else:
            out.append(100 - 100 / (1 + a / b))
    return out


def macd(c, fast=12, slow=26, sig=9):
    f, s = ema(c, fast), ema(c, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s)]
    signal = ema(line, sig)
    hist = [None if a is None or b is None else a - b for a, b in zip(line, signal)]
    return line, signal, hist


def _tr(h, l, c):
    return [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]


def atr(h, l, c, n=14):
    return rma(_tr(h, l, c), n) if c else []


def adx(h, l, c, n=14):
    """-> (adx, +DI, -DI)."""
    if not c:
        return [], [], []
    tr = [None] + _tr(h, l, c)[1:]
    pdm, mdm = [None], [None]
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    st, sp, sm = rma(tr, n), rma(pdm, n), rma(mdm, n)
    pdi = [None if t is None else (100 * p / t if t else 0.0) for t, p in zip(st, sp)]
    mdi = [None if t is None else (100 * m / t if t else 0.0) for t, m in zip(st, sm)]
    dx = [None if p is None else (100 * abs(p - m) / (p + m) if p + m else 0.0) for p, m in zip(pdi, mdi)]
    return rma(dx, n), pdi, mdi


def bbands(c, n=20, k=2):
    """-> (tengah, atas, bawah), deviasi standar populasi."""
    mid, up, lo = sma(c, n), [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        sd = (sum((v - mid[i]) ** 2 for v in c[i - n + 1:i + 1]) / n) ** 0.5
        up[i], lo[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, up, lo


def stoch(h, l, c, n=14, d=3):
    """-> (%K cepat, %D = SMA d dari %K)."""
    k = [None] * len(c)
    for i in range(n - 1, len(c)):
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        k[i] = 50.0 if hh == ll else 100 * (c[i] - ll) / (hh - ll)
    s = next((i for i, v in enumerate(k) if v is not None), len(k))
    dd = [None] * s + sma(k[s:], d)
    return k, dd


def swings(h, l, n=2):
    """Fraktal: -> (swing high [(i, harga)], swing low [(i, harga)]). Swing di i baru pasti di candle i+n."""
    sh, sl = [], []
    for i in range(n, len(h) - n):
        if h[i] > max(h[i - n:i]) and h[i] >= max(h[i + 1:i + n + 1]):
            sh.append((i, h[i]))
        if l[i] < min(l[i - n:i]) and l[i] <= min(l[i + 1:i + n + 1]):
            sl.append((i, l[i]))
    return sh, sl


def fvg(h, l):
    """Fair value gap 3 candle (i-2, i-1, i) -> [(i, 'buy'|'sell', lo, hi)]."""
    out = []
    for i in range(2, len(h)):
        if l[i] > h[i - 2]:
            out.append((i, "buy", h[i - 2], l[i]))
        elif h[i] < l[i - 2]:
            out.append((i, "sell", h[i], l[i - 2]))
    return out


def displacement(o, h, l, c, atr_, k=1.5):
    """Body candle >= k x ATR candle sebelumnya -> 'buy'|'sell', selain itu None."""
    out = [None] * len(c)
    for i in range(1, len(c)):
        a = atr_[i - 1]
        if a and abs(c[i] - o[i]) >= k * a:
            out[i] = "buy" if c[i] > o[i] else "sell"
    return out


def pivots(h, l, c):
    """Pivot klasik dari candle sebelumnya -> [{P, R1, S1, R2, S2} | None]."""
    out = [None]
    for i in range(1, len(c)):
        H, L, C = h[i - 1], l[i - 1], c[i - 1]
        p = (H + L + C) / 3
        out.append({"P": p, "R1": 2 * p - L, "S1": 2 * p - H, "R2": p + (H - L), "S2": p - (H - L)})
    return out


def _selftest():
    near = lambda a, b: abs(a - b) < 1e-9
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]          # seed 2, alpha 0.5
    assert ema([None, None, 5, 5, 5, 5], 2)[2:] == [None, 5, 5, 5]   # None di depan dilewati
    assert ema([7.0] * 30, 10)[-1] == 7.0
    # RSI n=2 hitung tangan: perubahan +1,-1,+1 -> 50 lalu g=.75 l=.25 -> 75
    assert rsi([1, 2, 1, 2], 2) == [None, None, 50.0, 75.0]
    assert rsi(list(range(30)))[-1] == 100.0 and rsi(list(range(30, 0, -1)))[-1] == 0.0
    assert rsi([5] * 20)[-1] == 50.0
    # ATR n=2: TR = 2, 3, 1 -> 2.5 lalu (2.5 + 1)/2 = 1.75
    assert atr([10, 12, 11], [8, 9, 10], [9, 11, 10.5], 2) == [None, 2.5, 1.75]
    up = list(range(100, 160))
    a, p, m = adx([x + 1 for x in up], [x - 1 for x in up], up)
    assert near(a[-1], 100) and m[-1] == 0 and p[-1] > 0 and a[26] is None and a[27] is not None
    ln, sg, hs = macd([3.0] * 50)
    assert ln[-1] == 0 and sg[-1] == 0 and hs[-1] == 0 and hs[32] is None and hs[33] == 0
    assert macd(list(range(60)))[0][-1] > 0
    mid, bu, bl = bbands([4.0] * 25)
    assert mid[-1] == bu[-1] == bl[-1] == 4.0 and mid[18] is None
    mid, bu, bl = bbands([1, 3] * 10, 20, 2)
    assert mid[-1] == 2 and bu[-1] == 4 and bl[-1] == 0
    k, d = stoch(up, up, up)
    assert k[-1] == 100 and d[-1] == 100 and d[14] is None and d[15] == 100
    sh, sl = swings([1, 2, 5, 2, 1, 3, 1], [1, 0, 4, 2, 0.5, 2, 1])
    assert sh == [(2, 5)] and sl == [(4, 0.5)], (sh, sl)
    assert fvg([10, 11, 14], [9, 10, 12]) == [(2, "buy", 10, 12)]
    assert fvg([10, 9, 7], [8, 7, 5]) == [(2, "sell", 7, 8)]
    ds = displacement([0, 0, 10], [1, 1, 10], [0, 0, 1], [1, 1, 2], [1, 2, 2])
    assert ds == [None, None, "sell"], ds                            # body 8 >= 1.5 x 2
    pv = pivots([12, 0], [8, 0], [10, 0])
    assert pv[0] is None and pv[1] == {"P": 10, "R1": 12, "S1": 8, "R2": 14, "S2": 6}, pv
    assert tutup([0, 300, 600], 300, 600) == 1 and tutup([0, 300], 300, 299) == -1
    assert kolom([[1, 2, 3, 4, 5, 6]]) == ([1], [2], [3], [4], [5])
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    import data
    rows = data.bersih(data.load(args[0], [args[1]]))[args[1]]
    t, o, h, l, c = kolom(rows)
    a, p, m = adx(h, l, c)
    print({"close": c[-1], "ema20": ema(c, 20)[-1], "ema50": ema(c, 50)[-1], "ema200": ema(c, 200)[-1],
           "rsi": rsi(c)[-1], "atr": atr(h, l, c)[-1], "adx": a[-1], "macd_hist": macd(c)[2][-1]})
