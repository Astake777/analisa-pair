"""Jembatan MT5: feed harga untuk website + sinkron EA SniperBot ke Supabase + file news untuk EA.

Satu proses (hemat memori), menempel ke terminal MT5 yang sudah login:
  - HTTP 127.0.0.1:5181  GET /candles?tf=M1..W1&n=1000 -> [{time,open,high,low,close}] (UTC)
                         GET /tick -> {bid, ask, last, time}
                         GET /mikro, /setup/batal, /akun/posisi
                         POST /setup/batal, /order/limit, /order/close, /order/close-all
                         (POST wajib header X-Jembatan-Token = isi data/jembatan_token.txt)
    Website memakai ini lewat proxy Vite /mt5 (feed Binance diblokir sebagian provider).
  - tiap 60 detik: order/posisi/deal EA (magic MAGIC_EA) -> bot_trades + bot_status (panel Bot website);
    perubahan dicetak sebagai baris "EA ..." (event Monitor).
  - tiap 6 jam: waktu news USD high impact -> Common\\Files\\analisa_news.csv (filter news EA, juga di Strategy Tester).
Pakai:  python jembatan_mt5.py
Self-check: python jembatan_mt5.py --selftest
"""
import datetime as dt
import hmac
import json
import math
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt5_link  # noqa: E402
from bot_mt5 import PIP, hari_wib, konfig, lot_untuk, syarat_live  # noqa: E402

PORT = 5181
MAGIC_EA = 770078
MAGIC_WEB = 770079
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
TOKEN_FILE = os.path.join(ROOT, "data", "jembatan_token.txt")
BATAL_FILE = os.path.join(ROOT, "data", "setup_dibatalkan.json")
MAKS_BODY = 10 * 1024
HARI_RIWAYAT = 60
TF_WEB = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
          "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1"}
NEWS_FILE = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files", "analisa_news.csv")
KUNCI = threading.Lock()   # paket MetaTrader5 tidak aman dipakai beberapa thread sekaligus
utc = lambda ts: int(ts - mt5_link.offset_server(ts - 3 * 3600))   # jam server -> UTC (pola mt5_link.candles)
iso = lambda ts: dt.datetime.fromtimestamp(utc(ts), dt.timezone.utc).isoformat() if ts else None


# ---- mikro: kondisi pasar intraday dari candle broker ----
def _median(x):
    x = sorted(v for v in x if v is not None)
    return x[len(x) // 2] if x else None


def _jendela(rows, a, b, f):
    """f(candle) untuk candle yang buka di [a, b); rata-rata, None kalau < 6 candle."""
    v = [f(r) for r in rows if a <= r[0] < b]
    return sum(v) / len(v) if len(v) >= 6 else None


def ringkas_mikro(m1, m5, d1, bid, ask, point, now):
    """m1/d1: [t,o,h,l,c,tickvol]; m5: [t,o,h,l,c,tickvol,spread] (t UTC, urut). Hari = candle D1 broker terakhir."""
    import orderflow
    from regime import WIB, jam_sesi
    mid = (bid + ask) / 2
    hari0, hari1 = d1[-1][0], (d1[-2][0] if len(d1) > 1 else None)
    rng = d1[-1][2] - d1[-1][3]
    adr = sum(r[2] - r[3] for r in d1[-21:-1]) / len(d1[-21:-1]) if len(d1) > 1 else None
    jam = lambda f, k: _jendela(m5, now - 3600 - k * 86400, now - k * 86400, f)
    vol_now = jam(lambda r: r[2] - r[3], 0)
    vol_norm = _median(jam(lambda r: r[2] - r[3], k) for k in range(1, 21))
    tv_now = jam(lambda r: r[5], 0)
    tv_norm = _median(jam(lambda r: r[5], k) for k in range(1, 21))
    vwap = orderflow.Vwap(m1, 60).nilai(hari0, now) if m1 else None
    a, lon, ny, akhir = jam_sesi(now)
    base = (now + WIB) // 86400 * 86400 - WIB
    sesi = {}
    for nama, j0, j1 in (("Asia", a, lon), ("London", lon, ny), ("NY", ny, akhir)):
        r = [x for x in m1 if base + j0 * 3600 <= x[0] < base + j1 * 3600]
        sesi[nama] = {"hi": max(x[2] for x in r), "lo": min(x[3] for x in r), "jalan": base + j1 * 3600 > now} if r else None
    bulat = lambda v, d=2: None if v is None else round(v, d)
    return {
        "waktu": int(now), "harga": round(mid, 2),
        "spread": {"poin": round((ask - bid) / point), "pips": round((ask - bid) / PIP, 1),
                   "median": _median(r[6] for r in m5 if r[0] >= now - 30 * 86400)},
        "volatilitas": {"m5": bulat(vol_now), "normal": bulat(vol_norm), "rasio": bulat(vol_now / vol_norm) if vol_now and vol_norm else None},
        "range": {"hari": bulat(rng), "adr": bulat(adr), "persen": bulat(rng / adr, 3) if adr else None,
                  "hi": d1[-1][2], "lo": d1[-1][3]},
        "vwap": {"nilai": bulat(vwap), "jarak": bulat(mid - vwap) if vwap else None},
        "profil": {"hari": orderflow.profil(m1, hari0, now + 60), "kemarin": orderflow.profil(m1, hari1, hari0) if hari1 else None},
        "volume": {"jam": bulat(tv_now, 0), "normal": bulat(tv_norm, 0), "rasio": bulat(tv_now / tv_norm) if tv_now and tv_norm else None},
        "sesi": sesi,
    }


_ada = lambda r: [] if r is None else r  # array numpy tidak bisa dipakai di `or`


def mikro(mt5, nama):
    rows = lambda tf, n, spread=False: [[utc(int(x["time"])), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]),
                                         float(x["tick_volume"])] + ([int(x["spread"])] if spread else [])
                                        for x in _ada(mt5.copy_rates_from_pos(nama, getattr(mt5, tf), 0, n))]
    t = mt5.symbol_info_tick(nama)
    return ringkas_mikro(rows("TIMEFRAME_M1", 3000), rows("TIMEFRAME_M5", 9000, True), rows("TIMEFRAME_D1", 22),
                         t.bid, t.ask, mt5.symbol_info(nama).point, time.time())


# ---- feed ----
def bars(mt5, nama, tf, n):
    r = mt5.copy_rates_from_pos(nama, getattr(mt5, TF_WEB[tf]), 0, n)
    return [{"time": utc(int(x["time"])), "open": float(x["open"]), "high": float(x["high"]),
             "low": float(x["low"]), "close": float(x["close"])} for x in (r if r is not None else [])]


# ---- web: token, setup dibatalkan, akun & order ----
def token_baca(path=None):
    """Token POST; dibuat acak sekali kalau file belum ada."""
    path = path or TOKEN_FILE
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="ascii").write(secrets.token_hex(16))
    return open(path, encoding="ascii").read().strip()


def token_ok(kirim, token):
    return bool(token) and hmac.compare_digest((kirim or "").encode(), token.encode())


def batal_baca():
    try:
        return json.load(open(BATAL_FILE, encoding="utf-8"))
    except FileNotFoundError:
        return {}


def batal_simpan(b, now=None):
    k = b.get("kunci")
    if not isinstance(k, str) or len(k) > 40 or not re.fullmatch(r"(buy|sell):\d+", k):
        raise ValueError("kunci tidak valid (contoh buy:41227)")
    d = {**batal_baca(), k: {"waktu": int(now or time.time()), "berjalan": bool(b.get("berjalan"))}}
    tmp = BATAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, BATAL_FILE)
    print(f"EA WEB setup dibatalkan {k}", flush=True)
    return {"ok": True}


def akun_posisi(mt5, nama, risiko):
    a, t = mt5.account_info(), mt5.symbol_info_tick(nama)
    side = lambda tp: "buy" if tp % 2 == 0 else "sell"   # 0/2/4/6 buy, 1/3/5/7 sell
    return {"akun": mt5_link.JENIS[a.trade_mode], "login": a.login, "saldo": a.balance, "ekuitas": a.equity,
            "mata_uang": a.currency, "harga": {"bid": t.bid, "ask": t.ask}, "risiko": risiko,
            "posisi": [{"tiket": p.ticket, "side": side(p.type), "lot": p.volume, "buka": p.price_open, "sl": p.sl,
                        "tp": p.tp, "profit": p.profit, "magic": p.magic, "komentar": p.comment, "waktu": utc(p.time)}
                       for p in mt5.positions_get(symbol=nama) or []],
            "order": [{"tiket": o.ticket, "side": side(o.type),
                       "jenis": {2: "limit", 3: "limit", 4: "stop", 5: "stop"}.get(o.type, "lain"),
                       "lot": o.volume_current, "harga": o.price_open, "sl": o.sl, "tp": o.tp, "magic": o.magic,
                       "komentar": o.comment, "waktu": utc(o.time_setup)}
                      for o in mt5.orders_get(symbol=nama) or []]}


def _kirim(mt5, req, sukses):
    r = mt5.order_send(req)
    if r is not None and r.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        return True, sukses, r
    return False, f"{r.comment} ({r.retcode})" if r is not None else f"order_send gagal {mt5.last_error()}", r


def order_limit(mt5, nama, cfg, b):
    """Buy/sell limit dari website. Akun real hanya kalau BOT_MODE=live. ValueError -> 400."""
    akun = mt5.account_info()
    if akun.trade_mode != 0 and cfg["mode"] != "live":
        raise ValueError("akun real terkunci: BOT_MODE bukan live")
    side = b.get("side")
    if side not in ("buy", "sell"):
        raise ValueError("side harus buy atau sell")
    sym, t = mt5.symbol_info(nama), mt5.symbol_info_tick(nama)
    try:
        entry, sl, tp = (round(float(b[k]), sym.digits) for k in ("entry", "sl", "tp"))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("entry, sl, tp harus angka")
    if not all(map(math.isfinite, (entry, sl, tp))):
        raise ValueError("entry, sl, tp harus angka")
    if side == "buy":
        if not sl < entry < tp:
            raise ValueError("buy: harus SL < entry < TP")
        if entry >= t.ask:
            raise ValueError(f"buy limit harus di bawah ask {t.ask}")
    else:
        if not tp < entry < sl:
            raise ValueError("sell: harus TP < entry < SL")
        if entry <= t.bid:
            raise ValueError(f"sell limit harus di atas bid {t.bid}")
    pip_value = PIP / sym.trade_tick_size * sym.trade_tick_value
    lot = b.get("lot")
    if lot is None:
        lot, _ = lot_untuk(sym, entry, sl, akun.balance, cfg["risiko"])
        if lot is None:
            raise ValueError("lot minimum melebihi risiko")
    else:
        try:   # dibulatkan ke bawah ke volume_step
            lot = round(math.floor(float(lot) / sym.volume_step + 1e-9) * sym.volume_step, 2)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("lot harus angka")
        if not sym.volume_min <= lot <= sym.volume_max:
            raise ValueError(f"lot harus {sym.volume_min}..{sym.volume_max}")
    # limit yang marginnya tidak cukup ditolak broker saat terisi; lot otomatis dikecilkan, lot manual ditolak
    jenis = mt5.ORDER_TYPE_BUY_LIMIT if side == "buy" else mt5.ORDER_TYPE_SELL_LIMIT
    per_lot = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL, nama, 1.0, entry) \
        if hasattr(mt5, "order_calc_margin") else None
    bebas = getattr(akun, "margin_free", None)
    if per_lot and bebas is not None and lot * per_lot > bebas * 0.95:
        muat = round(math.floor(bebas * 0.95 / per_lot / sym.volume_step + 1e-9) * sym.volume_step, 2)
        if b.get("lot") is not None or muat < sym.volume_min:
            raise ValueError(f"margin kurang: {lot} lot butuh ${lot * per_lot:.2f}, margin bebas ${bebas:.2f} (maks {muat} lot)")
        lot = muat
    req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": nama, "volume": lot, "price": entry, "sl": sl, "tp": tp,
           "type": jenis,
           "deviation": 20, "magic": MAGIC_WEB, "comment": "web",
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_RETURN}
    ok, pesan, r = _kirim(mt5, req, "order terpasang")
    print(f"EA WEB ORDER {side.upper()} LIMIT {lot} @ {entry} SL {sl} TP {tp} -> {pesan}", flush=True)
    return {"ok": ok, "pesan": pesan, "tiket": r.order if ok else None, "lot": lot,
            "rugi_di_sl": round(lot * abs(entry - sl) / PIP * pip_value, 2)}


def _tutup_posisi(mt5, nama, p):
    t = mt5.symbol_info_tick(nama)
    beli = p.type == 0
    return _kirim(mt5, {"action": mt5.TRADE_ACTION_DEAL, "symbol": nama, "volume": p.volume, "position": p.ticket,
                        "type": mt5.ORDER_TYPE_SELL if beli else mt5.ORDER_TYPE_BUY, "price": t.bid if beli else t.ask,
                        "deviation": 30, "magic": p.magic, "comment": "web close",
                        "type_filling": mt5.ORDER_FILLING_RETURN}, "posisi ditutup")[:2]


def _hapus_order(mt5, o):
    return _kirim(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}, "order dihapus")[:2]


def tutup(mt5, nama, b):
    """Tutup posisi / hapus order pending simbol emas (semua magic; selalu boleh, juga akun real)."""
    tk = b.get("tiket")
    if not isinstance(tk, int) or isinstance(tk, bool):
        raise ValueError("tiket harus angka bulat")
    p = [x for x in mt5.positions_get(symbol=nama) or [] if x.ticket == tk]
    o = [x for x in mt5.orders_get(symbol=nama) or [] if x.ticket == tk]
    if not p and not o:
        raise ValueError(f"tiket {tk} tidak ada di posisi/order {nama}")
    ok, pesan = _tutup_posisi(mt5, nama, p[0]) if p else _hapus_order(mt5, o[0])
    print(f"EA WEB TUTUP {tk} -> {pesan}", flush=True)
    return {"ok": ok, "pesan": pesan}


def tutup_semua(mt5, nama):
    pos, ords = list(mt5.positions_get(symbol=nama) or []), list(mt5.orders_get(symbol=nama) or [])
    hasil = [{"tiket": p.ticket, **dict(zip(("ok", "pesan"), _tutup_posisi(mt5, nama, p)))} for p in pos]
    hasil += [{"tiket": o.ticket, **dict(zip(("ok", "pesan"), _hapus_order(mt5, o)))} for o in ords]
    n_p, n_o = sum(h["ok"] for h in hasil[:len(pos)]), sum(h["ok"] for h in hasil[len(pos):])
    pesan = f"{n_p} posisi ditutup, {n_o} order dihapus" if hasil else "tidak ada posisi/order"
    print(f"EA WEB CLOSE ALL -> {pesan}", flush=True)
    return {"ok": all(h["ok"] for h in hasil), "pesan": pesan, "hasil": hasil}


def handler(mt5, nama, token=None):
    import data

    class H(BaseHTTPRequestHandler):
        def _json(self, kode, body):
            isi = json.dumps(body).encode()
            self.send_response(kode)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(isi)))
            self.end_headers()
            self.wfile.write(isi)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                with KUNCI:
                    if u.path == "/tick":
                        t = mt5.symbol_info_tick(nama)
                        body = {"bid": t.bid, "ask": t.ask, "last": round((t.bid + t.ask) / 2, 3), "time": utc(t.time)}
                    elif u.path == "/mikro":
                        body = mikro(mt5, nama)
                    elif u.path == "/setup/batal":
                        body = batal_baca()
                    elif u.path == "/akun/posisi":
                        body = akun_posisi(mt5, nama, konfig(data.env())["risiko"])
                    elif u.path == "/candles" and q.get("tf", ["M1"])[0] in TF_WEB:
                        body = bars(mt5, nama, q.get("tf", ["M1"])[0], min(int(q.get("n", ["1000"])[0]), 5000))
                    else:
                        return self.send_error(404)
                self._json(200, body)
            except Exception as e:
                self.send_error(503, str(e)[:100])

        def do_POST(self):
            if not token_ok(self.headers.get("X-Jembatan-Token"), token):
                return self._json(403, {"ok": False, "pesan": "token salah"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 <= n <= MAKS_BODY:
                    raise ValueError("body terlalu besar")
                b = json.loads(self.rfile.read(n)) if n else {}
                if not isinstance(b, dict):
                    raise ValueError("body harus objek JSON")
                path = urlparse(self.path).path
                with KUNCI:
                    if path == "/setup/batal":
                        body = batal_simpan(b)
                    elif path == "/order/limit":
                        body = order_limit(mt5, nama, konfig(data.env()), b)
                    elif path == "/order/close":
                        body = tutup(mt5, nama, b)
                    elif path == "/order/close-all":
                        body = tutup_semua(mt5, nama)
                    else:
                        return self._json(404, {"ok": False, "pesan": "endpoint tidak ada"})
                self._json(200, body)
            except ValueError as x:   # termasuk JSON rusak
                self._json(400, {"ok": False, "pesan": str(x)[:200]})
            except Exception as x:
                self._json(503, {"ok": False, "pesan": f"{type(x).__name__}: {str(x)[:200]}"})

        def log_message(self, *a):
            pass
    return H


# ---- sinkron EA ----
def baris_ea(mt5, akun, nama, risiko, sejak):
    """-> {id: baris bot_trades} dari order hidup, posisi hidup, dan riwayat order/deal EA."""
    jenis = {0: "demo", 1: "contest", 2: "real"}[akun.trade_mode]
    sym = mt5.symbol_info(nama)
    pip_value = PIP / sym.trade_tick_size * sym.trade_tick_value
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    h_orders = [o for o in (mt5.history_orders_get(sejak, now) or []) if o.magic == MAGIC_EA]
    deals = [d for d in (mt5.history_deals_get(sejak, now) or []) if d.magic == MAGIC_EA]
    out = {}

    def baru(o, status):
        return {"id": f"ea:{o.ticket}", "akun": jenis, "login": akun.login, "simbol": o.symbol, "strategi": "sniper-ea",
                "side": "buy" if o.type in (0, 2) else "sell", "lot": o.volume_initial, "risiko": risiko,
                "entry": o.price_open, "harga_isi": None, "sl": o.sl, "tp": o.tp, "order_ticket": o.ticket,
                "posisi_ticket": None, "dibuat": iso(o.time_setup), "dibuka": None, "ditutup": None,
                "status": status, "pl": None, "r": None}

    for o in h_orders:   # limit yang sudah selesai: terisi (lihat deal) atau batal/kedaluwarsa
        if o.type in (2, 3):
            out[o.ticket] = baru(o, "BATAL" if o.state in (mt5.ORDER_STATE_CANCELED, mt5.ORDER_STATE_EXPIRED) else "TERBUKA")
    for o in mt5.orders_get() or []:
        if o.magic == MAGIC_EA:
            out[o.ticket] = baru(o, "PENDING")
    per_posisi = {}
    for d in deals:
        per_posisi.setdefault(d.position_id, []).append(d)
    hidup = {p.identifier: p for p in (mt5.positions_get() or []) if p.magic == MAGIC_EA}
    for pid, ds in per_posisi.items():
        b = out.get(pid)
        if b is None:
            continue
        masuk = [d for d in ds if d.entry == 0]
        keluar = [d for d in ds if d.entry == 1]
        if masuk:
            b.update(harga_isi=masuk[0].price, dibuka=iso(masuk[0].time), posisi_ticket=pid, status="TERBUKA")
        risiko_usd = b["lot"] * abs(b["entry"] - b["sl"]) / PIP * pip_value   # sl di sini = SL awal order limit
        if keluar and pid not in hidup:
            x = keluar[-1]
            pl = sum(d.profit + d.commission + d.swap for d in ds)
            hasil = ("TP" if x.reason == mt5.DEAL_REASON_TP
                     else ("BE" if masuk and abs(x.price - masuk[0].price) <= 0.5 else "SL") if x.reason == mt5.DEAL_REASON_SL
                     else "DITUTUP: manual")
            b.update(status=hasil, pl=round(pl, 2), r=round(pl / risiko_usd, 2) if risiko_usd else None,
                     ditutup=iso(x.time))
    for pid, p in hidup.items():
        b = out.get(pid)
        if b:
            b.update(sl=p.sl, tp=p.tp, status="TERBUKA", posisi_ticket=pid, harga_isi=p.price_open)
    return {b["id"]: b for b in out.values()}


def peristiwa(lama, baru):
    """Baris event untuk perubahan status/SL antara dua sinkron."""
    out = []
    for i, b in baru.items():
        a = lama.get(i)
        s = f"{b['side'].upper()} {b['lot']} lot @ {b['entry']}"
        if a is None:
            if b["status"] in ("PENDING", "TERBUKA"):
                out.append(f"EA {'ORDER' if b['status'] == 'PENDING' else 'TERBUKA'} {s} | SL {b['sl']} | TP {b['tp']}")
            continue
        if a["status"] != b["status"]:
            extra = f": P/L {b['pl']:+.2f} ({b['r']:+.2f}R)" if b["pl"] is not None and b["r"] is not None else ""
            out.append(f"EA {b['status']} {s}{extra}")
        elif b["status"] == "TERBUKA" and a["sl"] != b["sl"] and b["harga_isi"] is not None and abs(b["sl"] - b["harga_isi"]) < 0.01:
            out.append(f"EA BE {s}: SL digeser ke entry {b['sl']}")
    return out


def status_ea(mt5, akun, nama, rows, risiko, now):
    hari = hari_wib(now)
    tutup_hari = [r for r in rows.values() if r["ditutup"] and hari_wib(dt.datetime.fromisoformat(r["ditutup"]).timestamp()) == hari]
    masuk_hari = [r for r in rows.values() if r["dibuka"] and hari_wib(dt.datetime.fromisoformat(r["dibuka"]).timestamp()) == hari]
    cek, _ = syarat_live([{**r, "akun": r["akun"]} for r in rows.values()], None)
    sym = mt5.symbol_info(nama)
    algo = bool(mt5.terminal_info().trade_allowed)
    return {"login": akun.login, "akun": {0: "demo", 1: "contest", 2: "real"}[akun.trade_mode], "server": akun.server,
            "simbol": nama, "mode": "ea", "risiko": risiko, "ekuitas": round(akun.equity, 2), "saldo": round(akun.balance, 2),
            "pl_hari_ini": round(sum(r["pl"] or 0 for r in tutup_hari), 2),
            "sl_hari_ini": sum(r["status"] == "SL" for r in tutup_hari), "entry_hari_ini": len(masuk_hari),
            "terbuka": [{k: r[k] for k in ("id", "side", "lot", "entry", "sl", "tp", "status")}
                        for r in rows.values() if r["status"] in ("PENDING", "TERBUKA")],
            "pengaman": {"boleh_order": algo, "alasan": "" if algo else "Algo Trading MT5 mati", "spread": sym.spread,
                         "spread_median": None, "news": False, "kill_switch": False, "ea": True},
            "syarat_live": cek, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def tulis_news():
    import kalender
    t = sorted(int(dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
               for e in kalender.fetch(24 * 220, 2) if e.get("importance") == 1)
    os.makedirs(os.path.dirname(NEWS_FILE), exist_ok=True)
    tmp = NEWS_FILE + ".tmp"
    open(tmp, "w", encoding="ascii").write("\n".join(map(str, t)) + "\n")
    os.replace(tmp, NEWS_FILE)
    return len(t)


def main():
    import data
    import publish
    e = data.env()
    try:
        mt5_link.sambung(e)
    except RuntimeError as x:
        sys.exit(str(x))
    mt5 = mt5_link.modul()
    nama = mt5_link.simbol_emas(e)
    risiko = konfig(e)["risiko"]
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), handler(mt5, nama, token_baca()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"JEMBATAN MULAI feed http://127.0.0.1:{PORT} ({nama}), sinkron EA magic {MAGIC_EA}", flush=True)
    lama, gagal, news_t = None, None, 0
    while True:
        now = time.time()
        try:
            if now - news_t > 6 * 3600:
                n = tulis_news()
                news_t = now
                print(f"JEMBATAN news: {n} event USD high impact ditulis untuk EA", flush=True)
            with KUNCI:
                akun = mt5.account_info()
                sejak = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=HARI_RIWAYAT)
                rows = baris_ea(mt5, akun, nama, risiko, sejak)
                st = status_ea(mt5, akun, nama, rows, risiko, now)
            for s in peristiwa(lama or rows, rows) if lama is not None else []:
                print(s, flush=True)
            berubah = [r for i, r in rows.items() if lama is None or lama.get(i) != r]
            if berubah:
                publish._post("bot_trades", [{**r, "updated_at": st["updated_at"]} for r in berubah], publish.MERGE)
            publish._post("bot_status", [st], publish.MERGE)
            lama = rows
            if gagal:
                print("JEMBATAN PULIH", flush=True)
            gagal = None
        except Exception as x:  # satu putaran gagal tidak menghentikan jembatan; error sama dilaporkan sekali
            if type(x).__name__ != gagal:
                print(f"JEMBATAN ERROR {type(x).__name__}: {str(x)[:200]}", flush=True)
            gagal = type(x).__name__
        time.sleep(60 - time.time() % 60 + 3)


def _selftest():
    from types import SimpleNamespace as N
    m = mt5_link.Palsu()
    m.ORDER_STATE_CANCELED, m.ORDER_STATE_EXPIRED, m.DEAL_REASON_SL, m.DEAL_REASON_TP = 2, 6, 4, 5
    ts = 1791432000   # jam server
    lim = lambda tk, st, sl=4095.0: N(ticket=tk, magic=MAGIC_EA, type=2, state=st, symbol="XAUUSD", volume_initial=0.05,
                                      price_open=4100.0, sl=sl, tp=4110.0, time_setup=ts)
    deal = lambda pid, ent, px, pl, rs: N(position_id=pid, magic=MAGIC_EA, entry=ent, price=px, profit=pl, commission=0.0,
                                          swap=0.0, reason=rs, time=ts + 600)
    m.history_orders_get = lambda a, b: [lim(1, 4), lim(2, 4), lim(3, 4), lim(4, 2), N(ticket=9, magic=1, type=2)]
    m.history_deals_get = lambda a, b: [deal(1, 0, 4100.0, 0, 3), deal(1, 1, 4110.0, 50.0, 5),
                                        deal(2, 0, 4100.0, 0, 3), deal(2, 1, 4095.0, -25.0, 4),
                                        deal(3, 0, 4100.0, 0, 3), deal(3, 1, 4100.0, 0.0, 4)]
    rows = baris_ea(m, m.akun, "XAUUSD", 0.25, None)
    st = {r["id"]: (r["status"], r["r"]) for r in rows.values()}
    # SL 50 pips x 0.05 lot x $10 = $25 risiko
    assert st == {"ea:1": ("TP", 2.0), "ea:2": ("SL", -1.0), "ea:3": ("BE", 0.0), "ea:4": ("BATAL", None)}, st
    assert rows["ea:1"]["dibuat"] == iso(ts) and utc(ts) == ts - 3 * 3600   # Okt 2026: server GMT+3
    m.positions_get = lambda: [N(identifier=5, magic=MAGIC_EA, sl=4100.0, tp=4110.0, price_open=4100.0)]
    m.history_orders_get = lambda a, b: [lim(5, 4)]
    m.history_deals_get = lambda a, b: [deal(5, 0, 4100.0, 0, 3)]
    sebelum = baris_ea(m, m.akun, "XAUUSD", 0.25, None)
    assert sebelum["ea:5"]["status"] == "TERBUKA" and sebelum["ea:5"]["sl"] == 4100.0
    ev = peristiwa({"ea:5": {**sebelum["ea:5"], "sl": 4095.0}}, sebelum)
    assert ev and ev[0].startswith("EA BE BUY"), ev
    assert peristiwa({}, {"ea:6": {**sebelum["ea:5"], "id": "ea:6", "status": "PENDING"}})[0].startswith("EA ORDER")
    b = bars(N(TIMEFRAME_M15=15, copy_rates_from_pos=lambda *a: [{"time": ts, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]),
             "XAUUSD", "M15", 1)
    assert b == [{"time": ts - 3 * 3600, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}], b
    # mikro: 21 hari D1 range 10 -> ADR 10; hari ini range 5 = 50%; VWAP dari M1 hari ini; volatilitas jam ini 2x normal
    D = 86400
    now = 30 * D + 12 * 3600
    d1 = [[k * D, 100, 110, 100, 105, 1] for k in range(9, 30)] + [[30 * D, 100, 104, 99, 103, 1]]
    m1 = [[30 * D + i * 60, 100, 101, 99, 100 + (i % 2), 10] for i in range(700)]
    m5 = [[k * D + 11 * 3600 + i * 300, 100, 101, 100, 100, 10, 30] for k in range(10, 30) for i in range(12)]
    m5 += [[30 * D + 11 * 3600 + i * 300, 100, 102, 100, 100, 20, 40] for i in range(12)]
    mk = ringkas_mikro(m1, m5, d1, 100.0, 100.34, 0.01, now)
    assert mk["range"]["adr"] == 10 and mk["range"]["persen"] == 0.5 and mk["spread"]["poin"] == 34, mk["range"]
    assert mk["volatilitas"]["rasio"] == 2.0 and mk["volume"]["rasio"] == 2.0, (mk["volatilitas"], mk["volume"])
    assert mk["vwap"]["nilai"] is not None and abs(mk["vwap"]["nilai"] - 100.17) < 0.05 and mk["profil"]["hari"], mk["vwap"]
    assert set(mk["sesi"]) == {"Asia", "London", "NY"}
    # ---- web: token, setup batal, order limit, tutup ----
    import contextlib
    import io
    import tempfile
    import urllib.error
    import urllib.request
    global BATAL_FILE
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        tok = token_baca(os.path.join(d, "x", "tok.txt"))
        assert re.fullmatch(r"[0-9a-f]{32}", tok) and token_baca(os.path.join(d, "x", "tok.txt")) == tok
        assert token_ok(tok, tok) and not token_ok("salah", tok) and not token_ok(None, tok) and not token_ok("", "")
        asli, BATAL_FILE = BATAL_FILE, os.path.join(d, "batal.json")
        try:
            assert batal_baca() == {}
            batal_simpan({"kunci": "buy:41227", "berjalan": True}, now=100)
            batal_simpan({"kunci": "sell:4100", "berjalan": False}, now=200)
            assert batal_baca() == {"buy:41227": {"waktu": 100, "berjalan": True}, "sell:4100": {"waktu": 200, "berjalan": False}}
            for k in ("x:1", "buy:", "buy:1" + "0" * 40, 5):
                try:
                    batal_simpan({"kunci": k})
                    raise AssertionError(k)
                except ValueError:
                    pass
            # HTTP: POST tanpa/salah token 403, benar 200; tanpa header CORS
            srv = ThreadingHTTPServer(("127.0.0.1", 0), handler(mt5_link.Palsu(), "XAUUSD", tok))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{srv.server_address[1]}/setup/batal"
            kirim = lambda h, isi=b'{"kunci": "buy:1", "berjalan": false}': urllib.request.urlopen(
                urllib.request.Request(url, isi, {"Content-Type": "application/json", **h}))
            try:
                kirim({"X-Jembatan-Token": "salah"})
                raise AssertionError("token salah harus 403")
            except urllib.error.HTTPError as x:
                assert x.code == 403 and json.load(x) == {"ok": False, "pesan": "token salah"}
            r = kirim({"X-Jembatan-Token": tok})
            assert json.load(r) == {"ok": True} and r.headers.get("Access-Control-Allow-Origin") is None
            assert "buy:1" in batal_baca()
            try:
                kirim({"X-Jembatan-Token": tok}, b"[1]")
                raise AssertionError("body bukan objek harus 400")
            except urllib.error.HTTPError as x:
                assert x.code == 400
            srv.shutdown()
        finally:
            BATAL_FILE = asli
    # order limit (Palsu: bid 4100.0 ask 4100.2, saldo 10000, $1/pip per 0.01 lot)
    m = mt5_link.Palsu()
    m.ORDER_TIME_GTC, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 0, 1
    demo = {"mode": "demo", "risiko": 0.25}
    with contextlib.redirect_stdout(io.StringIO()):
        for b, salah in (({"side": "buy", "entry": 4101, "sl": 4095, "tp": 4110}, "di bawah ask"),
                         ({"side": "buy", "entry": 4099, "sl": 4100, "tp": 4110}, "SL < entry"),
                         ({"side": "sell", "entry": 4099, "sl": 4105, "tp": 4090}, "di atas bid"),
                         ({"side": "buy", "entry": "x", "sl": 4095, "tp": 4110}, "angka"),
                         ({"side": "buy", "entry": 4099, "sl": 4095, "tp": 4110, "lot": 99}, "lot harus")):
            try:
                order_limit(m, "XAUUSD", demo, b)
                raise AssertionError(b)
            except ValueError as x:
                assert salah in str(x), (salah, x)
        r = order_limit(m, "XAUUSD", demo, {"side": "buy", "entry": 4095.004, "sl": 4090, "tp": 4110})
        q = m.kirim[-1]
        lot, _ = lot_untuk(m.sym, 4095.0, 4090.0, 10_000.0, 0.25)   # $2500 / (50 pips x $10) = 5 lot
        assert q["magic"] == MAGIC_WEB and q["type"] == m.ORDER_TYPE_BUY_LIMIT and q["price"] == 4095.0, q
        assert q["action"] == m.TRADE_ACTION_PENDING and q["volume"] == lot == 5.0 and q["comment"] == "web"
        assert r == {"ok": True, "pesan": "order terpasang", "tiket": 1, "lot": 5.0, "rugi_di_sl": 2500.0}, r
        r = order_limit(m, "XAUUSD", demo, {"side": "sell", "entry": 4105, "sl": 4110, "tp": 4090, "lot": 0.237})
        assert m.kirim[-1]["type"] == m.ORDER_TYPE_SELL_LIMIT and r["lot"] == 0.23 and r["rugi_di_sl"] == 115.0, r
        # margin: $825/lot, margin bebas $120 -> lot otomatis dikecilkan ke 0.13, lot manual 0.15 ditolak
        kecil = mt5_link.Palsu()
        kecil.ORDER_TIME_GTC, kecil.ORDER_TYPE_BUY, kecil.ORDER_TYPE_SELL = 0, 0, 1
        kecil.akun.balance = kecil.akun.margin_free = 120.0
        kecil.order_calc_margin = lambda t, s, v, p: 825.0 * v
        r = order_limit(kecil, "XAUUSD", demo, {"side": "buy", "entry": 4099, "sl": 4097, "tp": 4109})
        assert r["lot"] == 0.13, r
        try:
            order_limit(kecil, "XAUUSD", demo, {"side": "buy", "entry": 4099, "sl": 4097, "tp": 4109, "lot": 0.15})
            raise AssertionError("lot manual tanpa margin harus ditolak")
        except ValueError as x:
            assert "margin kurang" in str(x), x
        real = mt5_link.Palsu(trade_mode=2)
        real.ORDER_TIME_GTC = 0
        try:
            order_limit(real, "XAUUSD", demo, {"side": "buy", "entry": 4095, "sl": 4090, "tp": 4110})
            raise AssertionError("akun real harus terkunci")
        except ValueError as x:
            assert "akun real terkunci" in str(x) and not real.kirim
        assert order_limit(real, "XAUUSD", {**demo, "mode": "live"},
                           {"side": "buy", "entry": 4095, "sl": 4090, "tp": 4110})["ok"]
        # tutup: posisi buy -> DEAL sell di bid, order -> REMOVE; tiket asing 400
        m = mt5_link.Palsu()
        m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 1
        m.posisi = [N(ticket=7, type=0, volume=0.1, magic=MAGIC_EA, symbol="XAUUSD")]
        m.order = [N(ticket=8, type=2, magic=MAGIC_WEB, symbol="XAUUSD")]
        r = tutup_semua(m, "XAUUSD")
        deal, hapus = m.kirim
        assert deal["action"] == m.TRADE_ACTION_DEAL and deal["position"] == 7 and deal["type"] == 1 and deal["price"] == 4100.0
        assert deal["magic"] == MAGIC_EA and deal["comment"] == "web close" and deal["deviation"] == 30
        assert hapus == {"action": m.TRADE_ACTION_REMOVE, "order": 8}
        assert r["ok"] and r["pesan"] == "1 posisi ditutup, 1 order dihapus" and [h["tiket"] for h in r["hasil"]] == [7, 8], r
        assert tutup_semua(mt5_link.Palsu(), "XAUUSD") == {"ok": True, "pesan": "tidak ada posisi/order", "hasil": []}
        m.order = [N(ticket=8, type=2, magic=MAGIC_WEB, symbol="XAUUSD")]
        assert tutup(m, "XAUUSD", {"tiket": 8})["ok"] and m.kirim[-1]["action"] == m.TRADE_ACTION_REMOVE
        try:
            tutup(m, "XAUUSD", {"tiket": 999})
            raise AssertionError("tiket asing harus ditolak")
        except ValueError:
            pass
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main()
