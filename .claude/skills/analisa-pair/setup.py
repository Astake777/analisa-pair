"""Payload analisis engine untuk satu pair + mode (bentuk: supabase/payload.example.json).

Pakai:  python setup.py <PAIR> <scalp|intraday|swing> [out.json] [--source auto|binance|oanda|yahoo]
  Isi: bias per TF, level (EMA TF bias, pivot harian, swing, range Asia), regime, fase AMD,
  strategi pilihan pemilih.py dan sinyal terakhirnya (belum kedaluwarsa, belum kena SL/TP) -> entry.setup().
  Source auto = OANDA kalau ada kredensial, selain itu Binance XAUT yang digeser ke spot (field basis).
  Tanpa out.json: cetak ke stdout.
Self-check: python setup.py --selftest
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import EXPIRE  # noqa: E402
from entry import setup as hitung_setup  # noqa: E402
from indikator import adx, atr, ema, kolom, macd, rsi, swings, tutup  # noqa: E402
from pemilih import pilih, terbaru  # noqa: E402
from regime import MODES, STEP, TFS, regime_sekarang  # noqa: E402
from strategi import REGISTRY  # noqa: E402
from strategi.amd import fase_sekarang  # noqa: E402
import filter_kondisi  # noqa: E402

LABEL = {"1d": "1D", "4h": "4H", "1h": "1H", "30m": "30m", "15m": "15m", "5m": "5m"}
STATUS = [("AKTIF", "SETUP AKTIF"), ("SIAP", "SIAP"), ("TUNGGU PULLBACK", "TUNGGU PULLBACK")]
ZONA_ATR = 0.1     # lebar setengah zona dari entry sinyal tanpa FVG
GABUNG_ATR = 0.25  # level yang berjarak <= ini x ATR TF entry digabung
r2 = lambda x: round(x, 2)


def bias_tf(rows):
    """-> {bias, rsi, catatan} dari candle yang sudah tutup."""
    t, o, h, l, c = kolom(rows)
    e20, e50, e200 = (ema(c, n)[-1] for n in (20, 50, 200))
    if e50 is None:
        return {"bias": "Netral", "rsi": None, "catatan": "riwayat kurang"}
    x, a, hist, r = c[-1], adx(h, l, c)[0][-1], macd(c)[2][-1], rsi(c)[-1]
    emas = [e for e in (e20, e50, e200) if e is not None]
    naik, turun = x > e20 > e50 and (e200 is None or e50 > e200), x < e20 < e50 and (e200 is None or e50 < e200)
    kuat = a is not None and a >= 25
    if naik or turun:
        bias = ("Bullish" if naik else "Bearish") + (" kuat" if kuat else "")
    elif x > e50 and e20 > e50:
        bias = "Bullish"
    elif x < e50 and e20 < e50:
        bias = "Bearish"
    else:
        bias = "Netral"
    pos = "di atas semua EMA" if x > max(emas) else "di bawah semua EMA" if x < min(emas) else "di antara EMA"
    cat = f"ADX {a:.0f}, harga {pos}" if a is not None else f"harga {pos}"
    if hist is not None:
        cat += f", MACD {'bullish' if hist > 0 else 'bearish'}"
    return {"bias": bias, "rsi": r and round(r, 1), "catatan": cat}


def levels(closed, m, price, amd, atr_e):
    items = []
    for tf in m["bias"]:
        c = kolom(closed[tf])[4]
        for n in (20, 50, 200):
            v = ema(c, n)[-1]
            if v is not None:
                items.append((v, f"EMA{n} {LABEL[tf]}"))
    t, o, h, l, c = kolom(closed["1d"])
    if c:
        P = (h[-1] + l[-1] + c[-1]) / 3
        rg = h[-1] - l[-1]
        items += [(P, "pivot harian"), (2 * P - l[-1], "R1 harian"), (2 * P - h[-1], "S1 harian"),
                  (P + rg, "R2 harian"), (P - rg, "S2 harian")]
    tf = m["bias"][0]
    t, o, h, l, c = kolom(closed[tf])
    sh, sl = swings(h, l)
    up = [p for i, p in sh if p > price and max(h[i + 1:], default=0) <= p]   # swing high yang belum tembus
    dn = [p for i, p in sl if p < price and min(l[i + 1:], default=1e18) >= p]
    items += [(p, f"swing high {LABEL[tf]}") for p in sorted(up)[:2]]
    items += [(p, f"swing low {LABEL[tf]}") for p in sorted(dn, reverse=True)[:2]]
    if amd.get("rangeAsia"):
        items += [(amd["rangeAsia"]["hi"], "range Asia atas"), (amd["rangeAsia"]["lo"], "range Asia bawah")]
    groups = []
    for v, lab in sorted(items):
        if groups and v - groups[-1][-1][0] <= GABUNG_ATR * atr_e:
            groups[-1].append((v, lab))
        else:
            groups.append([(v, lab)])
    out = []
    for g in reversed(groups):
        v = sum(x for x, _ in g) / len(g)
        out.append({"price": r2(v), "label": ", ".join(lab for _, lab in g),
                    "kind": "resistance" if v > price else "support"})
    return out


def sinyal_aktif(sigs, rows, step, now, expire_s):
    """Sinyal terbaru yang belum kedaluwarsa dan belum menyentuh SL/TP pertamanya."""
    t, o, h, l, c = kolom(rows)
    for s in reversed(sigs):
        if s["time"] < now - expire_s:
            return None
        buy = s["side"] == "buy"
        after = [k for k in range(len(t)) if t[k] >= s["time"] and t[k] + step <= now]
        kena_sl = any((l[k] <= s["sl"]) if buy else (h[k] >= s["sl"]) for k in after)
        kena_tp = any((h[k] >= s["tp"][0]) if buy else (l[k] <= s["tp"][0]) for k in after)
        if not (kena_sl or kena_tp):
            return s
    return None


def jadi_setup(side, price, zone, atr_e, targets, label, m, extra=None):
    s = hitung_setup(side, price, zone, atr_e, targets)
    arah = "di atas" if side == "sell" else "di bawah"
    s.update(label=label, trigger=f"candle penolakan {m['entry']} di zona, konfirmasi {m['trigger']}",
             batal=f"close {m['entry']} {arah} SL {s['sl']}", **(extra or {}))
    return s


def bangun(by_tf, pair, mode, now, price, events=(), rows_bt=(), basis=None, sumber=""):
    m = MODES[mode]
    closed = {tf: r[:tutup([x[0] for x in r], STEP[tf], now) + 1] for tf, r in by_tf.items()}
    reg = regime_sekarang(closed, mode, now, events)
    pick = pilih(reg, list(rows_bt))
    entry_tfs = list(dict.fromkeys([m["entry"], m["trigger"]]))
    bias = {LABEL[tf]: bias_tf(closed[tf]) for tf in dict.fromkeys(m["bias"] + entry_tfs)}
    e = closed[m["entry"]]
    atr_e = atr(*kolom(e)[2:5])[-1]
    amd = fase_sekarang(closed, now)
    lv = levels(closed, m, price, amd, atr_e)
    notes = [f"Sumber candle {sumber}." if sumber else "", f"ATR {m['entry']} {atr_e:.2f}."]
    setups = []
    sig, kondisi = None, None
    if pick["terpilih"] != "NO TRADE":
        sigs = REGISTRY[pick["terpilih"]].signals(closed, mode)
        sig = sinyal_aktif(sigs, closed[m["trigger"]], STEP[m["trigger"]], now, EXPIRE * STEP[m["entry"]])
        if sig:
            ctx, lolos = filter_kondisi.Konteks(closed, mode, events).baca(sig["time"], sig["side"])
            aktif = filter_kondisi.terpakai(pick["terpilih"], mode)
            gagal = [f for f in aktif if not lolos[f]]
            kondisi = {**ctx, "filter": aktif, "lolos": lolos, "gagal": gagal}
            if gagal:
                notes.append(f"Sinyal {pick['terpilih']} {sig['side']} ditolak filter kondisi pasar: {', '.join(gagal)}.")
                sig = None
        if sig:
            zone = sig.get("zona") or [sig["entry"] - ZONA_ATR * atr_e, sig["entry"] + ZONA_ATR * atr_e]
            jauh = [x["price"] for x in lv if (x["price"] < sig["entry"]) == (sig["side"] == "sell")]
            setups.append(jadi_setup(sig["side"], price, zone, atr_e, sig["tp"] + jauh, "utama", m, {
                "strategi": pick["terpilih"], "alasan": sig["alasan"], "slStrategi": r2(sig["sl"]),
                "tpStrategi": [r2(x) for x in sig["tp"]]}))
            notes.append("SL setup memakai aturan entry.py (tepi zona +/- 0.5 ATR); SL/TP strategi yang "
                         "dibacktest ada di slStrategi/tpStrategi.")
        else:
            notes.append(f"Belum ada sinyal {pick['terpilih']} yang masih berlaku.")
    if pick["izinKontra"]:
        votes = sum(1 if b["bias"].startswith("Bullish") else -1 if b["bias"].startswith("Bearish") else 0
                    for b in bias.values())
        main = (1 if sig["side"] == "buy" else -1) if sig else (1 if votes > 0 else -1 if votes < 0 else 0)
        if main:
            side = "sell" if main > 0 else "buy"
            near = [x["price"] for x in lv if (x["price"] > price) == (side == "sell")]
            if near:
                p = min(near, key=lambda v: abs(v - price))
                tgt = [x["price"] for x in lv if (x["price"] < p) == (side == "sell")]
                setups.append(jadi_setup(side, price, [p - 0.15 * atr_e, p + 0.15 * atr_e], atr_e, tgt,
                                         "kontra-tren", m))
    zones = []
    for s in setups:
        kind = f"zona-{s['side']}"
        zones.append({"lo": s["zone"][0], "hi": s["zone"][1], "side": s["side"],
                      "label": f"Zona {s['side']} {s['label']}" + (f" · {s['strategi']}" if s.get("strategi") else "")})
        lv += [{"price": s["zone"][1], "label": f"Zona {s['side']} atas", "kind": kind},
               {"price": s["zone"][0], "label": f"Zona {s['side']} bawah", "kind": kind}]
    lv.sort(key=lambda x: -x["price"])
    main = setups[0] if setups and setups[0]["label"] == "utama" else None
    if reg["jendelaNews"]:
        status = "TUNGGU NEWS"
    elif not main:
        status = "NO TRADE"
    else:
        status = next((v for k, v in STATUS if main["status"].startswith(k)), "NO TRADE")
    searah = main and sum(b["bias"].startswith("Bullish" if main["side"] == "buy" else "Bearish")
                          for b in bias.values()) * 3 >= 2 * len(bias)
    keyakinan = "sedang" if searah and status != "NO TRADE" else "rendah"
    notes.append("Keyakinan dari engine maksimal 'sedang'; makro dan berita belum dinilai.")
    out = {"updatedAt": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "pair": pair, "mode": mode, "gaya": mode, "price": r2(price),
           "timeframes": {"bias": m["bias"], "entry": entry_tfs}, "status": status, "keyakinan": keyakinan,
           "bias": bias, "levels": lv, "zones": zones, "setups": setups,
           "strategi": {**{k: pick[k] for k in ("regime", "terpilih", "alasan", "kandidat", "izinKontra", "runId")},
                        "kondisi": kondisi},
           "amd": amd, "notes": [n for n in notes if n]}
    if basis is not None:
        out["basis"] = {"sumber": "XAUT Binance dikoreksi ke spot gold-api", "nilai": basis}
    return out


def _events():
    """Detik UTC event USD impor tinggi 2 jam ke belakang s/d 1 hari ke depan."""
    import kalender
    raw = kalender.fetch(2, 1)
    return [int(dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
            for e in raw if e.get("importance") == 1]


def main(pair, mode, out=None, source="auto"):
    import data
    pair = pair.upper()
    raw = data.load(pair, TFS, source=source)
    price = raw["5m"][-1][4]
    try:
        events = _events()
    except Exception as e:  # kalender gagal tidak menggagalkan setup
        print(f"kalender gagal: {e}", file=sys.stderr)
        events = []
    p = bangun(data.bersih(raw), pair, mode, int(time.time()), price, events, terbaru(pair, mode),
               data.load.basis, data.load.sym)
    body = json.dumps(p, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"{out}: status {p['status']}, strategi {p['strategi']['terpilih']}, {len(p['setups'])} setup")
    else:
        print(body)


def _selftest():
    import math
    from strategi import contoh, potong
    full = contoh(lambda i: 1000 + 0.05 * i + 2 * math.sin(i / 8))
    sig = REGISTRY["tren_pullback"].signals(full, "scalp")[-5]
    now = sig["time"]
    by = potong(full, now)
    rows_bt = [{"run_id": "r", "strategy": "tren_pullback", "regime": "semua", "sample": "oos", "trades": 20,
                "winrate": 0.5, "expectancy": 0.3}]
    price = by["5m"][-1][4]
    p = bangun(by, "XAUUSD", "scalp", now, price, [], rows_bt, -5.0, "sintetis")
    ex = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "supabase",
                                     "payload.example.json"), encoding="utf-8"))
    for k in ("mode", "timeframes", "status", "keyakinan", "bias", "levels", "zones", "setups", "strategi", "amd",
              "notes", "basis"):
        assert k in p, k
    for k in ("timeframes", "strategi", "amd", "basis"):
        assert set(ex[k]) <= set(p[k]), (k, set(ex[k]) - set(p[k]))
    assert p["strategi"]["terpilih"] == "tren_pullback" and p["basis"]["nilai"] == -5.0, p["strategi"]
    s = p["setups"][0]
    assert s["label"] == "utama" and s["side"] == "buy" and s["slStrategi"] == round(sig["sl"], 2), s
    assert set(ex["setups"][0]) <= set(s), set(ex["setups"][0]) - set(s)
    assert all(x["kind"] in ("resistance", "support", "zona-sell", "zona-buy") for x in p["levels"])
    assert [x["price"] for x in p["levels"]] == sorted((x["price"] for x in p["levels"]), reverse=True)
    assert set(p["bias"]) == {"1H", "30m", "5m"} and set(ex["bias"]["4H"]) == set(p["bias"]["1H"])
    assert set(ex["amd"]) == set(p["amd"]) and p["zones"][0]["side"] == "buy", p["amd"]
    q = bangun(by, "XAUUSD", "scalp", now, price, [now + 600], rows_bt)
    assert q["status"] == "TUNGGU NEWS" and q["strategi"]["regime"]["jendelaNews"] and "basis" not in q
    assert bangun(by, "XAUUSD", "scalp", now, price, [], [])["status"] == "NO TRADE"
    print("selftest OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        sys.exit()
    src = args[args.index("--source") + 1] if "--source" in args else "auto"
    args = [a for a in args if a not in ("--source", src)]
    main(args[0], args[1], args[2] if len(args) > 2 else None, src)
