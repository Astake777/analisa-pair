"""Skenario news high-impact USD (panas / sesuai / dingin) + data pendukung -> news_outlook.skenario/pendukung.

Pakai:  python skenario_news.py [PAIR] [--jam JAM] [--id ID[,ID]]     default XAUUSD, 48 jam ke depan
  Event: High (importance 1) yang belum rilis dan punya ambang di news.SPEC (+ Fed Interest Rate Decision).
  syarat   : forecast +- ambang news.SPEC (forecast kosong -> previous).
  peluang  : bucket Polymarket bulan yang sama; kalau tidak ada, dari lean indikator pendahulu outlook.py
             ((n + 1) / (N + 3) per kelas); kalau lean tidak ada -> null.
  gerak15m / gerak1h : median |gerak emas| ($) 15 menit / 1 jam sesudah rilis judul yang sama 12 bulan
             terakhir, hanya rilis sebelum event (XAUT 1m/5m sejak 26 Mar 2026, GC=F 1h untuk rilis tepat
             di awal jam); < 3 sampel -> null.
Self-check: python skenario_news.py --selftest   (fixture, tanpa jaringan)
"""
import calendar
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalender as cal  # noqa: E402
import outlook  # noqa: E402
import polymarket as pm  # noqa: E402
from news import PAIR_ON_HAWKISH, SPEC  # noqa: E402

FLIP = outlook.FLIP
_t = outlook._t
EXTRA = {"Fed Interest Rate Decision": ("fed_rate", (+1, 0.25, 1.0))}  # tidak ada di news.SPEC; % suku bunga
HIST_HARI = 365
POTONG_HARI = 60      # feed TradingView memotong di 2000 event per panggilan
MIN_SAMPEL = 3
STEP = {"1m": 60, "5m": 300, "1h": 3600}
KELAS = ("panas", "sesuai", "dingin")
ASET = [{"label": "US10Y", "sym": "^TNX", "relasi": "terbalik"},
        {"label": "DXY", "sym": "DX-Y.NYB", "relasi": "terbalik"},
        {"label": "EURUSD", "sym": "EURUSD=X", "relasi": "searah"},
        {"label": "S&P 500 futures", "sym": "ES=F", "relasi": "konteks"}]
# komponen -> topik polymarket.cari; PASAR = pola judul market yang bucket-nya memakai satuan komponen ini
PM_TOPIK = {"cpi_mm": "cpi", "cpi_yy": "cpi", "core_cpi_mm": "cpi", "core_cpi_yy": "cpi",
            "nfp": "nfp", "ahe_mm": "nfp", "unemployment": "unemployment", "fed_rate": "fomc"}
PM_PASAR = {"cpi_mm": r"Inflation US - Monthly", "core_cpi_mm": r"^Core CPI MoM", "nfp": r"jobs added",
            "unemployment": r"Unemployment Rate", "fed_rate": r"^Fed Decision in"}
NAMA_IND = {"adp": "ADP", "klaim": "Tren klaim pengangguran (4 rilis)", "energi": "Energi CL/RB m/m",
            "ppi": "PPI bulan yang sama"}
KONFIRMASI = {
    "SELL": "US10Y naik dan DXY menguat dalam 5 menit sesudah rilis; candle M5 emas tutup di bawah harga pra-rilis",
    "BUY": "US10Y turun dan DXY melemah dalam 5 menit sesudah rilis; candle M5 emas tutup di atas harga pra-rilis",
    "netral": "Tanpa kejutan: tunggu 15 menit, ikut arah hanya kalau US10Y dan DXY bergerak searah"}
BATAL = {
    "SELL": "US10Y berbalik turun atau emas kembali di atas harga pra-rilis dalam 15 menit",
    "BUY": "US10Y berbalik naik atau emas kembali di bawah harga pra-rilis dalam 15 menit",
    "netral": "US10Y dan DXY berlawanan arah atau spike tanpa lanjutan -> no trade"}


def riwayat(now, maju_jam, hari=HIST_HARI):
    """Kalender TradingView US dari now-hari sampai now+maju_jam, diambil per POTONG_HARI."""
    f = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out, t, end = {}, now - dt.timedelta(days=hari), now + dt.timedelta(hours=maju_jam)
    while t < end:
        t2 = min(t + dt.timedelta(days=POTONG_HARI), end)
        req = urllib.request.Request(cal.URL.format(f(t), f(t2)),
                                     headers={"Origin": "https://www.tradingview.com", "User-Agent": "Mozilla/5.0"})
        for e in json.load(urllib.request.urlopen(req, timeout=30))["result"]:
            out[e["id"]] = e
        t = t2
    return sorted(out.values(), key=lambda e: e["date"])


def harga():
    """{tf: {t: candle}}: XAUT 1m/5m (tanpa koreksi spot, gerak $ sama) + GC=F 1h untuk riwayat lebih lama."""
    import data
    out = {}
    try:
        out.update(data.load("XAUUSD", ["1m", "5m"], source="binance", spot=False))
    except Exception as ex:
        print(f"peringatan: candle XAUT gagal ({ex})", file=sys.stderr)
    try:
        out["1h"] = data.fetch("GC=F", "1h")
    except Exception as ex:
        print(f"peringatan: candle GC=F 1h gagal ({ex})", file=sys.stderr)
    return {tf: {r[0]: r for r in rows} for tf, rows in out.items()}


def _gerak(px, ts, menit, tfs):
    """|close candle terakhir dalam jendela - open candle rilis|, dari timeframe pertama yang lengkap."""
    for tf in tfs:
        rows = px.get(tf, {})
        a, b = rows.get(ts), rows.get(ts + menit * 60 - STEP[tf])
        if a and b:
            return abs(b[4] - a[1])
    return None


def gerak(title, waktu, raw, px):
    """(median 15m, median 1h, n15, n1h) dari rilis `title` yang jendela 1 jamnya selesai sebelum `waktu`."""
    lo, batas = waktu - dt.timedelta(days=HIST_HARI), waktu - dt.timedelta(hours=1)
    ts = sorted({int(_t(e).timestamp()) for e in raw
                 if e["title"] == title and e["actual"] is not None and lo <= _t(e) <= batas})
    g15 = [g for t in ts if (g := _gerak(px, t, 15, ("1m", "5m"))) is not None]
    g1h = [g for t in ts if (g := _gerak(px, t, 60, ("1m", "5m", "1h"))) is not None]
    med = lambda xs: round(median(xs), 2) if len(xs) >= MIN_SAMPEL else None
    return med(g15), med(g1h), len(g15), len(g1h)


def _kelas(v, fc, thr):
    eps = 1e-9
    return "panas" if v >= fc + thr - eps else "dingin" if v <= fc - thr + eps else "sesuai"


def peluang_pm(pasar, fc, thr, base=0.0):
    """Jumlah peluang 'Yes' bucket per kelas, dinormalisasi. None kalau ada label yang tidak terbaca."""
    tot = dict.fromkeys(KELAS, 0.0)
    for o in pasar["outcomes"]:
        v = pm.nilai(o["label"])
        if v is None:
            return None
        tot[_kelas(base + v, fc, thr)] += o["peluang"]
    s = sum(tot.values())
    return {k: round(v / s, 3) for k, v in tot.items()} if s > 0 else None


def peluang_lean(ind, lean):
    if not lean or not lean.get("arah"):
        return None
    n = {k: sum(i["arah"] == ("netral" if k == "sesuai" else k) for i in ind) for k in KELAS}
    # ponytail: hitungan indikator + smoothing Laplace, bukan model terkalibrasi; ganti kalau ada data hit-rate
    return {k: round((n[k] + 1) / (len(ind) + 3), 3) for k in KELAS}


def indikator(lean, sign):
    out = []
    for i in (lean or {}).get("indikator", []):
        s = i["nilai"] * sign
        arah = "panas" if s > 0 else "dingin" if s < 0 else "netral"
        usd = "hawkish" if i["nilai"] > 0 else "dovish" if i["nilai"] < 0 else None
        out.append({"nama": NAMA_IND.get(i["nama"], i["nama"]), "nilai": i["alasan"], "arah": arah,
                    "catatan": f"USD {usd} -> actual condong {arah}" if usd else "tidak ada sinyal"})
    return out


def _spec(r):
    if r["komponen"] in SPEC:
        return r["komponen"], SPEC[r["komponen"]]
    return EXTRA.get(r["title"], (None, None))


def satu(pair, r, ev, comp, spec, raw, px, pasar):
    """Satu baris {id, skenario, pendukung} + info sampel; None kalau tidak ada angka acuan."""
    sign, thr, _ = spec
    fc, acuan = r["forecast"], ""
    if fc is None:
        fc, acuan = r["previous"], " (forecast belum ada, acuan previous)"
    if fc is None:
        return None, "forecast dan previous kosong"
    hi, lo = round(fc + thr, 4), round(fc - thr, 4)
    hawk = PAIR_ON_HAWKISH[pair]
    panas = hawk if sign > 0 else FLIP[hawk]  # sama dengan arah news.score saat actual di atas forecast
    arah = {"panas": panas, "sesuai": "netral", "dingin": FLIP[panas]}
    syarat = {"panas": f"actual >= {hi:g}", "sesuai": f"{lo:g} < actual < {hi:g}", "dingin": f"actual <= {lo:g}"}

    ind = indikator(r["lean"], sign)
    bulan = r["event_time"][:7] if comp == "fed_rate" else outlook._ref_month(ev)
    nama_bulan = calendar.month_name[int(bulan[5:7])]
    pasar_ev = [x for x in pasar(PM_TOPIK[comp]) if nama_bulan in x["judul"]] if comp in PM_TOPIK else []
    pilih = next((x for x in pasar_ev if comp in PM_PASAR and re.search(PM_PASAR[comp], x["judul"])), None)
    base = r["previous"] if comp == "fed_rate" else 0.0  # bucket Fed = perubahan dari suku bunga sekarang
    p, sumber = None, "tidak ada"
    if pilih and base is not None:
        p, sumber = peluang_pm(pilih, fc, thr, base), f"Polymarket: {pilih['judul']}"
    if p is None:
        p, sumber = peluang_lean(ind, r["lean"]), "lean indikator"
        sumber = sumber if p else "tidak ada"

    g15, g1h, n15, n1h = gerak(r["title"], _t({"date": r["event_time"]}), raw, px)
    sk = [{"nama": k, "syarat": syarat[k] + acuan, "peluang": p[k] if p else None, "arahEmas": arah[k],
           "gerak15m": g15, "gerak1h": g1h, "konfirmasi": KONFIRMASI[arah[k]], "batal": BATAL[arah[k]]}
          for k in KELAS]
    row = {"id": r["id"], "skenario": sk, "pendukung": {"indikator": ind, "polymarket": pasar_ev, "aset": ASET}}
    return row, {"title": r["title"], "event_time": r["event_time"], "forecast": r["forecast"],
                 "previous": r["previous"], "sumber": sumber, "n15": n15, "n1h": n1h}


def build(pair="XAUUSD", jam=48, ids=None, raw=None, px=None, pasar=None, now=None, en=None):
    """Baris {id, skenario, pendukung} untuk news_outlook. build.info = {id: ringkasan}, build.lewati = [(row, alasan)]."""
    pair = pair.upper()
    now = now or dt.datetime.now(dt.timezone.utc)
    maju = 45 * 24 if ids else jam
    raw = riwayat(now, maju) if raw is None else raw
    rows = outlook.build(pair, max(1, math.ceil(maju / 24)), raw, now, en)
    if ids:
        sel = [r for r in rows if r["id"] in ids]
    else:
        end = now + dt.timedelta(hours=jam)
        sel = [r for r in rows if r["importance"] == 1 and r["actual"] is None
               and now <= _t({"date": r["event_time"]}) <= end]
    cache = {}
    if pasar is None:
        pasar = lambda topik: pm.cari(topik, maks=10)
    cari = lambda topik: cache[topik] if topik in cache else cache.setdefault(topik, pasar(topik))
    by_id = {str(e["id"]): e for e in raw}
    out, build.info, build.lewati = [], {}, []
    for r in sel:
        comp, spec = _spec(r)
        if spec is None:
            build.lewati.append((r, "tidak ada ambang di news.SPEC"))
            continue
        if px is None:
            px = harga()
        row, info = satu(pair, r, by_id[r["id"]], comp, spec, raw, px, cari)
        if row is None:
            build.lewati.append((r, info))
            continue
        out.append(row)
        build.info[r["id"]] = info
    return out


def ringkas(rows):
    out = []
    for row in rows:
        i = build.info[row["id"]]
        w = _t({"date": i["event_time"]}).astimezone(cal.WIB)
        f = lambda v: "-" if v is None else f"{v:g}"
        out.append(f"\n{w:%a %d %b %H:%M} WIB  {i['title']}  (id {row['id']}, F {f(i['forecast'])}, P {f(i['previous'])})")
        out.append(f"  peluang: {i['sumber']}; gerak: {i['n15']} sampel 15m, {i['n1h']} sampel 1h")
        for s in row["skenario"]:
            p = "-" if s["peluang"] is None else f"{s['peluang'] * 100:.0f}%"
            g = lambda v: "-" if v is None else f"${v:g}"
            out.append(f"  {s['nama']:<6} {s['syarat']:<34} peluang {p:>4}  emas {s['arahEmas']:<6} "
                       f"gerak 15m {g(s['gerak15m'])} / 1j {g(s['gerak1h'])}")
            out.append(f"         konfirmasi: {s['konfirmasi']}\n         batal: {s['batal']}")
        for x in row["pendukung"]["indikator"]:
            out.append(f"  indikator {x['nama']}: {x['nilai']} -> {x['arah']}")
        for x in row["pendukung"]["polymarket"]:
            top = ", ".join(f"{o['label']} {o['peluang'] * 100:.0f}%" for o in x["outcomes"])
            out.append(f"  polymarket {x['judul']} (${x['volume']:,.0f}): {top}")
    for r, alasan in build.lewati:
        w = _t({"date": r["event_time"]}).astimezone(cal.WIB)
        out.append(f"\n{w:%a %d %b %H:%M} WIB  {r['title']}: dilewati, {alasan}")
    return "\n".join(out) if out else "Tidak ada event High USD dalam jendela ini."


def _selftest():
    now = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)

    def ev(i, date, title, imp, a=None, f=None, p=None, ref=None):
        return {"id": i, "date": date + ":00.000Z", "title": title, "importance": imp,
                "actual": a, "forecast": f, "previous": p, "referenceDate": ref}
    claims = [ev(f"c{d}", f"2026-09-{d}T12:30", "Initial Jobless Claims", 0, 200, 210) for d in (17, 24)]
    claims += [ev(f"c{d}", f"2026-10-{d}T12:30", "Initial Jobless Claims", 0, 200, 210) for d in ("01", "08")]
    raw = claims + [
        ev("adp", "2026-10-12T12:15", "ADP Employment Change", 0, 150, 100),
        # rilis lalu untuk gerak: NFP 3 kali, CPI 2 kali
        ev("n1", "2026-07-02T12:30", "Non Farm Payrolls", 1, 57, 110, 129),
        ev("n2", "2026-08-07T12:30", "Non Farm Payrolls", 1, -23, 80, 20),
        ev("n3", "2026-09-04T12:30", "Non Farm Payrolls", 1, 162, 56, 21),
        ev("i1", "2026-08-12T12:30", "Inflation Rate MoM", 1, 0.1, 0.1, -0.4),
        ev("i2", "2026-09-11T12:30", "Inflation Rate MoM", 1, 0.4, 0.4, 0.1),
        # jendela 48 jam
        ev("cpi", "2026-10-14T12:30", "Inflation Rate MoM", 1, None, 0.4, 0.4, "2026-09-30"),
        ev("ccpi", "2026-10-14T12:30", "Core Inflation Rate MoM", 1, None, 0.3, 0.3, "2026-09-30"),
        ev("nfp", "2026-10-14T18:30", "Non Farm Payrolls", 1, None, 60, 162, "2026-09-30"),
        ev("ur", "2026-10-14T18:30", "Unemployment Rate", 1, None, 4.2, 4.2, "2026-09-30"),
        ev("mich", "2026-10-14T14:00", "Michigan Consumer Sentiment Prel", 1, None, 47.6, 48.1),
        ev("fomc", "2026-10-15T04:00", "Fed Interest Rate Decision", 1, None, 4.0, 4.0, "2026-10-15"),
        ev("jauh", "2026-10-16T12:30", "Retail Sales MoM", 1, None, 0.3, 0.2, "2026-09-30"),
    ]
    px = {"1m": {}, "5m": {}, "1h": {}}

    def kandel(tf, ts, buka, c15=None, c1h=None):
        s = STEP[tf]
        px[tf][ts] = [ts, buka, buka, buka, buka, 0]
        if c15 is not None:
            px[tf][ts + 900 - s] = [ts + 900 - s, 0, 0, 0, c15, 0]
        if c1h is not None:
            px[tf][ts + 3600 - s] = [ts + 3600 - s, 0, 0, 0, c1h, 0]
    T = lambda e: int(_t(e).timestamp())
    by = {e["id"]: e for e in raw}
    kandel("1m", T(by["n1"]), 4000, 4010, 3980)     # 15m 10, 1h 20
    kandel("5m", T(by["n2"]), 4000, 3980, 4030)     # 15m 20, 1h 30 (fallback 5m)
    kandel("1m", T(by["n3"]), 4000, 4030)           # 15m 30, 1h tidak ada
    kandel("1m", T(by["i1"]), 4000, 4005, 4007)
    kandel("1m", T(by["i2"]), 4000, 4009, 4011)

    fixture = {
        "cpi": [{"judul": "August Inflation US - Monthly", "url": "u0", "volume": 9e5,
                 "outcomes": [{"label": "0.4%", "peluang": 1.0}]},
                {"judul": "September Inflation US - Monthly", "url": "u1", "volume": 55369.0,
                 "outcomes": [{"label": "≤0.3%", "peluang": 0.1}, {"label": "0.4%", "peluang": 0.5},
                              {"label": "0.5%", "peluang": 0.3}, {"label": "≥0.6%", "peluang": 0.1}]}],
        "fomc": [{"judul": "Fed Decision in October?", "url": "u2", "volume": 2.8e7,
                  "outcomes": [{"label": "25 bps decrease", "peluang": 0.05}, {"label": "No change", "peluang": 0.8},
                               {"label": "25 bps increase", "peluang": 0.15}]}],
    }
    dipanggil = []
    pasar = lambda t: dipanggil.append(t) or fixture.get(t, [])
    rows = build("XAUUSD", 48, raw=raw, px=px, pasar=pasar, now=now, en={})
    r = {x["id"]: x for x in rows}
    assert set(r) == {"cpi", "ccpi", "nfp", "ur", "fomc"}, sorted(r)   # retail sales > 48 jam
    assert [(x["title"], a) for x, a in build.lewati] == [("Michigan Consumer Sentiment Prel", "tidak ada ambang di news.SPEC")]
    assert sorted(dipanggil) == ["cpi", "fomc", "nfp", "unemployment"], dipanggil      # tiap topik sekali (cache)
    for k, row in r.items():
        assert [s["nama"] for s in row["skenario"]] == ["panas", "sesuai", "dingin"], (k, row)
        assert set(row["pendukung"]) == {"indikator", "polymarket", "aset"} and row["pendukung"]["aset"] == ASET
        assert set(row["skenario"][0]) == {"nama", "syarat", "peluang", "arahEmas", "gerak15m", "gerak1h",
                                           "konfirmasi", "batal"}
    arah = lambda k: [s["arahEmas"] for s in r[k]["skenario"]]
    # CPI panas -> USD hawkish -> emas SELL; syarat dari forecast +- ambang SPEC 0.1
    c = r["cpi"]["skenario"]
    assert arah("cpi") == ["SELL", "netral", "BUY"] and c[0]["syarat"] == "actual >= 0.5", c
    assert c[1]["syarat"] == "0.3 < actual < 0.5" and c[2]["syarat"] == "actual <= 0.3", c
    # peluang dari bucket Polymarket bulan referensi (September), bukan Agustus
    assert [s["peluang"] for s in c] == [0.4, 0.5, 0.1], c
    assert [x["judul"] for x in r["cpi"]["pendukung"]["polymarket"]] == ["September Inflation US - Monthly"]
    assert "Polymarket" in build.info["cpi"]["sumber"] and c[0]["konfirmasi"].startswith("US10Y naik"), c[0]
    # CPI baru 2 rilis sebelumnya -> gerak null
    assert c[0]["gerak15m"] is None and c[0]["gerak1h"] is None and build.info["cpi"]["n15"] == 2, build.info["cpi"]
    # NFP: panas -> SELL; ADP + klaim hawkish -> peluang dari lean (2 dari 2 indikator panas)
    n = r["nfp"]["skenario"]
    assert arah("nfp") == ["SELL", "netral", "BUY"] and n[0]["syarat"] == "actual >= 110", n
    assert [s["peluang"] for s in n] == [0.6, 0.2, 0.2], n
    assert [i["arah"] for i in r["nfp"]["pendukung"]["indikator"]] == ["panas", "panas"], r["nfp"]["pendukung"]
    assert n[0]["gerak15m"] == 20 and n[0]["gerak1h"] is None and build.info["nfp"]["n1h"] == 2, (n[0], build.info["nfp"])
    # pengangguran: panas (naik) -> BUY; indikator hawkish = condong dingin
    u = r["ur"]["skenario"]
    assert arah("ur") == ["BUY", "netral", "SELL"] and [s["peluang"] for s in u] == [0.2, 0.2, 0.6], u
    # core CPI: hanya 1 indikator (PPI belum rilis), tanpa market core -> peluang null, tidak dikarang
    assert all(s["peluang"] is None for s in r["ccpi"]["skenario"]) and build.info["ccpi"]["sumber"] == "tidak ada"
    # Fed: bucket bps relatif ke suku bunga sekarang 4.0
    f = r["fomc"]["skenario"]
    assert arah("fomc") == ["SELL", "netral", "BUY"] and [s["peluang"] for s in f] == [0.15, 0.8, 0.05], f
    assert f[0]["syarat"] == "actual >= 4.25" and r["fomc"]["pendukung"]["polymarket"][0]["url"] == "u2"
    assert "dilewati" in ringkas(rows) and "Non Farm Payrolls" in ringkas(rows)
    # kausal: rilis sesudah event (dan yang jendelanya belum selesai) tidak dipakai
    later = [ev("n4", "2026-11-06T13:30", "Non Farm Payrolls", 1, 999, 60, 90)]
    kandel("1m", T(later[0]), 4000, 4500, 4500)
    t_nfp = _t(by["nfp"])
    assert gerak("Non Farm Payrolls", t_nfp, raw + later, px)[:3] == (20, None, 3)
    assert gerak("Non Farm Payrolls", _t(by["n3"]), raw, px)[2] == 2   # hanya n1, n2 sebelum n3
    # GC=F 1h dipakai untuk rilis tepat di awal jam
    fed = [ev(f"f{i}", d, "Fed Interest Rate Decision", 1, 4.0, 4.0, 3.75) for i, d in
           enumerate(["2026-06-17T18:00", "2026-07-29T18:00", "2026-09-16T18:00"])]
    for e, c1h in zip(fed, (4010, 3990, 4030)):
        px["1h"][T(e)] = [T(e), 4000, 0, 0, c1h, 0]
    assert gerak("Fed Interest Rate Decision", now, fed, px) == (None, 10, 0, 3)
    # id tertentu, di luar 48 jam
    assert [x["id"] for x in build("XAUUSD", 48, ids={"jauh"}, raw=raw, px=px, pasar=pasar, now=now, en={})] == ["jauh"]
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    pair = next((a for a in args if not a.startswith("--") and not a.isdigit()), "XAUUSD")
    jam = int(args[args.index("--jam") + 1]) if "--jam" in args else 48
    ids = set(args[args.index("--id") + 1].split(",")) if "--id" in args else None
    print(ringkas(build(pair, jam, ids)))
