"""Watcher sniper: tiap menit cari setup baru dari semua strategi sniper, lacak sampai selesai, perbarui website.

Pakai:  python pantau.py [PAIR]            loop terus (dijalankan Claude lewat Monitor -> push ke HP)
        python pantau.py [PAIR] --sekali   satu putaran
        python pantau.py [PAIR] --uji      publikasi setup UJI dari harga sekarang, lalu kembalikan analisis semula
Baris keluaran (stdout = event): SETUP, TERISI, SELESAI, BATAL, ERROR (sekali per jenis), PULIH.
Aturan setup:
  - batal kalau belum terisi dan harga sudah BATAL_FRAC (70%) jalan ke TP1, atau limit lewat EXPIRE_S strategi;
  - setup searah yang zonanya berdekatan (< DEKAT dollar) hanya disimpan satu: lulus validasi dulu, lalu skor,
    lalu yang paling dekat harga; buy dan sell bersamaan -> hanya yang searah EMA20/50 1H;
  - setiap strategi diberi label SETUP VALID atau uji coba dari laporan validasi.py terbaru; strategi yang
    expectancy OOS-nya <= 0 atau belum divalidasi tidak dikabarkan.
State: data/pantau_<PAIR>.json. Self-check: python pantau.py --selftest
"""
import datetime as dt
import glob
import importlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indikator import kolom, swings  # noqa: E402
from regime import STEP  # noqa: E402
from validasi import BATAL_FRAC  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
STRATEGI = ["sniper", "alchemist_london", "alchemist_crt"]
TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
HARI_DATA = 60
MODE = "scalp"
DEKAT = 3.0
NAMA = {"sniper": "Sniper 1m", "alchemist_london": "Alchemist London", "alchemist_crt": "Alchemist CRT"}
r2 = lambda x: round(x, 2)
pip = lambda d: round(abs(d) * 10)
fmt = lambda x: f"{x:,.2f}"


def sid(nama, s):
    return f"{nama}:{s['time']}-{s['side']}-{s['entry']}"


def lacak(s, m1, now, expire_s):
    """-> (terisi, hasil). hasil: None selama berjalan, atau 'TP' / 'SL' / 'BATAL: ...'."""
    t, o, h, l, c = kolom(m1)
    buy = s["side"] == "buy"
    batal = s["entry"] + (s["tp"][0] - s["entry"]) * BATAL_FRAC
    isi = None
    for k in range(len(t)):
        if t[k] < s["time"]:
            continue
        if isi is None:
            if t[k] >= s["time"] + expire_s:
                return False, "BATAL: limit tidak terisi dalam batas waktu"
            if (l[k] <= s["entry"]) if buy else (h[k] >= s["entry"]):
                isi = k
            elif (h[k] >= batal) if buy else (l[k] <= batal):
                return False, "BATAL: harga sudah dekat target tanpa entry"
            else:
                continue
        if (l[k] <= s["sl"]) if buy else (h[k] >= s["sl"]):
            return True, "SL"
        if k > isi and ((h[k] >= s["tp"][0]) if buy else (l[k] <= s["tp"][0])):
            return True, "TP"
    if isi is None and now >= s["time"] + expire_s:
        return False, "BATAL: limit tidak terisi dalam batas waktu"
    return isi is not None, None


def info_validasi(nama, sumber=""):
    """Laporan validasi.py terbaru (sumber "" = Binance, "mt5" = data broker) -> {valid, layak, teks}."""
    label = f"{nama}-{sumber}" if sumber else nama
    files = sorted(f for f in glob.glob(os.path.join(ROOT, "data", "backtest", "validasi", f"{label}_*.json"))
                   if not f.endswith("_trades.json"))
    if not files:
        return {"valid": False, "layak": False, "teks": "Belum divalidasi."}
    rep = json.load(open(files[-1], encoding="utf-8"))
    m = rep["oos"]
    if not m["trades"]:
        return {"valid": False, "layak": False, "teks": "Validasi belum punya trade OOS."}
    angka = f"OOS {m['trades']} trade, menang {m['winrate'] * 100:.0f}%, {m['expectancy']:+.2f}R per trade"
    if rep["valid"]:
        return {"valid": True, "layak": True, "teks": f"Lulus validasi: {angka}."}
    return {"valid": False, "layak": m["expectancy"] > 0, "teks": f"Belum lulus validasi ({angka}). Pakai lot kecil."}


def tp2(s, h1):
    """Swing 1H berikutnya di balik TP1 (maks $30 dari entry), kalau ada."""
    t, o, h, l, c = kolom(h1)
    sh, sl = swings(h, l)
    if s["side"] == "sell":
        lv = sorted((p for _, p in sl if s["tp"][0] - 30 < p < s["tp"][0] - 2), reverse=True)
    else:
        lv = sorted(p for _, p in sh if s["tp"][0] + 2 < p < s["tp"][0] + 30)
    return r2(lv[0]) if lv else None


def kartu(nama, s, h1, info):
    risk = abs(s["entry"] - s["sl"])
    t2 = tp2(s, h1)
    tps = [s["tp"][0]] + ([t2] if t2 else [])
    return {
        "side": s["side"], "label": nama, "zone": s["zona"], "entry": s["entry"], "sl": s["sl"], "risk": r2(risk),
        "tp": tps, "rr": [r2(abs(t - s["entry"]) / risk) for t in tps],
        "alasan": {
            "entry": s.get("alasan", "").split(", SL")[0] + ".",
            "sl": f"{pip(risk)} pips dari tepi pertama zona.",
            "tp": f"TP1 {pip(tps[0] - s['entry'])} pips ({r2(abs(tps[0] - s['entry']) / risk)}R)"
                  + (f"; TP2 swing 1H {fmt(tps[1])}." if len(tps) > 1 else "."),
        },
        "langkah": [f"Pasang {s['side'].upper()} limit {fmt(s['entry'])}.",
                    "Batal otomatis kalau harga sudah 70% ke TP1 tanpa entry atau limit kedaluwarsa."],
        "batal": f"harga menyentuh {fmt(s['sl'])} setelah entry (SL).",
        "valid": info["valid"], "eksperimen": None if info["valid"] else info["teks"],
        "catatanValidasi": info["teks"], "sinyalId": None, "skor": s.get("skor", 0),
    }


def peringkat(k, price):
    return (k["valid"], k.get("skor", 0), -abs(k["entry"] - price))


def saring(kandidat, aktif, price, bias):
    """Pilih setup yang tampil. kandidat: [(id, kartu, _)], aktif: [(id, kartu, terisi)] -> (baru, id aktif dibuang).
    Urutan: yang sudah terisi, lulus validasi, skor, paling dekat harga. Setup belum terisi dibuang kalau melawan
    bias 1H atau zonanya berdekatan dengan setup searah yang peringkatnya lebih tinggi."""
    semua = [(i, k, terisi, False) for i, k, terisi in aktif] + [(i, k, False, True) for i, k, _ in kandidat]
    semua.sort(key=lambda x: (x[2], *peringkat(x[1], price)), reverse=True)
    simpan, baru, buang = [], [], set()
    for i, k, terisi, is_baru in semua:
        lawan = bias and (1 if k["side"] == "buy" else -1) != bias
        bentrok = any(x["side"] == k["side"] and abs(x["entry"] - k["entry"]) < DEKAT for x in simpan)
        if not terisi and (lawan or bentrok):
            if not is_baru:
                buang.add(i)
            continue
        simpan.append(k)
        if is_baru:
            baru.append((i, k))
    return baru, buang


def _get(table, query):
    import publish
    url, key = publish._creds()
    req = urllib.request.Request(f"{url}/rest/v1/{table}?{query}", headers=publish._headers(key, "return=representation"))
    return json.load(urllib.request.urlopen(req, timeout=30))


def payload_terakhir(pair):
    rows = _get("analyses", urllib.parse.urlencode(
        {"select": "payload", "pair": f"eq.{pair}", "mode": f"eq.{MODE}", "order": "created_at.desc", "limit": 1}))
    return rows[0]["payload"] if rows else None


def susun(old, kartu_aktif, price, now):
    """Payload terakhir dengan setup/zona milik watcher diganti kartu_aktif (setup lain tetap)."""
    p = dict(old or {"pair": "XAUUSD", "mode": MODE, "bias": {}, "levels": [], "notes": [], "status": "NO TRADE"})
    milik = set(STRATEGI)
    lama = {tuple(x.get("zone") or ()) for x in p.get("setups", []) if x.get("label") in milik}
    p["setups"] = kartu_aktif + [x for x in p.get("setups", []) if x.get("label") not in milik]
    p["zones"] = [{"lo": k["zone"][0], "hi": k["zone"][1], "side": k["side"], "sumber": "pantau",
                   "label": f"Zona {k['side'].upper()} {fmt(k['zone'][0])}–{fmt(k['zone'][1])}"} for k in kartu_aktif] + \
        [z for z in p.get("zones", []) if z.get("sumber") not in ("pantau", "sniper") and (z.get("lo"), z.get("hi")) not in lama]
    if kartu_aktif:
        p["status"] = "SETUP AKTIF" if any(k.get("terisi") for k in kartu_aktif) else "SIAP"
    elif p.get("status") in ("SIAP", "SETUP AKTIF") or not p["setups"]:
        p["status"] = "NO TRADE"
    p["price"] = r2(price)
    p["updatedAt"] = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return p


def baris(jenis, k):
    tp = " / ".join(f"{fmt(t)} (+{pip(t - k['entry'])} pips)" for t in k["tp"])
    return (f"{jenis} {NAMA.get(k['label'], k['label'])} {k['side'].upper()} limit {fmt(k['entry'])} | zona "
            f"{fmt(k['zone'][0])}-{fmt(k['zone'][1])} | SL {fmt(k['sl'])} (-{pip(k['entry'] - k['sl'])} pips) | TP {tp}")


def log_row(pair, i, a, hasil=None):
    s, k = a["sinyal"], a["kartu"]
    r = None
    if hasil == "TP":
        r = round(abs(s["tp"][0] - s["entry"]) / abs(s["entry"] - s["sl"]), 2)
    elif hasil == "SL":
        r = -1
    elif hasil:
        r = 0
    return {"id": i, "pair": pair, "strategi": k["label"], "side": s["side"], "entry": s["entry"], "sl": s["sl"],
            "tp": k["tp"], "zona": k["zone"], "valid": k["valid"], "terisi": bool(a.get("terisi")),
            "dibuat": dt.datetime.fromtimestamp(s["time"], dt.timezone.utc).isoformat(), "hasil": hasil, "r": r,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def catat(rows):
    """Rekam jejak ke setup_log; gagal (mis. tabel belum dibuat) tidak menghentikan watcher."""
    import publish
    if not rows:
        return
    try:
        publish.upsert_setup_log(rows)
    except Exception as e:
        print(f"ERROR setup_log: {e}"[:300], flush=True)


def putaran(pair, state, now=None, publikasi=True):
    import data
    import publish
    from strategi.sniper import arah_bias
    now = now or int(time.time())
    by = data.load(pair, TFS, refresh=True)
    by = {tf: [r for r in rows if r[0] >= now - HARI_DATA * 86400] for tf, rows in by.items()}
    closed = {tf: [r for r in rows if r[0] + STEP[tf] <= now] for tf, rows in by.items()}
    m1, price = by["1m"], by["1m"][-1][4]
    berubah, log = False, []

    for i, a in list(state["aktif"].items()):
        terisi, hasil = lacak(a["sinyal"], m1, now, a["expire_s"])
        k = a["kartu"]
        if terisi and not a.get("terisi"):
            a["terisi"] = k["terisi"] = True
            print(baris("TERISI", k), flush=True)
            berubah = True
            log.append(log_row(pair, i, a))
        if hasil:
            jenis = "BATAL" if hasil.startswith("BATAL") else "SELESAI"
            print(f"{jenis} {NAMA.get(k['label'], k['label'])} {k['side'].upper()} {fmt(k['entry'])}: {hasil}", flush=True)
            log.append(log_row(pair, i, a, hasil))
            del state["aktif"][i]
            berubah = True

    kandidat = []
    for nama in STRATEGI:
        mod = importlib.import_module(f"strategi.{nama}")
        exp_s = getattr(mod, "EXPIRE_S", 3600)
        info = info_validasi(nama)
        if not info["layak"]:
            continue  # strategi yang OOS-nya rugi atau belum teruji tidak dikabarkan sama sekali
        for s in mod.signals(closed, MODE):
            i = sid(nama, s)
            if s["time"] < now - exp_s or i in state["seen"]:
                continue
            terisi, hasil = lacak(s, m1, now, exp_s)
            if terisi or hasil:
                state["seen"].append(i)  # terlambat: sudah terisi atau sudah batal sebelum terlihat
                continue
            kandidat.append((i, {**kartu(nama, s, closed["1h"], info), "sinyalId": i}, (s, exp_s)))

    bias = arah_bias(closed, ["1h"])(now)
    aktif = [(i, a["kartu"], a.get("terisi", False)) for i, a in state["aktif"].items()]
    baru, buang = saring([(i, k, None) for i, k, _ in kandidat], aktif, price, bias)
    for i in buang:
        a = state["aktif"].pop(i)
        print(f"BATAL {NAMA.get(a['kartu']['label'])} {a['kartu']['side'].upper()} {fmt(a['kartu']['entry'])}: "
              f"diganti setup yang lebih sesuai kondisi market", flush=True)
        berubah = True
        log.append(log_row(pair, i, a, "BATAL: diganti setup yang lebih sesuai"))
    asal = {i: x for i, _, x in kandidat}
    for i, k in baru:
        s, exp_s = asal[i]
        state["aktif"][i] = {"sinyal": s, "kartu": k, "expire_s": exp_s}
        print(baris("SETUP VALID" if k["valid"] else "SETUP UJI COBA", k), flush=True)
        berubah = True
        log.append(log_row(pair, i, state["aktif"][i]))
    state["seen"] = (state["seen"] + [i for i, _, _ in kandidat])[-1000:]

    if publikasi:
        catat(log)
    if berubah and publikasi:
        kart = sorted((a["kartu"] for a in state["aktif"].values()), key=lambda k: peringkat(k, price), reverse=True)
        publish.insert_analysis(pair, MODE, susun(payload_terakhir(pair), kart, price, now))


def _path(pair):
    return os.path.join(ROOT, "data", f"pantau_{pair}.json")


def baca(pair):
    try:
        st = json.load(open(_path(pair), encoding="utf-8"))
        return st if "aktif" in st and all("kartu" in a for a in st["aktif"].values()) else {"seen": st.get("seen", []), "aktif": {}}
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
    by = data.load(pair, ["1m", "1h"], refresh=True)
    price = by["1m"][-1][4]
    e = r2(price + 5)
    s = {"time": int(time.time()), "side": "sell", "entry": e, "sl": r2(e + 3.5), "tp": [r2(e - 10.5)],
         "zona": [e, r2(e + 2)], "alasan": "UJI notifikasi"}
    k = {**kartu("sniper", s, by["1h"], {"valid": False, "layak": True, "teks": "UJI NOTIFIKASI, abaikan. Hilang dalam 2 menit."})}
    publish.insert_analysis(pair, MODE, susun(old, [k], price, int(time.time())))
    print("UJI " + baris("SETUP", k), flush=True)
    time.sleep(120)
    if old:
        publish.insert_analysis(pair, MODE, {**old, "updatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    print("UJI selesai, analisis semula dipulihkan", flush=True)


def _selftest():
    s = {"time": 600, "side": "sell", "entry": 100.0, "sl": 103.5, "tp": [89.5]}
    m = lambda t, hi, lo: [t, (hi + lo) / 2, hi, lo, (hi + lo) / 2, 0]
    assert lacak(s, [m(600, 99, 98), m(660, 99.5, 98)], 720, 3600) == (False, None)
    assert lacak(s, [m(600, 100.2, 99), m(660, 99, 89)], 720, 3600) == (True, "TP")
    assert lacak(s, [m(600, 100.2, 99), m(660, 104, 99)], 720, 3600) == (True, "SL")
    assert lacak(s, [m(600, 99, 98)], 600 + 3700, 3600)[1].startswith("BATAL: limit")
    # 70% ke TP1 (100 -> 89.5) = 92.65 tersentuh sebelum entry -> batal
    assert lacak(s, [m(600, 99, 92.6)], 700, 3600)[1] == "BATAL: harga sudah dekat target tanpa entry"
    k = lambda side, e, valid=False, skor=1: {"side": side, "entry": e, "valid": valid, "skor": skor}
    # dua sell berdekatan: yang lulus validasi menang; buy dibuang karena bias 1H turun
    baru, buang = saring([("a", k("sell", 100), None), ("b", k("sell", 101, True), None), ("c", k("buy", 90), None)],
                         [], 95, -1)
    assert [i for i, _ in baru] == ["b"] and not buang, baru
    # setup aktif belum terisi kalah dari kandidat lebih baik -> diganti
    baru, buang = saring([("n", k("sell", 100.5, True), None)], [("x", k("sell", 100), False)], 95, 0)
    assert [i for i, _ in baru] == ["n"] and buang == {"x"}, (baru, buang)
    # setup yang sudah terisi tidak pernah dibuang
    baru, buang = saring([("n", k("sell", 100.5, True), None)], [("x", k("sell", 100), True)], 95, 0)
    assert not buang
    p = susun({"setups": [{"label": "sniper", "zone": [1, 2]}, {"label": "utama"}], "zones": [{"lo": 1, "hi": 2}],
               "status": "SIAP"}, [], 99.0, 0)
    assert [x["label"] for x in p["setups"]] == ["utama"] and not p["zones"] and p["status"] == "NO TRADE", p
    print("selftest OK")


def main(args):
    pair = next((a.upper() for a in args if not a.startswith("--")), "XAUUSD")
    if "--uji" in args:
        return uji(pair)
    state, gagal = baca(pair), None
    while True:
        try:
            putaran(pair, state)
            simpan(pair, state)
            if gagal:
                print("PULIH pantau: koneksi data kembali normal", flush=True)
            gagal = None
        except Exception as e:  # satu putaran gagal (jaringan) tidak menghentikan watcher; error sama dilaporkan sekali
            jenis = type(e).__name__
            if jenis != gagal:
                print(f"ERROR pantau: {jenis}: {e}", flush=True)
            gagal = jenis
        if "--sekali" in args:
            return
        time.sleep(60 - time.time() % 60 + 5)  # 5 detik setelah candle 1m tutup


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(sys.argv[1:])
