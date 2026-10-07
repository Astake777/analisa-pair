-- Rekam jejak live setiap setup sniper yang dikabarkan pantau.py (forward test nyata, bukan backtest).
create table if not exists public.setup_log (
  id text primary key,                 -- strategi:waktu-side-entry
  pair text not null,
  strategi text not null,
  side text not null check (side in ('buy', 'sell')),
  entry numeric not null,
  sl numeric not null,
  tp jsonb not null,
  zona jsonb,
  valid boolean not null default false,
  dibuat timestamptz not null,
  terisi boolean not null default false,
  hasil text,                          -- null = masih berjalan; TP / SL / BATAL: ...
  r numeric,                           -- hasil dalam R (TP = +rr, SL = -1, batal = 0)
  updated_at timestamptz not null default now()
);
create index if not exists setup_log_pair_dibuat on public.setup_log (pair, dibuat desc);

alter table public.setup_log enable row level security;
create policy "baca setup_log" on public.setup_log for select to anon, authenticated using (true);
alter publication supabase_realtime add table public.setup_log;
