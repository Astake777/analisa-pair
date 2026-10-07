"""Filter kondisi pasar untuk sinyal strategi: hanya filter yang terbukti membantu di data OOS yang dipakai.

Filter (True = sinyal boleh lewat):
  tren_searah  regime tren TF bias utama searah sinyal (bukan range, bukan berlawanan)
  bukan_sepi   sesi bukan 'sepi' (akhir NY sampai Asia buka)
  vol_normal   volatilitas ATR TF entry tidak 'rendah'
  tidak_jenuh  RSI 1H tidak > 70 untuk buy / < 30 untuk sell
  amd_searah   tidak melawan arah sapuan AMD hari itu (sapu atas -> hanya sell, sapu bawah -> hanya buy)
  jauh_news    tidak dalam 60 menit dari news USD high impact
Pemilihan: walk-forward lipatan validasi.py; di tiap lipatan dipilih kombinasi filter dengan expectancy
in-sample tertinggi (>= MIN_IS trade), dinilai di OOS. Kombinasi yang paling sering terpilih dan untung di OOS
disimpan ke data/backtest/filter_kondisi_<strategi>_<mode>.json dan dipakai setup.py.
Catatan: trade disimulasikan sekali tanpa filter lalu disaring; slot posisi yang kosong karena filter tidak
diisi ulang oleh sinyal lain (perkiraan sedikit konservatif).
Pakai:  python filter_kondisi.py <strategi> <mode>      contoh: python filter_kondisi.py tren_pullback scalp
Self-check: python filter_kondisi.py --selftest
"""
import importlib
import itertools
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import EXPIRE, metrik, simulasi  # noqa: E402
from indikator import kolom, rsi, tutup  # noqa: E402
from regime import MODES, STEP, jendela_news, sesi, tren_seri, vol_seri  # noqa: E402
from validasi import MIN_IS, lipatan  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "backtest")
NAMA = ["tren_searah", "bukan_sepi", "vol_normal", "tidak_jenuh", "amd_searah", "jauh_news"]


class Konteks:
    """Hitung seri regime sekali, lalu baca konteks per waktu T (hanya candle yang sudah tutup)."""

    def __init__(self, by_tf, mode, news=()):
        m = MODES[mode]
        self.by, self.news = by_tf, list(news)
        b, e = by_tf[m["bias"][0]], by_tf[m["entry"]]
        self.tb, self.sb, self.tren = [r[0] for r in b], STEP[m["bias"][0]], tren_seri(b)
        self.te, self.se, self.vol = [r[0] for r in e], STEP[m["entry"]], vol_seri(e)
        h1 = by_tf["1h"]
        self.th, self.rsi = [r[0] for r in h1], rsi(kolom(h1)[4])
        self._amd = None

    def amd(self, T):
        from strategi import amd
        if "5m" not in self.by:
            return None
        if self._amd is None:
            self._amd = amd._prep(self.by, amd.PARAMS)
        sw = amd._hari(self._amd, amd._hari_wib(T), T, amd.PARAMS)["sweep"]
        return sw["sisi"] if sw else None

    def baca(self, T, side):
        i, j, k = tutup(self.tb, self.sb, T), tutup(self.te, self.se, T), tutup(self.th, 3600, T)
        r = self.rsi[k] if k >= 0 else None
        sw = self.amd(T)
        buy = side == "buy"
        ctx = {"tren": self.tren[i] if i >= 0 else "range", "volatilitas": self.vol[j] if j >= 0 else "normal",
               "sesi": sesi(T), "rsi1h": None if r is None else round(r, 1), "sweepAmd": sw,
               "dekatNews": jendela_news(T, self.news)}
        lolos = {
            "tren_searah": ctx["tren"] == ("trend-naik" if buy else "trend-turun"),
            "bukan_sepi": ctx["sesi"] != "sepi",
            "vol_normal": ctx["volatilitas"] != "rendah",
            "tidak_jenuh": r is None or (r <= 70 if buy else r >= 30),
            "amd_searah": sw is None or (sw == "bawah") == buy,
            "jauh_news": not ctx["dekatNews"],
        }
        return ctx, lolos


def saring(trades, aktif):
    return [x for x in trades if all(x["lolos"][f] for f in aktif)]


def pilih_filter(trades, folds):
    """-> (laporan per lipatan, trade OOS gabungan, kombinasi paling sering terpilih)."""
    semua = [c for n in range(len(NAMA) + 1) for c in itertools.combinations(NAMA, n)]
    info, oos = [], []
    for a, b, c in folds:
        best = None
        for kombi in semua:
            m = metrik(saring([x for x in trades if a <= x["masuk"] < b], kombi))
            if m["trades"] >= MIN_IS and (best is None or m["expectancy"] > best[1]):
                best = (kombi, m["expectancy"])
        if not best:
            info.append({"oos_mulai": b, "filter": None, "oos": metrik([])})
            continue
        part = saring([x for x in trades if b <= x["masuk"] < c], best[0])
        oos += part
        info.append({"oos_mulai": b, "filter": list(best[0]), "oos": metrik(part)})
    hitung = Counter(tuple(f["filter"]) for f in info if f["filter"] is not None)
    return info, oos, list(hitung.most_common(1)[0][0]) if hitung else []


def atribusi(by, nama, mode, news):
    mod = importlib.import_module(f"strategi.{nama}")
    m = MODES[mode]
    stf = getattr(mod, "SIM", m["trigger"])
    exp = getattr(mod, "EXPIRE_S", EXPIRE * STEP[m["entry"]]) // STEP[stf]
    k = Konteks(by, mode, news)
    sig = mod.signals(by, mode)
    for s in sig:
        s["ctx"], s["lolos"] = k.baca(s["time"], s["side"])
    tr = simulasi(by[stf], sig, STEP[stf], exp)
    lol = {s["time"]: s["lolos"] for s in sig}
    for x in tr:
        x["lolos"] = lol[x["sinyal"]]
    folds = lipatan(by[stf][0][0], by[stf][-1][0])
    info, oos, kombi = pilih_filter(tr, folds)
    tunggal = {f: metrik(saring(tr, [f])) for f in NAMA}
    return {"strategi": nama, "mode": mode, "tanpaFilter": metrik(tr), "perFilter": tunggal,
            "lipatan": info, "oos": metrik(oos), "oosTanpaFilter": metrik([x for x in tr if x["masuk"] >= folds[0][1]] if folds else []),
            "dipakai": kombi}


def terpakai(nama, mode):
    """Kombinasi filter tersimpan untuk strategi+mode, [] kalau belum ada atau OOS-nya tidak lebih baik."""
    try:
        rep = json.load(open(os.path.join(OUT, f"filter_kondisi_{nama}_{mode}.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    a, b = rep["oos"]["expectancy"], rep["oosTanpaFilter"]["expectancy"]
    return rep["dipakai"] if a is not None and (b is None or a > b) else []


def cetak(rep):
    f = lambda m: "-" if not m["trades"] else f"{m['trades']} tr, menang {m['winrate'] * 100:.0f}%, {m['expectancy']:+.2f}R"
    print(f"\n== filter kondisi {rep['strategi']} {rep['mode']} ==")
    print(f"  tanpa filter (semua data): {f(rep['tanpaFilter'])}")
    for k, m in rep["perFilter"].items():
        print(f"  hanya {k:<12}: {f(m)}")
    for x in rep["lipatan"]:
        print(f"  lipatan OOS {x['oos_mulai']}: filter {x['filter']} -> {f(x['oos'])}")
    print(f"  OOS tanpa filter: {f(rep['oosTanpaFilter'])}")
    print(f"  OOS dengan filter terpilih per lipatan: {f(rep['oos'])}")
    print(f"  dipakai live: {rep['dipakai'] or 'tidak ada'}")


def _selftest():
    D = 86400
    tr = []
    for d in range(0, 200, 2):
        bagus = d % 4 == 0
        tr.append({"masuk": d * D, "r": 2 if bagus else -1, "r_net": 1.9 if bagus else -1.1,
                   "lolos": {**{f: True for f in NAMA}, "bukan_sepi": bagus}})
    info, oos, kombi = pilih_filter(tr, lipatan(0, 200 * D))
    assert "bukan_sepi" in kombi and all(x["r_net"] > 0 for x in oos), (kombi, info)
    assert saring(tr, []) == tr and len(saring(tr, ["bukan_sepi"])) == 50
    print("selftest OK")


def main(nama, mode):
    import data
    import kalender
    from regime import TFS
    tfs = TFS + ["1m"] if nama in ("sniper", "alchemist_crt", "alchemist_london") else TFS
    by = data.bersih(data.load("XAUUSD", tfs, source="binance", spot=False))
    try:
        news = [kalender.dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp()
                for e in kalender.fetch(24 * 220, 1) if e.get("importance") == 1]
    except Exception as e:  # kalender gagal: filter news menganggap tidak ada news
        print(f"kalender gagal: {e}", file=sys.stderr)
        news = []
    rep = atribusi(by, nama, mode, news)
    cetak(rep)
    path = os.path.join(OUT, f"filter_kondisi_{nama}_{mode}.json")
    json.dump(rep, open(path, "w", encoding="utf-8"), indent=1)
    print(path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(*sys.argv[1:3])
