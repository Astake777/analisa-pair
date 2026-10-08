"""Bot MT5: eksekusi otomatis setup sniper (order limit + SL + TP), lacak sampai tutup, jurnal ke Supabase.

Pakai:  python bot_mt5.py              loop (Claude menjalankannya lewat Monitor -> push ke HP)
        python bot_mt5.py --sekali     satu putaran
        python bot_mt5.py --status     koneksi, akun, simbol, batas harian, syarat live
        python bot_mt5.py --stop       kill switch: batalkan order bot, tutup posisi bot
        python bot_mt5.py --uji-order  DEMO SAJA: pasang order limit lot minimum jauh dari harga lalu batalkan
.env:   MT5_LOGIN / MT5_PASSWORD / MT5_SERVER (akun demo HFM), BOT_MODE=demo|live (bawaan demo),
        BOT_RISK_PERCENTAGE=25 (persen saldo per trade, dikunci 25-30), opsional MT5_SYMBOL, MT5_PATH.
Lot dinamis: Risk_Amount = saldo akun x Risk_Percentage / 100; Lot = Risk_Amount / (SL_pips x Pip_Value),
dibulatkan ke bawah ke step broker. SL lebar -> lot kecil, rugi di SL tetap = Risk_Amount.
Auto break-even: BOT_BE_TRIGGER_PIPS=50 (0 = mati). Floating profit >= itu -> SL posisi digeser ke harga entry
(dicek tiap putaran 1 menit); kalau harga balik, posisi tutup di entry dengan status BE.
Pengaman (dicek sebelum setiap order):
  - akun real ditolak kecuali BOT_MODE=live DAN strategi lulus validasi DAN syarat real test demo lulus;
  - berhenti order baru setelah MAKS_SL_HARIAN SL atau MAKS_ENTRY_HARIAN entry dalam satu hari WIB;
  - maksimal satu order/posisi bot terbuka; tidak ada order NEWS_MENIT sebelum/sesudah news USD high impact;
  - spread > SPREAD_X x median spread -> tunda; file data/BOT_STOP -> batalkan semua dan berhenti.
Order: limit di entry, SL dan TP1 terpasang, kedaluwarsa = EXPIRE_S strategi; dibatalkan bot kalau harga sudah
BATAL_FRAC (70%) ke TP1 tanpa terisi (aturan sama dengan backtest dan watcher).
Baris keluaran (event Monitor): BOT ORDER / TERISI / BE / TP / SL / DITUTUP / BATAL / LEWATI / STOP / ERROR / PULIH.
Self-check: python bot_mt5.py --selftest
"""
import datetime as dt
import importlib
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regime import STEP  # noqa: E402
from validasi import BATAL_FRAC  # noqa: E402
import mt5_link  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
STOP_FILE = os.path.join(ROOT, "data", "BOT_STOP")
STATE_FILE = os.path.join(ROOT, "data", "bot_state.json")
TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
HARI_DATA = 60
MAGIC = 770077
RISIKO_MIN, RISIKO_MAKS = 0.25, 0.30
PIP = 0.10   # 1 pip XAUUSD
MAKS_SL_HARIAN, MAKS_ENTRY_HARIAN, MAKS_TERBUKA = 2, 3, 1
NEWS_MENIT, SPREAD_X = 15, 3.0
SYARAT_LIVE = {"trade": 30, "expectancy": 0.15, "dd": 0.60, "beda_winrate": 0.15, "slip": 0.30}
WIB = 7 * 3600
r2 = lambda x: round(x, 2)
PRIVAT = ("risiko_usd", "ekuitas_awal", "be", "sl_awal")   # hanya di state, tidak ada kolomnya di Supabase


def konfig(e):
    mode = e.get("BOT_MODE", "demo").strip().lower()
    try:
        risiko = float(e.get("BOT_RISK_PERCENTAGE", "25")) / 100
    except ValueError:
        risiko = RISIKO_MIN
    try:
        be = max(float(e.get("BOT_BE_TRIGGER_PIPS", "50")), 0.0)
    except ValueError:
        be = 50.0
    return {"mode": "live" if mode == "live" else "demo", "risiko": min(max(risiko, RISIKO_MIN), RISIKO_MAKS),
            "be_pips": be}


def perlu_be(side, buka, sl, bid, ask, be_pips):
    """Auto break-even: floating profit >= be_pips dan SL belum di entry. -> (geser?, profit pips)"""
    pips = ((bid - buka) if side == "buy" else (buka - ask)) / PIP
    belum = (sl or 0) < buka if side == "buy" else (not sl or sl > buka)
    return bool(be_pips) and pips >= be_pips and belum, round(pips, 1)


def hari_wib(t):
    return dt.datetime.fromtimestamp(t + WIB, dt.timezone.utc).strftime("%Y-%m-%d")


def lot_untuk(sym, entry, sl, saldo, risiko):
    """Lot dinamis: Risk_Amount / (SL_pips x Pip_Value), dibulatkan ke bawah ke volume_step.
    -> (lot, rinci); lot None kalau lot minimum sudah melebihi Risk_Amount."""
    risk_amount = saldo * risiko
    sl_pips = abs(entry - sl) / PIP
    pip_value = PIP / sym.trade_tick_size * sym.trade_tick_value   # $ per pip per 1 lot
    rinci = {"risk_amount": round(risk_amount, 2), "sl_pips": round(sl_pips, 1), "pip_value": round(pip_value, 2)}
    if sl_pips <= 0 or pip_value <= 0:
        return None, rinci
    lot = math.floor(risk_amount / (sl_pips * pip_value) / sym.volume_step + 1e-9) * sym.volume_step
    if lot < sym.volume_min:
        return None, rinci
    lot = round(min(lot, sym.volume_max), 2)
    return lot, {**rinci, "rugi_di_sl": round(lot * sl_pips * pip_value, 2)}


def izin_akun(akun, cfg, valid, syarat_ok):
    if akun.trade_mode == 0:
        return True, "akun demo"
    if cfg["mode"] != "live":
        return False, "akun real ditolak: BOT_MODE bukan live"
    if not valid:
        return False, "akun real ditolak: strategi belum lulus validasi"
    if not syarat_ok:
        return False, "akun real ditolak: syarat real test demo belum lulus"
    return True, "akun real diizinkan"


def dekat_news(now, news):
    return any(abs(now - t) <= NEWS_MENIT * 60 for t in news)


def lewat_batal(s, bid, ask):
    """Harga sudah BATAL_FRAC jalan ke TP1 tanpa entry terisi."""
    batas = s["entry"] + (s["tp"][0] - s["entry"]) * BATAL_FRAC
    return ask >= batas if s["side"] == "buy" else bid <= batas


def req_limit(mt5, nama, s, lot, expire_s):
    return {"action": mt5.TRADE_ACTION_PENDING, "symbol": nama, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY_LIMIT if s["side"] == "buy" else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": s["entry"], "sl": s["sl"], "tp": s["tp"][0], "deviation": 20, "magic": MAGIC,
            "comment": s["id"][-31:], "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": mt5_link.ke_server(s["time"] + expire_s), "type_filling": mt5.ORDER_FILLING_RETURN}


def syarat_live(jurnal, winrate_bt):
    """Checklist real test dari trade demo yang sudah selesai (TP/SL/DITUTUP)."""
    sel = [x for x in jurnal if x["akun"] == "demo" and x.get("r") is not None
           and (x["status"] in ("TP", "SL", "BE") or str(x["status"]).startswith("DITUTUP"))]
    n = len(sel)
    rs = [x["r"] for x in sel]
    eq = puncak = 1.0
    dd = 0.0
    for x in sel:
        eq *= 1 + x["r"] * x["risiko"]
        puncak = max(puncak, eq)
        dd = max(dd, (puncak - eq) / puncak)
    win = sum(r > 0 for r in rs) / n if n else 0
    slip = [abs(x["harga_isi"] - x["entry"]) for x in sel if x.get("harga_isi") is not None]
    cek = {
        f"trade demo selesai >= {SYARAT_LIVE['trade']} ({n})": n >= SYARAT_LIVE["trade"],
        f"expectancy >= +{SYARAT_LIVE['expectancy']}R": bool(n) and sum(rs) / n >= SYARAT_LIVE["expectancy"],
        f"drawdown demo <= {SYARAT_LIVE['dd'] * 100:.0f}%": dd <= SYARAT_LIVE["dd"],
        "winrate demo dekat backtest": bool(n) and winrate_bt is not None and win >= winrate_bt - SYARAT_LIVE["beda_winrate"],
        f"slippage rata-rata <= {SYARAT_LIVE['slip']}": not slip or sum(slip) / len(slip) <= SYARAT_LIVE["slip"],
    }
    return cek, all(cek.values())


class Bot:
    def __init__(self, mt5, cfg, state, catat=None, cetak=print):
        self.mt5, self.cfg, self.st = mt5, cfg, state
        self.catat = catat or (lambda rows, status: None)
        self.cetak = lambda s: cetak(s, flush=True) if cetak is print else cetak(s)
        self.nama = None

    # ---- jurnal ----
    def _baris(self, s, **kw):
        """Baris jurnal untuk sinyal s (dibuat sekali), diperbarui dengan kw dan diantrikan ke Supabase."""
        j = self.st["jurnal"].get(s["id"]) or self.st["jurnal"].setdefault(s["id"], {
            "id": s["id"], "akun": {0: "demo", 1: "contest", 2: "real"}[self.akun.trade_mode], "login": self.akun.login,
            "simbol": self.nama, "strategi": s["strategi"], "side": s["side"], "lot": s.get("lot"),
            "risiko": self.cfg["risiko"], "entry": s["entry"], "harga_isi": None, "sl": s["sl"], "tp": s["tp"][0],
            "order_ticket": None, "posisi_ticket": None,
            "dibuat": dt.datetime.fromtimestamp(s["time"], dt.timezone.utc).isoformat(),
            "dibuka": None, "ditutup": None, "status": "PENDING", "pl": None, "r": None, "risiko_usd": None})
        j.update(kw)
        j["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self.ubah.append({k: v for k, v in j.items() if k not in PRIVAT})
        return j

    def _harian(self, now):
        h = self.st.setdefault("harian", {})
        if h.get("tanggal") != hari_wib(now):
            h.update(tanggal=hari_wib(now), sl=0, entry=0, pl=0.0)
        return h

    # ---- aksi ----
    def batalkan(self, j, alasan):
        r = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": j["order_ticket"]})
        ok = r is not None and r.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED)
        if ok:
            self._baris(j, status=f"BATAL: {alasan}")
            self.cetak(f"BOT BATAL {j['side'].upper()} {j['entry']}: {alasan}")
        else:
            self.cetak(f"BOT ERROR batal order {j['order_ticket']}: {getattr(r, 'comment', self.mt5.last_error())}")
        return ok

    def tutup_posisi(self, p, alasan):
        tick = self.mt5.symbol_info_tick(p.symbol)
        beli = p.type == 0
        r = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                                 "type": 1 if beli else 0, "position": p.ticket,
                                 "price": tick.bid if beli else tick.ask, "deviation": 30, "magic": MAGIC,
                                 "comment": alasan[:31], "type_filling": self.mt5.ORDER_FILLING_RETURN})
        self.cetak(f"BOT DITUTUP posisi {p.ticket}: {alasan} ({getattr(r, 'comment', '')})")

    def geser_be(self, j, p, pips):
        r = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket,
                                 "sl": p.price_open, "tp": p.tp, "magic": MAGIC})
        if r is None or r.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.cetak(f"BOT ERROR BE posisi {p.ticket}: {getattr(r, 'comment', self.mt5.last_error())}")
            return
        self._baris(j, sl=p.price_open, sl_awal=j.get("sl_awal", j["sl"]), be=True)
        self.cetak(f"BOT BE {j['side'].upper()} {j['entry']}: SL digeser ke entry {p.price_open} (profit {pips:+.1f} pips)")

    def stop(self):
        milik = {j.get("order_ticket"): j for j in self.st["jurnal"].values()}
        for o in self.mt5.orders_get(magic=MAGIC) or []:
            if o.ticket in milik:
                self.batalkan(milik[o.ticket], "kill switch")
            else:
                self.mt5.order_send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        for p in self.mt5.positions_get() or []:
            if getattr(p, "magic", None) == MAGIC:
                self.tutup_posisi(p, "kill switch")
        self.cetak("BOT STOP: kill switch aktif, semua order/posisi bot dibatalkan/ditutup")

    def sinkron(self, now):
        """Perbarui jurnal dari order, posisi, dan riwayat deal milik bot."""
        h = self._harian(now)
        orders = {o.ticket: o for o in (self.mt5.orders_get(magic=MAGIC) or [])}
        posisi = {p.identifier: p for p in (self.mt5.positions_get() or []) if getattr(p, "magic", None) == MAGIC}
        tick = self.mt5.symbol_info_tick(self.nama)
        for j in list(self.st["jurnal"].values()):
            if j["status"] == "PENDING":
                if j["order_ticket"] in orders:
                    if lewat_batal(j | {"tp": [j["tp"]]}, tick.bid, tick.ask):
                        self.batalkan(j, "harga sudah 70% ke TP1 tanpa entry")
                    elif dekat_news(now, self.news):
                        self.batalkan(j, f"jendela news {NEWS_MENIT} menit")
                    continue
                p = posisi.get(j["order_ticket"])
                if p is not None:
                    self._baris(j, status="TERBUKA", posisi_ticket=p.ticket, harga_isi=p.price_open,
                                dibuka=dt.datetime.fromtimestamp(p.time, dt.timezone.utc).isoformat())
                    h["entry"] += 1
                    self.cetak(f"BOT TERISI {j['side'].upper()} {j['lot']} lot @ {p.price_open} (rencana {j['entry']}, "
                               f"SL {j['sl']}, TP {j['tp']})")
                    continue
                deals = [d for d in (self.mt5.history_deals_get(position=j["order_ticket"]) or [])]
                if not deals:
                    self._baris(j, status="BATAL: kedaluwarsa tanpa terisi")
                    self.cetak(f"BOT BATAL {j['side'].upper()} {j['entry']}: kedaluwarsa tanpa terisi")
                    continue
                j.update(posisi_ticket=j["order_ticket"], status="TERBUKA")
                h["entry"] += 1   # terisi dan sudah tutup di antara dua putaran
            hidup = {p.ticket: p for p in posisi.values()}
            if j["status"] == "TERBUKA" and j["posisi_ticket"] in hidup:
                p = hidup[j["posisi_ticket"]]
                geser, pips = perlu_be(j["side"], p.price_open, p.sl, tick.bid, tick.ask, self.cfg["be_pips"])
                if geser:
                    self.geser_be(j, p, pips)
                continue
            if j["status"] == "TERBUKA":
                deals = self.mt5.history_deals_get(position=j["posisi_ticket"]) or []
                if not deals:
                    continue
                pl = sum(d.profit + getattr(d, "commission", 0) + getattr(d, "swap", 0) for d in deals)
                keluar = [d for d in deals if getattr(d, "entry", 1) == 1]
                harga = keluar[-1].price if keluar else None
                isi = [d for d in deals if getattr(d, "entry", 0) == 0]
                if isi and j["harga_isi"] is None:
                    j["harga_isi"] = isi[0].price
                hasil = "TP" if harga is not None and abs(harga - j["tp"]) <= abs(harga - j["sl"]) else "SL"
                if hasil == "SL" and j.get("be"):
                    hasil = "BE"
                if harga is not None and min(abs(harga - j["tp"]), abs(harga - j["sl"])) > 1.0:
                    hasil = "DITUTUP: manual atau kill switch"
                sl0 = j.get("sl_awal", j["sl"])
                risiko_usd = j.get("risiko_usd") or lot_untuk(self.mt5.symbol_info(self.nama), j["entry"], sl0, 0, 0)[1][
                    "pip_value"] * abs(j["entry"] - sl0) / PIP * (j["lot"] or 0)
                r = round(pl / risiko_usd, 2) if risiko_usd else None
                self._baris(j, status=hasil, pl=r2(pl), r=r, ditutup=dt.datetime.fromtimestamp(
                    keluar[-1].time if keluar else now, dt.timezone.utc).isoformat())
                h["pl"] += pl
                if hasil == "SL":
                    h["sl"] += 1
                self.cetak(f"BOT {hasil.split(':')[0]} {j['side'].upper()} {j['entry']}: P/L {pl:+.2f} {self.akun.currency} "
                           f"({'-' if r is None else f'{r:+.2f}'}R)")

    def boleh_order(self, now, spread_now, spread_med):
        h = self._harian(now)
        hidup = [j for j in self.st["jurnal"].values() if j["status"] in ("PENDING", "TERBUKA")]
        if h["sl"] >= MAKS_SL_HARIAN:
            return False, f"batas harian: {h['sl']} SL hari ini"
        if h["entry"] >= MAKS_ENTRY_HARIAN:
            return False, f"batas harian: {h['entry']} entry hari ini"
        if len(hidup) >= MAKS_TERBUKA:
            return False, "masih ada order/posisi bot terbuka"
        if dekat_news(now, self.news):
            return False, f"jendela news {NEWS_MENIT} menit"
        if spread_med and spread_now > SPREAD_X * spread_med:
            return False, f"spread {spread_now} > {SPREAD_X}x median {spread_med:.0f}"
        return True, ""

    def pasang(self, s, expire_s, info, syarat_ok, now):
        izin, alasan = izin_akun(self.akun, self.cfg, info["valid"], syarat_ok)
        if not izin:
            return self.lewati(s, alasan)
        sym = self.mt5.symbol_info(self.nama)
        saldo = self.akun.balance
        lot, rc = lot_untuk(sym, s["entry"], s["sl"], saldo, self.cfg["risiko"])
        if lot is None:
            return self.lewati(s, f"SL {rc['sl_pips']} pips: lot minimum {sym.volume_min} rugi lebih dari "
                                  f"{rc['risk_amount']} ({self.cfg['risiko'] * 100:.0f}% saldo)")
        tick = self.mt5.symbol_info_tick(self.nama)
        if lewat_batal(s, tick.bid, tick.ask):
            return self.lewati(s, "harga sudah 70% ke TP1")
        if (s["side"] == "buy" and tick.ask <= s["entry"]) or (s["side"] == "sell" and tick.bid >= s["entry"]):
            return self.lewati(s, "harga sudah melewati entry (limit akan langsung jadi market)")
        s["lot"] = lot
        r = self.mt5.order_send(req_limit(self.mt5, self.nama, s, lot, expire_s))
        if r is None or r.retcode not in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED):
            self.cetak(f"BOT ERROR order {s['id']}: {getattr(r, 'comment', self.mt5.last_error())}")
            self.st["lewati"].append(s["id"])
            return
        self._baris(s, order_ticket=r.order, lot=lot, risiko_usd=rc["rugi_di_sl"])
        self.cetak(f"BOT ORDER {s['side'].upper()} LIMIT {lot} lot @ {s['entry']} | SL {s['sl']} | TP {s['tp'][0]} | "
                   f"{info['nama']} {'VALID' if info['valid'] else 'uji coba'} | akun "
                   f"{'demo' if self.akun.trade_mode == 0 else 'REAL'} | lot {lot} = {rc['risk_amount']:.2f} / "
                   f"({rc['sl_pips']} pips x {rc['pip_value']:.2f}) | rugi di SL {rc['rugi_di_sl']:.2f} {self.akun.currency} "
                   f"({self.cfg['risiko'] * 100:.0f}% saldo {saldo:,.2f})")

    def lewati(self, s, alasan):
        if s["id"] not in self.st["lewati"]:
            self.st["lewati"].append(s["id"])
            self.cetak(f"BOT LEWATI {s['side'].upper()} {s['entry']}: {alasan}")

    def putaran(self, now, sinyal, news, spread_med, winrate_bt):
        """sinyal: [(s dengan id+strategi, expire_s, info)] urut prioritas."""
        self.news, self.ubah = news, []
        self.akun = self.mt5.account_info()
        if os.path.exists(STOP_FILE):
            self.stop()
            return "stop"
        self.sinkron(now)
        cek, syarat_ok = syarat_live(list(self.st["jurnal"].values()), winrate_bt)
        sym = self.mt5.symbol_info(self.nama)
        ok, alasan = self.boleh_order(now, sym.spread, spread_med)
        for s, expire_s, info in sinyal:
            if s["id"] in self.st["jurnal"] or s["id"] in self.st["lewati"]:
                continue
            if not ok:
                self.lewati(s, alasan)
                continue
            self.pasang(s, expire_s, info, syarat_ok, now)
            ok, alasan = self.boleh_order(now, sym.spread, spread_med)
        self.st["lewati"] = self.st["lewati"][-500:]
        h = self._harian(now)
        hidup = [{k: j[k] for k in ("id", "side", "lot", "entry", "sl", "tp", "status")} for j in self.st["jurnal"].values()
                 if j["status"] in ("PENDING", "TERBUKA")]
        status = {"login": self.akun.login, "akun": {0: "demo", 1: "contest", 2: "real"}[self.akun.trade_mode],
                  "server": self.akun.server, "simbol": self.nama, "mode": self.cfg["mode"], "risiko": self.cfg["risiko"],
                  "ekuitas": r2(self.akun.equity), "saldo": r2(self.akun.balance), "pl_hari_ini": r2(h["pl"]),
                  "sl_hari_ini": h["sl"], "entry_hari_ini": h["entry"], "terbuka": hidup,
                  "pengaman": {"news": dekat_news(now, news), "boleh_order": ok, "alasan": alasan,
                               "spread": sym.spread, "spread_median": spread_med, "kill_switch": False},
                  "syarat_live": cek, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.catat(self.ubah, status)
        return status


# ---- sambungan ke data, strategi, Supabase ----
def sinyal_sekarang(by, now, seen_fn):
    """Sinyal semua strategi layak dari candle broker yang sudah tutup, urut peringkat pantau.py."""
    import pantau
    closed = {tf: [r for r in rows if r[0] + STEP[tf] <= now and r[0] >= now - HARI_DATA * 86400] for tf, rows in by.items()}
    ada_taker = len(closed["1m"][-1]) > 6 if closed["1m"] else False
    out = []
    for nama in pantau.STRATEGI:
        mod = importlib.import_module(f"strategi.{nama}")
        # live butuh lulus validasi di data broker; demo cukup layak di salah satu sumber
        broker, umum = pantau.info_validasi(nama, "mt5"), pantau.info_validasi(nama)
        info = {"valid": broker["valid"], "layak": broker["layak"] or umum["layak"], "nama": pantau.NAMA.get(nama, nama)}
        if not info["layak"]:
            continue
        p = dict(mod.PARAMS)
        if p.get("delta") and not ada_taker:
            p["delta"] = False   # candle broker tanpa sisi agresor; varian tanpa delta (lihat laporan validasi mt5)
        exp_s = getattr(mod, "EXPIRE_S", 3600)
        for s in mod.signals(closed, pantau.MODE, p):
            if s["time"] < now - exp_s:
                continue
            terisi, hasil = pantau.lacak(s, closed["1m"], now, exp_s)
            if terisi or hasil or seen_fn(pantau.sid(nama, s)):
                continue
            out.append(({**s, "id": pantau.sid(nama, s), "strategi": nama}, exp_s, info))
    out.sort(key=lambda x: (x[2]["valid"], x[0].get("skor", 0)), reverse=True)
    return out


def news_high(now):
    import kalender
    try:
        return [dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp()
                for e in kalender.fetch(2, 1) if e.get("importance") == 1]
    except Exception as e:  # kalender gagal: tidak ada jendela news, dicatat
        print(f"BOT ERROR kalender: {e}", flush=True)
        return []


def catat_supabase(rows, status):
    import publish
    try:
        if rows:
            publish._post("bot_trades", list({r["id"]: r for r in rows}.values()), publish.MERGE)
        publish._post("bot_status", [status], publish.MERGE)
    except Exception as e:
        print(f"BOT ERROR supabase: {str(e)[:200]}", flush=True)


def winrate_backtest():
    import pantau
    import glob
    f = sorted(x for x in glob.glob(os.path.join(ROOT, "data", "backtest", "validasi", "sniper_*.json")) if not x.endswith("_trades.json"))
    return json.load(open(f[-1], encoding="utf-8"))["oos"]["winrate"] if f else None


def baca_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"jurnal": {}, "lewati": [], "harian": {}}


def simpan_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, STATE_FILE)


def main(args):
    import data
    import mt5_link
    e = data.env()
    cfg = konfig(e)
    try:
        akun = mt5_link.sambung(e)
    except RuntimeError as x:
        sys.exit(str(x))
    mt5 = mt5_link.modul()
    nama = mt5_link.simbol_emas(e)
    st = baca_state()
    bot = Bot(mt5, cfg, st, catat_supabase)
    bot.nama = nama
    if "--stop" in args:
        open(STOP_FILE, "w").close()
        bot.akun = akun
        bot.ubah = []
        bot.stop()
        simpan_state(st)
        return
    if "--status" in args:
        s = mt5.symbol_info(nama)
        cek, ok = syarat_live(list(st["jurnal"].values()), winrate_backtest())
        ukur, cocok = mt5_link.cek_offset(nama)
        print(f"jam server: {'aturan GMT+' + str(mt5_link.offset_server(time.time()) // 3600)}"
              f"{'' if ukur is None else f', terukur GMT+{ukur}'}{'' if cocok else '  <-- TIDAK COCOK, cek offset_server'}")
        print(f"akun {mt5_link.JENIS.get(akun.trade_mode)} {akun.server}, saldo {akun.balance} {akun.currency}, "
              f"ekuitas {akun.equity}, mode bot {cfg['mode']}, risiko {cfg['risiko'] * 100:.0f}% saldo = "
              f"{akun.balance * cfg['risiko']:,.2f} {akun.currency}/trade, auto BE {format(cfg['be_pips'], 'g') + ' pips' if cfg['be_pips'] else 'mati'}, simbol {nama} spread {s.spread}, "
              f"algo trading {'ON' if mt5.terminal_info().trade_allowed else 'OFF'}")
        for k, v in cek.items():
            print(f"  [{'LULUS' if v else 'BELUM'}] {k}")
        return
    if "--uji-order" in args:
        if akun.trade_mode != 0:
            sys.exit("--uji-order hanya untuk akun demo.")
        s, tick = mt5.symbol_info(nama), mt5.symbol_info_tick(nama)
        harga = r2(tick.bid - 50)
        req = req_limit(mt5, nama, {"side": "buy", "entry": harga, "sl": r2(harga - 3.5), "tp": [r2(harga + 10.5)],
                                    "id": "uji-order", "time": int(time.time())}, s.volume_min, 600)
        r = mt5.order_send(req)
        print(f"uji order: retcode {getattr(r, 'retcode', None)} {getattr(r, 'comment', mt5.last_error())}, ticket {getattr(r, 'order', None)}")
        if r is not None and r.order:
            time.sleep(3)
            r2_ = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": r.order})
            print(f"batal: retcode {getattr(r2_, 'retcode', None)} {getattr(r2_, 'comment', '')}")
        return
    if os.path.exists(STOP_FILE):
        sys.exit(f"Kill switch aktif ({STOP_FILE}). Hapus file itu untuk menjalankan bot lagi.")
    print(f"BOT MULAI akun {mt5_link.JENIS.get(akun.trade_mode)} {akun.server}, simbol {nama}, risiko {cfg['risiko'] * 100:.0f}% "
          f"saldo = {akun.balance * cfg['risiko']:,.2f} {akun.currency}/trade (lot dinamis dari lebar SL), "
          f"{'auto BE di +' + format(cfg['be_pips'], 'g') + ' pips' if cfg['be_pips'] else 'auto BE mati'}", flush=True)
    gagal, news, news_t = None, [], 0
    while True:
        now = int(time.time())
        try:
            if now - news_t > 1800:
                news, news_t = news_high(now), now
            by = data.load("XAUUSD", TFS, refresh=True, source="mt5")
            spread_med = mt5.symbol_info(nama).spread
            sig = sinyal_sekarang(by, now, lambda i: i in st["jurnal"] or i in st["lewati"])
            hasil = bot.putaran(now, sig, news, st.setdefault("spread_med", spread_med), winrate_backtest())
            st["spread_med"] = round(0.98 * st["spread_med"] + 0.02 * mt5.symbol_info(nama).spread, 2)
            simpan_state(st)
            if gagal:
                print("BOT PULIH: koneksi normal lagi", flush=True)
            gagal = None
            if hasil == "stop":
                return
        except Exception as x:  # satu putaran gagal tidak menghentikan bot; error sama dilaporkan sekali
            if type(x).__name__ != gagal:
                print(f"BOT ERROR {type(x).__name__}: {str(x)[:200]}", flush=True)
            gagal = type(x).__name__
        if "--sekali" in args:
            return
        time.sleep(60 - time.time() % 60 + 5)


def _selftest():
    import mt5_link
    from types import SimpleNamespace as N
    P = mt5_link.Palsu
    sym = P().sym
    # contoh user: saldo $100, 25% = $25; SL 50 pips -> 0.05 lot; SL 100 pips -> 0.025 -> 0.02; 30% & 50 pips -> 0.06
    lot, rc = lot_untuk(sym, 4100.0, 4105.0, 100, 0.25)
    assert lot == 0.05 and rc == {"risk_amount": 25.0, "sl_pips": 50.0, "pip_value": 10.0, "rugi_di_sl": 25.0}, rc
    assert lot_untuk(sym, 4100.0, 4090.0, 100, 0.25)[0] == 0.02 and lot_untuk(sym, 4100.0, 4105.0, 100, 0.30)[0] == 0.06
    assert lot_untuk(sym, 4100.0, 4140.0, 100, 0.25)[0] is None                     # SL 400 pips: lot min > $25
    assert [konfig({"BOT_RISK_PERCENTAGE": v})["risiko"] for v in ("10", "60", "abc", "27")] == [0.25, 0.30, 0.25, 0.27]
    assert konfig({})["risiko"] == 0.25 and konfig({})["mode"] == "demo"
    real, demo = N(trade_mode=2), N(trade_mode=0)
    assert izin_akun(demo, konfig({}), False, False)[0]
    assert not izin_akun(real, konfig({"BOT_MODE": "live"}), False, True)[0]
    assert not izin_akun(real, konfig({}), True, True)[0]
    assert not izin_akun(real, konfig({"BOT_MODE": "live"}), True, False)[0]
    assert izin_akun(real, konfig({"BOT_MODE": "live"}), True, True)[0]
    s = {"id": "sniper:1000-sell-4119.5", "strategi": "sniper", "time": 1000, "side": "sell", "entry": 4119.5,
         "sl": 4122.5, "tp": [4109.0]}
    assert lewat_batal(s, 4112.0, 4112.2) and not lewat_batal(s, 4113.0, 4113.2)    # batas 70% = 4112.15
    m = P()
    req = req_limit(m, "XAUUSD", s, 0.66, 3600)
    assert req["type"] == m.ORDER_TYPE_SELL_LIMIT and req["price"] == 4119.5 and req["sl"] == 4122.5 and req["tp"] == 4109.0
    assert req["expiration"] == mt5_link.ke_server(4600) and req["magic"] == MAGIC and len(req["comment"]) <= 31
    # alur: order terpasang lalu dibatalkan karena harga 70% ke TP
    keluar = []
    st = {"jurnal": {}, "lewati": [], "harian": {}}
    m.akun.balance = 100.0   # $25 / (30 pips x $10) = 0.083 -> 0.08 lot
    bot = Bot(m, konfig({}), st, cetak=keluar.append)
    bot.nama = "XAUUSD"
    m.symbol_info_tick = lambda n: N(bid=4115.0, ask=4115.2)
    info = {"valid": False, "nama": "Sniper 1m"}
    now = 1500
    bot.putaran(now, [(dict(s), 3600, info)], [], 20, 0.5)
    assert any(x.startswith("BOT ORDER SELL LIMIT 0.08") and "rugi di SL 24.00" in x for x in keluar), keluar
    assert st["jurnal"][s["id"]]["risiko_usd"] == 24.0 and "risiko_usd" not in bot.ubah[-1]
    assert st["jurnal"][s["id"]]["status"] == "PENDING" and len(m.order) == 1
    m.symbol_info_tick = lambda n: N(bid=4112.0, ask=4112.2)
    bot.putaran(now + 60, [], [], 20, 0.5)
    assert st["jurnal"][s["id"]]["status"].startswith("BATAL: harga sudah 70%") and not m.order, st["jurnal"]
    # akun real tanpa izin: dilewati, tidak ada order
    m2 = P(trade_mode=2)
    st2 = {"jurnal": {}, "lewati": [], "harian": {}}
    k2 = []
    b2 = Bot(m2, konfig({}), st2, cetak=k2.append)
    b2.nama = "XAUUSD"
    m2.symbol_info_tick = lambda n: N(bid=4115.0, ask=4115.2)
    b2.putaran(now, [(dict(s), 3600, info)], [], 20, 0.5)
    assert not m2.kirim and any("akun real ditolak" in x for x in k2), k2
    # batas harian dan jendela news
    st3 = {"jurnal": {}, "lewati": [], "harian": {"tanggal": hari_wib(now), "sl": 2, "entry": 2, "pl": -400}}
    b3 = Bot(P(), konfig({}), st3, cetak=[].append)
    b3.news = []
    assert not b3.boleh_order(now, 20, 20)[0]
    st3["harian"]["sl"] = 0
    b3.news = [now + 600]
    assert not b3.boleh_order(now, 20, 20)[0] and b3.boleh_order(now + 3600, 20, 20)[0]
    assert not b3.boleh_order(now + 3600, 100, 20)[0]                                   # spread melebar
    # skenario user: $100, 25%, BUY SL 50 pips -> 0.05 lot; +49 pips belum BE, +50 pips SL ke entry; balik -> BE
    assert konfig({})["be_pips"] == 50 and konfig({"BOT_BE_TRIGGER_PIPS": "0"})["be_pips"] == 0
    assert perlu_be("sell", 4100.0, 4105.0, 4094.8, 4095.0, 50) == (True, 50.0)
    assert not perlu_be("sell", 4100.0, 4100.0, 4094.8, 4095.0, 50)[0] and not perlu_be("buy", 4100, 4095, 4105, 4105.2, 0)[0]
    m4, k4 = P(), []
    m4.akun.balance = 100.0
    st4 = {"jurnal": {}, "lewati": [], "harian": {}}
    b4 = Bot(m4, konfig({}), st4, cetak=k4.append)
    b4.nama = "XAUUSD"
    s4 = {"id": "sniper:2000-buy-4100.0", "strategi": "sniper", "time": 2000, "side": "buy", "entry": 4100.0,
          "sl": 4095.0, "tp": [4112.0]}
    m4.symbol_info_tick = lambda n: N(bid=4102.0, ask=4102.2)
    b4.putaran(2000, [(dict(s4), 3600, info)], [], 20, 0.5)
    j4 = st4["jurnal"][s4["id"]]
    assert j4["lot"] == 0.05 and "rugi di SL 25.00" in k4[-1], k4
    m4.order = []
    m4.posisi = [N(ticket=j4["order_ticket"], identifier=j4["order_ticket"], magic=MAGIC, price_open=4100.0, sl=4095.0,
                   tp=4112.0, symbol="XAUUSD", type=0, volume=0.05, time=2100)]
    for bid in (4104.9, 4104.9, 4105.0, 4105.0):
        m4.symbol_info_tick = lambda n, b=bid: N(bid=b, ask=b + 0.2)
        b4.putaran(2200, [], [], 20, 0.5)
    sltp = [x for x in m4.kirim if x["action"] == m4.TRADE_ACTION_SLTP]
    assert len(sltp) == 1 and sltp[0]["sl"] == 4100.0 and sltp[0]["tp"] == 4112.0, m4.kirim
    assert j4["be"] and j4["sl"] == 4100.0 and j4["sl_awal"] == 4095.0 and any(x.startswith("BOT BE BUY") for x in k4)
    assert "be" not in b4.ubah[-1] if b4.ubah else True
    m4.posisi = []
    m4.deal = [N(entry=0, price=4100.0, profit=0.0, time=2100), N(entry=1, price=4100.0, profit=0.0, time=2400)]
    b4.putaran(2400, [], [], 20, 0.5)
    assert j4["status"] == "BE" and j4["r"] == 0 and st4["harian"]["sl"] == 0, j4
    # syarat live
    j = [{"akun": "demo", "status": "TP" if i % 2 else "SL", "r": 3.0 if i % 2 else -1.0, "risiko": 0.02,
          "harga_isi": 1.0, "entry": 1.1} for i in range(30)]
    cek, ok = syarat_live(j, 0.55)
    assert ok, cek
    assert not syarat_live(j[:10], 0.55)[1]
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(sys.argv[1:])
