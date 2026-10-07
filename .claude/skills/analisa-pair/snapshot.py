"""Gabungkan analisis Claude + candle + driver + kalender -> dokumen dashboard.

Pakai:  python snapshot.py <PAIR> <analysis.json> [outdir]
  Menulis <outdir>/doc.json (untuk pairs/<PAIR>) dan <outdir>/history.json
  (untuk pairs/<PAIR>/history/<id>), lalu mencetak ringkasan + id history.
  analysis.json wajib berisi: status, keyakinan, bias, levels, zones, setups.
  Opsional: gaya, makroKonfirmasi, headlines, notes, events, updatedAt, price.
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
MAX_BYTES = 250_000  # batas dokumen db 256 KiB, sisakan ruang


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


def build(pair, analysis, price_payload, driver_payloads, events):
    missing = [k for k in REQUIRED if k not in analysis]
    if missing:
        raise ValueError(f"analysis.json kurang field: {missing}")
    doc = dict(analysis)
    doc["pair"] = pair
    doc["priceSymbol"] = PRICE_SYM[pair]
    doc["updatedAt"] = analysis.get("updatedAt") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["candles"] = candles(price_payload)
    doc.setdefault("price", doc["candles"][-1][4])
    doc["drivers"] = [driver(s, lab, rel, p) for (s, lab, rel), p in zip(DRIVERS[pair], driver_payloads) if p]
    doc.setdefault("events", events)
    doc.setdefault("headlines", [])
    doc.setdefault("notes", [])
    while len(json.dumps(doc)) > MAX_BYTES and len(doc["candles"]) > 50:
        doc["candles"] = doc["candles"][20:]  # buang candle tertua dulu
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
         "updatedAt": "2026-10-07T08:23:00Z"}
    doc, hist, hid = build("XAUUSD", a, _fixture([4150.0, 4158.5]), [_fixture([5.3, 5.27])] * 4, [])
    assert doc["price"] == 4158.5 and len(doc["drivers"]) == 4 and doc["priceSymbol"] == "GC=F", doc
    assert hid == "202610070823" and hist["side"] == "sell" and hist["sl"] == 4249, hist
    try:
        build("XAUUSD", {"status": "x"}, _fixture([1.0]), [], [])
        raise AssertionError("harus gagal tanpa field wajib")
    except ValueError as e:
        assert "keyakinan" in str(e)
    big = build("XAUUSD", a, _fixture([4000.0 + i % 50 for i in range(20000)]), [], [])[0]
    assert len(json.dumps(big)) <= MAX_BYTES and big["candles"][-1][4] == 4000.0 + 19999 % 50
    print("selftest OK")


def main(pair, analysis_path, outdir="."):
    pair = pair.upper()
    analysis = json.load(open(analysis_path, encoding="utf-8"))
    price_payload = yahoo(PRICE_SYM[pair], "60m", "1mo")
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
    doc, hist, hid = build(pair, analysis, price_payload, drv, events)
    os.makedirs(outdir, exist_ok=True)
    for name, body in (("doc.json", doc), ("history.json", hist)):
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
    print(json.dumps({"doc": os.path.join(outdir, "doc.json"), "history": os.path.join(outdir, "history.json"),
                      "historyId": hid, "bytes": len(json.dumps(doc)), "candles": len(doc["candles"]),
                      "drivers": [d["label"] for d in doc["drivers"]], "events": len(doc["events"])}, indent=2))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(*sys.argv[1:])
