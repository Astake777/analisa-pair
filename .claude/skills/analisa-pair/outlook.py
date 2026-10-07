"""Outlook news 45 hari ke depan: dampak per event, lean sebelum rilis, hasil sesudah rilis.

Pakai:  python outlook.py [PAIR] [--days HARI]      tabel waktu WIB, default XAUUSD 45 hari
Baris build() cocok dengan tabel news_outlook (publish.py outlook <PAIR>).
Lean hanya kalau >= 2 indikator pendahulu sepakat arah dan tidak ada yang melawan:
  nfp/ahe_mm/unemployment : ADP terakhir (<= 10 hari sebelum event) vs forecast, tren 4 klaim terakhir
  cpi/ppi headline        : rata-rata harian CL=F + RB=F bulan referensi vs bulan sebelumnya (> +3% / < -3%)
  cpi headline + core     : kejutan PPI bulan referensi yang sama kalau sudah rilis
Self-check: python outlook.py --selftest
"""
import datetime as dt
import os
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalender as cal  # noqa: E402
from news import PAIR_ON_HAWKISH, SPEC, score  # noqa: E402
from snapshot import candles, yahoo  # noqa: E402

LOOKBACK_H = 35 * 24   # cukup untuk 4 klaim terakhir, ADP, dan PPI bulan yang sama
WINDOW_BACK_H = 6      # baris outlook mulai 6 jam ke belakang
LABOUR = {"nfp", "ahe_mm", "unemployment"}
HEADLINE = {"cpi_mm", "cpi_yy", "ppi_mm"}
CPI = {"cpi_mm", "cpi_yy", "core_cpi_mm", "core_cpi_yy"}
PPI_TITLES = ("PPI MoM", "Core PPI MoM")
FLIP = {"BUY": "SELL", "SELL": "BUY"}


def _t(e):
    return dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))


def _ref_month(e):
    """Bulan data yang dilaporkan (YYYY-MM); tanpa referenceDate dianggap bulan sebelum rilis."""
    if e.get("referenceDate"):
        return e["referenceDate"][:7]
    return _prev_month(e["date"][:7])


def _prev_month(m):
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def dampak(pair, comp, angka=False):
    if comp is None:
        return {"catatan": "angka, tidak dipetakan ke news.SPEC" if angka else "teks, tidak diskor"}
    hawk = PAIR_ON_HAWKISH[pair.upper()]
    panas = hawk if SPEC[comp][0] > 0 else FLIP[hawk]  # panas = actual di atas forecast
    return {"panas": panas, "dingin": FLIP[panas]}


def _ind(nama, nilai, alasan):
    return {"nama": nama, "nilai": nilai, "alasan": alasan}


def ind_adp(e, raw):
    t = _t(e)
    adp = [x for x in raw if x["title"] == "ADP Employment Change" and x["actual"] is not None
           and x["forecast"] is not None and t - dt.timedelta(days=10) <= _t(x) <= t]
    if not adp:
        return _ind("adp", 0, "ADP belum rilis dalam 10 hari sebelum event")
    x = max(adp, key=_t)
    d = x["actual"] - x["forecast"]
    return _ind("adp", (d > 0) - (d < 0), f"ADP {x['actual']:g}K vs F {x['forecast']:g}K")


def ind_klaim(e, raw):
    c = sorted((x for x in raw if x["title"] == "Initial Jobless Claims" and x["actual"] is not None
                and x["forecast"] is not None and _t(x) < _t(e)), key=_t)[-4:]
    if len(c) < 4:
        return _ind("klaim", 0, f"hanya {len(c)} data klaim")
    below = sum(x["actual"] < x["forecast"] for x in c)
    above = sum(x["actual"] > x["forecast"] for x in c)
    v = 1 if below >= 3 else -1 if above >= 3 else 0  # klaim di bawah forecast = pasar kerja ketat
    avg = mean(x["actual"] - x["forecast"] for x in c)
    return _ind("klaim", v, f"4 klaim terakhir: {below} di bawah, {above} di atas forecast (rata-rata {avg:+.1f}K)")


def ind_energi(e, en):
    m = _ref_month(e)
    pm, pcts = _prev_month(m), []
    for sym in ("CL=F", "RB=F"):
        cur = [c for mo, c in en.get(sym, []) if mo == m]
        prev = [c for mo, c in en.get(sym, []) if mo == pm]
        if len(cur) < 3 or not prev:
            return _ind("energi", 0, f"data {sym} {m} belum cukup")
        pcts.append((mean(cur) / mean(prev) - 1) * 100)
    p = mean(pcts)
    return _ind("energi", 1 if p > 3 else -1 if p < -3 else 0,
                f"CL {pcts[0]:+.1f}%, RB {pcts[1]:+.1f}% rata-rata {m} vs {pm}")


def ind_ppi(pair, e, raw):
    m = _ref_month(e)
    ppi = [x for x in raw if x["title"] in PPI_TITLES and _ref_month(x) == m
           and x["actual"] is not None and x["forecast"] is not None and _t(x) < _t(e)]
    if not ppi:
        return _ind("ppi", 0, f"PPI {m} belum rilis")
    s = score(pair, {cal.component(x["title"]): {"actual": x["actual"], "forecast": x["forecast"]} for x in ppi})
    return _ind("ppi", (s["skor"] > 0) - (s["skor"] < 0), f"kejutan PPI {m} skor {s['skor']:+g}")


def lean(pair, e, comp, raw, en):
    ind = []
    if comp in LABOUR:
        ind += [ind_adp(e, raw), ind_klaim(e, raw)]
    if comp in HEADLINE:
        ind.append(ind_energi(e, en))
    if comp in CPI:
        ind.append(ind_ppi(pair, e, raw))
    signs = [i["nilai"] for i in ind if i["nilai"]]
    if len(signs) < 2 or len(set(signs)) != 1:
        return {"arah": None, "alasan": "tidak ada edge", "indikator": ind}
    s, (arah, thr, _) = signs[0], SPEC[comp]
    ref, note = e["forecast"], ""
    if ref is None:
        ref, note = e["previous"], "; forecast belum ada, acuan = previous"
    if ref is None:
        return {"arah": None, "alasan": "forecast belum ada", "indikator": ind}
    r = score(pair, {comp: {"expected": ref + s * arah * thr, "forecast": ref}})
    # arah dari dampak; kekuatan dari bobot komponen di news.score (bobot 0.5 -> lemah)
    return {"arah": dampak(pair, comp)["panas" if s * arah > 0 else "dingin"], "kekuatan": r["kekuatan"],
            "alasan": f"{len(signs)} indikator condong {'hawkish' if s > 0 else 'dovish'}{note}",
            "indikator": ind}


def energi():
    """Penutupan harian CL=F dan RB=F 3 bulan -> {sym: [(YYYY-MM, close)]}."""
    return {sym: [(dt.datetime.fromtimestamp(c[0], dt.timezone.utc).strftime("%Y-%m"), c[4])
                  for c in candles(yahoo(sym, "1d", "3mo"))] for sym in ("CL=F", "RB=F")}


def build(pair="XAUUSD", days=45, raw=None, now=None, en=None):
    pair = pair.upper()
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = cal.fetch(LOOKBACK_H, days) if raw is None else raw
    lo, hi = now - dt.timedelta(hours=WINDOW_BACK_H), now + dt.timedelta(days=days)
    sel = [(e, cal.component(e["title"])) for e in raw if lo <= _t(e) <= hi]
    sel = [(e, c) for e, c in sel if cal.impact(e)]
    if en is None:
        en = {}
        if any(c in HEADLINE and e["actual"] is None for e, c in sel):
            try:
                en = energi()
            except Exception as ex:  # Yahoo gagal -> indikator energi netral, outlook tetap jalan
                print(f"peringatan: data energi gagal ({ex})", file=sys.stderr)
    groups = defaultdict(dict)
    for e, c in sel:
        if c and e["actual"] is not None and e["forecast"] is not None:
            groups[e["date"][:16]][c] = {"actual": e["actual"], "forecast": e["forecast"]}
    hasil = {t: score(pair, d) for t, d in groups.items()}
    rows = []
    for e, c in sorted(sel, key=lambda x: _t(x[0])):
        rows.append({
            "id": str(e["id"]), "pair": pair, "event_time": e["date"], "title": e["title"], "komponen": c,
            "importance": 1 if cal.impact(e) == "High" else 0, "forecast": e["forecast"], "previous": e["previous"],
            "actual": e["actual"],
            "dampak": dampak(pair, c, any(e[k] is not None for k in ("actual", "forecast", "previous"))),
            "lean": lean(pair, e, c, raw, en) if c and e["actual"] is None else None,
            "hasil": hasil.get(e["date"][:16]) if c else None})
    return rows


def table(rows):
    out = []
    for r in rows:
        w = _t({"date": r["event_time"]}).astimezone(cal.WIB)
        d = r["dampak"]
        dmp = f"panas {d['panas']}/dingin {d['dingin']}" if "panas" in d else d["catatan"]
        if r["hasil"]:
            info = f"HASIL {r['hasil']['arah_pair']} ({r['hasil']['kekuatan']}, skor {r['hasil']['skor']:+g})"
        elif r["lean"] and r["lean"]["arah"]:
            info = f"LEAN {r['lean']['arah']} ({r['lean']['kekuatan']}): {r['lean']['alasan']}"
        elif r["lean"]:
            info = f"lean: {r['lean']['alasan']}"
        else:
            info = ""
        f = lambda v: "-" if v is None else f"{v:g}"
        out.append(f"{w:%a %d %b %H:%M} WIB  {r['title'][:30]:<30} F {f(r['forecast']):<6} P {f(r['previous']):<6} "
                   f"A {f(r['actual']):<6} {dmp:<36} {info}")
    return "\n".join(out)


def _selftest():
    def ev(i, date, title, imp, a=None, f=None, p=None, ref=None):
        return {"id": i, "date": date + ":00.000Z", "title": title, "importance": imp,
                "actual": a, "forecast": f, "previous": p, "referenceDate": ref}
    claims = [ev(f"c{i}", f"2026-10-{d}T12:30", "Initial Jobless Claims", 0, a, 210)
              for i, (d, a) in enumerate([("08", 200), ("15", 205), ("22", 215), ("29", 198)])]
    raw = claims + [
        ev("adp", "2026-11-04T13:15", "ADP Employment Change", 0, 150, 100),
        ev("ppi", "2026-11-05T09:30", "PPI MoM", 1, 0.5, 0.3, 0.2, "2026-10-31"),
        ev("cppi", "2026-11-05T09:30", "Core PPI MoM", 0, 0.4, 0.3, 0.2, "2026-10-31"),
        ev("nfp", "2026-11-06T13:30", "Non Farm Payrolls", 1, None, 100, 90),
        ev("ur", "2026-11-06T13:30", "Unemployment Rate", 1, None, 4.2, 4.2),
        ev("ahe", "2026-11-06T13:30", "Average Hourly Earnings MoM", 0, None, 0.3, 0.3),
        ev("cpi", "2026-11-12T13:30", "Inflation Rate MoM", 1, None, 0.3, 0.2, "2026-10-31"),
        ev("ccpi", "2026-11-12T13:30", "Core Inflation Rate MoM", 1, None, 0.3, 0.3, "2026-10-31"),
        ev("fomc", "2026-11-18T19:00", "FOMC Minutes", 1),
        ev("dur", "2026-11-19T13:30", "Durable Goods Orders MoM", 0, None, 0.1, 0),
        ev("eia", "2026-11-19T15:30", "EIA Crude Oil Stocks Change", 0, None, 1.7, 0.9),
        ev("whl", "2026-11-19T15:00", "Wholesale Inventories MoM", -1, None, 0.1, 0.2),
    ]
    en = {s: [("2026-09", 60.0)] * 5 + [("2026-10", 63.0)] * 5 for s in ("CL=F", "RB=F")}  # +5%
    now = dt.datetime(2026, 11, 5, 12, 0, tzinfo=dt.timezone.utc)
    r = {x["id"]: x for x in build("XAUUSD", 45, raw, now, en)}
    # jendela & dampak: klaim/ADP lama di luar jendela, hanya High/Medium ala ForexFactory
    assert set(r) == {"ppi", "cppi", "nfp", "ur", "ahe", "cpi", "ccpi", "fomc", "dur"}, sorted(r)
    assert r["dur"]["importance"] == 0 and r["ahe"]["importance"] == 1, (r["dur"], r["ahe"])
    assert r["nfp"]["komponen"] == "nfp" and r["fomc"]["komponen"] is None
    # dampak: NFP panas -> emas SELL; pengangguran panas (naik) -> emas BUY
    assert r["nfp"]["dampak"] == {"panas": "SELL", "dingin": "BUY"}, r["nfp"]["dampak"]
    assert r["ur"]["dampak"] == {"panas": "BUY", "dingin": "SELL"}, r["ur"]["dampak"]
    assert r["fomc"]["dampak"] == {"catatan": "teks, tidak diskor"} and r["fomc"]["lean"] is None
    # grup rilis yang sama berbagi satu hasil
    assert r["ppi"]["hasil"] is r["cppi"]["hasil"] and r["ppi"]["hasil"]["arah_pair"] == "SELL", r["ppi"]["hasil"]
    assert r["ppi"]["lean"] is None and r["nfp"]["hasil"] is None
    # ADP + klaim (3/4 di bawah forecast) sepakat hawkish -> lean SELL; pengangguran juga SELL
    for k in ("nfp", "ur", "ahe"):
        assert r[k]["lean"]["arah"] == "SELL" and r[k]["lean"]["kekuatan"] == "sedang", (k, r[k]["lean"])
    # CPI headline: energi +5% + PPI panas -> SELL lemah (bobot 0.5); core hanya punya 1 indikator
    assert r["cpi"]["lean"]["arah"] == "SELL" and r["cpi"]["lean"]["kekuatan"] == "lemah", r["cpi"]["lean"]
    assert r["ccpi"]["lean"] == {"arah": None, "alasan": "tidak ada edge", "indikator": r["ccpi"]["lean"]["indikator"]}
    # ADP di bawah forecast melawan klaim -> tidak ada edge
    raw2 = [dict(x, actual=80) if x["id"] == "adp" else x for x in raw]
    assert build("XAUUSD", 45, raw2, now, en)[2]["lean"]["arah"] is None
    # forecast & previous kosong -> tidak mengarang lean
    raw3 = [dict(x, forecast=None, previous=None) if x["id"] == "nfp" else x for x in raw]
    n = next(x for x in build("XAUUSD", 45, raw3, now, en) if x["id"] == "nfp")
    assert n["lean"]["arah"] is None and n["lean"]["alasan"] == "forecast belum ada", n["lean"]
    raw4 = [dict(x, forecast=None) if x["id"] == "nfp" else x for x in raw]
    n = next(x for x in build("XAUUSD", 45, raw4, now, en) if x["id"] == "nfp")
    assert n["lean"]["arah"] == "SELL" and "acuan = previous" in n["lean"]["alasan"], n["lean"]
    assert "Thu 05 Nov 16:30 WIB" in table(list(r.values())), table(list(r.values()))
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    pair = next((a for a in args if not a.startswith("--") and not a.isdigit()), "XAUUSD")
    days = int(args[args.index("--days") + 1]) if "--days" in args else 45
    print(table(build(pair, days)))
