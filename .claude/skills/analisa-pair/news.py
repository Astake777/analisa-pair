"""Skor kejutan news -> arah USD -> arah pair.

Pakai:  python news.py '<json>'
  json: {"pair":"XAUUSD",
         "data":{"nfp":{"actual":150,"forecast":90},
                 "unemployment":{"actual":4.1,"forecast":4.1},
                 "ahe_mm":{"expected":0.4,"forecast":0.3}}}
  - "actual" = sesudah rilis. "expected" = perkiraan sebelum rilis (hasilnya LEAN, bukan kepastian).
  - Komponen tanpa actual/expected dilewati.
Self-check: python news.py --selftest
"""
import json
import sys

# komponen: (arah, ambang kejutan bermakna, bobot)
# arah +1 = angka lebih tinggi dari forecast -> USD hawkish; -1 = lebih tinggi -> USD dovish
SPEC = {
    "nfp": (+1, 50, 1.0),             # ribu
    "adp": (+1, 50, 0.5),             # ribu
    "unemployment": (-1, 0.1, 1.0),   # %
    "ahe_mm": (+1, 0.1, 1.0),         # %
    "claims": (-1, 15, 1.0),          # ribu
    "jolts": (+1, 0.3, 0.5),          # juta
    "cpi_mm": (+1, 0.1, 0.5),
    "cpi_yy": (+1, 0.1, 0.5),
    "core_cpi_mm": (+1, 0.1, 1.0),
    "core_cpi_yy": (+1, 0.1, 0.5),
    "ppi_mm": (+1, 0.1, 0.5),
    "core_ppi_mm": (+1, 0.1, 1.0),
    "core_pce_mm": (+1, 0.1, 1.0),
    "retail_sales_mm": (+1, 0.3, 1.0),
    "ism_mfg": (+1, 1.5, 0.75),
    "ism_services": (+1, 1.5, 0.75),
    "gdp_qq": (+1, 0.5, 1.0),
}
# arah pair saat USD hawkish (dovish = kebalikannya)
PAIR_ON_HAWKISH = {"XAUUSD": "SELL", "XAGUSD": "SELL", "EURUSD": "SELL", "GBPUSD": "SELL",
                   "AUDUSD": "SELL", "BTCUSD": "SELL", "US100": "SELL", "US500": "SELL",
                   "USDJPY": "BUY", "USDCHF": "BUY", "USDCAD": "BUY", "DXY": "BUY"}
HAWK = 0.75     # skor >= ini = USD hawkish
STRONG = 1.5


def score(pair, data):
    parts, total, mode = [], 0.0, None
    for name, v in data.items():
        sign, thr, w = SPEC[name]
        val = v.get("actual", v.get("expected"))
        if val is None:
            continue
        mode = mode or ("actual" if "actual" in v else "expected")
        z = max(-2.0, min(2.0, sign * (val - v["forecast"]) / thr))
        total += w * z
        parts.append({"komponen": name, "z": round(z, 2), "kontribusi": round(w * z, 2)})
    total = round(total, 2)
    if total >= HAWK:
        usd = "HAWKISH"
    elif total <= -HAWK:
        usd = "DOVISH"
    else:
        usd = "CAMPUR/SESUAI FORECAST"
    hawk_dir = PAIR_ON_HAWKISH[pair.upper()]
    flip = {"BUY": "SELL", "SELL": "BUY"}
    direction = {"HAWKISH": hawk_dir, "DOVISH": flip[hawk_dir]}.get(usd, "TIDAK ADA ARAH (tunggu reaksi)")
    strength = "kuat" if abs(total) >= STRONG else "sedang" if abs(total) >= HAWK else "lemah"
    label = "HASIL" if mode == "actual" else "LEAN (perkiraan, belum rilis)"
    return {"pair": pair.upper(), "jenis": label, "skor": total, "usd": usd,
            "arah_pair": direction, "kekuatan": strength, "rincian": parts}


def _selftest():
    # CPI Agustus 2026 (rilis 11 Sep): core 0.3 vs 0.2, sisanya sesuai -> hawkish -> XAU SELL
    r = score("XAUUSD", {"cpi_mm": {"actual": 0.4, "forecast": 0.4},
                         "cpi_yy": {"actual": 3.4, "forecast": 3.4},
                         "core_cpi_mm": {"actual": 0.3, "forecast": 0.2},
                         "core_cpi_yy": {"actual": 2.4, "forecast": 2.4}})
    assert r["skor"] == 1.0 and r["usd"] == "HAWKISH" and r["arah_pair"] == "SELL", r
    # PPI Agustus 2026 (rilis 10 Sep): core 0.2 vs 0.3 -> dovish -> skor bilang XAU BUY.
    # Faktanya emas TURUN karena US10Y tetap naik. Kasus inilah alasan wajib konfirmasi yield.
    r = score("XAUUSD", {"ppi_mm": {"actual": 0.4, "forecast": 0.4},
                         "core_ppi_mm": {"actual": 0.2, "forecast": 0.3}})
    assert r["usd"] == "DOVISH" and r["arah_pair"] == "BUY", r
    # NFP: payrolls jauh di atas (z dibatasi +2), upah di bawah -> tetap hawkish
    r = score("XAUUSD", {"nfp": {"actual": 250, "forecast": 90},
                         "unemployment": {"actual": 4.1, "forecast": 4.1},
                         "ahe_mm": {"actual": 0.2, "forecast": 0.3}})
    assert r["skor"] == 1.0 and r["arah_pair"] == "SELL", r
    # unemployment naik = dovish; USDJPY kebalikan emas
    r = score("USDJPY", {"unemployment": {"actual": 4.3, "forecast": 4.1}})
    assert r["usd"] == "DOVISH" and r["arah_pair"] == "SELL" and r["kekuatan"] == "kuat", r
    # sesuai forecast -> tidak ada arah
    r = score("XAUUSD", {"nfp": {"actual": 95, "forecast": 90}})
    assert r["arah_pair"].startswith("TIDAK ADA"), r
    # mode lean
    r = score("XAUUSD", {"nfp": {"expected": 40, "forecast": 90}})
    assert r["jenis"].startswith("LEAN") and r["arah_pair"] == "BUY", r
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        print(json.dumps(score(**json.loads(sys.argv[1])), indent=2, ensure_ascii=False))
