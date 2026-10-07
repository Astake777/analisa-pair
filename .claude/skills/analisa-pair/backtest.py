"""Backtest bar-per-bar semua strategi untuk satu mode, walk-forward 40 hari in-sample + 20 hari out-of-sample.

Pakai:  python backtest.py <PAIR> <scalp|intraday|swing> [--source binance|yahoo|oanda] [--strategi NAMA]
  Simulasi di TF trigger mode (5m/5m/15m), atau di TF `SIM` milik strategi (sniper: 1m). Limit terisi kalau harga menyentuh entry dalam 12 candle TF entry,
  batal kalau target tersentuh duluan. SL dan TP di candle yang sama = SL. Candle pengisian hanya dicek SL.
  Biaya 0.30 spread + 0.10 slippage per trade, dikurangkan dalam R. Satu posisi/order per strategi.
  avg_r = rata-rata R kotor, expectancy = rata-rata R bersih biaya, winrate dari R bersih > 0.
Keluaran: data/backtest/<PAIR>_<mode>_<run_id>.json (baris tabel backtest_results)
          dan <PAIR>_<mode>_<run_id>_trades.json (log trade).
Self-check: python backtest.py --selftest
"""
import datetime as dt
import json
import os
import sys
from bisect import bisect_left

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indikator import kolom, tutup  # noqa: E402
from regime import MODES, STEP, TFS, tren_seri  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "backtest"))
COST = 0.30 + 0.10
EXPIRE = 12        # candle TF entry
IS_DAYS, OOS_DAYS = 40, 20
GRID = {}          # nama strategi -> [params, ...] untuk sweep nanti; kosong = PARAMS bawaan


def simulasi(rows, sigs, step, expire, cost=COST):
    """-> list trade. rows = candle TF simulasi, expire dalam candle TF simulasi."""
    t, o, h, l, c = kolom(rows)
    out, bebas = [], 0
    for s in sorted(sigs, key=lambda s: s["time"]):
        i = bisect_left(t, s["time"])
        if s["time"] < bebas or i >= len(t):
            continue
        buy = s["side"] == "buy"
        e, sl, tp = s["entry"], s["sl"], s["tp"][0]
        fill, worst = None, e
        for k in range(i, min(i + expire, len(t))):
            if (l[k] <= e) if buy else (h[k] >= e):
                fill = k
                break
            if (h[k] >= tp) if buy else (l[k] <= tp):
                break
        if fill is None:
            bebas = t[k] + step
            continue
        for k in range(fill, len(t)):
            worst = min(worst, l[k]) if buy else max(worst, h[k])
            if (l[k] <= sl) if buy else (h[k] >= sl):
                px, hasil = sl, "SL"
                break
            if k > fill and ((h[k] >= tp) if buy else (l[k] <= tp)):
                px, hasil = tp, "TP"
                break
        else:
            px, hasil = c[-1], "akhir data"
        risk = abs(e - sl)
        r = (px - e) / risk * (1 if buy else -1)
        out.append({"sinyal": s["time"], "masuk": t[fill], "keluar": t[k], "side": s["side"], "entry": e,
                    "sl": sl, "tp": tp, "exit": px, "hasil": hasil, "r": r, "r_net": r - cost / risk,
                    "floating": round(min(abs(worst - e), risk), 2),
                    "alasan": s.get("alasan", "")})
        bebas = t[k] + step
    return out


def metrik(trades):
    n = len(trades)
    if not n:
        return {"trades": 0, "winrate": None, "avg_r": None, "expectancy": None, "max_dd_r": None,
                "profit_factor": None}
    rs = [x["r_net"] for x in trades]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak, dd = max(peak, eq), max(dd, peak - eq)
    win, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    r3 = lambda x: round(x, 3)
    return {"trades": n, "winrate": r3(sum(r > 0 for r in rs) / n), "avg_r": r3(sum(x["r"] for x in trades) / n),
            "expectancy": r3(sum(rs) / n), "max_dd_r": r3(dd), "profit_factor": r3(win / loss) if loss else None}


def ringkas(trades, base):
    """Baris backtest_results per sample x regime (+ 'semua')."""
    rows = []
    for sample in ("in", "oos"):
        tr = [x for x in trades if x["sample"] == sample]
        for reg in ["semua"] + sorted({x["regime"] for x in tr}):
            sub = tr if reg == "semua" else [x for x in tr if x["regime"] == reg]
            rows.append({**base, "regime": reg, "sample": sample, **metrik(sub)})
    return rows


def run(by_tf, pair, mode, run_id, registry=None):
    from strategi import REGISTRY
    m = MODES[mode]
    sim, step = by_tf[m["trigger"]], STEP[m["trigger"]]
    end = sim[-1][0] + step
    oos0, is0 = end - OOS_DAYS * 86400, end - (IS_DAYS + OOS_DAYS) * 86400
    main, mstep = by_tf[m["bias"][0]], STEP[m["bias"][0]]
    reg, mt = tren_seri(main), [r[0] for r in main]
    expire = EXPIRE * STEP[m["entry"]] // step
    rows, log = [], []
    for name, mod in (registry or REGISTRY).items():
        for params in GRID.get(name, [mod.PARAMS]):
            sigs = [s for s in mod.signals(by_tf, mode, params) if s["time"] >= is0]
            stf = getattr(mod, "SIM", m["trigger"])  # jendela IS/OOS tetap dari TF trigger
            exp = getattr(mod, "EXPIRE_S", expire * step) // STEP[stf]
            tr = simulasi(by_tf[stf], sigs, STEP[stf], exp)
            for x in tr:
                x.update(strategy=name, regime=reg[tutup(mt, mstep, x["masuk"])],
                         sample="oos" if x["masuk"] >= oos0 else "in")
            log += tr
            rows += ringkas(tr, {"run_id": run_id, "pair": pair, "mode": mode, "strategy": name, "params": params})
    return rows, log


def tabel(rows):
    f = lambda v, d=2: "-" if v is None else f"{v:.{d}f}"
    out = [f"{'strategi':<14}{'regime':<13}{'sample':<7}{'trades':>6}{'win%':>7}{'avgR':>7}{'exp':>7}{'maxDD':>7}{'PF':>6}"]
    for r in rows:
        out.append(f"{r['strategy']:<14}{r['regime']:<13}{r['sample']:<7}{r['trades']:>6}"
                   f"{f(r['winrate'] and r['winrate'] * 100, 0):>7}{f(r['avg_r']):>7}{f(r['expectancy']):>7}"
                   f"{f(r['max_dd_r'], 1):>7}{f(r['profit_factor']):>6}")
    return "\n".join(out)


def _selftest():
    rows = [[i * 300, 100, 101, 99, 100, 0] for i in range(5)]
    rows += [[1500, 100, 100.5, 97, 98, 0],    # buy limit 98 terisi
             [1800, 98, 104, 97.5, 103, 0],    # TP 104 kena
             [2100, 103, 106, 90, 95, 0]]      # SL dan TP satu candle (tidak terpakai lagi)
    sig = {"time": 1500, "side": "buy", "entry": 98, "sl": 96, "tp": [104]}
    tr = simulasi(rows, [sig], 300, 12)
    assert len(tr) == 1 and tr[0]["hasil"] == "TP" and tr[0]["masuk"] == 1500 and tr[0]["keluar"] == 1800, tr
    assert tr[0]["r"] == 3 and abs(tr[0]["r_net"] - (3 - 0.4 / 2)) < 1e-9, tr
    # SL dan TP di candle yang sama -> SL
    both = rows[:6] + [[1800, 98, 105, 95, 100, 0]]
    tr = simulasi(both, [sig], 300, 12)
    assert tr[0]["hasil"] == "SL" and tr[0]["r"] == -1, tr
    # candle pengisian menyentuh TP tapi tidak SL -> belum keluar
    tr = simulasi(rows[:5] + [[1500, 100, 104.5, 97, 98, 0], [1800, 98, 99, 95, 96, 0]], [sig], 300, 12)
    assert tr[0]["hasil"] == "SL", tr
    # kedaluwarsa: tidak pernah menyentuh entry dalam 2 candle; sinyal kedua di jendela itu diabaikan
    sell = {"time": 0, "side": "sell", "entry": 110, "sl": 112, "tp": [100]}
    assert simulasi(rows, [sell, {**sell, "time": 300}], 300, 2) == []
    # target tersentuh sebelum entry -> batal
    assert simulasi(rows, [{"time": 0, "side": "sell", "entry": 100.8, "sl": 103, "tp": [99]}], 300, 12)[0]["masuk"] == 0
    assert simulasi(rows, [{"time": 0, "side": "sell", "entry": 101.5, "sl": 103, "tp": [99]}], 300, 12) == []
    # posisi terbuka sampai data habis: ditutup di close terakhir
    tr = simulasi(rows[:5], [{"time": 0, "side": "buy", "entry": 100, "sl": 90, "tp": [120]}], 300, 12)
    assert tr[0]["hasil"] == "akhir data" and tr[0]["r"] == 0, tr
    m = metrik([{"r": 2, "r_net": 1.8}, {"r": -1, "r_net": -1.2}, {"r": -1, "r_net": -1.2}, {"r": 2, "r_net": 1.8}])
    assert m == {"trades": 4, "winrate": 0.5, "avg_r": 0.5, "expectancy": 0.3, "max_dd_r": 2.4,
                 "profit_factor": 1.5}, m
    tr = [{"sample": "in", "regime": "range", "r": 1, "r_net": 1}, {"sample": "oos", "regime": "trend-naik",
                                                                    "r": -1, "r_net": -1}]
    rr = ringkas(tr, {"strategy": "x"})
    assert [(r["sample"], r["regime"], r["trades"]) for r in rr] == \
        [("in", "semua", 1), ("in", "range", 1), ("oos", "semua", 1), ("oos", "trend-naik", 1)], rr
    print("selftest OK")


def main(pair, mode, source="binance", only=None):
    import data
    from strategi import EKSTRA, REGISTRY
    pair = pair.upper()
    reg = {k: v for k, v in {**REGISTRY, **EKSTRA}.items() if k == only} if only else REGISTRY
    tfs = TFS + sorted({mod.SIM for mod in reg.values() if hasattr(mod, "SIM")} - set(TFS))
    by = data.bersih(data.load(pair, tfs, source=source, spot=False))
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M}-{pair}-{mode}"
    rows, log = run(by, pair, mode, run_id, reg)
    out = os.path.join(OUT, only) if only else OUT  # run satu strategi tidak boleh jadi run terbaru pemilih
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{pair}_{mode}_{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    with open(path.replace(".json", "_trades.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    sim = by[MODES[mode]["trigger"]]
    print(f"{pair} {mode} | sumber {data.load.sym} | simulasi {MODES[mode]['trigger']} "
          f"{iso(sim[-1][0] - (IS_DAYS + OOS_DAYS) * 86400)} -> {iso(sim[-1][0])} UTC | oos mulai "
          f"{iso(sim[-1][0] - OOS_DAYS * 86400)}")
    print(tabel(rows))
    for name in reg:
        menang = sorted(x["floating"] for x in log if x["strategy"] == name and x["r_net"] > 0)
        if menang:
            print(f"{name}: floating trade menang median ${menang[len(menang) // 2]:.2f}, terburuk ${menang[-1]:.2f}")
    print(f"\n{path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    main(args[0], args[1], opt("--source", "binance"), opt("--strategi"))
