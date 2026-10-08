@echo off
rem Nyalakan semuanya: MT5 HFM + EA SniperBot (XAUUSD M1), jembatan MT5 (feed website + sinkron EA), website.
rem Kalau MT5 sudah terbuka tanpa EA, tutup MT5 dulu lalu jalankan file ini lagi.
cd /d "%~dp0"
tasklist /FI "IMAGENAME eq terminal64.exe" | find /I "terminal64.exe" >nul || start "" "C:\Program Files\HFM Metatrader 5\terminal64.exe" /config:"%~dp0.claude\skills\analisa-pair\mt5\start.ini"
timeout /t 20 /nobreak >nul
start "jembatan MT5" /min cmd /c "python -u .claude\skills\analisa-pair\jembatan_mt5.py >> data\jembatan.log 2>&1"
start "website" /min cmd /c "cd web && npm run dev"
echo MT5 + EA, jembatan, dan website dinyalakan. Website: http://localhost:5180
