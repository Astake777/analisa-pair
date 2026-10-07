"""Gabungkan analisis Claude + candle + driver + kalender -> dokumen dashboard.

Pakai:
  python snapshot.py <PAIR> <analysis.json> [outdir]   analisis lengkap
  python snapshot.py <PAIR> --pantau [outdir]          hanya candle + harga (untuk /loop)
Keluaran di outdir:
  doc.json          -> pairs/<PAIR>                (analisis lengkap)
  history.json      -> pairs/<PAIR>/history/<id>   (analisis lengkap)
  price.json        -> update pairs/<PAIR>         (mode pantau: price, priceAt)
  candles_<tf>.json -> pairs/<PAIR>/candles/<tf>   (keduanya)
analysis.json wajib berisi: status, keyakinan, bias, levels, zones, setups.
Self-check: python snapshot.py --selftest
"""
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalender as cal  # noqa: E402

PRICE_SYM = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "BTCUSD": "BTC-USD", "US100": "NQ=F"}
# (simbol, label, relasi ke pair): terbalik = driver naik -> pair turun; konteks = tanpa tag
_GOLD = [("^TNX", "US10Y", "terbalik"), ("DX-Y.NYB", "DXY", "terbalik"),
         ("CL=F", "Crude", "konteks"), ("^GSPC", "S&P 500", "konteks")]
DRIVERS = {
    "XAUUSD": _GOLD,
    "XAGUSD": _GOLD,
    "BTCUSD": [("^TNX", "US10Y", "terbalik"), ("DX-Y.NYB", "DXY", "terbalik"), ("NQ=F", "Nasdaq 100", "searah")],
    "US100": [("^TNX", "US10Y", "terbalik"), ("DX-Y.NYB", "DXY", "terbalik"), ("^VIX", "VIX", "terbalik")],
}
REQUIRED = ("status", "keyakinan", "bias", "levels", "zones", "setups")
# jumlah candle per dokumen chart; tiap dokumen < 256 KiB
CANDLE_KEEP = {"1m": 1440, "5m": 864, "15m": 672, "30m": 480, "1h": 720, "4h": 400}


def yahoo(sym, interval, rng):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
           f"?interval={interval}&range={rng}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def candles(payload):
    """Payload Yahoo -> [[t,o,h,l,c], ...], baris dengan null dibuang."""
    r = payload["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    rows = zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"])
    return [[t, round(o, 4), round(h, 4), round(lo, 4), round(c, 4)]
            for t, o, h, lo, c in rows if None not in (o, h, lo, c)]


def driver(sym, label, relasi, payload):
    series = [[c[0], c[4]] for c in candles(payload)]
    last_t, last = series[-1]
    # perubahan 24 jam: titik terakhir yang sudah >= 24 jam sebelum data terakhir
    base = next((c for t, c in reversed(series) if t <= last_t - 86400), series[0][1])
    return {"sym": sym, "label": label, "relasi": relasi, "last": last,
            "chg": round(last - base, 4), "chgPct": round((last - base) / base * 100, 2),
            "series": series}


def candle_doc(tf, rows):
    keep = rows[-CANDLE_KEEP[tf]:]
    return {"tf": tf, "rows": [[r[0]] + [round(x, 2) for x in r[1:5]] for r in keep]}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(pair, analysis, price, driver_payloads, events):
    missing = [k for k in REQUIRED if k not in analysis]
    if missing:
        raise ValueError(f"analysis.json kurang field: {missing}")
    doc = dict(analysis)
    doc["pair"] = pair
    doc["priceSymbol"] = PRICE_SYM[pair]
    doc["updatedAt"] = analysis.get("updatedAt") or now_iso()
    doc["price"] = price
    doc["priceAt"] = now_iso()
    doc["drivers"] = [driver(s, lab, rel, p) for (s, lab, rel), p in zip(DRIVERS[pair], driver_payloads) if p]
    doc.setdefault("events", events)
    doc.setdefault("headlines", [])
    doc.setdefault("notes", [])
    doc.pop("candles", None)  # candle sekarang di pairs/<PAIR>/candles/<tf>
    main = doc["setups"][0] if doc["setups"] else {}
    hist = {"updatedAt": doc["updatedAt"], "price": doc["price"], "status": doc["status"],
            "keyakinan": doc["keyakinan"], "side": main.get("side"), "entry": main.get("entry"),
            "sl": main.get("sl"), "tp": main.get("tp", [])}
    hist_id = doc["updatedAt"][:16].replace("-", "").replace("T", "").replace(":", "")
    return doc, hist, hist_id


def _fixture(closes, t0=1_790_000_000, step=3600):
    n = len(closes)
    return {"chart": {"result": [{"timestamp": [t0 + i * step for i in range(n)],
                                  "indicators": {"quote": [{"open": closes, "high": closes,
                                                            "low": closes, "close": closes}]}}]}}


def _selftest():
    p = _fixture([100.0, None, 101.0, 102.0])
    assert candles(p) == [[1790000000, 100.0, 100.0, 100.0, 100.0],
                          [1790007200, 101.0, 101.0, 101.0, 101.0],
                          [1790010800, 102.0, 102.0, 102.0, 102.0]], candles(p)
    # 30 titik per jam, naik 1 per jam: perubahan 24 jam = +24
    d = driver("^TNX", "US10Y", "terbalik", _fixture([5.0 + i for i in range(30)]))
    assert d["last"] == 34.0 and d["chg"] == 24.0 and d["relasi"] == "terbalik", d
    a = {"status": "TUNGGU NEWS", "keyakinan": "sedang", "bias": {}, "levels": [], "zones": [],
         "setups": [{"side": "sell", "entry": 4225.5, "sl": 4249, "tp": [4131, 4088]}],
         "updatedAt": "2026-10-07T08:23:00Z", "candles": [[1, 1, 1, 1, 1]]}
    doc, hist, hid = build("XAUUSD", a, 4158.5, [_fixture([5.3, 5.27])] * 4, [])
    assert doc["price"] == 4158.5 and len(doc["drivers"]) == 4 and "candles" not in doc, doc
    assert hid == "202610070823" and hist["side"] == "sell" and hist["sl"] == 4249, hist
    try:
        build("XAUUSD", {"status": "x"}, 1.0, [], [])
        raise AssertionError("harus gagal tanpa field wajib")
    except ValueError as e:
        assert "keyakinan" in str(e)
    rows = [[i * 60, 4000.123, 4001.456, 3999.0, 4000.5, 9] for i in range(2000)]
    cd = candle_doc("1m", rows)
    assert len(cd["rows"]) == 1440 and cd["rows"][-1] == [1999 * 60, 4000.12, 4001.46, 3999.0, 4000.5], cd["rows"][-1]
    assert len(json.dumps(cd)) < 250_000
    print("selftest OK")


def write_candles(pair, outdir):
    import data
    rows = data.load(pair, list(CANDLE_KEEP), refresh=True)
    for tf, r in rows.items():
        with open(os.path.join(outdir, f"candles_{tf}.json"), "w", encoding="utf-8") as f:
            json.dump(candle_doc(tf, r), f)
    basis = getattr(data.load, "basis", None)
    sumber = f"{data.load.sym} + koreksi spot {basis:+.2f}" if basis is not None else data.load.sym
    return rows["1m"][-1][4], {tf: len(candle_doc(tf, r)["rows"]) for tf, r in rows.items()}, sumber


def main(pair, arg, outdir="."):
    pair = pair.upper()
    os.makedirs(outdir, exist_ok=True)
    price, counts, sumber = write_candles(pair, outdir)
    if arg == "--pantau":
        with open(os.path.join(outdir, "price.json"), "w", encoding="utf-8") as f:
            json.dump({"price": price, "priceAt": now_iso()}, f)
        print(json.dumps({"price": price, "candles": counts}, indent=2))
        return
    analysis = json.load(open(arg, encoding="utf-8"))
    drv = []
    for sym, _, _ in DRIVERS[pair]:
        try:
            drv.append(yahoo(sym, "60m", "5d"))
        except Exception as e:  # satu driver gagal tidak menggagalkan dashboard
            print(f"driver {sym} gagal: {e}", file=sys.stderr)
            drv.append(None)
    try:
        events = cal.events(cal.fetch(6, 7), pair)
    except Exception as e:
        print(f"kalender gagal: {e}", file=sys.stderr)
        events = []
    doc, hist, hid = build(pair, analysis, price, drv, events)
    doc["priceSymbol"] = sumber
    for name, body in (("doc.json", doc), ("history.json", hist)):
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
    print(json.dumps({"historyId": hid, "bytes": len(json.dumps(doc)), "price": price, "candles": counts,
                      "drivers": [d["label"] for d in doc["drivers"]], "events": len(doc["events"])}, indent=2))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(*sys.argv[1:])
