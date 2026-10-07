"""Candle multi-timeframe dari Binance (XAUT), OANDA v20 atau Yahoo, dengan cache yang terus bertambah.

Pakai:  python data.py <PAIR> [tf ...] [--source auto|binance|oanda|yahoo]
  contoh: python data.py XAUUSD 5m 15m 1h 4h
  Mencetak jumlah candle dan rentang waktu per timeframe.
Baris candle: [t, o, h, l, c, v], t = detik UTC awal candle.
Cache: E:/Trade Folders/data/cache/<simbol>_<tf>.json. Yahoo hanya memberi 60 hari intraday,
jadi cache digabung per timestamp supaya riwayat bertambah panjang setiap kali diambil.
source auto: OANDA kalau OANDA_TOKEN dan OANDA_ACCOUNT_ID ada di .env/environment
(OANDA_ENV = practice | live, simbol cache OANDA_XAU_USD), selain itu Binance XAUTUSDT
(simbol cache BINANCE_XAUTUSDT) yang digeser ke spot: basis = harga gold-api - close XAUT terakhir.
Pair tanpa padanan OANDA/Binance tetap memakai Yahoo.
Self-check: python data.py --selftest
"""
import datetime as dt
import functools
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snapshot import PRICE_SYM, yahoo  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
CACHE = os.path.join(ROOT, "data", "cache")
ENV_FILE = os.path.join(ROOT, ".env")
# tf -> (interval Yahoo, range, umur cache maksimum detik)
YAHOO_TF = {
    "1m": ("1m", "7d", 50), "5m": ("5m", "60d", 120), "15m": ("15m", "60d", 300), "30m": ("30m", "60d", 600),
    "1h": ("60m", "730d", 900), "1d": ("1d", "2y", 3600),
}
AGG = {"4h": ("1h", 4 * 3600)}  # 4h tidak ada di Yahoo, diagregasi dari 1h
STEP = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
OANDA_TF = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D"}
OANDA_SYM = {"XAUUSD": "XAU_USD", "XAGUSD": "XAG_USD"}
OANDA_HOST = {"practice": "api-fxpractice.oanda.com", "live": "api-fxtrade.oanda.com"}
OANDA_PAGES = 4  # 4 x 5000 candle saat cache masih kosong
BINANCE_SYM = {"XAUUSD": "XAUTUSDT"}
BINANCE_DAYS = {"1m": 200, "5m": 200, "15m": 200, "30m": 200, "1h": 730, "4h": 730, "1d": 1000}  # 60 hari + warm-up
SPOT_URL = {"XAUUSD": "https://api.gold-api.com/price/XAU"}


def parse(payload):
    r = payload["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    vol = q.get("volume") or [0] * len(r["timestamp"])
    return [[t, o, h, lo, c, v or 0]
            for t, o, h, lo, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], vol)
            if None not in (o, h, lo, c)]


def parse_oanda(payload):
    out = []
    for c in payload.get("candles", []):
        t = dt.datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        m = c["mid"]
        out.append([int(t.timestamp()), float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]),
                    c.get("volume", 0)])
    return out


def parse_binance(klines):
    return [[k[0] // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]


def merge(old, new):
    """Gabung per timestamp; baris baru menimpa yang lama (candle terakhir yang belum tutup ikut diperbarui)."""
    by_t = {r[0]: r for r in old}
    by_t.update({r[0]: r for r in new})
    return [by_t[t] for t in sorted(by_t)]


def aggregate(rows, sec):
    out = []
    for t, o, h, lo, c, v in rows:
        b = t - t % sec
        if out and out[-1][0] == b:
            cur = out[-1]
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], lo), c, cur[5] + v
        else:
            out.append([b, o, h, lo, c, v])
    return out


def libur(t):
    """Spot emas tutup: Jumat 21:00 - Minggu 22:00 UTC (candle XAUT akhir pekan hampir datar)."""
    m = ((t // 86400 + 3) % 7) * 1440 + t % 86400 // 60  # menit sejak Senin 00:00 UTC
    return 4 * 1440 + 21 * 60 <= m < 6 * 1440 + 22 * 60


def bersih(by_tf):
    """Buang candle akhir pekan dan candle intraday yang tidak sejajar (candle live Yahoo, sesi libur)."""
    # ponytail: jam tutup musim panas dipakai sepanjang tahun; candle 4h Minggu 20:00 ikut terbuang
    return {tf: [r for r in rows if not libur(r[0]) and (tf == "1d" or r[0] % STEP[tf] == 0)]
            for tf, rows in by_tf.items()}


def env(path=ENV_FILE):
    """KEY=VALUE dari .env (kalau ada), ditimpa os.environ."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                out[k.removeprefix("export ").strip()] = v.strip().strip("'\"")
    out.update(os.environ)
    return out


def oanda_creds(e=None):
    e = env() if e is None else e
    if e.get("OANDA_TOKEN") and e.get("OANDA_ACCOUNT_ID"):
        return {"token": e["OANDA_TOKEN"], "account": e["OANDA_ACCOUNT_ID"],
                "host": OANDA_HOST[e.get("OANDA_ENV", "practice").lower()]}
    return None


def symbol(pair, source="auto", creds=None):
    """Kunci simbol cache: OANDA_<instrumen>, BINANCE_<simbol>, atau simbol Yahoo."""
    pair = pair.upper()
    if source == "oanda" or (source == "auto" and creds and pair in OANDA_SYM):
        return f"OANDA_{OANDA_SYM[pair]}"
    if source in ("auto", "binance") and pair in BINANCE_SYM:
        return f"BINANCE_{BINANCE_SYM[pair]}"
    return PRICE_SYM[pair]


def _rfc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(sym, tf):
    return os.path.join(CACHE, f"{sym.replace('=', '_').replace('^', '')}_{tf}.json")


def _oanda_get(instr, tf, creds, **q):
    q = {"granularity": OANDA_TF[tf], "price": "M", "count": 5000, **q}
    url = f"https://{creds['host']}/v3/instruments/{instr}/candles?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds['token']}"})
    return parse_oanda(json.load(urllib.request.urlopen(req, timeout=20)))


def _cached(path, tf, refresh):
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    fresh = old and not refresh and time.time() - os.path.getmtime(path) < YAHOO_TF.get(tf, (0, 0, 900))[2]
    return old, fresh


def _save(path, rows):
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return rows


def _fetch_oanda(sym, tf, refresh):
    path = _path(sym, tf)
    old, fresh = _cached(path, tf, refresh)
    if fresh:
        return old
    creds, instr = oanda_creds(), sym.removeprefix("OANDA_")
    if old:
        new = _oanda_get(instr, tf, creds, **{"from": _rfc(old[-1][0])})
    else:
        new, to = [], None
        for _ in range(OANDA_PAGES):  # mundur per 5000 candle
            page = _oanda_get(instr, tf, creds, **({"to": _rfc(to)} if to else {}))
            if not page:
                break
            new, to = page + new, page[0][0]
    return _save(path, merge(old, new))


def _http(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _binance_get(sym, tf, **q):
    q = {"symbol": sym, "interval": tf, "limit": 1000, **q}
    return parse_binance(_http(f"https://api.binance.com/api/v3/klines?{urllib.parse.urlencode(q)}"))


def _fetch_binance(sym, tf, refresh):
    path = _path(sym, tf)
    old, fresh = _cached(path, tf, refresh)
    if fresh:
        return old
    bsym = sym.removeprefix("BINANCE_")
    new = []
    if old:  # maju dari candle terakhir sampai sekarang
        start = old[-1][0]
        while True:
            page = _binance_get(bsym, tf, startTime=start * 1000)
            new += page
            if len(page) < 1000:
                break
            start = page[-1][0] + 1
    else:  # mundur dari sekarang sampai BINANCE_DAYS terpenuhi atau data habis
        stop, end = time.time() - BINANCE_DAYS[tf] * 86400, None
        while True:
            page = _binance_get(bsym, tf, **({"endTime": end} if end else {}))
            new = page + new
            if len(page) < 1000 or page[0][0] <= stop:
                break
            end = page[0][0] * 1000 - 1
    return _save(path, merge(old, new))


@functools.cache
def spot_basis(pair, ref):
    """Basis spot = harga spot (gold-api) - ref (close XAUT terakhir); None kalau gagal."""
    try:
        return round(float(_http(SPOT_URL[pair.upper()])["price"]) - ref, 2)
    except Exception as e:  # spot gagal: candle tidak dikoreksi
        print(f"spot {pair} gagal: {e}", file=sys.stderr)
        return None


def geser(by_tf, b):
    """Tambah basis b ke o/h/l/c semua candle."""
    return {tf: [[r[0], r[1] + b, r[2] + b, r[3] + b, r[4] + b, r[5]] for r in rows] for tf, rows in by_tf.items()}


def fetch(sym, tf, refresh=False):
    if sym.startswith("BINANCE_"):
        return _fetch_binance(sym, tf, refresh)
    if sym.startswith("OANDA_"):
        return _fetch_oanda(sym, tf, refresh)
    if tf in AGG:
        base, sec = AGG[tf]
        return aggregate(fetch(sym, base, refresh), sec)
    interval, rng, _ = YAHOO_TF[tf]
    path = _path(sym, tf)
    old, fresh = _cached(path, tf, refresh)
    if fresh:
        return old
    return _save(path, merge(old, parse(yahoo(sym, interval, rng))))


def load(pair, tfs, refresh=False, source="auto", spot=True):
    """{tf: candle}. Sumber Binance + spot=True: digeser ke spot, basisnya di load.basis."""
    sym = symbol(pair, source, oanda_creds() if source == "auto" else None)
    out = {tf: fetch(sym, tf, refresh) for tf in tfs}
    load.sym, load.basis = sym, None
    if spot and sym.startswith("BINANCE_") and pair.upper() in SPOT_URL:
        last = fetch(sym, "1m", refresh)[-1][4]
        load.basis = spot_basis(pair, last)
        if load.basis is not None:
            out = geser(out, load.basis)
    return out


def _selftest():
    payload = {"chart": {"result": [{"timestamp": [0, 3600, 7200, 10800, 14400],
               "indicators": {"quote": [{"open": [1, 2, None, 4, 5], "high": [2, 3, 9, 5, 6],
                                         "low": [0, 1, 1, 3, 4], "close": [2, 3, 3, 5, 6],
                                         "volume": [10, None, 5, 7, 8]}]}}]}}
    rows = parse(payload)
    assert rows == [[0, 1, 2, 0, 2, 10], [3600, 2, 3, 1, 3, 0], [10800, 4, 5, 3, 5, 7], [14400, 5, 6, 4, 6, 8]], rows
    m = merge([[0, 1, 1, 1, 1, 1], [3600, 2, 2, 2, 2, 2]], [[3600, 9, 9, 9, 9, 9], [7200, 3, 3, 3, 3, 3]])
    assert [r[0] for r in m] == [0, 3600, 7200] and m[1][1] == 9, m
    a = aggregate(rows, 14400)
    assert a == [[0, 1, 5, 0, 5, 17], [14400, 5, 6, 4, 6, 8]], a
    fx = {"instrument": "XAU_USD", "granularity": "H1", "candles": [
        {"complete": True, "volume": 12, "time": "2026-10-07T13:00:00.000000000Z",
         "mid": {"o": "4100.1", "h": "4101.0", "l": "4099.5", "c": "4100.7"}},
        {"complete": False, "volume": 3, "time": "2026-10-07T14:00:00.000000000Z",
         "mid": {"o": "4100.7", "h": "4102.0", "l": "4100.0", "c": "4101.5"}}]}
    o = parse_oanda(fx)
    assert o == [[1791378000, 4100.1, 4101.0, 4099.5, 4100.7, 12],
                 [1791381600, 4100.7, 4102.0, 4100.0, 4101.5, 3]], o
    assert _rfc(1791378000) == "2026-10-07T13:00:00Z"
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
        f.write("# komentar\nOANDA_TOKEN='abc'\nexport OANDA_ACCOUNT_ID=101-1\nOANDA_ENV=live\n")
    e = env(f.name)
    os.unlink(f.name)
    if not any(k.startswith("OANDA_") for k in os.environ):
        assert {k: v for k, v in e.items() if k.startswith("OANDA_")} == \
            {"OANDA_TOKEN": "abc", "OANDA_ACCOUNT_ID": "101-1", "OANDA_ENV": "live"}, e
        assert oanda_creds(e)["host"] == "api-fxtrade.oanda.com"
    assert oanda_creds({"OANDA_TOKEN": "x"}) is None
    cr = {"token": "x", "account": "y", "host": "h"}
    assert symbol("xauusd", "auto", cr) == "OANDA_XAU_USD" and symbol("XAUUSD", "yahoo", None) == "GC=F"
    assert symbol("BTCUSD", "auto", cr) == "BTC-USD" and symbol("XAUUSD", "yahoo", cr) == "GC=F"
    assert symbol("XAUUSD", "auto", None) == "BINANCE_XAUTUSDT" == symbol("XAUUSD", "binance", cr)
    kl = [[1791379200000, "4101.0", "4103.07", "4096.52", "4096.52", "41.0512", 1791379499999, "1", 396, "0", "0", "0"]]
    assert parse_binance(kl) == [[1791379200, 4101.0, 4103.07, 4096.52, 4096.52, 41.0512]]
    assert geser({"5m": [[0, 10, 12, 9, 11, 5]]}, -5) == {"5m": [[0, 5, 7, 4, 6, 5]]}
    assert _path("OANDA_XAU_USD", "4h").endswith("OANDA_XAU_USD_4h.json")
    b = bersih({"5m": [[0, 1, 1, 1, 1, 0], [301, 1, 1, 1, 1, 0]], "1d": [[14400, 1, 1, 1, 1, 0]]})
    assert b == {"5m": [[0, 1, 1, 1, 1, 0]], "1d": [[14400, 1, 1, 1, 1, 0]]}, b
    fri, sun = 1791331200 + 2 * 86400, 1791331200 + 4 * 86400  # Jumat 9 dan Minggu 11 Okt 2026, 00:00 UTC
    assert [libur(fri + 20 * 3600), libur(fri + 21 * 3600), libur(sun + 21 * 3600), libur(sun + 22 * 3600)] ==         [False, True, True, False]
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    src = args[args.index("--source") + 1] if "--source" in args else "auto"
    args = [a for a in args if a not in ("--source", src)]
    pair, tfs = args[0], args[1:] or ["5m", "15m", "30m", "1h", "4h", "1d"]
    by = load(pair, tfs, source=src)
    print(f"sumber {load.sym}, basis spot {load.basis}")
    for tf, rows in by.items():
        f = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"{tf:>3}: {len(rows):6} candle  {f(rows[0][0])} -> {f(rows[-1][0])} UTC  close {rows[-1][4]}")
