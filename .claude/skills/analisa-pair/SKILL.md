---
name: analisa-pair
description: Auto-analisis satu pair trading (default XAUUSD) — harga, makro (US10Y/DXY/minyak), berita + kalender ekonomi, multi-timeframe, lalu setup entry terbaik (entry/SL/TP/RR) atau NO TRADE. Pakai saat user minta analisa pair, cari setup/entry, atau "/analisa-pair XAUUSD scalp".
argument-hint: "[PAIR] [scalp|intraday|swing]"
---

# Analisa Pair

Argumen: `$ARGUMENTS`. Pair default `XAUUSD`, gaya default `intraday`. Jawab dalam Bahasa Indonesia, waktu dalam WIB (UTC+7).

Ini **analisis saja**. Jangan pernah memanggil tool eksekusi order (mis. connector trading `execute_order`, `close_position`) kecuali user meminta eksplisit untuk order spesifik itu.

## 1. Peta simbol

| Pair | Harga (`yahoo_price`) | TA (`coin_analysis` / `multi_timeframe_analysis`) | Berita (`financial_news`, `market_sentiment`) | Driver makro |
|---|---|---|---|---|
| XAUUSD | `GC=F` | coba `XAUUSD` @ `OANDA` dulu (spot, tanpa konversi), fallback `GLD` @ `AMEX` | `GLD` | `^TNX` (terbalik), `DX-Y.NYB` (terbalik), `CL=F` |
| XAGUSD | `SI=F` | proxy `SLV` @ `AMEX` | `SLV` | `^TNX`, `DX-Y.NYB` |
| BTCUSD | `BTC-USD` | `BTCUSDT` @ `BINANCE` (langsung) | `BTC` (category crypto) | `^TNX`, `NQ=F` |
| US100 | `NQ=F` | proxy `QQQ` @ `NASDAQ` | `QQQ` | `^TNX` (terbalik) |

`OANDA`/`FX_IDC`/`TVC` diterima server untuk XAUUSD, XAGUSD, EURUSD, GBPUSD, USDJPY, tapi baru terverifikasi di level parameter (scanner sedang down saat dites 7 Okt). Jangan pakai `FOREXCOM` atau futures `GC1!` @ `COMEX`/`CME` di `coin_analysis`: exchange-nya diam-diam diganti `KUCOIN`. Cek kolom `exchange` di hasil; kalau berbunyi `KUCOIN` padahal kamu minta yang lain, hasilnya tidak valid.

Pair lain: cari padanan di tabel ini; kalau tidak ada, pakai `yahoo_price` saja dan bilang TA tidak tersedia.

**Konversi proxy** (GLD → XAU dst.): `ratio = harga_futures / harga_proxy`, diambil di waktu yang sama. Kalikan semua level proxy dengan ratio (`entry.py` bisa melakukannya lewat key `ratio`). Dua peringatan wajib ditulis ke user:
- ETF hanya diperdagangkan 13:30–20:00 UTC. Di luar jam itu data proxy basi; ratio dan indikator intraday kurang akurat.
- Harga futures ≠ harga spot di chart broker (selisih ±$30–60 di emas). Minta user menyesuaikan offset.

## 2. Ambil data (panggil paralel)

1. `date -u` untuk jam sekarang.
2. `yahoo_price`: harga pair + semua driver makro + `^GSPC`.
3. `multi_timeframe_analysis` proxy; `coin_analysis` proxy di `1D` dan `4h`; tambah `1h` dan `15m` kalau gaya `scalp`.
4. `financial_news` dan `market_sentiment` untuk simbol berita. **Baca judulnya sendiri.** Label sentimen tool ini pernah menandai judul jelas-bearish ("Gold ... Face Pressure as Rates Rise") sebagai bullish, jadi jangan percaya labelnya.
5. Kalender: `python .claude/skills/analisa-pair/kalender.py <PAIR>` → event USD penting 6 jam ke belakang s/d 7 hari ke depan dalam WIB, A/F/P, dan skor buy/sell otomatis untuk rilis yang sudah ada actual-nya. Kalau feed gagal, pakai WebSearch "<event> <bulan tahun> forecast".

Kuota Marketaux (gratis): 100 request/hari, 3 artikel per request. Panggil `financial_news` dan `market_sentiment` masing-masing sekali per analisis; jangan dipanggil di setiap putaran `/loop`.

`multi_timeframe_analysis` tetap mengeluarkan `alignment` dan `recommendation` walaupun timeframe-nya gagal (pernah terjadi: semua TF error tapi hasilnya "HOLD/NO TRADE"). Kalau ada TF yang berisi `error`, abaikan `alignment` dan `recommendation`, lalu nilai bias hanya dari TF yang berhasil.

Kalau tool TradingView mengembalikan `UPSTREAM_ERROR` (retryable), lanjutkan panggilan lain lalu coba sekali lagi di akhir. Kalau masih gagal, lanjut dengan data yang ada dan tulis bagian mana yang hilang.

## 3. Tentukan bias

- **Arah utama** = gabungan 1W + 1D. Setup hanya searah itu, kecuali alignment `NEUTRAL`.
- **Konfirmasi makro (emas/perak)**: BUY butuh US10Y tidak sedang membuat high baru (datar/turun) dan DXY tidak naik kuat. SELL sebaliknya. Kalau berlawanan, turunkan keyakinan satu level.
- **Cek rezim**: kalau hari ini emas bergerak berlawanan dengan minyak tapi searah kebalikan yield, kanal suku bunga yang dominan. Utamakan sinyal yield.
- **Filter jenuh**: Stochastic 4H **dan** 1D < 20 → jangan SELL di harga sekarang, hanya di pantulan ke zona. > 80 → jangan BUY di harga sekarang.

## 4. Cari zona entry

Zona = **≥2 level berdekatan** (selisih ≤ 0.3 × ATR 4H) dari: EMA20/50/200 4H dan 1D, SMA200 1D, pivot/R/S 4H dan 1D, Bollinger band, high/low kemarin. Untuk SELL ambil zona resistance terdekat **di atas** harga; BUY ambil zona support terdekat **di bawah** harga. Target = level-level berikutnya searah trade.

ATR yang dipakai: 4H untuk `intraday`/`swing`, 15m untuk `scalp` (dengan zona dari 1H/15m).

Hitung dengan:
```
python .claude/skills/analisa-pair/entry.py '{"side":"sell","price":<harga>,"zone":[<lo>,<hi>],"atr":<atr>,"targets":[<t1>,<t2>,<t3>]}'
```
Tambah `"ratio":<r>` kalau level masih dalam satuan proxy. Pakai status dari script apa adanya: `NO TRADE` dari script berarti NO TRADE.

**Trigger** (tulis ke user, jangan dihitung script): di TF entry (1H untuk intraday, 5m/15m untuk scalp) tunggu candle penolakan di zona, atau MACD cross searah trade, sebelum masuk.

## 5. Filter news

Ada event USD berdampak tinggi dalam **60 menit** (scalp), **4 jam** (intraday), atau **24 jam** (swing) → status **TUNGGU NEWS**, ganti setup dengan rencana news:
- Flat 15 menit sebelum rilis. Jangan klik saat rilis.
- Tandai high/low candle 1m pertama. Masuk setelah menit ke-3 hanya dengan tembus + retest **dan** US10Y bergerak searah.
- Stop ≥ 1.5 × ATR 15m, lot 1/3 dari normal. FOMC: jangan masuk sebelum konferensi pers berjalan 10 menit.
- Yang ditradingkan adalah reaksi pasar, bukan tebakan angka. Headline yang sesuai forecast biasanya sudah di harga; kejutan biasanya ada di core/upah.

## 6. Prediksi dampak news

Untuk setiap event USD penting dalam jendela filter di atas, beri **arah buy/sell yang paling mungkin**. Skornya dari `news.py`, bukan dari perasaan.

**Sesudah rilis:** `kalender.py` sudah mencetak skornya. Arah itu hanya valid kalau **US10Y bergerak searah dalam 5 menit** (emas: SELL butuh 10Y naik, BUY butuh 10Y turun). Dua kasus nyata di mana skor benar tapi emas bergerak berlawanan karena yield: PPI 10 Sep 2026 (core di bawah forecast, emas turun $10 karena 10Y naik ke high 52w) dan NFP 2 Okt 2026 (payrolls jauh di bawah forecast, emas tetap turun $45 dalam 3 hari karena 10Y terus naik).

**Sebelum rilis (LEAN):** cari indikator pendahulu dengan WebSearch, lalu isi `expected` hanya kalau **≥2 indikator independen sepakat** arah kejutannya. Isi nilainya `forecast ± 1 ambang` (NFP ±50K, inflasi ±0.1, klaim ±15K). Kalau tidak sepakat, tulis "tidak ada edge, tunggu actual".

| Event | Indikator pendahulu |
|---|---|
| NFP / upah / unemployment | ADP (2 hari sebelumnya, ada di `kalender.py`), tren Initial Jobless Claims di minggu survei, ISM Employment, Challenger layoffs, pasar prediksi (Kalshi/Polymarket) vs konsensus |
| CPI / core CPI | PPI bulan yang sama (keluar lebih dulu), harga bensin m/m (EIA/AAA), Cleveland Fed Inflation Nowcast, Polymarket core CPI |
| PPI | harga minyak/bensin rata-rata bulanan, import prices |
| Core PCE | CPI + PPI bulan yang sama (ekonom menerjemahkannya langsung; kejutan PCE jarang besar) |
| Retail sales | penjualan mobil, data kartu (Bank of America/Chicago Fed CARTS) |
| FOMC (keputusan) | CME FedWatch: >90% artinya keputusan sudah di harga, yang menentukan adalah dots dan konferensi pers |

```
python .claude/skills/analisa-pair/news.py '{"pair":"XAUUSD","data":{"nfp":{"expected":40,"forecast":90},"ahe_mm":{"expected":0.3,"forecast":0.3}}}'
```

Tulis ke user: arah lean, kekuatan, indikator yang mendukung, dan **apa yang membatalkannya**. Lean bukan sinyal entry. Entry tetap mengikuti rencana news di bagian 5.

## 7. Format output

```
## <PAIR> — <tanggal> <jam> WIB  |  gaya: <scalp/intraday/swing>

**Status: SETUP AKTIF / SIAP / TUNGGU PULLBACK / TUNGGU NEWS / NO TRADE**

| Aset | Harga | Hari ini |   ← pair + driver makro

### Bias      ← tabel TF (1W/1D/4H/1H/15m) + alignment + konfirmasi makro (1–2 kalimat)
### Berita    ← 3 judul terbaru (bacaanmu sendiri) + event kalender terdekat (WIB, A/F/P)
### Prediksi news ← per event: LEAN atau HASIL, arah pair, kekuatan, syarat konfirmasi 10Y
### Level     ← blok kode level atas→bawah, tandai SEKARANG dan zona
### Setup
- Arah, zona entry, trigger
- SL, TP1 (RR), TP2 (RR)
- Invalidasi (level + kondisi)
- Keyakinan: rendah/sedang/tinggi + alasan satu kalimat
### Catatan   ← data proxy/basi, offset spot, tool yang gagal
```

Kalau statusnya NO TRADE atau TUNGGU, tetap tulis zona dan harga yang ditunggu, supaya user tahu kapan setup jadi valid.

Jangan menulis keyakinan "tinggi" kalau ada konflik antara TF besar, makro, atau berita. Ini analisis teknikal, bukan nasihat keuangan.
