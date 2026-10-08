"""Jembatan MT5: feed harga untuk website + sinkron EA SniperBot ke Supabase + file news untuk EA.

Satu proses (hemat memori), menempel ke terminal MT5 yang sudah login:
  - HTTP 127.0.0.1:5181  GET /candles?tf=M1..W1&n=1000 -> [{time,open,high,low,close}] (UTC)
                         GET /tick -> {bid, ask, last, time}
    Website memakai ini lewat proxy Vite /mt5 (feed Binance diblokir sebagian provider).
  - tiap 60 detik: order/posisi/deal EA (magic MAGIC_EA) -> bot_trades + bot_status (panel Bot website);
    perubahan dicetak sebagai baris "EA ..." (event Monitor).
  - tiap 6 jam: waktu news USD high impact -> Common\\Files\\analisa_news.csv (filter news EA, juga di Strategy Tester).
Pakai:  python jembatan_mt5.py
Self-check: python jembatan_mt5.py --selftest
"""
import datetime as dt
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt5_link  # noqa: E402
from bot_mt5 import PIP, hari_wib, syarat_live  # noqa: E402

PORT = 5181
MAGIC_EA = 770078
HARI_RIWAYAT = 60
TF_WEB = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
          "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1"}
NEWS_FILE = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files", "analisa_news.csv")
KUNCI = threading.Lock()   # paket MetaTrader5 tidak aman dipakai beberapa thread sekaligus
utc = lambda ts: int(ts - mt5_link.offset_server(ts - 3 * 3600))   # jam server -> UTC (pola mt5_link.candles)
iso = lambda ts: dt.datetime.fromtimestamp(utc(ts), dt.timezone.utc).isoformat() if ts else None


# ---- feed ----
def bars(mt5, nama, tf, n):
    r = mt5.copy_rates_from_pos(nama, getattr(mt5, TF_WEB[tf]), 0, n)
    return [{"time": utc(int(x["time"])), "open": float(x["open"]), "high": float(x["high"]),
             "low": float(x["low"]), "close": float(x["close"])} for x in (r if r is not None else [])]


def handler(mt5, nama):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                with KUNCI:
                    if u.path == "/tick":
                        t = mt5.symbol_info_tick(nama)
                        body = {"bid": t.bid, "ask": t.ask, "last": round((t.bid + t.ask) / 2, 3), "time": utc(t.time)}
                    elif u.path == "/candles" and q.get("tf", ["M1"])[0] in TF_WEB:
                        body = bars(mt5, nama, q.get("tf", ["M1"])[0], min(int(q.get("n", ["1000"])[0]), 5000))
                    else:
                        return self.send_error(404)
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(503, str(e)[:100])

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
    from bot_mt5 import konfig
    e = data.env()
    try:
        mt5_link.sambung(e)
    except RuntimeError as x:
        sys.exit(str(x))
    mt5 = mt5_link.modul()
    nama = mt5_link.simbol_emas(e)
    risiko = konfig(e)["risiko"]
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), handler(mt5, nama))
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
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main()
