//+------------------------------------------------------------------+
//| SniperBot.mq5                                                     |
//| Port strategi/sniper.py ke MT5: POI 15m (OB + FVG + level MSNR    |
//| fresh, searah EMA20/50 H1+M30) -> sweep + CHoCH 1m -> buy/sell    |
//| limit. Lot dinamis 25-30% saldo dan auto break-even.              |
//| Varian tanpa delta orderflow (candle broker tidak punya taker).   |
//+------------------------------------------------------------------+
#property copyright "analisa-pair"
#property version   "1.00"
#property description "Sniper XAUUSD: POI M15 -> sweep + CHoCH M1 -> limit. Lot = (saldo x Risk%) / (SL pips x pip value). SL ke entry saat profit >= BE_Trigger_Pips."
#include <Trade/Trade.mqh>

input group "Risiko"
input double Risk_Percentage   = 25.0;   // Risk % saldo per trade (dikunci 25-30)
input double BE_Trigger_Pips   = 50.0;   // Profit pips -> SL digeser ke entry (0 = mati)
input int    Max_SL_Harian     = 2;
input int    Max_Entry_Harian  = 3;
input int    Max_Spread_Points = 100;
input int    News_Menit        = 15;     // Tidak order +/- menit dari news USD high (live: kalender MT5 + analisa_news.csv, tester: analisa_news.csv)
input group "Sniper (PARAMS strategi/sniper.py)"
input double Disp         = 1.5;         // Body displacement >= Disp x ATR M15
input int    Cari_OB      = 3;
input double Zona_Max     = 10.0;        // Lebar OB maksimum ($)
input int    Min_Skor     = 2;
input int    Umur_Jam     = 12;          // Umur POI
input int    LB           = 3;           // Candle M1 sebelum ekstrem untuk garis CHoCH
input double Pad          = 0.5;         // SL = ekstrem sweep -/+ Pad ($)
input double SL_Jarak     = 3.0;         // Entry = SL +/- SL_Jarak ($3 = 30 pips)
input double RR           = 3.0;
input double TP_Min       = 10.0;        // TP minimal $10 = 100 pips
input bool   Pakai_MSNR   = true;
input int    Expire_Menit = 60;          // Limit dibatalkan kalau belum terisi
input double Batal_Frac   = 0.7;         // Limit dibatalkan kalau harga sudah 70% ke TP tanpa terisi
input long   Magic        = 770078;

#define PIP 0.10                         // 1 pip XAUUSD

struct Level { double harga; bool support; bool fresh; };
struct Poi   { datetime t_ok; int side; double lo, hi; int skor; bool masuk; double ext; datetime ext_t; string alasan; };

CTrade   trade;
Level    papan[];
Poi      pois[];
datetime last_m1 = 0, last_m15 = 0, be_gagal = 0;
int      hari = -1, n_sl = 0, n_entry = 0;
int      hE20H1, hE50H1, hE20M30, hE50M30;
bool     tester;
datetime news[];                         // event USD high dari analisa_news.csv (epoch UTC, urut)
datetime news_muat = 0;
int      fcsv = INVALID_HANDLE;          // log tester SniperBot_tester.csv
string   terakhir = "";                  // pesan log terakhir (untuk status ke Claude)
datetime status_t = 0;
#define CMD_FILE    "sniperbot_cmd.txt"     // perintah dari ea_kontrol.py (Common\Files)
#define STATUS_FILE "sniperbot_status.json"

//--- util
void Log(string s)
{
   Print(s);
   terakhir = s;
   if(!tester)
      SendNotification(s);
}

double N(double x) { return NormalizeDouble(x, _Digits); }

// override dari Claude (ea_kontrol.py) disimpan di global variable terminal, bertahan saat restart
string GV(string k) { return StringFormat("SniperBot_%I64d_%s", Magic, k); }
double Override(string k, double bawaan) { return !tester && GlobalVariableCheck(GV(k)) ? GlobalVariableGet(GV(k)) : bawaan; }
double RiskFrac() { return MathMin(MathMax(Override("risk", Risk_Percentage), 25.0), 30.0) / 100.0; }
double BePips() { return Override("be", BE_Trigger_Pips); }
bool   Dijeda() { return Override("pause", 0) > 0; }

//--- waktu: server HFM = UTC+3 saat DST AS, selain itu UTC+2 (TimeGMT tidak jalan di tester)
datetime MingguKe(int y, int m, int n)   // hari Minggu ke-n bulan m, jam 00:00
{
   datetime t = StringToTime(StringFormat("%d.%02d.01", y, m));
   MqlDateTime d;
   TimeToStruct(t, d);
   return t + ((7 - d.day_of_week) % 7 + 7 * (n - 1)) * 86400;
}

datetime KeUtc(datetime server_t)
{
   datetime u = server_t - 3 * 3600;     // perkiraan UTC untuk menentukan DST
   MqlDateTime d;
   TimeToStruct(u, d);
   bool dst = u >= MingguKe(d.year, 3, 2) + 7 * 3600 && u < MingguKe(d.year, 11, 1) + 6 * 3600;
   return server_t - (dst ? 3 : 2) * 3600;
}

int HariWib() { return (int)((KeUtc(TimeCurrent()) + 7 * 3600) / 86400); }

void ResetHarian()
{
   int d = HariWib();
   if(d != hari) { hari = d; n_sl = 0; n_entry = 0; }
}

//--- lot dinamis: Risk_Amount / (SL_pips x Pip_Value), dibulatkan ke bawah ke step broker; 0 = lot minimum > risiko
double LotDinamis(double entry, double sl, double &risk_amount, double &sl_pips, double &pip_value)
{
   risk_amount = AccountInfoDouble(ACCOUNT_BALANCE) * RiskFrac();
   sl_pips     = MathAbs(entry - sl) / PIP;
   pip_value   = PIP / SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE) * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(sl_pips <= 0 || pip_value <= 0)
      return 0;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double lot  = MathFloor(risk_amount / (sl_pips * pip_value) / step + 1e-9) * step;
   if(lot < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
      return 0;
   return NormalizeDouble(MathMin(lot, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX)), 2);
}

//--- MSNR: A (bull lalu bear) = resistance di close bull, V (bear lalu bull) = support di close bear
void PapanTambah(double harga, bool support)
{
   int n = ArraySize(papan);
   if(n >= 60) { ArrayRemove(papan, 0, 1); n--; }
   ArrayResize(papan, n + 1);
   papan[n].harga = harga; papan[n].support = support; papan[n].fresh = true;
}

void PapanCandle(double h, double l, double c)
{
   for(int i = 0; i < ArraySize(papan); i++)
   {
      double p = papan[i].harga;
      if(!papan[i].support)
      {
         if(c > p) { papan[i].support = true; papan[i].fresh = true; }      // RBS
         else if(h >= p) papan[i].fresh = false;
      }
      else
      {
         if(c < p) { papan[i].support = false; papan[i].fresh = true; }     // SBR
         else if(l <= p) papan[i].fresh = false;
      }
   }
}

bool FreshDiZona(bool support, double lo, double hi)
{
   for(int i = 0; i < ArraySize(papan); i++)
      if(papan[i].fresh && papan[i].support == support && papan[i].harga >= lo - 0.5 && papan[i].harga <= hi + 0.5)
         return true;
   return false;
}

//--- indikator
void AtrWilder(const MqlRates &r[], int n, double &out[])
{
   int N_ = ArraySize(r);
   ArrayResize(out, N_);
   ArrayInitialize(out, 0);
   if(N_ < n) return;
   double tr[];
   ArrayResize(tr, N_);
   for(int i = 0; i < N_; i++)
      tr[i] = i == 0 ? r[i].high - r[i].low
              : MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
   double e = 0;
   for(int i = 0; i < n; i++) e += tr[i];
   e /= n;
   out[n - 1] = e;
   for(int i = n; i < N_; i++) { e += (tr[i] - e) / n; out[i] = e; }
}

int Arah(int h20, int h50, ENUM_TIMEFRAMES tf, datetime T)
{
   int sh = iBarShift(_Symbol, tf, T - PeriodSeconds(tf), false);   // candle terakhir yang sudah tutup pada T
   if(sh < 0) return 0;
   double a[1], b[1];
   if(CopyBuffer(h20, 0, sh, 1, a) != 1 || CopyBuffer(h50, 0, sh, 1, b) != 1) return 0;
   return a[0] > b[0] ? 1 : -1;
}

int Bias(datetime T)
{
   int s1 = Arah(hE20H1, hE50H1, PERIOD_H1, T), s2 = Arah(hE20M30, hE50M30, PERIOD_M30, T);
   return s1 == s2 ? s1 : 0;
}

bool PivotDiZona(datetime tk, double lo, double hi)
{
   int sh = iBarShift(_Symbol, PERIOD_D1, tk - 86400, false);
   if(sh < 0) return false;
   double H = iHigh(_Symbol, PERIOD_D1, sh), L = iLow(_Symbol, PERIOD_D1, sh), C = iClose(_Symbol, PERIOD_D1, sh);
   double p = (H + L + C) / 3, v[5];
   v[0] = p; v[1] = 2 * p - L; v[2] = 2 * p - H; v[3] = p + (H - L); v[4] = p - (H - L);
   for(int i = 0; i < 5; i++)
      if(v[i] >= lo - 0.5 && v[i] <= hi + 0.5) return true;
   return false;
}

//--- POI: candle idx baru tutup (= i+1 di sniper.py), displacement di i = idx-1
void ProsesM15(const MqlRates &r[], int idx, const double &a[], bool cari_poi)
{
   if(idx >= 2)
   {
      int x = idx - 2;   // pasangan (x, x+1) diketahui saat candle x+1 tutup = sebelum candle idx
      if(r[x].close > r[x].open && r[x + 1].close < r[x + 1].open) PapanTambah(r[x].close, false);
      else if(r[x].close < r[x].open && r[x + 1].close > r[x + 1].open) PapanTambah(r[x].close, true);
   }
   PapanCandle(r[idx].high, r[idx].low, r[idx].close);
   int i = idx - 1;
   if(!cari_poi || i < 25 || a[i - 1] <= 0) return;
   if(MathAbs(r[i].close - r[i].open) < Disp * a[i - 1]) return;
   int side = r[i].close > r[i].open ? 1 : -1;
   double gap = side > 0 ? r[i + 1].low - r[i - 1].high : r[i - 1].low - r[i + 1].high;
   if(gap <= 0) return;
   datetime T = r[i + 1].time + 900;
   if(Bias(T) != side) return;
   int k = -1;
   for(int x = i - 1; x >= MathMax(0, i - Cari_OB); x--)
      if((r[x].close < r[x].open) == (side > 0) && r[x].close != r[x].open) { k = x; break; }
   if(k < 0 || r[k].high - r[k].low > Zona_Max) return;
   double lo = r[k].low, hi = r[k].high;
   int skor = 0;
   string alasan = "";
   if(PivotDiZona(r[k].time, lo, hi)) { skor++; alasan += "pivot harian, "; }
   if(MathFloor(hi / 5) * 5 >= lo)     { skor++; alasan += "angka bulat, "; }
   if(k > 0)
   {
      double ekstrem = side > 0 ? DBL_MAX : -DBL_MAX;
      for(int x = MathMax(0, k - 20); x < k; x++)
         ekstrem = side > 0 ? MathMin(ekstrem, r[x].low) : MathMax(ekstrem, r[x].high);
      if(side > 0 ? lo < ekstrem : hi > ekstrem) { skor++; alasan += "sapu likuiditas, "; }
   }
   if(gap >= 0.5 * a[i - 1]) { skor++; alasan += "FVG lebar, "; }
   if(Pakai_MSNR)
   {
      if(!FreshDiZona(side > 0, lo, hi)) return;
      skor++;
      alasan += "level MSNR fresh, ";
   }
   if(skor < Min_Skor) return;
   int n = ArraySize(pois);
   ArrayResize(pois, n + 1);
   pois[n].t_ok = T; pois[n].side = side; pois[n].lo = lo; pois[n].hi = hi; pois[n].skor = skor;
   pois[n].masuk = false; pois[n].ext = 0; pois[n].ext_t = 0;
   pois[n].alasan = StringSubstr(alasan, 0, StringLen(alasan) - 2);
}

void M15Baru(bool warmup)
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, PERIOD_M15, 1, warmup ? 1500 : 300, r);
   if(n < 40) return;
   double a[];
   AtrWilder(r, 14, a);
   if(warmup)
   {
      ArrayResize(papan, 0);
      for(int idx = 2; idx < n; idx++) ProsesM15(r, idx, a, true);
   }
   else
      ProsesM15(r, n - 1, a, true);
}

//--- entry M1: masuk POI -> ekstrem (sweep) -> close menembus garis LB candle sebelum ekstrem (CHoCH)
bool BolehOrder(string &alasan);
void Pasang(const Poi &z, double ujung, datetime tsig);

void ProsesM1(const MqlRates &b, bool replay)
{
   int umur = Umur_Jam * 3600;
   for(int q = ArraySize(pois) - 1; q >= 0; q--)
   {
      if(pois[q].t_ok > b.time) continue;   // belum aktif
      int s = pois[q].side;
      bool buang = b.time > pois[q].t_ok + umur
                   || (s > 0 ? b.close < pois[q].lo - Pad : b.close > pois[q].hi + Pad);
      if(!buang && !pois[q].masuk)
      {
         if(s > 0 ? b.low <= pois[q].hi : b.high >= pois[q].lo)
         { pois[q].masuk = true; pois[q].ext = s > 0 ? b.low : b.high; pois[q].ext_t = b.time; }
         continue;
      }
      if(!buang)
      {
         if(s > 0 ? b.low < pois[q].ext : b.high > pois[q].ext) { pois[q].ext = s > 0 ? b.low : b.high; pois[q].ext_t = b.time; }
         if(pois[q].ext_t == b.time) continue;
         MqlRates ref[];
         int nr = CopyRates(_Symbol, PERIOD_M1, pois[q].ext_t - 60, LB, ref);
         if(nr <= 0) continue;
         double garis = s > 0 ? -DBL_MAX : DBL_MAX;
         for(int x = 0; x < nr; x++)
            garis = s > 0 ? MathMax(garis, ref[x].high) : MathMin(garis, ref[x].low);
         if(!(s > 0 ? b.close > garis : b.close < garis)) continue;
         if(!replay) Pasang(pois[q], pois[q].ext, b.time + 60);
      }
      ArrayRemove(pois, q, 1);   // hangus, kedaluwarsa, atau terpakai setelah CHoCH
   }
}

//--- pengaman
int Terbuka()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == Magic) n++;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
      if(OrderGetTicket(i) > 0 && OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == Magic) n++;
   return n;
}

//--- news dari file Python di Common Files: satu epoch UTC per baris
void MuatNews()
{
   news_muat = TimeCurrent();
   int f = FileOpen("analisa_news.csv", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE)
   {
      static bool sudah = false;
      if(!sudah) Print("Peringatan: analisa_news.csv tidak ada di Common Files, lanjut tanpa news dari file");
      sudah = true;
      return;
   }
   ArrayResize(news, 0);
   while(!FileIsEnding(f))
   {
      long v = StringToInteger(FileReadString(f));
      if(v <= 0) continue;
      int n = ArraySize(news);
      ArrayResize(news, n + 1, 256);
      news[n] = (datetime)v;
   }
   FileClose(f);
   ArraySort(news);
}

bool DekatNews()
{
   if(News_Menit <= 0) return false;
   static datetime cek = 0;
   static bool hasil = false;
   if(TimeCurrent() - cek < 60) return hasil;
   cek = TimeCurrent();
   hasil = false;
   if(!tester)
   {
      if(TimeCurrent() - news_muat >= 6 * 3600) MuatNews();
      MqlCalendarValue v[];
      datetime now = TimeTradeServer();
      if(CalendarValueHistory(v, now - News_Menit * 60, now + News_Menit * 60, NULL, "USD") > 0)
         for(int i = 0; i < ArraySize(v); i++)
         {
            MqlCalendarEvent ev;
            if(CalendarEventById(v[i].event_id, ev) && ev.importance == CALENDAR_IMPORTANCE_HIGH) { hasil = true; break; }
         }
   }
   if(!hasil)   // tester, atau kalender kosong/gagal
   {
      datetime u = KeUtc(TimeCurrent());
      for(int i = 0; i < ArraySize(news) && news[i] <= u + News_Menit * 60; i++)
         if(news[i] >= u - News_Menit * 60) { hasil = true; break; }
   }
   return hasil;
}

//--- log CSV tester: waktu epoch UTC, teks tanpa koma
void Catat(string jenis, datetime w, datetime masuk, bool beli, double entry, double sl, double tp, double lot,
           string keluar, string pl, string hasil, string alasan)
{
   if(fcsv == INVALID_HANDLE) return;
   StringReplace(alasan, ",", ";");
   FileWrite(fcsv, jenis, IntegerToString((long)w), masuk > 0 ? IntegerToString((long)masuk) : "", beli ? "buy" : "sell",
             DoubleToString(entry, 2), DoubleToString(sl, 2), DoubleToString(tp, 2), DoubleToString(lot, 2),
             keluar, pl, hasil, DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2), alasan);
   FileFlush(fcsv);
}

bool BolehOrder(string &alasan)
{
   ResetHarian();
   if(Dijeda())                    { alasan = "dijeda dari Claude"; return false; }
   if(n_sl >= Max_SL_Harian)       { alasan = StringFormat("batas harian: %d SL hari ini", n_sl); return false; }
   if(n_entry >= Max_Entry_Harian) { alasan = StringFormat("batas harian: %d entry hari ini", n_entry); return false; }
   if(Terbuka() > 0)               { alasan = "masih ada order/posisi bot"; return false; }
   long sp = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(sp > Max_Spread_Points)      { alasan = StringFormat("spread %d > %d", sp, Max_Spread_Points); return false; }
   if(DekatNews())                 { alasan = StringFormat("jendela news %d menit", News_Menit); return false; }
   return true;
}

void Pasang(const Poi &z, double ujung, datetime tsig)
{
   int s = z.side;
   double sl = N(ujung - s * Pad), entry = N(sl + s * SL_Jarak), tp = N(entry + s * MathMax(TP_Min, RR * SL_Jarak));
   string arah = s > 0 ? "BUY" : "SELL";
   string alasan = "";
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double batas = entry + (tp - entry) * Batal_Frac;
   double ra = 0, pips = 0, pv = 0, lot = 0;
   if(!BolehOrder(alasan)) {}
   else if(s > 0 ? ask <= entry : bid >= entry) alasan = "harga sudah melewati entry";
   else if(s > 0 ? ask >= batas : bid <= batas) alasan = "harga sudah 70% ke TP";
   else if((lot = LotDinamis(entry, sl, ra, pips, pv)) <= 0)
      alasan = StringFormat("SL %.1f pips, lot minimum rugi lebih dari %.2f", pips, ra);
   else
   {
      trade.SetExpertMagicNumber(Magic);
      bool ok = s > 0 ? trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "sniper")
                      : trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "sniper");
      if(ok)
      {
         string poi = StringFormat("POI M15 %.2f-%.2f: %s, sweep %.2f lalu CHoCH M1", z.lo, z.hi, z.alasan, ujung);
         Log(StringFormat("BOT ORDER %s LIMIT %.2f lot @ %.2f | SL %.2f | TP %.2f | lot = %.2f / (%.1f pips x %.2f) | rugi di SL %.2f (%.0f%% saldo) | %s",
                          arah, lot, entry, sl, tp, ra, pips, pv, lot * pips * pv, RiskFrac() * 100, poi));
         Catat("SINYAL", KeUtc(tsig), 0, s > 0, entry, sl, tp, lot, "", "", "ORDER", poi);
         return;
      }
      Log(StringFormat("BOT ERROR order %s %.2f: %d %s", arah, entry, trade.ResultRetcode(), trade.ResultComment()));
      Catat("SINYAL", KeUtc(tsig), 0, s > 0, entry, sl, tp, 0, "", "", "LEWATI",
            StringFormat("error order %d %s", trade.ResultRetcode(), trade.ResultComment()));
      return;
   }
   Log(StringFormat("BOT LEWATI %s %.2f: %s", arah, entry, alasan));
   Catat("SINYAL", KeUtc(tsig), 0, s > 0, entry, sl, tp, 0, "", "", "LEWATI", alasan);
}

//--- tiap tick: auto break-even dan pembatalan limit
void Kelola()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double be_pips = BePips();
   if(be_pips > 0 && TimeCurrent() - be_gagal >= 10)
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
         bool beli = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
         double buka = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
         double pips = (beli ? bid - buka : buka - ask) / PIP;
         bool belum = beli ? sl < buka : (sl == 0 || sl > buka);
         if(pips < be_pips || !belum) continue;
         trade.SetExpertMagicNumber(Magic);
         if(trade.PositionModify(tk, buka, tp))
            Log(StringFormat("BOT BE %s %.2f: SL digeser ke entry (profit %+.1f pips)", beli ? "BUY" : "SELL", buka, pips));
         else
         {
            be_gagal = TimeCurrent();
            Log(StringFormat("BOT ERROR BE posisi %I64u: %d %s", tk, trade.ResultRetcode(), trade.ResultComment()));
         }
      }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0 || OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != Magic) continue;
      bool beli = OrderGetInteger(ORDER_TYPE) == ORDER_TYPE_BUY_LIMIT;
      double e = OrderGetDouble(ORDER_PRICE_OPEN), tp = OrderGetDouble(ORDER_TP), batas = e + (tp - e) * Batal_Frac;
      string alasan = "";
      if(beli ? ask >= batas : bid <= batas) alasan = "harga sudah 70% ke TP1 tanpa entry";
      else if(TimeCurrent() >= (datetime)OrderGetInteger(ORDER_TIME_SETUP) + Expire_Menit * 60) alasan = "kedaluwarsa tanpa terisi";
      else if(DekatNews()) alasan = StringFormat("jendela news %d menit", News_Menit);
      if(alasan != "" && trade.OrderDelete(tk))
         Log(StringFormat("BOT BATAL %s %.2f: %s", beli ? "BUY" : "SELL", e, alasan));
   }
}

//--- kontrol dari Claude: ea_kontrol.py menulis "id|PERINTAH" ke CMD_FILE, EA menjalankan lalu menulis status + ack
void TutupSemua()
{
   trade.SetExpertMagicNumber(Magic);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk > 0 && OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == Magic) trade.OrderDelete(tk);
   }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == Magic) trade.PositionClose(tk);
   }
}

string Jalankan(string cmd)
{
   string k = cmd, v = "";
   int eq = StringFind(cmd, "=");
   if(eq > 0) { k = StringSubstr(cmd, 0, eq); v = StringSubstr(cmd, eq + 1); }
   StringToUpper(k);
   double x = StringToDouble(v);
   if(k == "PAUSE")  { GlobalVariableSet(GV("pause"), 1); return "dijeda: tidak ada order baru, posisi tetap dikelola"; }
   if(k == "RESUME") { GlobalVariableDel(GV("pause")); return "jalan lagi"; }
   if(k == "STOP")   { GlobalVariableSet(GV("pause"), 1); TutupSemua(); return "STOP: order dibatalkan, posisi ditutup, EA dijeda"; }
   if(k == "RISK")
   {
      if(x < 25 || x > 30) return "ditolak: RISK harus 25-30";
      GlobalVariableSet(GV("risk"), x);
      return StringFormat("risiko jadi %.1f%% saldo", x);
   }
   if(k == "BE")
   {
      if(v == "" || x < 0) return "ditolak: BE harus angka >= 0";
      GlobalVariableSet(GV("be"), x);
      return x > 0 ? StringFormat("auto BE di +%.0f pips", x) : "auto BE mati";
   }
   if(k == "RESET")  { GlobalVariableDel(GV("risk")); GlobalVariableDel(GV("be")); return "risk/BE kembali ke input EA"; }
   if(k == "STATUS") return "status";
   return "perintah tidak dikenal: " + cmd;
}

void TulisStatus(string ack, string hasil)
{
   string pos = "";
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      pos += StringFormat("%s{\"tiket\":%I64u,\"side\":\"%s\",\"lot\":%.2f,\"buka\":%.2f,\"sl\":%.2f,\"tp\":%.2f,\"profit\":%.2f}",
                          pos == "" ? "" : ",", tk, PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "buy" : "sell",
                          PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_OPEN), PositionGetDouble(POSITION_SL),
                          PositionGetDouble(POSITION_TP), PositionGetDouble(POSITION_PROFIT));
   }
   string ord = "";
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0 || OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != Magic) continue;
      ord += StringFormat("%s{\"tiket\":%I64u,\"side\":\"%s\",\"lot\":%.2f,\"entry\":%.2f,\"sl\":%.2f,\"tp\":%.2f}",
                          ord == "" ? "" : ",", tk, OrderGetInteger(ORDER_TYPE) == ORDER_TYPE_BUY_LIMIT ? "buy" : "sell",
                          OrderGetDouble(ORDER_VOLUME_INITIAL), OrderGetDouble(ORDER_PRICE_OPEN), OrderGetDouble(ORDER_SL), OrderGetDouble(ORDER_TP));
   }
   string alasan = "", log_akhir = terakhir;
   bool boleh = BolehOrder(alasan);
   StringReplace(log_akhir, "\"", "'");
   StringReplace(hasil, "\"", "'");
   string js = StringFormat("{\"ack\":\"%s\",\"hasil\":\"%s\",\"waktu_utc\":%I64d,\"saldo\":%.2f,\"ekuitas\":%.2f,\"mata_uang\":\"%s\","
                            "\"algo_trading\":%s,\"dijeda\":%s,\"risk_persen\":%.1f,\"be_pips\":%.0f,\"boleh_order\":%s,\"alasan\":\"%s\","
                            "\"sl_hari_ini\":%d,\"entry_hari_ini\":%d,\"poi_aktif\":%d,\"posisi\":[%s],\"order\":[%s],\"log_terakhir\":\"%s\"}",
                            ack, hasil, (long)KeUtc(TimeCurrent()), AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY),
                            AccountInfoString(ACCOUNT_CURRENCY), TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "true" : "false",
                            Dijeda() ? "true" : "false", RiskFrac() * 100, BePips(), boleh ? "true" : "false", alasan,
                            n_sl, n_entry, ArraySize(pois), pos, ord, log_akhir);
   int f = FileOpen(STATUS_FILE + ".tmp", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE) return;
   FileWriteString(f, js);
   FileClose(f);
   FileMove(STATUS_FILE + ".tmp", FILE_COMMON, STATUS_FILE, FILE_COMMON | FILE_REWRITE);
   status_t = TimeCurrent();
}

void OnTimer()
{
   if(FileIsExist(CMD_FILE, FILE_COMMON))
   {
      int f = FileOpen(CMD_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
      string baris = f == INVALID_HANDLE ? "" : FileReadString(f);
      if(f != INVALID_HANDLE) FileClose(f);
      FileDelete(CMD_FILE, FILE_COMMON);
      StringTrimLeft(baris);
      StringTrimRight(baris);
      int bar = StringFind(baris, "|");
      string id = bar > 0 ? StringSubstr(baris, 0, bar) : "", cmd = bar > 0 ? StringSubstr(baris, bar + 1) : baris;
      string hasil = Jalankan(cmd);
      if(hasil != "status") Log("BOT KONTROL " + cmd + ": " + hasil);
      TulisStatus(id, hasil);
      return;
   }
   if(TimeCurrent() - status_t >= 30) TulisStatus("", "");
}

//--- event MT5
int OnInit()
{
   if(StringFind(_Symbol, "XAU") < 0)
      Print("Peringatan: SniperBot dirancang untuk XAUUSD (PIP = 0.10)");
   tester  = (bool)MQLInfoInteger(MQL_TESTER);
   hE20H1  = iMA(_Symbol, PERIOD_H1, 20, 0, MODE_EMA, PRICE_CLOSE);
   hE50H1  = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   hE20M30 = iMA(_Symbol, PERIOD_M30, 20, 0, MODE_EMA, PRICE_CLOSE);
   hE50M30 = iMA(_Symbol, PERIOD_M30, 50, 0, MODE_EMA, PRICE_CLOSE);
   if(hE20H1 == INVALID_HANDLE || hE50H1 == INVALID_HANDLE || hE20M30 == INVALID_HANDLE || hE50M30 == INVALID_HANDLE)
      return INIT_FAILED;
   MuatNews();
   if(tester)
   {
      fcsv = FileOpen("SniperBot_tester.csv", FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
      if(fcsv == INVALID_HANDLE) Print("Peringatan: SniperBot_tester.csv gagal dibuka: ", GetLastError());
      else
      {
         FileWrite(fcsv, "jenis", "waktu_utc", "masuk_utc", "side", "entry", "sl", "tp", "lot", "harga_keluar", "pl", "hasil", "saldo", "alasan");
         FileFlush(fcsv);
      }
   }
   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(20);
   Log(StringFormat("BOT MULAI %s, risiko %.0f%% saldo = %.2f %s/trade (lot dinamis dari lebar SL), auto BE %s",
                    _Symbol, RiskFrac() * 100, AccountInfoDouble(ACCOUNT_BALANCE) * RiskFrac(), AccountInfoString(ACCOUNT_CURRENCY),
                    BePips() > 0 ? StringFormat("+%.0f pips", BePips()) : "mati"));
   if(!tester)
      EventSetTimer(2);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(hE20H1); IndicatorRelease(hE50H1); IndicatorRelease(hE20M30); IndicatorRelease(hE50M30);
   if(fcsv != INVALID_HANDLE) { FileClose(fcsv); fcsv = INVALID_HANDLE; }
}

void OnTick()
{
   Kelola();
   datetime t1 = iTime(_Symbol, PERIOD_M1, 0);
   if(t1 == 0 || t1 == last_m1) return;
   bool awal = last_m1 == 0;
   last_m1 = t1;
   datetime t15 = iTime(_Symbol, PERIOD_M15, 0);
   if(t15 != last_m15)
   {
      M15Baru(awal);
      last_m15 = t15;
   }
   if(awal)
   {
      // POI yang masih hidup: putar ulang M1 sejak POI tertua tanpa order (sinyal lama tidak dikejar)
      datetime dari = t1;
      for(int q = 0; q < ArraySize(pois); q++) dari = MathMin(dari, pois[q].t_ok);
      MqlRates m[];
      ArraySetAsSeries(m, false);
      int n = CopyRates(_Symbol, PERIOD_M1, dari, t1 - 60, m);
      for(int x = 0; x < n; x++) ProsesM1(m[x], true);
      return;
   }
   MqlRates b[];
   if(CopyRates(_Symbol, PERIOD_M1, 1, 1, b) == 1)
      ProsesM1(b[0], false);
}

void OnTradeTransaction(const MqlTradeTransaction &t, const MqlTradeRequest &rq, const MqlTradeResult &rs)
{
   if(t.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(t.deal)) return;
   if(HistoryDealGetInteger(t.deal, DEAL_MAGIC) != Magic) return;
   ResetHarian();
   long masuk = HistoryDealGetInteger(t.deal, DEAL_ENTRY);
   double harga = HistoryDealGetDouble(t.deal, DEAL_PRICE);
   if(masuk == DEAL_ENTRY_IN)
   {
      n_entry++;
      Log(StringFormat("BOT TERISI %.2f lot @ %.2f", HistoryDealGetDouble(t.deal, DEAL_VOLUME), harga));
      return;
   }
   if(masuk != DEAL_ENTRY_OUT) return;
   double pl = HistoryDealGetDouble(t.deal, DEAL_PROFIT) + HistoryDealGetDouble(t.deal, DEAL_COMMISSION) + HistoryDealGetDouble(t.deal, DEAL_SWAP);
   long alasan = HistoryDealGetInteger(t.deal, DEAL_REASON);
   double saldo_awal = AccountInfoDouble(ACCOUNT_BALANCE) - pl;
   string hasil = alasan == DEAL_REASON_TP ? "TP"
                  : alasan == DEAL_REASON_SL ? (pl < -0.05 * saldo_awal ? "SL" : "BE")
                  : "DITUTUP";
   if(hasil == "SL") n_sl++;
   if(fcsv != INVALID_HANDLE)
   {
      // id posisi = tiket limit order; SL asli dari order, sebelum digeser BE
      datetime t_out = (datetime)HistoryDealGetInteger(t.deal, DEAL_TIME);
      double vol = HistoryDealGetDouble(t.deal, DEAL_VOLUME);
      long pos = HistoryDealGetInteger(t.deal, DEAL_POSITION_ID);
      datetime t_in = 0;
      double e_in = 0, sl0 = 0, tp0 = 0;
      bool beli = true;
      if(HistorySelectByPosition(pos))
         for(int i = 0; i < HistoryDealsTotal(); i++)
         {
            ulong d = HistoryDealGetTicket(i);
            if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
            t_in = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
            e_in = HistoryDealGetDouble(d, DEAL_PRICE);
            beli = HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY;
            break;
         }
      if(HistoryOrderSelect(pos)) { sl0 = HistoryOrderGetDouble(pos, ORDER_SL); tp0 = HistoryOrderGetDouble(pos, ORDER_TP); }
      Catat("TRADE", KeUtc(t_out), t_in > 0 ? KeUtc(t_in) : 0, beli, e_in, sl0, tp0, vol,
            DoubleToString(harga, 2), DoubleToString(pl, 2), hasil, "");
   }
   Log(StringFormat("BOT %s @ %.2f: P/L %+.2f %s, saldo %.2f", hasil, harga, pl, AccountInfoString(ACCOUNT_CURRENCY), AccountInfoDouble(ACCOUNT_BALANCE)));
}
//+------------------------------------------------------------------+
