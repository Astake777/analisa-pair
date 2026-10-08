-- Jurnal dan status bot MT5 (bot_mt5.py). Real test di demo dinilai dari tabel ini sebelum boleh live.
create table if not exists public.bot_trades (
  id text primary key,                 -- id sinyal (strategi:waktu-side-entry)
  akun text not null check (akun in ('demo', 'real', 'contest')),
  login bigint not null,
  simbol text not null,
  strategi text not null,
  side text not null check (side in ('buy', 'sell')),
  lot numeric not null,
  risiko numeric not null,             -- fraksi ekuitas, mis. 0.05
  entry numeric not null,              -- harga rencana
  harga_isi numeric,                   -- harga terisi (slippage = harga_isi - entry)
  sl numeric not null,
  tp numeric not null,
  order_ticket bigint,
  posisi_ticket bigint,
  dibuat timestamptz not null,
  dibuka timestamptz,
  ditutup timestamptz,
  status text not null,                -- PENDING / TERBUKA / TP / SL / BATAL: ... / DITUTUP: ...
  pl numeric,                          -- profit/loss dalam mata uang akun
  r numeric,
  updated_at timestamptz not null default now()
);
create index if not exists bot_trades_dibuat on public.bot_trades (dibuat desc);

create table if not exists public.bot_status (
  login bigint primary key,
  akun text not null,
  server text not null,
  simbol text,
  mode text not null,                  -- demo / live
  risiko numeric not null,
  ekuitas numeric,
  saldo numeric,
  pl_hari_ini numeric,
  sl_hari_ini int,
  entry_hari_ini int,
  terbuka jsonb,                       -- order/posisi bot yang masih hidup
  pengaman jsonb,                      -- status news, spread, batas harian, kill switch
  syarat_live jsonb,                   -- checklist real test
  updated_at timestamptz not null default now()
);

alter table public.bot_trades enable row level security;
alter table public.bot_status enable row level security;
-- aman dijalankan ulang
drop policy if exists "baca bot_trades" on public.bot_trades;
drop policy if exists "baca bot_status" on public.bot_status;
create policy "baca bot_trades" on public.bot_trades for select to anon, authenticated using (true);
create policy "baca bot_status" on public.bot_status for select to anon, authenticated using (true);
do $$ begin
  alter publication supabase_realtime add table public.bot_trades;
exception when duplicate_object then null; end $$;
do $$ begin
  alter publication supabase_realtime add table public.bot_status;
exception when duplicate_object then null; end $$;
