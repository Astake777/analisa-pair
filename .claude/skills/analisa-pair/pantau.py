"""Watcher sniper: tiap menit cari setup sniper baru, publikasi ke website, cetak satu baris per kabar.

Pakai:  python pantau.py [PAIR]            loop terus (dijalankan Claude lewat Monitor -> push ke HP)
        python pantau.py [PAIR] --sekali   satu putaran
        python pantau.py [PAIR] --uji      publikasi setup UJI dari harga sekarang, lalu kembalikan analisis semula
Baris keluaran (stdout = event): "SETUP ...", "SELESAI ...", "ERROR ...". Selain itu diam.
State: data/pantau_<PAIR>.json (id sinyal yang sudah dikabarkan dan yang masih berjalan).
Self-check: python pantau.py --selftest
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indikator import kolom, swings  # noqa: E402
from regime import STEP  # noqa: E402
from strategi import sniper  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
MODE = "scalp"
UJI_COBA = "Backtest 60 hari: 12 trade, 7 menang (58%). Sampel kecil, pakai lot kecil."
r2 = lambda x: round(x, 2)
pip = lambda d: round(abs(d) * 10)
fmt = lambda x: f"{x:,.2f}"


def sid(s):
    return f"{s['time']}-{s['side']}-{s['entry']}"


def tutup_semua(by, now):
    return {tf: [r for r in rows if r[0] + STEP[tf] <= now] for tf, rows in by.items()}


def nasib(s, m1, now):
    """'TP' / 'SL' / 'KEDALUWARSA' (limit tak terisi dalam EXPIRE_S) / None (masih berjalan)."""
    t, o, h, l, c = kolom(m1)
    buy = s["side"] == "buy"
    isi = None
    for k in range(len(t)):
        if t[k] < s["time"]:
            continue
        if isi is None:
            if t[k] >= s["time"] + sniper.EXPIRE_S:
                return "KEDALUWARSA"
            if (l[k] <= s["entry"]) if buy else (h[k] >= s["entry"]):
                isi = k
            else:
                continue
        if (l[k] <= s["sl"]) if buy else (h[k] >= s["sl"]):
            return "SL"
        if k > isi and ((h[k] >= s["tp"][0]) if buy else (l[k] <= s["tp"][0])):
            return "TP"
    if isi is None and now >= s["time"] + sniper.EXPIRE_S:
        return "KEDALUWARSA"
    return None


def tp2(s, h1):
    """Swing 1h berikutnya di balik TP1 (maks $30 dari entry), kalau ada."""
    t, o, h, l, c = kolom(h1)
    sh, sl = swings(h, l)
    if s["side"] == "sell":
        lv = sorted((p for _, p in sl if s["tp"][0] - 30 < p < s["tp"][0] - 2), reverse=True)
    else:
        lv = sorted(p for _, p in sh if s["tp"][0] + 2 < p < s["tp"][0] + 30)
    return r2(lv[0]) if lv else None


def kartu(s, h1):
    sell = s["side"] == "sell"
    risk = abs(s["entry"] - s["sl"])
    t2 = tp2(s, h1)
    tps = [s["tp"][0]] + ([t2] if t2 else [])
    lo, hi = s["poi"]
    return {
        "side": s["side"], "label": "sniper", "zone": s["zona"], "entry": s["entry"], "sl": s["sl"], "risk": r2(risk),
        "tp": tps, "rr": [r2(abs(t - s["entry"]) / risk) for t in tps],
        "alasan": {
            "entry": f"Zona {'supply' if sell else 'demand'} 15m {fmt(lo)}–{fmt(hi)} sudah menolak harga di 1m.",
            "sl": f"{pip(risk)} pips dari tepi zona, di {'atas' if sell else 'bawah'} ujung sweep.",
            "tp": f"TP1 {pip(tps[0] - s['entry'])} pips ({r2(abs(tps[0] - s['entry']) / risk)}R)"
                  + (f"; TP2 swing 1H {fmt(tps[1])}." if len(tps) > 1 else "."),
        },
        "langkah": [f"Pasang {s['side'].upper()} limit {fmt(s['entry'])}.", "Batal kalau 1 jam tidak terisi."],
        "batal": f"harga menyentuh {fmt(s['sl'])} sebelum entry terisi.",
        "eksperimen": UJI_COBA, "sinyalId": sid(s),
    }


def baris(s, k):
    tp = " / ".join(f"{fmt(t)} (+{pip(t - s['entry'])} pips)" for t in k["tp"])
    return (f"SETUP SNIPER {s['side'].upper()} limit {fmt(s['entry'])} | zona {fmt(k['zone'][0])}-{fmt(k['zone'][1])} | "
            f"SL {fmt(s['sl'])} (-{pip(s['entry'] - s['sl'])} pips) | TP {tp} | uji coba")


def _get(table, query):
    import publish
    url, key = publish._creds()
    req = urllib.request.Request(f"{url}/rest/v1/{table}?{query}", headers=publish._headers(key, "return=representation"))
    return json.load(urllib.request.urlopen(req, timeout=30))


def payload_terakhir(pair):
    rows = _get("analyses", urllib.parse.urlencode(
        {"select": "payload", "pair": f"eq.{pair}", "mode": f"eq.{MODE}", "order": "created_at.desc", "limit": 1}))
    return rows[0]["payload"] if rows else None


def gabung(old, k, price, now):
    """Payload analisis terakhir + setup sniper baru di depan (setup/zona sniper lama dibuang)."""
    p = dict(old or {"pair": "XAUUSD", "mode": MODE, "bias": {}, "levels": [], "notes": []})
    lama = {tuple(x.get("zone") or ()) for x in p.get("setups", []) if x.get("label") == "sniper"}
    p["setups"] = [k] + [x for x in p.get("setups", []) if x.get("label") != "sniper"]
    p["zones"] = [{"lo": k["zone"][0], "hi": k["zone"][1], "side": k["side"], "sumber": "sniper",
                   "label": f"Zona {k['side'].upper()} {fmt(k['zone'][0])}–{fmt(k['zone'][1])}"}] + \
        [z for z in p.get("zones", []) if z.get("sumber") != "sniper" and (z.get("lo"), z.get("hi")) not in lama]
    p["status"] = "SIAP"
    p["price"] = r2(price)
    p["updatedAt"] = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return p


def putaran(pair, state, now=None, publikasi=True):
    import data
    import publish
    by = data.load(pair, TFS, refresh=True)
    now = now or int(time.time())
    closed = tutup_semua(by, now)
    m1 = closed["1m"]
    for i, s in list(state["aktif"].items()):
        hasil = nasib(s, by["1m"], now)
        if hasil:
            print(f"SELESAI SNIPER {s['side'].upper()} {fmt(s['entry'])}: {hasil}", flush=True)
            del state["aktif"][i]
    sigs = [s for s in sniper.signals(closed, MODE) if s["time"] >= now - sniper.EXPIRE_S]
    for s in sigs:
        if sid(s) in state["seen"] or nasib(s, m1, now):
            continue
        state["seen"].append(sid(s))
        k = kartu(s, closed["1h"])
        if publikasi:
            publish.insert_analysis(pair, MODE, gabung(payload_terakhir(pair), k, m1[-1][4], now))
        state["aktif"][sid(s)] = s
        print(baris(s, k), flush=True)
    state["seen"] = state["seen"][-500:]


def _path(pair):
    return os.path.join(ROOT, "data", f"pantau_{pair}.json")


def baca(pair):
    try:
        return json.load(open(_path(pair), encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": [], "aktif": {}}


def simpan(pair, state):
    os.makedirs(os.path.dirname(_path(pair)), exist_ok=True)
    json.dump(state, open(_path(pair), "w", encoding="utf-8"))


def uji(pair):
    """Setup palsu dari harga sekarang, ditandai UJI, lalu analisis semula dipulihkan 2 menit kemudian."""
    import data
    import publish
    old = payload_terakhir(pair)
    price = data.load(pair, ["1m"], refresh=True)["1m"][-1][4]
    e = r2(price + 5)
    s = {"time": int(time.time()), "side": "sell", "entry": e, "sl": r2(e + 3.5), "tp": [r2(e - 10.5)],
         "zona": [e, r2(e + 2)], "poi": [e, r2(e + 4)]}
    k = {**kartu(s, data.load(pair, ["1h"])["1h"]), "eksperimen": "UJI NOTIFIKASI, abaikan. Hilang dalam 2 menit."}
    publish.insert_analysis(pair, MODE, gabung(old, k, price, int(time.time())))
    print("UJI " + baris(s, k), flush=True)
    time.sleep(120)
    if old:
        publish.insert_analysis(pair, MODE, {**old, "updatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    print("UJI selesai, analisis semula dipulihkan", flush=True)


def _selftest():
    s = {"time": 600, "side": "sell", "entry": 100.0, "sl": 103.5, "tp": [89.5]}
    m = lambda t, hi, lo: [t, (hi + lo) / 2, hi, lo, (hi + lo) / 2, 0]
    assert nasib(s, [m(600, 99, 98), m(660, 99.5, 98)], 720) is None              # belum terisi
    assert nasib(s, [m(600, 100.2, 99), m(660, 99, 89)], 720) == "TP"             # terisi lalu TP
    assert nasib(s, [m(600, 100.2, 99), m(660, 104, 99)], 720) == "SL"
    assert nasib(s, [m(600, 99, 98)], 600 + 3700) == "KEDALUWARSA"
    k = {"zone": [100.0, 102.0], "tp": [89.5]}
    assert baris(s, k) == ("SETUP SNIPER SELL limit 100.00 | zona 100.00-102.00 | SL 103.50 (-35 pips) | "
                           "TP 89.50 (+105 pips) | uji coba"), baris(s, k)
    g = gabung({"setups": [{"label": "sniper", "zone": [1, 2]}, {"label": "utama"}], "zones": [{"lo": 1, "hi": 2}],
                "status": "NO TRADE"}, {"side": "sell", "zone": [100.0, 102.0], "label": "sniper"}, 99.0, 0)
    assert [x["label"] for x in g["setups"]] == ["sniper", "utama"] and len(g["zones"]) == 1 and g["status"] == "SIAP"
    print("selftest OK")


def main(args):
    pair = next((a.upper() for a in args if not a.startswith("--")), "XAUUSD")
    if "--uji" in args:
        return uji(pair)
    state = baca(pair)
    while True:
        try:
            putaran(pair, state)
            simpan(pair, state)
        except Exception as e:  # satu putaran gagal (jaringan) tidak menghentikan watcher
            print(f"ERROR pantau: {type(e).__name__}: {e}", flush=True)
        if "--sekali" in args:
            return
        time.sleep(60 - time.time() % 60 + 5)  # 5 detik setelah candle 1m tutup


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(sys.argv[1:])
