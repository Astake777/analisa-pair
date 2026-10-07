"""Validasi strategi sniper sebelum setupnya boleh dikabarkan sebagai SETUP VALID.

Pakai:  python validasi.py <strategi> [scalp]        contoh: python validasi.py alchemist_crt
Alur:
  1. Semua kombinasi GRID modul disimulasikan sekali di TF `SIM` modul (1m) atas seluruh data Binance.
     Limit terisi hanya kalau harga tembus entry >= TEMBUS; batal kalau harga sudah 70% ke TP1 tanpa entry;
     SL dan TP di candle yang sama = SL.
  2. Walk-forward bergulir: parameter dipilih di in-sample IS_HARI (expectancy tertinggi, >= MIN_IS trade),
     dinilai di OOS_HARI berikutnya, jendela digeser OOS_HARI. Trade OOS semua lipatan digabung.
  3. Lulus kalau semua KRITERIA terpenuhi, termasuk biaya 2x dan parameter tetangga (satu langkah grid
     dari kombinasi terbaik seluruh sampel) yang juga positif.
Keluaran: ringkasan di layar + data/backtest/validasi/<strategi>_<waktu>.json
Self-check: python validasi.py --selftest
"""
import datetime as dt
import importlib
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import COST, metrik, simulasi  # noqa: E402
from regime import STEP, sesi  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "backtest", "validasi")
IS_HARI, OOS_HARI, MIN_LIPAT_HARI = 90, 30, 15
MIN_IS = 8
TEMBUS = 0.10
BATAL_FRAC = 0.7   # order batal kalau harga sudah 70% jalan ke TP1 tanpa entry (aturan yang sama di pantau.py)
KRITERIA = {"trade_oos": 30, "expectancy": 0.15, "profit_factor": 1.3, "porsi_lipatan_untung": 2 / 3, "max_dd_r": 8.0}


def kombinasi(mod):
    keys = list(getattr(mod, "GRID", {}))
    return [dict(zip(keys, v)) for v in itertools.product(*(mod.GRID[k] for k in keys))] or [{}]


def lipatan(t0, t1):
    """[(awal IS, awal OOS, akhir OOS)]; lipatan terakhir boleh pendek (>= MIN_LIPAT_HARI)."""
    out, a = [], t0
    while a + (IS_HARI + MIN_LIPAT_HARI) * 86400 <= t1:
        b = a + IS_HARI * 86400
        out.append((a, b, min(b + OOS_HARI * 86400, t1)))
        a += OOS_HARI * 86400
    return out


def pilih(hasil, a, b):
    """Index kombinasi dengan expectancy in-sample tertinggi di [a, b), atau None."""
    best, idx = None, None
    for i, (_, tr) in enumerate(hasil):
        m = metrik([x for x in tr if a <= x["masuk"] < b])
        if m["trades"] >= MIN_IS and (best is None or m["expectancy"] > best):
            best, idx = m["expectancy"], i
    return idx


def biaya2(tr):
    return [{**x, "r_net": x["r"] - 2 * COST / abs(x["entry"] - x["sl"])} for x in tr]


def tetangga(grid, kombi):
    """Kombinasi yang beda satu nilai, bergeser satu langkah di daftar GRID."""
    out = []
    for k, vals in grid.items():
        i = vals.index(kombi[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                out.append({**kombi, k: vals[j]})
    return out


def nilai(hasil, grid, folds):
    oos, info = [], []
    for a, b, c in folds:
        i = pilih(hasil, a, b)
        if i is None:
            info.append({"oos_mulai": b, "params": None, "oos": metrik([])})
            continue
        part = [x for x in hasil[i][1] if b <= x["masuk"] < c]
        oos += part
        info.append({"oos_mulai": b, "params": hasil[i][0], "oos": metrik(part)})
    full = [(k, metrik(tr)) for k, tr in hasil]
    layak = [(k, m) for k, m in full if m["trades"] >= MIN_IS]
    best = max(layak, key=lambda x: x[1]["expectancy"])[0] if layak else None
    peta = {json.dumps(k, sort_keys=True): m for k, m in full}
    teta = [peta[json.dumps(n, sort_keys=True)] for n in tetangga(grid, best)] if best and grid else []
    m, m2 = metrik(oos), metrik(biaya2(oos))
    dinilai = [f for f in info if f["oos"]["trades"]]
    porsi = sum(f["oos"]["expectancy"] > 0 for f in dinilai) / len(dinilai) if dinilai else 0
    cek = {
        f"trade OOS >= {KRITERIA['trade_oos']}": m["trades"] >= KRITERIA["trade_oos"],
        f"expectancy >= +{KRITERIA['expectancy']}R": (m["expectancy"] or -9) >= KRITERIA["expectancy"],
        f"profit factor >= {KRITERIA['profit_factor']}": (m["profit_factor"] or 0) >= KRITERIA["profit_factor"],
        "tetap untung dengan biaya 2x": (m2["expectancy"] or -9) > 0,
        "lipatan OOS untung >= 2/3": porsi >= KRITERIA["porsi_lipatan_untung"],
        f"max drawdown <= {KRITERIA['max_dd_r']}R": m["max_dd_r"] is not None and m["max_dd_r"] <= KRITERIA["max_dd_r"],
        "parameter tetangga juga positif": bool(teta) and all((x["expectancy"] or -9) > 0 for x in teta),
    }
    return {"oos": m, "oos_biaya2x": m2, "lipatan": info, "terbaik_seluruh": best, "tetangga": teta,
            "cek": cek, "valid": all(cek.values()), "trades": oos}


def rincian(tr):
    bulan, ses, hari = {}, {}, {"Senin": [], "lain": []}
    for x in tr:
        bulan.setdefault(dt.datetime.fromtimestamp(x["masuk"], dt.timezone.utc).strftime("%Y-%m"), []).append(x)
        ses.setdefault(sesi(x["masuk"]), []).append(x)
        hari["Senin" if (x["masuk"] // 86400 + 3) % 7 == 0 else "lain"].append(x)
    f = lambda d: {k: metrik(v) for k, v in sorted(d.items())}
    return {"bulan": f(bulan), "sesi": f(ses), "hari": f(hari)}


def validasi(by, nama, mode="scalp"):
    mod = importlib.import_module(f"strategi.{nama}")
    stf = getattr(mod, "SIM", "5m")
    exp = getattr(mod, "EXPIRE_S", 3600) // STEP[stf]
    hasil = []
    for k in kombinasi(mod):
        sig = mod.signals(by, mode, {**mod.PARAMS, **k})
        hasil.append((k, simulasi(by[stf], sig, STEP[stf], exp, tembus=TEMBUS, batal_frac=BATAL_FRAC)))
        print(f"  {k}: {len(sig)} sinyal, {len(hasil[-1][1])} terisi", flush=True)
    t0, t1 = by[stf][0][0], by[stf][-1][0]
    rep = nilai(hasil, getattr(mod, "GRID", {}), lipatan(t0, t1))
    rep["rincian"] = rincian(rep["trades"])
    rep["floating_menang"] = sorted(x["floating"] for x in rep["trades"] if x["r_net"] > 0)
    rep.update(strategi=nama, mode=mode, data=[t0, t1])
    return rep


def cetak(rep):
    f = lambda m: "-" if not m["trades"] else (f"{m['trades']} trade, menang {m['winrate'] * 100:.0f}%, "
                                               f"exp {m['expectancy']:+.2f}R, PF {m['profit_factor']}, DD {m['max_dd_r']}R")
    d = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %b")
    print(f"\n== {rep['strategi']} ({d(rep['data'][0])} - {d(rep['data'][1])}) ==")
    for x in rep["lipatan"]:
        print(f"  OOS mulai {d(x['oos_mulai'])}: params {x['params']} -> {f(x['oos'])}")
    print(f"  OOS gabungan : {f(rep['oos'])}")
    print(f"  biaya 2x     : {f(rep['oos_biaya2x'])}")
    fl = rep["floating_menang"]
    if fl:
        print(f"  floating trade menang: median ${fl[len(fl) // 2]:.2f}, terburuk ${fl[-1]:.2f}")
    print(f"  terbaik seluruh sampel: {rep['terbaik_seluruh']}; tetangga exp: "
          f"{[m['expectancy'] for m in rep['tetangga']]}")
    for k, v in rep["rincian"].items():
        print(f"  per {k}: " + "; ".join(f"{a} {m['trades']}tr {m['expectancy']:+.2f}R" for a, m in v.items() if m["trades"]))
    for k, ok in rep["cek"].items():
        print(f"  [{'LULUS' if ok else 'GAGAL'}] {k}")
    print(f"  => {'SETUP VALID' if rep['valid'] else 'UJI COBA (belum lulus)'}")


def _selftest():
    D = 86400
    f = lipatan(0, 200 * D)
    assert f[0] == (0, 90 * D, 120 * D) and f[-1][2] == 200 * D and len(f) == 4, f
    tr = lambda t, r: {"masuk": t, "r": r, "r_net": r - 0.1, "entry": 100, "sl": 96.5}
    good = [tr(d * D, 3 if d % 2 else -1) for d in range(0, 200, 3)]
    bad = [tr(d * D, -1) for d in range(0, 200, 3)]
    hasil = [({"x": 1}, bad), ({"x": 2}, good), ({"x": 3}, good)]
    rep = nilai(hasil, {"x": [1, 2, 3]}, f)
    assert all(x["params"] == {"x": 2} for x in rep["lipatan"]), rep["lipatan"]
    assert rep["terbaik_seluruh"] == {"x": 2} and len(rep["tetangga"]) == 2
    assert not rep["cek"]["parameter tetangga juga positif"], "tetangga x=1 rugi"
    assert tetangga({"a": [1, 2], "b": [5, 6, 7]}, {"a": 1, "b": 6}) == [{"a": 2, "b": 6}, {"a": 1, "b": 5}, {"a": 1, "b": 7}]
    print("selftest OK")


def main(nama, mode="scalp"):
    import data
    from regime import TFS
    by = data.bersih(data.load("XAUUSD", TFS + ["1m"], source="binance", spot=False))
    rep = validasi(by, nama, mode)
    cetak(rep)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{nama}_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M}.json")
    json.dump({k: v for k, v in rep.items() if k != "trades"}, open(path, "w", encoding="utf-8"), indent=1)
    json.dump(rep["trades"], open(path.replace(".json", "_trades.json"), "w", encoding="utf-8"))
    print(path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(*sys.argv[1:])
