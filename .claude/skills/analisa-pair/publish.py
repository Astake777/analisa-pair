"""Kirim hasil skill ke Supabase (REST, service role) sesuai supabase/migrations/*_init.sql.

Pakai:
  python publish.py analysis <PAIR> <mode> <payload.json>   1 baris ke analyses
  python publish.py outlook <PAIR>                          outlook.build -> upsert news_outlook
  python publish.py backtest <results.json>                 list baris (atau {"rows": [...]}) ke backtest_results
  python publish.py makro                                   makro.fetch_all -> upsert macro_series
  python publish.py skenario <PAIR> [--jam JAM]             skenario_news.build -> PATCH news_outlook.skenario/pendukung
Kredensial: E:/Trade Folders/.env (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY); env var asli menang.
Self-check: python publish.py --selftest
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env")
CHUNK = 500
MERGE = "resolution=merge-duplicates,return=minimal"
OPENER = urllib.request.urlopen  # diganti di selftest


def load_env(path=ENV_FILE):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update(os.environ)
    return env


def _creds():
    env = load_env()
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not env.get(k)]
    if missing:
        sys.exit(f"publish: {', '.join(missing)} belum diisi. Salin .env.example ke .env di root repo lalu isi.")
    return env["SUPABASE_URL"].rstrip("/"), env["SUPABASE_SERVICE_ROLE_KEY"]


def _headers(key, prefer):
    h = {"apikey": key, "Content-Type": "application/json", "Prefer": prefer}
    if key.startswith("eyJ"):  # kunci lama berupa JWT; kunci baru sb_secret_ ditolak di Authorization
        h["Authorization"] = f"Bearer {key}"
    return h


def _post(table, rows, prefer, query=""):
    url, key = _creds()
    out = []
    for i in range(0, len(rows), CHUNK):
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}{query}", data=json.dumps(rows[i:i + CHUNK]).encode(), method="POST",
            headers=_headers(key, prefer))
        try:
            body = OPENER(req, timeout=60).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Supabase {table}: HTTP {e.code} {e.read().decode(errors='replace')}") from None
        if body:
            out += json.loads(body)
    return out


def insert_analysis(pair, mode, payload):
    row = {"pair": pair.upper(), "mode": mode, "status": payload["status"],
           "keyakinan": payload.get("keyakinan"), "price": payload.get("price"), "payload": payload}
    return _post("analyses", [row], "return=representation")[0]


def upsert_outlook(rows):
    now = dt.datetime.now(dt.timezone.utc).isoformat()  # default kolom hanya berlaku saat insert
    rows = [dict(r, updated_at=now) for r in rows]
    # event yang sudah rilis tidak mengirim "lean", supaya lean sebelum rilis tetap tersimpan untuk dinilai
    pre = [r for r in rows if r.get("actual") is None]
    post = [{k: v for k, v in r.items() if k != "lean"} for r in rows if r.get("actual") is not None]
    return _post("news_outlook", pre, MERGE) + _post("news_outlook", post, MERGE)


MIGRASI = ("publish: kolom news_outlook.skenario/pendukung belum ada. Jalankan isi file "
           "supabase/migrations/20261008010000_skenario.sql di Supabase SQL editor, lalu ulangi.")


def _kirim(req):
    try:
        return OPENER(req, timeout=60).read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        if any(k in msg for k in ("PGRST204", "42703", "Could not find the", "does not exist")):
            sys.exit(MIGRASI)
        raise RuntimeError(f"Supabase news_outlook: HTTP {e.code} {msg}") from None


def upsert_skenario(rows):
    """PATCH per id (hanya skenario, pendukung, updated_at); upsert parsial gagal karena kolom NOT NULL lain.
    Mengembalikan jumlah baris yang ada di news_outlook dan diperbarui."""
    url, key = _creds()
    # cek kolom dulu, supaya pesan migrasi muncul walau tidak ada event
    _kirim(urllib.request.Request(f"{url}/rest/v1/news_outlook?select=id,skenario,pendukung&limit=1",
                                  headers=_headers(key, "return=minimal")))
    now, n = dt.datetime.now(dt.timezone.utc).isoformat(), 0
    for r in rows:
        body = {"skenario": r["skenario"], "pendukung": r["pendukung"], "updated_at": now}
        req = urllib.request.Request(
            f"{url}/rest/v1/news_outlook?id=eq.{urllib.parse.quote(r['id'])}&select=id",
            data=json.dumps(body).encode(), method="PATCH", headers=_headers(key, "return=representation"))
        n += len(json.loads(_kirim(req) or b"[]"))
    return n


def insert_backtest(rows):
    return _post("backtest_results", rows, "return=minimal")


def upsert_setup_log(rows):
    return _post("setup_log", rows, MERGE)


def upsert_macro(rows):
    return _post("macro_series", rows, MERGE, "?on_conflict=series,date")


def _selftest():
    global OPENER, load_env
    calls = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

    def fake(req, timeout):
        calls.append((req.full_url, dict(req.header_items()), json.loads(req.data) if req.data else None))
        return Resp(b'[{"id": "u1"}]' if "representation" in req.get_header("Prefer") else b"")

    OPENER = fake
    load_env = lambda path=None: {"SUPABASE_URL": "https://x.supabase.co/", "SUPABASE_SERVICE_ROLE_KEY": "k"}
    r = insert_analysis("xauusd", "intraday", {"status": "TUNGGU NEWS", "keyakinan": "rendah", "price": 4151.5})
    url, h, body = calls[-1]
    assert r == {"id": "u1"} and url == "https://x.supabase.co/rest/v1/analyses", (r, url)
    assert h["Apikey"] == "k" and "Authorization" not in h and h["Content-type"] == "application/json", h
    assert _headers("eyJabc", "x")["Authorization"] == "Bearer eyJabc"
    assert body[0]["pair"] == "XAUUSD" and body[0]["price"] == 4151.5 and body[0]["payload"]["status"] == "TUNGGU NEWS"
    upsert_macro([{"series": "DFII10", "date": f"2026-01-{i % 28 + 1:02d}", "value": i} for i in range(1201)])
    assert len(calls) == 4 and [len(c[2]) for c in calls[1:]] == [500, 500, 201], [len(c[2]) for c in calls]
    assert calls[-1][0].endswith("/macro_series?on_conflict=series,date") and calls[-1][1]["Prefer"] == MERGE
    upsert_outlook([{"id": "1", "title": "NFP", "actual": None, "lean": {"arah": "BUY"}},
                    {"id": "2", "title": "CPI", "actual": 0.3, "lean": None}])
    assert calls[-2][0].endswith("/news_outlook") and "updated_at" in calls[-2][2][0] and "lean" in calls[-2][2][0]
    assert calls[-1][2][0]["id"] == "2" and "lean" not in calls[-1][2][0], calls[-1][2]

    sk = [{"id": "397068", "skenario": [{"nama": "panas"}], "pendukung": {"aset": []}, "pair": "XAUUSD"},
          {"id": "a b", "skenario": [], "pendukung": {}}]
    assert upsert_skenario(sk) == 2
    (u0, h0, b0), (u1, h1, b1) = calls[-3], calls[-2]
    assert u0.endswith("/news_outlook?select=id,skenario,pendukung&limit=1") and b0 is None, calls[-3]
    assert u1.endswith("/news_outlook?id=eq.397068&select=id") and calls[-1][0].endswith("id=eq.a%20b&select=id")
    assert set(b1) == {"skenario", "pendukung", "updated_at"} and b1["skenario"] == [{"nama": "panas"}], b1
    assert h1["Prefer"] == "return=representation"

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, __import__("io").BytesIO(b'{"code":"23505"}'))

    def tanpa_kolom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, __import__("io").BytesIO(
            b'{"code":"42703","message":"column news_outlook.skenario does not exist"}'))
    OPENER = tanpa_kolom
    try:
        upsert_skenario(sk)
        raise AssertionError("kolom hilang harus keluar dengan pesan migrasi")
    except SystemExit as e:
        assert e.code == MIGRASI and "20261008010000_skenario.sql" in e.code, e.code
    OPENER = boom
    try:
        insert_backtest([{"run_id": "x"}])
        raise AssertionError("HTTPError harus diteruskan")
    except RuntimeError as e:
        assert "HTTP 409" in str(e) and "23505" in str(e), e
    print("selftest OK")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        _selftest()
        sys.exit()
    if a[:1] in (["analysis"], ["outlook"], ["backtest"], ["makro"], ["skenario"]):
        _creds()  # gagal cepat sebelum fetch yang lama
    if a[:1] == ["analysis"] and len(a) == 4:
        print(insert_analysis(a[1], a[2], json.load(open(a[3], encoding="utf-8")))["id"])
    elif a[:1] == ["outlook"]:
        import outlook
        rows = outlook.build(a[1] if len(a) > 1 else "XAUUSD")
        upsert_outlook(rows)
        print(f"news_outlook: {len(rows)} baris")
    elif a[:1] == ["backtest"] and len(a) == 2:
        data = json.load(open(a[1], encoding="utf-8"))
        rows = data["rows"] if isinstance(data, dict) else data
        insert_backtest(rows)
        print(f"backtest_results: {len(rows)} baris")
    elif a[:1] == ["skenario"]:
        import skenario_news
        jam = int(a[a.index("--jam") + 1]) if "--jam" in a else 48
        pair = a[1] if len(a) > 1 and not a[1].startswith("--") else "XAUUSD"
        rows = skenario_news.build(pair, jam)
        n = upsert_skenario(rows)
        print(f"news_outlook skenario: {n} dari {len(rows)} baris diperbarui"
              + ("" if n == len(rows) else " (sisanya belum ada di news_outlook: jalankan publish.py outlook dulu)"))
    elif a == ["makro"]:
        import makro
        rows = makro.fetch_all()
        upsert_macro(rows)
        print(f"macro_series: {len(rows)} baris")
    else:
        sys.exit(__doc__)
