"""Koneksi ke terminal MetaTrader 5 (paket Python MetaTrader5): login dari .env, simbol emas, candle broker.

.env: MT5_LOGIN, MT5_PASSWORD, MT5_SERVER; kalau kosong, menempel ke akun yang sudah login di terminal.
MT5_PATH (opsional, terminal64.exe), MT5_SYMBOL (opsional).
Password tidak pernah dicetak.
Pakai:  python mt5_link.py         status koneksi (akun demo/real, simbol, spesifikasi)
Self-check: python mt5_link.py --selftest
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TF = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "30m": "TIMEFRAME_M30",
      "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}
JENIS = {0: "demo", 1: "contest", 2: "real"}
POTONG_HARI = 20
DEFAULT_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
_mt5 = None


def modul():
    global _mt5
    if _mt5 is None:
        import MetaTrader5
        _mt5 = MetaTrader5
    return _mt5


def sambung(e=None, mt5=None):
    """Tempel ke terminal; login dari .env hanya kalau akun yang sedang login berbeda (login ulang membuat MT5
    mematikan Algo Trading). -> account_info. Gagal -> RuntimeError berbahasa Indonesia."""
    import data
    mt5 = mt5 or modul()
    e = e or data.env()
    path = {"path": e.get("MT5_PATH") or DEFAULT_PATH}
    if mt5.terminal_info() is None:
        mt5.initialize(timeout=30000, **path)
    ai = mt5.account_info()
    if ai is not None and (not e.get("MT5_LOGIN") or str(ai.login) == e["MT5_LOGIN"]):
        return ai
    hilang = [k for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER") if not e.get(k)]
    if hilang:
        raise RuntimeError("MT5: terminal belum login. Login akun demo HFM di jendela MT5 (centang Save password), "
                           f"atau isi {', '.join(hilang)} di .env.")
    kw = {"login": int(e["MT5_LOGIN"]), "password": e["MT5_PASSWORD"], "server": e["MT5_SERVER"], "timeout": 30000, **path}
    if not mt5.initialize(**kw) or mt5.account_info() is None:
        raise RuntimeError(f"MT5: gagal login ke {e['MT5_SERVER']} ({mt5.last_error()}). Cek login/server dan terminal MT5.")
    return mt5.account_info()


def simbol_emas(e=None, mt5=None):
    """Nama simbol emas di broker: MT5_SYMBOL di .env, atau simbol XAUUSD* yang bisa ditrade."""
    import data
    mt5 = mt5 or modul()
    e = e or data.env()
    if e.get("MT5_SYMBOL"):
        nama = e["MT5_SYMBOL"]
    else:
        calon = [s for s in (mt5.symbols_get("*XAU*") or []) if s.name.upper().startswith("XAUUSD") and s.trade_mode != 0]
        if not calon:
            raise RuntimeError("MT5: simbol XAUUSD tidak ditemukan; isi MT5_SYMBOL di .env.")
        nama = min(calon, key=lambda s: (not s.visible, len(s.name))).name
    mt5.symbol_select(nama, True)
    return nama


def offset_server(t):
    """Selisih jam server HFM terhadap UTC pada waktu UTC t: GMT+3 saat DST AS, GMT+2 di luar itu
    (server mengikuti penutupan New York = 00:00 server)."""
    from regime import DST_US, _panas
    return (3 if _panas(t, DST_US) else 2) * 3600


def ke_server(t):
    return int(t + offset_server(t))


def cek_offset(nama, mt5=None):
    """Bandingkan jam tick live dengan aturan offset; -> (offset terukur jam, cocok?) atau (None, True) tanpa tick."""
    import time
    mt5 = mt5 or modul()
    tick = mt5.symbol_info_tick(nama)
    if not tick or not tick.time or time.time() - (tick.time - offset_server(time.time())) > 3600:
        return None, True   # pasar tutup / tick lama: tidak bisa diukur
    ukur = round((tick.time - time.time()) / 3600)
    return ukur, ukur * 3600 == offset_server(time.time())


def candles(nama, tf, start, end=None, mt5=None):
    """Candle broker [t, o, h, l, c, tick_volume] untuk [start, end) detik UTC (jam server dikonversi ke UTC)."""
    mt5 = mt5 or modul()
    end = end or int(dt.datetime.now(dt.timezone.utc).timestamp()) + 60
    f = lambda t: dt.datetime.fromtimestamp(ke_server(t), dt.timezone.utc)
    out, a = [], start
    while a < end:   # per potongan 20 hari: rentang besar sekaligus ditolak terminal ("Invalid params")
        b = min(a + POTONG_HARI * 86400, end)
        r = mt5.copy_rates_range(nama, getattr(mt5, TF[tf]), f(a), f(b))
        if r is None:
            if out or a > start:   # riwayat broker lebih pendek dari yang diminta: lewati potongan kosong
                a = b
                continue
            raise OSError(f"MT5 copy_rates_range {nama} {tf}: {mt5.last_error()}")
        for x in r:
            ts = int(x["time"])
            t = ts - offset_server(ts - 3 * 3600)
            if not out or t > out[-1][0]:
                out.append([t, float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x["tick_volume"])])
        a = b
    return out


class Palsu:
    """MT5 tiruan untuk selftest (dipakai juga oleh bot_mt5.py)."""
    TIMEFRAME_M1 = 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
    TRADE_ACTION_PENDING, TRADE_ACTION_REMOVE, TRADE_ACTION_DEAL = 5, 8, 1
    ORDER_TIME_SPECIFIED, ORDER_FILLING_RETURN = 2, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008

    def __init__(self, trade_mode=0, login=111):
        from types import SimpleNamespace as N
        self.N = N
        self.akun = N(login=login, trade_mode=trade_mode, equity=10_000.0, balance=10_000.0, currency="USD",
                      server="HFMarketsGlobal-Demo", leverage=500)
        self.sym = N(name="XAUUSD", visible=True, trade_mode=4, digits=2, trade_tick_size=0.01, trade_tick_value=1.0,
                     trade_contract_size=100, volume_min=0.01, volume_step=0.01, volume_max=50.0, spread=20,
                     point=0.01, trade_stops_level=0, filling_mode=2)
        self.kirim, self.order, self.posisi, self.deal = [], [], [], []
        self.login_ok = True

    def initialize(self, **kw):
        self.init_kw = getattr(self, "init_kw", []) + [kw]
        return self.login_ok

    def terminal_info(self):
        return self.N(connected=True, trade_allowed=True, name="MetaTrader 5")

    def account_info(self):
        return self.akun

    def last_error(self):
        return (1, "ok")

    def symbols_get(self, pola):
        return [self.sym]

    def symbol_select(self, nama, on):
        return True

    def symbol_info(self, nama):
        return self.sym

    def symbol_info_tick(self, nama):
        return self.N(bid=4100.0, ask=4100.2, time=0)

    def orders_get(self, **kw):
        return [o for o in self.order if o.magic == kw.get("magic", o.magic)] if "magic" in kw else self.order

    def positions_get(self, **kw):
        return self.posisi

    def history_deals_get(self, *a, **kw):
        return self.deal

    def order_send(self, req):
        self.kirim.append(req)
        if req["action"] == self.TRADE_ACTION_PENDING:
            self.order.append(self.N(ticket=len(self.kirim), magic=req["magic"], comment=req["comment"],
                                     type=req["type"], price_open=req["price"], sl=req["sl"], tp=req["tp"],
                                     volume_initial=req["volume"], symbol=req["symbol"]))
            return self.N(retcode=self.TRADE_RETCODE_PLACED, order=len(self.kirim), comment="placed")
        if req["action"] == self.TRADE_ACTION_REMOVE:
            self.order = [o for o in self.order if o.ticket != req["order"]]
        return self.N(retcode=self.TRADE_RETCODE_DONE, order=req.get("order", 0), comment="done")

    def copy_rates_range(self, nama, tf, a, b):
        t0 = int(a.timestamp()) // 60 * 60
        return [{"time": t0 + i * 60, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "tick_volume": 7}
                for i in range(3)]

    def shutdown(self):
        return True


def _selftest():
    p = Palsu()
    e = {"MT5_LOGIN": "111", "MT5_PASSWORD": "x", "MT5_SERVER": "HFMarketsGlobal-Demo"}
    assert sambung(e, p).trade_mode == 0
    assert not any("login" in kw for kw in getattr(p, "init_kw", [])), "akun sama: tidak boleh login ulang"
    assert sambung({}, Palsu(login=2)).login == 2           # tanpa .env: menempel ke akun yang login di terminal
    kosong = Palsu(login=3)
    kosong.login_ok = False
    try:
        sambung({"MT5_LOGIN": "1"}, kosong)
        raise AssertionError("harus gagal tanpa password/server dan terminal belum login")
    except RuntimeError as x:
        assert "belum login" in str(x) and "MT5_PASSWORD" in str(x)
    p2 = Palsu(login=5)
    p2.login_ok = False
    try:
        sambung(e, p2)
        raise AssertionError("harus gagal login")
    except RuntimeError as x:
        assert "gagal login" in str(x) and "x" not in str(x).split("(")[0].replace("MT5", "")
    assert simbol_emas({}, p) == "XAUUSD" and simbol_emas({"MT5_SYMBOL": "XAUUSD.b"}, p) == "XAUUSD.b"
    # Palsu mengembalikan bar mulai jam server dari permintaan; hasil harus kembali ke UTC
    t0 = 1785715200   # 3 Agu 2026 (DST AS aktif, server GMT+3)
    c = candles("XAUUSD", "1m", t0, t0 + 300, p)
    assert c[0] == [t0, 1.0, 2.0, 0.5, 1.5, 7.0] and len(c) == 3, c
    assert offset_server(t0) == 3 * 3600 and offset_server(1795000000) == 2 * 3600   # 1795000000 = Nov 2026
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        try:
            ai = sambung()
            mt5 = modul()
            nama = simbol_emas()
            s, tick = mt5.symbol_info(nama), mt5.symbol_info_tick(nama)
            print(f"terhubung: server {ai.server}, akun {JENIS.get(ai.trade_mode, ai.trade_mode)}, {ai.currency}, "
                  f"leverage 1:{ai.leverage}, algo trading {'ON' if mt5.terminal_info().trade_allowed else 'OFF'}")
            print(f"simbol {nama}: bid {tick.bid} ask {tick.ask}, spread {s.spread} poin, tick {s.trade_tick_size} = "
                  f"${s.trade_tick_value}/lot, lot min {s.volume_min} step {s.volume_step}")
        except RuntimeError as x:
            sys.exit(str(x))
