"""Kontrol EA SniperBot di MT5 dari Claude: kirim perintah lewat file di Common\\Files, tunggu ack, cetak status.

Pakai:  python ea_kontrol.py status          status terbaru (saldo, posisi, order, risk/BE aktif, alasan)
        python ea_kontrol.py pause           tidak ada order baru; posisi tetap dikelola (BE, batal limit)
        python ea_kontrol.py resume
        python ea_kontrol.py stop            batalkan order EA, tutup posisi EA, lalu pause
        python ea_kontrol.py risk 27         risiko 25-30% saldo
        python ea_kontrol.py be 60           auto BE di +60 pips (0 = mati)
        python ea_kontrol.py reset           risk/BE kembali ke input EA
EA membaca perintah tiap 2 detik (OnTimer); override risk/BE/pause disimpan di global variable terminal.
Self-check: python ea_kontrol.py --selftest
"""
import json
import os
import sys
import time

FOLDER = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files")
CMD, STATUS = "sniperbot_cmd.txt", "sniperbot_status.json"
PERINTAH = {"status": "STATUS", "pause": "PAUSE", "resume": "RESUME", "stop": "STOP", "reset": "RESET"}


def baris_perintah(args):
    """['risk', '27'] -> 'RISK=27'; None kalau tidak dikenal."""
    if not args:
        return "STATUS"
    k = args[0].lower()
    if k in PERINTAH and len(args) == 1:
        return PERINTAH[k]
    if k in ("risk", "be") and len(args) == 2:
        try:
            float(args[1])
        except ValueError:
            return None
        return f"{k.upper()}={args[1]}"
    return None


def baca_status(folder=FOLDER):
    try:
        return json.load(open(os.path.join(folder, STATUS), encoding="ascii", errors="replace"))
    except (OSError, ValueError):
        return None


def kirim(cmd, folder=FOLDER, tunggu=15.0):
    """Tulis 'id|cmd', tunggu status dengan ack == id. -> status atau None kalau EA tidak menjawab."""
    i = str(int(time.time() * 1000))
    tmp = os.path.join(folder, CMD + ".tmp")
    open(tmp, "w", encoding="ascii").write(f"{i}|{cmd}\n")
    os.replace(tmp, os.path.join(folder, CMD))
    batas = time.time() + tunggu
    while time.time() < batas:
        st = baca_status(folder)
        if st and st.get("ack") == i:
            return st
        time.sleep(0.5)
    return None


def cetak(st):
    umur = time.time() - st["waktu_utc"]
    print(f"EA SniperBot ({umur:.0f} dtk lalu): saldo {st['saldo']:.2f} {st['mata_uang']}, ekuitas {st['ekuitas']:.2f}")
    print(f"  algo trading {'ON' if st['algo_trading'] else 'OFF (nyalakan tombol Algo Trading di MT5)'}, "
          f"{'DIJEDA' if st['dijeda'] else 'aktif'}, risiko {st['risk_persen']:.1f}% saldo, "
          f"auto BE {'+%.0f pips' % st['be_pips'] if st['be_pips'] else 'mati'}")
    print(f"  order baru: {'boleh' if st['boleh_order'] else 'ditahan: ' + st['alasan']}; hari ini {st['sl_hari_ini']} SL, "
          f"{st['entry_hari_ini']} entry; POI aktif {st['poi_aktif']}")
    for p in st["posisi"]:
        print(f"  POSISI {p['side'].upper()} {p['lot']} lot @ {p['buka']} SL {p['sl']} TP {p['tp']} profit {p['profit']:+.2f}")
    for o in st["order"]:
        print(f"  ORDER {o['side'].upper()} LIMIT {o['lot']} lot @ {o['entry']} SL {o['sl']} TP {o['tp']}")
    if st.get("log_terakhir"):
        print(f"  log terakhir: {st['log_terakhir']}")


def main(args):
    cmd = baris_perintah(args)
    if cmd is None:
        sys.exit(__doc__)
    st = kirim(cmd)
    if st is None:
        lama = baca_status()
        sys.exit("EA tidak menjawab dalam 15 detik: cek MT5 terbuka dan SniperBot terpasang di chart XAUUSD."
                 + (f" Status terakhir {time.time() - lama['waktu_utc']:.0f} dtk lalu." if lama else ""))
    if st["hasil"] != "status":
        print(f"{cmd}: {st['hasil']}")
    cetak(st)


def _selftest():
    import tempfile
    import threading
    assert baris_perintah([]) == "STATUS" and baris_perintah(["risk", "27"]) == "RISK=27"
    assert baris_perintah(["be", "x"]) is None and baris_perintah(["hapus"]) is None and baris_perintah(["stop"]) == "STOP"
    d = tempfile.mkdtemp()

    def ea_palsu():   # meniru OnTimer: baca perintah, hapus, tulis status + ack
        p = os.path.join(d, CMD)
        while not os.path.exists(p):
            time.sleep(0.05)
        i, cmd = open(p).read().strip().split("|")
        os.remove(p)
        json.dump({"ack": i, "hasil": f"ok {cmd}", "waktu_utc": time.time()}, open(os.path.join(d, STATUS), "w"))
    threading.Thread(target=ea_palsu, daemon=True).start()
    st = kirim("PAUSE", d, tunggu=5)
    assert st and st["hasil"] == "ok PAUSE", st
    assert kirim("STATUS", d, tunggu=1) is None   # tidak ada EA yang menjawab
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        main(sys.argv[1:])
