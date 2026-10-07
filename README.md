# Trade Folders

Sistem analisis pair trading (default XAUUSD) untuk Claude Code.

- `.claude/skills/analisa-pair/` — skill `/analisa-pair <PAIR> [scalp|intraday|swing]`
  - `entry.py` — setup entry: zona → SL, TP, RR, status
  - `news.py` — skor kejutan news → USD hawkish/dovish → arah pair
  - `kalender.py` — kalender ekonomi USD (TradingView, WIB) + skor otomatis
  - `snapshot.py` — gabungkan analisis + candle + driver → dokumen dashboard
- `dashboard/` — halaman dashboard (Artifact claude.ai) yang membaca hasil analisis

Self-test semua script:

```
cd .claude/skills/analisa-pair
for f in entry news kalender snapshot; do python $f.py --selftest; done
```

Analisis teknikal, bukan nasihat keuangan.
