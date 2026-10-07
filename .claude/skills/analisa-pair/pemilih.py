"""Pilih strategi untuk regime sekarang dari hasil backtest terbaru.

Aturan: expectancy OOS tertinggi di regime tren sekarang dengan >= MIN_TRADES trade OOS dan expectancy > 0;
kalau tidak ada, aturan yang sama di regime 'semua'; kalau tetap tidak ada: NO TRADE.
Setup kontra-tren hanya diizinkan saat regime tren = range dan ada strategi terpilih.
Pakai:  python pemilih.py <PAIR> <scalp|intraday|swing>
Self-check: python pemilih.py --selftest
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import OUT  # noqa: E402
from strategi import HANYA_INDIKATOR  # noqa: E402

MIN_TRADES = 15


def terbaru(pair, mode):
    """Baris backtest_results dari run terbaru (pair, mode); [] kalau belum ada."""
    files = [f for f in glob.glob(os.path.join(OUT, f"{pair.upper()}_{mode}_*.json")) if not f.endswith("_trades.json")]
    return json.load(open(max(files), encoding="utf-8")) if files else []


def pilih(regime, rows, min_trades=MIN_TRADES):
    oos = [r for r in rows if r["sample"] == "oos" and r["strategy"] not in HANYA_INDIKATOR]
    kandidat = [{"strategy": r["strategy"], "regime": r["regime"], "trades": r["trades"], "winrate": r["winrate"],
                 "expectancy": r["expectancy"], "sample": "oos"}
                for r in oos if r["regime"] in (regime["tren"], "semua")]
    base = {"regime": regime, "kandidat": kandidat, "runId": rows[0]["run_id"] if rows else None}
    for reg in (regime["tren"], "semua"):
        ok = [r for r in oos if r["regime"] == reg and r["trades"] >= min_trades and (r["expectancy"] or 0) > 0]
        if ok:
            b = max(ok, key=lambda r: r["expectancy"])
            return {**base, "terpilih": b["strategy"], "izinKontra": regime["tren"] == "range",
                    "alasan": f"expectancy OOS tertinggi di regime {reg}: {b['expectancy']:+.2f}R dari {b['trades']} "
                              f"trade (winrate {b['winrate'] * 100:.0f}%)"}
    why = "belum ada hasil backtest" if not rows else \
        f"tidak ada strategi dengan >= {min_trades} trade OOS dan expectancy > 0 di regime {regime['tren']} atau semua"
    return {**base, "terpilih": "NO TRADE", "izinKontra": False, "alasan": why}


def _selftest():
    row = lambda s, reg, n, e, sample="oos": {"run_id": "r1", "strategy": s, "regime": reg, "sample": sample,
                                              "trades": n, "winrate": 0.5, "expectancy": e}
    rows = [row("a", "trend-naik", 20, 0.2), row("b", "trend-naik", 30, 0.4), row("c", "trend-naik", 10, 0.9),
            row("a", "semua", 40, 0.1), row("b", "semua", 50, -0.1), row("c", "range", 20, 0.3),
            row("d", "trend-naik", 99, 2.0, "in")]
    reg = {"tren": "trend-naik", "volatilitas": "normal", "sesi": "London", "jendelaNews": False}
    p = pilih(reg, rows)
    assert p["terpilih"] == "b" and not p["izinKontra"] and p["regime"] == reg and "+0.40R" in p["alasan"], p
    assert {k["strategy"] for k in p["kandidat"]} == {"a", "b", "c"} and p["runId"] == "r1", p
    p = pilih({**reg, "tren": "trend-turun"}, rows)                       # jatuh ke 'semua'
    assert p["terpilih"] == "a" and "semua" in p["alasan"], p
    p = pilih({**reg, "tren": "range"}, rows)
    assert p["terpilih"] == "c" and p["izinKontra"], p
    assert pilih(reg, [row("a", "semua", 40, -0.1)])["terpilih"] == "NO TRADE"
    assert pilih(reg, [])["alasan"] == "belum ada hasil backtest"
    assert pilih(reg, [row("amd", "semua", 40, 0.9)])["terpilih"] == "NO TRADE"   # AMD hanya indikator fase
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    import data
    from regime import TFS, regime_sekarang
    by = data.bersih(data.load(args[0], TFS))
    print(json.dumps(pilih(regime_sekarang(by, args[1]), terbaru(args[0], args[1])), indent=2, ensure_ascii=False))
