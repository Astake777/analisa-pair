"""Kalender ekonomi (TradingView, gratis, ada actual) + skor buy/sell per rilis.

Pakai:  python kalender.py [PAIR] [--back JAM] [--days HARI]
  default: XAUUSD, 6 jam ke belakang, 7 hari ke depan, negara US.
Self-check: python kalender.py --selftest
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news import score  # noqa: E402

URL = "https://economic-calendar.tradingview.com/events?from={}&to={}&countries=US"
WIB = dt.timezone(dt.timedelta(hours=7))
# judul persis di feed TradingView -> komponen news.py
TITLE = {
    "Non Farm Payrolls": "nfp", "ADP Employment Change": "adp",
    "Unemployment Rate": "unemployment", "Average Hourly Earnings MoM": "ahe_mm",
    "Initial Jobless Claims": "claims", "JOLTs Job Openings": "jolts",
    "Inflation Rate MoM": "cpi_mm", "Inflation Rate YoY": "cpi_yy",
    "Core Inflation Rate MoM": "core_cpi_mm", "Core Inflation Rate YoY": "core_cpi_yy",
    "PPI MoM": "ppi_mm", "Core PPI MoM": "core_ppi_mm",
    "Core PCE Price Index MoM": "core_pce_mm", "Retail Sales MoM": "retail_sales_mm",
    "ISM Manufacturing PMI": "ism_mfg", "ISM Services PMI": "ism_services",
}


def component(title):
    if title.startswith("GDP Growth Rate QoQ"):
        return "gdp_qq"
    return TITLE.get(title)


def fetch(back_h, days):
    now = dt.datetime.now(dt.timezone.utc)
    f = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    req = urllib.request.Request(
        URL.format(f(now - dt.timedelta(hours=back_h)), f(now + dt.timedelta(days=days))),
        headers={"Origin": "https://www.tradingview.com", "User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=20))["result"]


def events(raw, pair):
    """Kelompokkan event per jam rilis -> list dict, plus skor kalau actual sudah ada."""
    groups = defaultdict(list)
    for e in raw:
        comp = component(e["title"])
        if comp or e.get("importance") == 1:
            groups[e["date"][:16]].append((e, comp))
    out = []
    for t in sorted(groups):
        when = dt.datetime.fromisoformat(t + ":00+00:00").astimezone(WIB)
        g = {"waktuUTC": t + "Z", "waktuWIB": f"{when:%a %d %b %H:%M} WIB", "items": [],
             "jenis": None, "usd": None, "arah": None, "kekuatan": None, "skor": None}
        data = {}
        for e, comp in groups[t]:
            g["items"].append({k: e[k] for k in ("title", "actual", "forecast", "previous")})
            if comp and e["forecast"] is not None and e["actual"] is not None:
                data[comp] = {"actual": e["actual"], "forecast": e["forecast"]}
        if data:
            s = score(pair, data)
            g.update(jenis="HASIL", usd=s["usd"], arah=s["arah_pair"], kekuatan=s["kekuatan"], skor=s["skor"])
        elif any(c for _, c in groups[t]):
            g["jenis"] = "BELUM RILIS"
        out.append(g)
    return out


def report(raw, pair):
    out = []
    for g in events(raw, pair):
        out.append(f"\n{g['waktuWIB']}")
        for i in g["items"]:
            out.append(f"  {i['title']:<32} A {i['actual']!s:<7} F {i['forecast']!s:<7} P {i['previous']}")
        if g["jenis"] == "HASIL":
            out.append(f"  -> USD {g['usd']} | {pair.upper()} {g['arah']} ({g['kekuatan']}, skor {g['skor']})")
        elif g["jenis"]:
            out.append("  -> belum rilis: buat LEAN dengan news.py (key 'expected')")
    return "\n".join(out)


def _selftest():
    ev = [  # NFP 2 Okt 2026, data asli dari feed
        {"date": "2026-10-02T12:30:00.000Z", "title": "Non Farm Payrolls", "importance": 1,
         "actual": 29, "forecast": 90, "previous": 133},
        {"date": "2026-10-02T12:30:00.000Z", "title": "Average Hourly Earnings MoM", "importance": 0,
         "actual": 0.1, "forecast": 0.3, "previous": 0.3},
        {"date": "2026-10-02T12:30:00.000Z", "title": "Unemployment Rate", "importance": 1,
         "actual": 4.1, "forecast": 4.1, "previous": 4.1},
        {"date": "2026-10-09T14:00:00.000Z", "title": "Michigan Consumer Sentiment Prel", "importance": 1,
         "actual": None, "forecast": 47.6, "previous": 48.1},
        {"date": "2026-10-09T12:30:00.000Z", "title": "Durable Goods Orders MoM", "importance": 0,
         "actual": None, "forecast": 0.1, "previous": 0},
    ]
    r = report(ev, "XAUUSD")
    assert "Fri 02 Oct 19:30 WIB" in r, r                  # 12:30 UTC -> 19:30 WIB
    assert "USD DOVISH | XAUUSD BUY (kuat" in r, r          # NFP -61K & upah -0.2 -> dovish kuat
    assert "Fri 09 Oct 21:00 WIB" in r and "Durable" not in r, r  # impor tinggi masuk, impor rendah tak terpetakan dibuang
    assert component("GDP Growth Rate QoQ Adv") == "gdp_qq"
    g = events(ev, "XAUUSD")
    assert g[0]["jenis"] == "HASIL" and g[0]["arah"] == "BUY" and len(g[0]["items"]) == 3, g[0]
    assert g[-1]["waktuWIB"] == "Fri 09 Oct 21:00 WIB" and g[-1]["jenis"] is None, g[-1]
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    pair = next((a for a in args if not a.startswith("--") and not a.isdigit()), "XAUUSD")
    opt = lambda k, d: int(args[args.index(k) + 1]) if k in args else d
    print(report(fetch(opt("--back", 6), opt("--days", 7)), pair))
