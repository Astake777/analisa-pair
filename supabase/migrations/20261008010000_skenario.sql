-- Skenario news (skenario_news.py) dan data pendukungnya per event news_outlook.
alter table public.news_outlook add column if not exists skenario jsonb, add column if not exists pendukung jsonb;
