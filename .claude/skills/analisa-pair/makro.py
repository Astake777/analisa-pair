"""Data makro untuk emas: real yield, breakeven, dollar luas (FRED) + COT emas (CFTC).

Pakai:  python makro.py          cetak nilai terakhir + perubahan 1 bulan tiap seri
Baris keluaran fetch_all(): {"series", "date": "YYYY-MM-DD", "value"} -> tabel macro_series.
Sumber (tanpa key):
  FRED fredgraph.csv: DFII10 (real yield 10Y), T10YIE (breakeven 10Y), DTWEXBGS (broad dollar)
  CFTC PRE dataset 72hh-3qpy (Disaggregated - Futures Only), COMEX gold kode 088691:
    COT_GOLD_MM_LONG/SHORT = m_money_positions_long_all/short_all, NET = long - short,
    COT_GOLD_OI = open_interest_all
Self-check: python makro.py --selftest
"""
import csv
import datetime as dt
import io
import json
import sys
import urllib.parse
import urllib.request

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_IDS = ("DFII10", "T10YIE", "DTWEXBGS")
COT = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json?" + urllib.parse.urlencode({
    "cftc_contract_market_code": "088691",
    "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 104})


def _get(url):
    # UA browser polos ditahan FRED sampai timeout; UA non-browser langsung dilayani
    req = urllib.request.Request(url, headers={"User-Agent": "analisa-pair/1.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def fred_rows(series, text, keep=400):
    rows = [{"series": series, "date": d, "value": float(v)}
            for d, v in list(csv.reader(io.StringIO(text)))[1:] if v not in (".", "")]
    return rows[-keep:]


def cot_rows(data):
    out = []
    for r in sorted(data, key=lambda r: r["report_date_as_yyyy_mm_dd"]):
        d = r["report_date_as_yyyy_mm_dd"][:10]
        lo, sh = int(r["m_money_positions_long_all"]), int(r["m_money_positions_short_all"])
        for name, v in (("LONG", lo), ("SHORT", sh), ("NET", lo - sh), ("OI", int(r["open_interest_all"]))):
            out.append({"series": f"COT_GOLD_{'MM_' if name != 'OI' else ''}{name}", "date": d, "value": v})
    return out


def fetch_all():
    rows = []
    for s in FRED_IDS:
        rows += fred_rows(s, _get(FRED.format(s)))
    return rows + cot_rows(json.loads(_get(COT)))


def summary(rows):
    """Per seri: nilai terakhir dan perubahan terhadap nilai terakhir yang >= 30 hari sebelumnya."""
    by = {}
    for r in rows:
        by.setdefault(r["series"], []).append((r["date"], r["value"]))
    out = []
    for s, pts in by.items():
        pts.sort()
        d, v = pts[-1]
        cut = (dt.date.fromisoformat(d) - dt.timedelta(days=30)).isoformat()
        base = next((x for t, x in reversed(pts) if t <= cut), None)
        out.append({"series": s, "date": d, "value": v,
                    "chg1m": None if base is None else round(v - base, 4)})
    return out


def _selftest():
    csv_txt = "observation_date,DFII10\n2026-08-28,2.70\n2026-09-04,.\n2026-09-07,2.80\n2026-10-05,2.95\n"
    r = fred_rows("DFII10", csv_txt)
    assert [x["value"] for x in r] == [2.70, 2.80, 2.95] and r[-1]["date"] == "2026-10-05", r
    assert len(fred_rows("X", csv_txt, keep=2)) == 2
    cot = [{"report_date_as_yyyy_mm_dd": "2026-09-29T00:00:00.000", "m_money_positions_long_all": "131711",
            "m_money_positions_short_all": "11393", "open_interest_all": "406456"},
           {"report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000", "m_money_positions_long_all": "120000",
            "m_money_positions_short_all": "20000", "open_interest_all": "400000"}]
    c = {(x["series"], x["date"]): x["value"] for x in cot_rows(cot)}
    assert c[("COT_GOLD_MM_NET", "2026-09-29")] == 120318 and c[("COT_GOLD_OI", "2026-08-25")] == 400000, c
    s = {x["series"]: x for x in summary(r + cot_rows(cot))}
    assert s["DFII10"]["chg1m"] == 0.25 and s["COT_GOLD_MM_NET"]["chg1m"] == 20318, s
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        for x in summary(fetch_all()):
            chg = "" if x["chg1m"] is None else f"  1b {x['chg1m']:+,}"
            print(f"{x['series']:<18} {x['date']}  {x['value']:>10,}{chg}")
