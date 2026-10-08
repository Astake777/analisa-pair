---
name: analisa-setup-bulanan
description: Rencana trade harian untuk target growth bulan ini (default XAUUSD) — kandidat setup hari ini berperingkat peluang dari validasi OOS (grade A/B/C), status hari dari akun MT5 demo (W/L, maks 2 loss, setelah 1 loss hanya setup peluang tertinggi), status bulan (modal awal, growth, sisa hari bursa, proyeksi), lot per kandidat. Pakai saat user minta setup harian/bulanan, "trade apa hari ini", target growth bulan ini, atau "/analisa-setup-bulanan XAUUSD".
argument-hint: "[PAIR]"
---

# Analisa Setup Bulanan

Argumen: `$ARGUMENTS` (pair, default `XAUUSD`). Bahasa Indonesia, waktu WIB (UTC+7). Jalankan dari root repo `E:\Trade Folders`; file sementara ke scratchpad sesi.

Ini **analisis saja**. Jangan pernah memanggil tool eksekusi order (`execute_order`, `close_position`, tool order MT5/MCP apa pun, `bot_mt5.py --uji-order`, `ea_kontrol.py stop`) kecuali user meminta eksplisit untuk order spesifik itu.

## Kejujuran
Walk-forward dan Strategy Tester: hanya `sniper` versi ketat yang untung (≈2–3 setup per bulan); semua varian yang dilonggarkan rugi. Jadi "tiap hari ada setup" artinya: tiap hari tampilkan kandidat terbaik yang **nyata** beserta peluang sebenarnya dari validasi. Hanya grade A yang disebut setup kuat. Jangan mengarang setup, level, atau peluang yang tidak ada di keluaran script. Proyeksi growth adalah ekstrapolasi naif, bukan target yang dijanjikan.

## 1. Jalankan
```
python .claude/skills/analisa-pair/setup_bulanan.py <PAIR> --json <scratchpad>/setup_bulanan.json
```
Isi: kandidat (sinyal aktif tiap strategi, zona POI sniper 15m yang masih hidup = PENDING, setup engine `setup.py` scalp + intraday), grade, lot dari `BOT_RISK_PERCENTAGE`, deal hari ini dan bulan ini (semua magic, termasuk manual dan EA 770078), news USD high hari ini, status engine. Kalau MT5 mati, kandidat tetap dari cache dan status akun "tidak tersedia".

Grade: **A** = lolos ≥ 6/7 kriteria `validasi.py` dan expectancy OOS > 0 (broker MT5 dulu, lalu umum); **B** = expectancy OOS > 0 tapi kriteria kurang; **C** = belum teruji atau expectancy ≤ 0 (tampil dengan peringatan, bukan setup kuat).

Aturan hari: 0 loss → semua kandidat boleh; 1 loss → hanya kandidat #1 dan hanya grade A, selain itu "tunggu setup peluang tertinggi"; ≥ 2 loss → STOP hari ini. Menang tidak menghentikan. |P/L| < 0.5% saldo = BE.

## 2. Data pendukung (paralel, singkat)
- `yahoo_price` (MCP tradingview): `^TNX` dan `DX-Y.NYB`, perubahan hari ini. BUY emas butuh 10Y dan DXY tidak naik kuat; SELL sebaliknya. Kalau berlawanan, sebutkan.
- News hari ini dari keluaran script. Ada news high dalam 60 menit → scalp TUNGGU NEWS.

## 3. Format di chat
```
## Setup Bulanan <PAIR> — <tanggal> <jam> WIB | sesi <sesi> | harga <x>

**Bulan:** modal awal <x> → saldo <y> (<growth %>), <W>W/<L>L, sisa <n> hari bursa, proyeksi naif <p %> (bukan janji)
**Hari ini:** <W>W / <L>L / <BE>BE, P/L <x> → aturan aktif: <semua boleh / hanya #1 grade A / STOP>
**Makro:** 10Y <..>, DXY <..> (1 kalimat) | **News:** <jam WIB event> atau tidak ada

| # | Grade | Strategi | Arah | Zona/Entry | SL | TP | RR | Lot | Peluang OOS | Trigger |

**Trade berikutnya yang boleh diambil:** <satu baris dari script>
**Risiko:** <satu baris: risiko % saldo per trade = rugi di SL, sampel validasi kecil, grade C bukan setup kuat>
```
Zona PENDING sniper: tulis pita entry dan aturan SL/TP (angka pasti baru ada setelah sweep + CHoCH 1m). Tulis status engine apa adanya (`NO TRADE` tetap NO TRADE). Tidak ada kandidat grade A/B → katakan terus terang "belum ada setup teruji hari ini" dan sebut zona/harga yang ditunggu.
