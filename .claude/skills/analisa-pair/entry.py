"""Hitung setup entry: SL, TP, RR, dan status dari zona konfluensi.

Pakai:  python entry.py '<json>'
  json: {"side":"sell"|"buy", "price":4163, "zone":[4223,4228], "atr":42,
         "targets":[4131,4088,4032], "ratio":10.97 (opsional)}
  - zone/targets/atr dalam satuan yang sama; kalau "ratio" ada, semuanya
    dikali ratio dulu (konversi level proxy, mis. GLD -> XAU). price selalu harga asli.
  - atr = ATR timeframe setup (4H untuk intraday/swing, 15m untuk scalp).
Self-check: python entry.py --selftest
"""
import json
import sys

MIN_RR1 = 1.5   # TP1 minimal 1.5R
MIN_RR2 = 2.5   # TP2 minimal 2.5R
SL_PAD = 0.5    # SL = tepi zona +/- 0.5 x ATR
CHASE = 1.0     # harga > 1 ATR lewat zona ke arah trade = jangan kejar


def setup(side, price, zone, atr, targets, ratio=None):
    if ratio:
        zone = [z * ratio for z in zone]
        targets = [t * ratio for t in targets]
        atr = atr * ratio
    lo, hi = min(zone), max(zone)
    entry = (lo + hi) / 2
    if side == "sell":
        sl = hi + SL_PAD * atr
        tps = sorted((t for t in targets if t < entry), reverse=True)
        if price > sl:
            status = "INVALID: zona sudah tembus ke atas"
        elif price < lo - CHASE * atr:
            status = "TUNGGU PULLBACK ke zona"
        elif lo <= price <= hi:
            status = "AKTIF: harga di zona, tunggu trigger"
        else:
            status = "SIAP: pasang di zona"
    elif side == "buy":
        sl = lo - SL_PAD * atr
        tps = sorted(t for t in targets if t > entry)
        if price < sl:
            status = "INVALID: zona sudah tembus ke bawah"
        elif price > hi + CHASE * atr:
            status = "TUNGGU PULLBACK ke zona"
        elif lo <= price <= hi:
            status = "AKTIF: harga di zona, tunggu trigger"
        else:
            status = "SIAP: pasang di zona"
    else:
        raise ValueError("side harus 'buy' atau 'sell'")

    risk = abs(entry - sl)
    rr = [round(abs(tp - entry) / risk, 2) for tp in tps[:2]]
    if not rr or rr[0] < MIN_RR1:
        status = f"NO TRADE: RR TP1 {rr[0] if rr else 0} < {MIN_RR1}"
    elif len(rr) > 1 and rr[1] < MIN_RR2:
        status += f" (TP2 RR {rr[1]} < {MIN_RR2}, pakai TP1 saja)"
    r = lambda x: round(x, 2)
    return {"side": side, "entry": r(entry), "zone": [r(lo), r(hi)], "sl": r(sl),
            "risk": r(risk), "tp": [r(t) for t in tps[:2]], "rr": rr, "status": status}


def _selftest():
    # sell dari zona 4223-4228, ATR 42: entry 4225.5, SL 4249, risk 23.5
    s = setup("sell", 4163, [4223, 4228], 42, [4131, 4088, 4032])
    assert s["entry"] == 4225.5 and s["sl"] == 4249 and s["risk"] == 23.5, s
    assert s["tp"] == [4131, 4088] and s["rr"] == [4.02, 5.85], s
    assert s["status"].startswith("TUNGGU PULLBACK"), s      # harga 62 di bawah zona > 1 ATR
    assert setup("sell", 4226, [4223, 4228], 42, [4131])["status"].startswith("AKTIF")
    assert setup("sell", 4260, [4223, 4228], 42, [4131])["status"].startswith("INVALID")
    assert setup("sell", 4200, [4223, 4228], 42, [4210])["status"].startswith("NO TRADE")
    # buy mirror
    b = setup("buy", 4135, [4126, 4131], 42, [4166, 4228])
    assert b["sl"] == 4105 and b["status"].startswith("SIAP"), b
    # konversi proxy GLD -> XAU
    p = setup("sell", 4163, [385, 385.5], 3.84, [376.3], ratio=10.97)
    assert p["zone"] == [4223.45, 4228.94], p
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        print(json.dumps(setup(**json.loads(sys.argv[1])), indent=2))
