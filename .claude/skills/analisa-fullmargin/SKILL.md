---
name: analisa-fullmargin
description: Analisa full margin (all-in) XAUUSD — sniper 1 shot 0 floating di jam volatil (news USD high impact, London open, NY open). Ukuran all-in dari akun MT5 HFM (lot maks, $/pip, SL habis-saldo, jarak stop-out), jendela volatil 7 hari berperingkat dari candle M5 broker, statistik 0-floating dan SL ketat 10/15/20 pips dari histori sniper, lalu kandidat shot atau TIDAK ADA SHOT. Analisis saja. Pakai saat user minta "/analisa-fullmargin", all-in, full margin, 1 shot, atau sniper tanpa floating.
argument-hint: "[PAIR]"
---

# Analisa Full Margin

Argumen: `$ARGUMENTS` (pair, bawaan `XAUUSD`; hanya emas MT5 yang didukung). Jawab dalam Bahasa Indonesia, waktu WIB (UTC+7). Konteks strategi, filter news, dan aturan umum ada di `.claude/skills/analisa-pair/SKILL.md`.

Ini **analisis saja**. Jangan pernah memanggil tool eksekusi order (`execute_order`, `close_position`, tool order MT5/MCP, `order_send`, `bot_mt5.py`, `ea_kontrol.py`) kecuali user meminta eksplisit untuk order spesifik itu.

## Jalankan

```
python .claude/skills/analisa-pair/fullmargin.py XAUUSD --json <scratchpad>/fullmargin.json
```
Butuh terminal MT5 HFM menyala dan login. Kalau MT5 mati, ukuran all-in "tidak tersedia" dan statistik dihitung dari cache candle (`data/cache/MT5_XAUUSD_*`); sebutkan itu. Untuk syarat US10Y di rencana news, ambil `^TNX` dengan `yahoo_price` (MCP tradingview).

Pakai angka dari script apa adanya. Status `TIDAK ADA SHOT` dari script berarti tidak ada shot; jangan mengarang entry yang tidak dihasilkan script.

## Format output di chat

```
## FULL MARGIN <PAIR> — <tanggal> <jam> WIB

**Status: ADA SHOT / TIDAK ADA SHOT**

### Ukuran all-in
lot maks, $/pip, SL habis-saldo (pips), jarak stop-out (pips, level SO broker), batas SL pakai (setelah spread)

### 3 jendela volatil berikutnya
| WIB | jenis | alasan | range 30m | >=100 pips 2 jam | n |

### Histori sniper (0 floating)
SL asli vs 10/15/20 pips: menang/n, win rate, expectancy R; porsi 0-floating; per news / jam open / lain. Selalu tulis sampel.

### Kandidat shot
- Arah, entry, SL ketat (pips), TP (pips), muat batas all-in atau tidak
- Peluang 0-floating dan menang dari histori + sampel (n)
- Zona POI yang ditunggu (kalau tidak ada sinyal aktif)

### Syarat trigger
- Sniper: harga masuk zona -> sweep -> CHoCH 1m, limit di tepi pertama, batal 1 jam / 70% ke TP
- News: flat 15 menit sebelum rilis, tandai high/low candle 1m pertama, entry setelah menit ke-3 hanya tembus + retest dan US10Y searah

> Peringatan: all-in = satu kali kena SL (atau stop-out) menghabiskan hampir seluruh saldo.
```

Peringatan satu baris itu **wajib** di setiap jawaban.

## Aturan

- Jangan pernah menulis kepastian ("pasti TP", "aman"). Sampel histori kecil (puluhan trade); tulis n di setiap persentase.
- Kandidat hanya layak kalau SL ketat ≤ batas SL pakai (jarak stop-out dikurangi spread). Stop ideal news 1.5×ATR15m yang melebihi batas berarti lewati news itu atau kecilkan lot; katakan itu terang-terangan.
- News high impact dalam 60 menit dari sinyal sniper → tunggu news (aturan scalp analisa-pair).
- Lot maks dari margin broker (`order_calc_margin`), bukan dari leverage akun: emas di HFM bisa punya margin lebih tinggi dari 1:2000.
- Jangan mengubah `.env`, `BOT_MODE`, risiko bot, atau EA atas inisiatif sendiri.
