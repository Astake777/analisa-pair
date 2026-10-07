-- Skema Analisa Pair. Ditulis oleh skill/engine Python (service role), dibaca web React (anon).

create table public.analyses (
  id uuid primary key default gen_random_uuid(),
  pair text not null,
  mode text not null check (mode in ('scalp', 'intraday', 'swing')),
  created_at timestamptz not null default now(),
  status text not null,
  keyakinan text,
  price numeric,
  -- bentuk lengkap: supabase/payload.example.json
  payload jsonb not null
);
create index analyses_pair_mode_time on public.analyses (pair, mode, created_at desc);

create table public.news_outlook (
  id text primary key,                 -- id event dari kalender TradingView
  pair text not null default 'XAUUSD',
  event_time timestamptz not null,
  title text not null,
  komponen text,                       -- kunci news.SPEC, mis. 'nfp', 'core_cpi_mm'; null kalau tidak terpetakan
  importance int,
  forecast numeric,
  previous numeric,
  actual numeric,
  dampak jsonb,                        -- {"panas": "SELL", "dingin": "BUY"} untuk pair
  lean jsonb,                          -- {"arah","kekuatan","alasan","indikator":[...]} atau null
  hasil jsonb,                         -- output news.score setelah actual keluar, atau null
  updated_at timestamptz not null default now()
);
create index news_outlook_time on public.news_outlook (event_time);

create table public.backtest_results (
  id bigint generated always as identity primary key,
  run_id text not null,                -- satu run = satu id, mis. '20261007T1400-XAUUSD-intraday'
  run_at timestamptz not null default now(),
  pair text not null,
  mode text not null,
  strategy text not null,
  regime text not null,                -- 'semua' untuk total
  sample text not null check (sample in ('in', 'oos')),
  trades int not null,
  winrate numeric,
  avg_r numeric,
  expectancy numeric,
  max_dd_r numeric,
  profit_factor numeric,
  params jsonb
);
create index backtest_results_run on public.backtest_results (pair, mode, run_at desc);

create table public.macro_series (
  series text not null,                -- 'DFII10', 'T10YIE', 'COT_GOLD_MM_NET', ...
  date date not null,
  value numeric not null,
  primary key (series, date)
);

alter table public.analyses enable row level security;
alter table public.news_outlook enable row level security;
alter table public.backtest_results enable row level security;
alter table public.macro_series enable row level security;

-- Baca untuk semua (web memakai anon key); tulis hanya lewat service role, yang melewati RLS.
create policy "baca analyses" on public.analyses for select to anon, authenticated using (true);
create policy "baca news_outlook" on public.news_outlook for select to anon, authenticated using (true);
create policy "baca backtest_results" on public.backtest_results for select to anon, authenticated using (true);
create policy "baca macro_series" on public.macro_series for select to anon, authenticated using (true);

alter publication supabase_realtime add table public.analyses, public.news_outlook, public.backtest_results;
