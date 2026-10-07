"""Peluang pasar prediksi Polymarket (Gamma API publik, tanpa kunci) untuk news USD.

Pakai:  python polymarket.py <topik>        topik: fomc | cpi | nfp | unemployment | teks bebas
  Mencetak maksimal 3 event aktif dengan volume terbesar beserta peluang tiap outcome.
Self-check: python polymarket.py --selftest   (fixture, tanpa jaringan)
"""
import json
import re
import sys
import urllib.parse
import urllib.request

URL = "https://gamma-api.polymarket.com/public-search?q={}&events_status=active&limit_per_type=20"
# topik -> (kata kunci pencarian, pola judul event yang relevan)
TOPIK = {
    "fomc": ("fed decision", r"^Fed Decision in "),
    "cpi": ("cpi", r"Inflation US - Monthly|^Core CPI MoM"),
    "nfp": ("jobs added", r"^How many jobs added in "),
    "unemployment": ("unemployment rate", r"^\w+ Unemployment Rate$"),
}
OPENER = urllib.request.urlopen  # diganti di selftest


def _get(q):
    req = urllib.request.Request(URL.format(urllib.parse.quote(q)), headers={"User-Agent": "Mozilla/5.0"})
    return json.load(OPENER(req, timeout=20))


def _yes(m):
    try:
        return float(json.loads(m["outcomePrices"])[0])
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def olah(payload, pola=None, maks=3):
    """Respons public-search -> [{judul, url, volume, outcomes:[{label, peluang}]}], urut volume."""
    out = []
    for e in payload.get("events") or []:
        if not e.get("active") or e.get("closed") or (pola and not re.search(pola, e.get("title", ""))):
            continue
        ms = [m for m in e.get("markets") or [] if m.get("active") and not m.get("closed") and _yes(m) is not None]
        ms.sort(key=lambda m: float(m.get("groupItemThreshold") or 0))
        out.append({"judul": e["title"], "url": f"https://polymarket.com/event/{e['slug']}",
                    "volume": round(float(e.get("volume") or 0), 2),
                    "outcomes": [{"label": m.get("groupItemTitle") or m.get("question", ""), "peluang": _yes(m)}
                                 for m in ms]})
    return sorted(out, key=lambda x: -x["volume"])[:maks]


def cari(topik, maks=3):
    q, pola = TOPIK.get(topik.lower(), (topik, None))
    try:
        return olah(_get(q), pola, maks)
    except Exception as ex:  # jaringan/format gagal: lanjut tanpa Polymarket
        print(f"peringatan: Polymarket '{topik}' gagal ({ex})", file=sys.stderr)
        return []


def nilai(label):
    """Label bucket -> angka wakil: '0.4%' -> 0.4, '≥0.8%' -> 0.8, '25k to 50k' -> 37.5,
    '25 bps increase' -> 0.25, 'No change' -> 0. None kalau tidak terbaca."""
    s = label.replace(",", "").strip()
    if re.fullmatch(r"(?i)no change", s):
        return 0.0
    m = re.fullmatch(r"(?i)(\d+)\+? ?bps (increase|decrease)", s)
    if m:
        return int(m[1]) / 100 * (1 if m[2].lower() == "increase" else -1)
    n = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]
    if " to " in s and len(n) == 2:
        return (n[0] + n[1]) / 2  # ponytail: bucket rentang diwakili titik tengah
    return n[0] if len(n) == 1 else None


FIXTURE = {"events": [
    {"slug": "fed-decision-in-october-x", "title": "Fed Decision in October?", "active": True, "closed": False,
     "volume": 28509686.5, "markets": [
         {"groupItemTitle": "No change", "groupItemThreshold": "2", "outcomePrices": '["0.835", "0.165"]',
          "active": True, "closed": False},
         {"groupItemTitle": "25 bps increase", "groupItemThreshold": "3", "outcomePrices": '["0.155", "0.845"]',
          "active": True, "closed": False},
         {"groupItemTitle": "25 bps decrease", "groupItemThreshold": "1", "outcomePrices": '["0.005", "0.995"]',
          "active": True, "closed": False},
         {"groupItemTitle": "50+ bps decrease", "groupItemThreshold": "0", "outcomePrices": '["0", "1"]',
          "active": True, "closed": True}]},
    {"slug": "fed-decision-in-december-x", "title": "Fed Decision in December?", "active": True, "closed": False,
     "volume": 2767832, "markets": []},
    {"slug": "how-many-dissent", "title": "How many dissent at the October Fed meeting?", "active": True,
     "closed": False, "volume": 99e6, "markets": []},
    {"slug": "fed-decision-in-september", "title": "Fed Decision in September?", "active": True, "closed": True,
     "volume": 5e8, "markets": []},
    {"slug": "fed-decision-in-january-x", "title": "Fed Decision in January?", "active": True, "closed": False,
     "volume": 278136, "markets": []},
    {"slug": "fed-decision-in-march-x", "title": "Fed Decision in March?", "active": True, "closed": False,
     "volume": 1000, "markets": []},
]}


def _selftest():
    global OPENER
    r = olah(FIXTURE, TOPIK["fomc"][1])
    assert [x["judul"] for x in r] == ["Fed Decision in October?", "Fed Decision in December?",
                                       "Fed Decision in January?"], r  # aktif, cocok pola, top 3 volume
    assert r[0]["url"] == "https://polymarket.com/event/fed-decision-in-october-x" and r[0]["volume"] == 28509686.5
    assert r[0]["outcomes"] == [{"label": "25 bps decrease", "peluang": 0.005}, {"label": "No change", "peluang": 0.835},
                                {"label": "25 bps increase", "peluang": 0.155}], r[0]["outcomes"]  # market tutup dibuang
    assert [nilai(x) for x in ("0.4%", "≤0.0%", "≥0.8%", "0.6%+", "<-25k", "-25k to 0", "25k to 50k", "100k+",
                               "4.1%", "No change", "25 bps increase", "50+ bps decrease", "lainnya")] == \
        [0.4, 0.0, 0.8, 0.6, -25, -12.5, 37.5, 100, 4.1, 0.0, 0.25, -0.5, None]

    def boom(req, timeout):
        raise OSError("jaringan putus")
    OPENER = boom
    assert cari("cpi") == []
    OPENER = lambda req, timeout: __import__("io").BytesIO(json.dumps(FIXTURE).encode())
    assert len(cari("fomc")) == 3 and len(cari("fed", maks=10)) == 5  # teks bebas: tanpa filter judul
    print("selftest OK")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        _selftest()
        sys.exit()
    if not a:
        sys.exit(__doc__)
    for ev in cari(" ".join(a)):
        print(f"\n{ev['judul']}  (volume ${ev['volume']:,.0f})\n  {ev['url']}")
        for o in ev["outcomes"]:
            print(f"  {o['label']:<20} {o['peluang'] * 100:5.1f}%")
