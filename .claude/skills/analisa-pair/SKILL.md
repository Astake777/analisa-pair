---
name: analisa-pair
description: Auto-analisis satu pair trading (default XAUUSD) per gaya scalp/intraday/swing — engine strategi + backtest, fase AMD, makro (US10Y/DXY/real yield/COT), berita + outlook news 45 hari, lalu setup entry (entry/SL/TP/RR) atau NO TRADE, dan publikasi ke website lokal (React + Supabase). Pakai saat user minta analisa pair, cari setup/entry, prediksi news, atau "/analisa-pair XAUUSD scalp".
argument-hint: "[PAIR] [scalp|intraday|swing]"
---

# Analisa Pair

Argumen: `$ARGUMENTS`. Pair default `XAUUSD`, gaya default `intraday`. Jawab dalam Bahasa Indonesia, waktu dalam WIB (UTC+7). Semua script ada di `.claude/skills/analisa-pair/` dan dijalankan dari root repo `E:\Trade Folders`. Hasil kerja sementara ditulis ke scratchpad sesi.

Ini **analisis saja**. Jangan pernah memanggil tool eksekusi order (mis. connector trading `execute_order`, `close_position`) kecuali user meminta eksplisit untuk order spesifik itu.

## 1. Gaya dan sumber data

| Gaya | Bias | Entry | Target |
|---|---|---|---|
| scalp | 1h + 30m (filter 4h) | 5m | 1.5R |
| intraday | 4h + 1h + 30m | 15m + pemicu 5m | 2R |
| swing | 1d + 4h + 1h | 1h / 15m | 3R |

Harga dan candle (`data.load(source="auto")`): OANDA v20 kalau `OANDA_TOKEN` + `OANDA_ACCOUNT_ID` ada di `.env`, kalau tidak Binance XAUTUSDT yang digeser ke spot gold-api (`load.basis`). Yahoo GC=F terlambat 10 menit dan berupa futures (selisih ±$15 dari spot); jangan dipakai untuk harga live. Semua level ke user dan ke website dalam satuan **spot**.

## 2. Jalankan engine

```
python .claude/skills/analisa-pair/setup.py XAUUSD <gaya> <scratchpad>/setup.json
```
Keluarannya: bias per TF, level, regime (tren/volatilitas/sesi/jendela news), fase AMD, strategi terpilih + alasan (dari backtest OOS terbaru), dan setup via `entry.py`. Pakai status dari engine apa adanya: `NO TRADE` dari engine berarti NO TRADE. Jangan mengarang setup yang tidak dihasilkan engine.

Hasil backtest dipakai pemilih strategi. Kalau run terakhir lebih tua dari 7 hari atau belum ada, jalankan dulu:
```
python .claude/skills/analisa-pair/backtest.py XAUUSD <gaya>
python .claude/skills/analisa-pair/publish.py backtest <file hasil di data/backtest/>
```

## 3. Data pendukung (panggil paralel)

1. `date -u`.
2. `yahoo_price` (MCP tradingview): `^TNX`, `DX-Y.NYB`, `CL=F`, `^GSPC` untuk perubahan hari ini.
3. `python .claude/skills/analisa-pair/makro.py` untuk real yield 10Y (DFII10), breakeven, dollar luas, dan COT managed money emas.
4. `financial_news` dan `market_sentiment` (MCP tradingview, simbol `GLD` untuk emas), **masing-masing sekali per analisis**. Kuota Marketaux 100 request/hari, jadi jangan dipanggil di setiap putaran `/loop`. Baca judulnya sendiri: label sentimen tool ini pernah menandai judul jelas-bearish sebagai bullish.
5. `python .claude/skills/analisa-pair/outlook.py XAUUSD` untuk event USD 45 hari ke depan: dampak panas/dingin ke pair, lean otomatis (hanya kalau ≥2 indikator pendahulu sepakat), dan skor hasil untuk event yang sudah rilis.

## 4. Konfirmasi makro dan keyakinan

- BUY emas butuh US10Y (dan real yield) tidak sedang membuat high baru, dan DXY tidak naik kuat. SELL sebaliknya. Kalau berlawanan, turunkan keyakinan satu level.
- Kalau hari ini emas bergerak searah kebalikan yield tapi berlawanan dengan minyak, kanal suku bunga yang dominan. Utamakan sinyal yield.
- Jangan menulis keyakinan "tinggi" kalau ada konflik antara TF besar, makro, berita, atau fase AMD.

## 5. Filter news

Ada event USD berdampak tinggi dalam **60 menit** (scalp), **4 jam** (intraday), atau **24 jam** (swing) → status **TUNGGU NEWS**, setup diganti rencana news:
- Flat 15 menit sebelum rilis. Jangan klik saat rilis.
- Tandai high/low candle 1m pertama. Masuk setelah menit ke-3 hanya dengan tembus + retest **dan** US10Y bergerak searah.
- Stop ≥ 1.5 × ATR 15m, lot 1/3 dari normal. FOMC: jangan masuk sebelum konferensi pers berjalan 10 menit.
- Yang ditradingkan adalah reaksi pasar, bukan tebakan angka. Headline yang sesuai forecast biasanya sudah di harga; kejutan biasanya ada di core/upah.

## 6. Prediksi dampak news

Skor arah dari `news.py`, bukan dari perasaan. Sesudah rilis, arah hanya valid kalau **US10Y bergerak searah dalam 5 menit**. Dua kasus nyata di mana skor benar tapi emas bergerak berlawanan karena yield: PPI 10 Sep 2026 (core di bawah forecast, emas turun $10 karena 10Y naik ke high 52w) dan NFP 2 Okt 2026 (payrolls jauh di bawah forecast, emas tetap turun $45 dalam 3 hari karena 10Y terus naik).

Kalau `outlook.py` belum memberi lean, cari indikator pendahulu tambahan dengan WebSearch dan isi `expected` hanya kalau **≥2 indikator independen sepakat** (nilai `forecast ± 1 ambang`: NFP ±50K, inflasi ±0.1, klaim ±15K). Kalau tidak sepakat, tulis "tidak ada edge, tunggu actual".

| Event | Indikator pendahulu |
|---|---|
| NFP / upah / unemployment | ADP, tren Initial Jobless Claims di minggu survei, ISM Employment, Challenger layoffs, Kalshi/Polymarket vs konsensus |
| CPI / core CPI | PPI bulan yang sama, bensin m/m, Cleveland Fed Inflation Nowcast, Polymarket core CPI |
| PPI | minyak/bensin rata-rata bulanan, import prices |
| Core PCE | CPI + PPI bulan yang sama |
| Retail sales | penjualan mobil, data kartu (BofA, Chicago Fed CARTS) |
| FOMC | CME FedWatch: >90% artinya keputusan sudah di harga; yang menentukan dots dan konferensi pers |

```
python .claude/skills/analisa-pair/news.py '{"pair":"XAUUSD","data":{"nfp":{"expected":40,"forecast":90}}}'
```

## 7. Publikasi ke website

Website lokal: `cd web && npm run dev` lalu buka http://localhost:5180 (harga live, chart M1–H4, panel analisis dari Supabase secara realtime).

1. Tambahkan ke `setup.json` hasil bacaanmu: `headlines` (`[{title, url, published, bacaan}]`), `prediksiNews` (`[{event, waktuWIB, arah, kekuatan, alasan, batal}]`), `makroKonfirmasi` (1 kalimat), dan `notes` tambahan.
2. `python .claude/skills/analisa-pair/snapshot.py XAUUSD <scratchpad>/setup.json <scratchpad>/out` → menambahkan driver makro, kalender, dan harga terkini ke `out/doc.json`.
3. `python .claude/skills/analisa-pair/publish.py analysis XAUUSD <gaya> <scratchpad>/out/doc.json`
4. Sekali sehari (atau saat diminta): `publish.py outlook XAUUSD` dan `publish.py makro`.
5. Kalau publish gagal (mis. `SUPABASE_SERVICE_ROLE_KEY` kosong), analisis di chat tetap dikirim. Sebutkan bahwa website belum diperbarui dan apa penyebabnya.

Untuk analisis berkala: `/loop 30m /analisa-pair XAUUSD intraday`. Harga di website sudah live sendiri, jadi `/loop` hanya untuk memperbarui analisis.

### Modul pendukung analisis
- Orderflow: `python .claude/skills/analisa-pair/orderflow.py` → delta 1 jam, CVD hari ini, AVWAP (hari, minggu, London, NY, swing 1H), volume profile kemarin dan hari ini (POC/VAH/VAL). Volume dari Binance XAUT (proksi, bukan COMEX); sebutkan itu kalau mengutipnya.
- Skenario news: `python .claude/skills/analisa-pair/skenario_news.py XAUUSD [--jam 48]` → 3 skenario panas/sesuai/dingin, peluang Polymarket, gerak median historis; `publish.py skenario XAUUSD` (butuh migrasi `20261008010000_skenario.sql`). Event tanpa ambang di news.SPEC dilewati, jangan mengarang ambang.
- Filter kondisi pasar: `python filter_kondisi.py <strategi> <mode>`; hanya dipakai live kalau memperbaiki OOS (8 Okt: tidak ada yang lolos).
- Panel Hasil backtest: setelah `validasi.py`/`backtest.py`, jalankan `python kinerja.py` (menulis `web/public/kinerja.json`, hanya trade OOS).
- Kalau watcher melaporkan error SSL/DNS ke Binance: jaringan memblokir exchange crypto (biasa terjadi di data seluler). Minta user pindah ke WiFi atau menyalakan VPN/WARP; jangan menebak harga dari sumber lain.

### Bot MT5 (eksekusi otomatis)
- `python .claude/skills/analisa-pair/mt5_link.py` cek koneksi; `bot_mt5.py --status` (akun, risiko, syarat live), `--uji-order` (demo saja), `--stop` (kill switch), tanpa argumen = loop. Jalankan loop lewat `Monitor` dan teruskan baris `BOT ORDER/TERISI/TP/SL/DITUTUP/STOP/ERROR` ke HP dengan `PushNotification`.
- Akun real hanya kalau `BOT_MODE=live`, strategi lulus `validasi.py <strategi> --sumber mt5`, dan syarat real test demo lulus. Jangan pernah mengubah `BOT_MODE`, `BOT_RISK_PERCENTAGE`, atau kredensial MT5 atas inisiatif sendiri; itu keputusan user. Jangan hapus `data/BOT_STOP` kecuali user minta.
- Lot dinamis: `Risk_Amount = saldo akun × BOT_RISK_PERCENTAGE / 100` (dikunci 25–30%, bawaan 25), `Lot = Risk_Amount / (SL pips × $10)`, dibulatkan ke bawah 0.01. SL lebar → lot kecil, rugi di SL tetap = Risk_Amount. Auto break-even: floating profit ≥ `BOT_BE_TRIGGER_PIPS` (bawaan 50) → SL posisi digeser ke entry; tutup di entry = status `BE`. Sebutkan drawdown di risiko itu (panel Hasil backtest memakai angka yang sama).
- Backtest bot (aturan bot persis, dalam dollar, data HFM): `python backtest_bot.py [strategi ...]`, lalu `python kinerja.py` (tab "Bot HFM"). 8 Okt: hanya sniper untung (+81%, DD −34,5% di risiko 10%); strategi lain rugi.

### Validasi sebelum mengabarkan setup
- `python .claude/skills/analisa-pair/validasi.py <strategi>`: walk-forward 90 hari IS / 30 hari OOS di data 200 hari, fill harus tembus $0.10, order batal kalau harga sudah 70% ke TP1 tanpa entry, biaya 2x, parameter tetangga. Label **SETUP VALID** hanya kalau semua syarat `KRITERIA` lulus.
- Hasil 8 Okt 2026: `sniper` (POI 15m + level MSNR fresh + sweep/CHoCH 1m) OOS 18 trade, menang 44%, +0.79R, lolos 6/7 (kurang jumlah trade) → dikabarkan sebagai uji coba. `alchemist_crt` OOS −0.41R dan `alchemist_london` hampir tidak pernah terisi → **tidak dikabarkan**. Jalankan ulang validasi tiap minggu atau setelah aturan strategi berubah.
- Jangan pernah mengabarkan setup dari strategi yang expectancy OOS-nya ≤ 0, walau diminta "cari setup": jawab bahwa belum ada setup yang teruji.

### Sniper (scalp, uji coba) dan kabar otomatis
- Aturan (`strategi/sniper.py`): zona supply/demand 15m (OB + displacement 1.5x ATR + FVG, skor konfluensi ≥1) searah bias 1H+30m. Saat harga masuk zona, tunggu sweep lalu CHoCH 1m. SL = ujung sweep ± 0.5; zona entry 20 pips; SL 35 pips dari tepi pertama; TP1 ≥ 100 pips dan ≥ 3R. 1 pip = $0.10.
- Backtest: `python strategi/sniper.py --sweep scalp` (corong sinyal + IS/OOS). Hasil 8 Okt 2026: 12 trade/60 hari, 7 menang. Selalu tandai **uji coba** sampai OOS ≥ 15 trade dan expectancy > 0.
- Watcher: Claude menjalankan `python .claude/skills/analisa-pair/pantau.py XAUUSD` lewat `Monitor` (timeout maks, arm ulang setiap habis). Setiap baris `SETUP ...` atau `SELESAI ...` diteruskan ke HP dengan `PushNotification`; baris `ERROR ...` dicek. Website menampilkan banner, bunyi, dan notifikasi browser sendiri.
- Kartu setup di payload: `zone`, `entry`, `sl`, `tp`, `rr`, `alasan: {entry, sl, tp}` (masing-masing satu kalimat pendek), `langkah` (≤ 2 baris), `batal`, `eksperimen`.

## 8. Format output di chat

```
## <PAIR> — <tanggal> <jam> WIB  |  gaya: <scalp/intraday/swing>

**Status: SETUP AKTIF / SIAP / TUNGGU PULLBACK / TUNGGU NEWS / NO TRADE**

| Aset | Harga | Hari ini |   ← pair (spot) + driver makro

### Bias        ← tabel TF + regime + fase AMD + konfirmasi makro (1–2 kalimat)
### Strategi    ← strategi terpilih + angka backtest OOS-nya (trade, winrate, expectancy), atau alasan NO TRADE
### Berita      ← 3 judul terbaru (bacaanmu sendiri) + event kalender terdekat (WIB, A/F/P)
### Prediksi news ← per event: LEAN atau HASIL, arah pair, kekuatan, syarat konfirmasi 10Y
### Level       ← blok kode level atas→bawah, tandai SEKARANG dan zona
### Setup
- Arah, zona entry, trigger
- SL, TP1 (RR), TP2 (RR)
- Invalidasi (level + kondisi)
- Keyakinan: rendah/sedang/tinggi + alasan satu kalimat
### Catatan     ← sumber harga + basis spot, data yang gagal diambil, website diperbarui atau tidak
```

Kalau statusnya NO TRADE atau TUNGGU, tetap tulis zona dan harga yang ditunggu, supaya user tahu kapan setup jadi valid. Sampel backtest masih kecil (≈60 hari); sebutkan itu saat mengutip winrate. Ini analisis teknikal, bukan nasihat keuangan.
