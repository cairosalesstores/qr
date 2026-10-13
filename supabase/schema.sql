-- Cairo Sales QR — database structure
-- Run once in the Supabase dashboard: SQL Editor → New query → paste → Run.
-- Safe to run again; every statement is guarded.

-- ---------------------------------------------------------------- products
-- One row per shelf item. Written by the nightly job, read by the product page.
create table if not exists products (
  code                text primary key,              -- the store's item code (كود الصنف)
  model               text not null default '',
  name                text not null default '',      -- name as the branch stock report has it
  title               text not null default '',      -- name as the website has it
  brand               text not null default '',
  category            text not null default '',
  family              text not null default '',      -- tv / hood / washer / other
  price_cash          numeric,                       -- null means "no price on the website"
  price_instalment    numeric,
  in_stock            boolean generated always as (price_cash is not null) stored,
  warranty            text not null default '',
  country             text not null default '',
  specs               jsonb not null default '{}'::jsonb,
  description         text not null default '',
  image_url           text not null default '',
  site_url            text not null default '',      -- product page on cairosales.com
  last_price_cash     numeric,                       -- remembered while a price is missing
  price_missing_runs  smallint not null default 0,
  updated_at          timestamptz not null default now()
);

create index if not exists products_family_price_idx on products (family, price_cash);
create index if not exists products_category_idx on products (category);

-- ------------------------------------------------------------ price history
-- One row per product per run, so we can show "price unchanged for a month".
create table if not exists price_history (
  id                bigserial primary key,
  code              text not null references products (code) on delete cascade,
  price_cash        numeric,
  price_instalment  numeric,
  seen_at           timestamptz not null default now()
);

create index if not exists price_history_code_seen_idx on price_history (code, seen_at desc);

-- -------------------------------------------------------------------- scans
-- One row per QR scan. No names, no phone numbers, no device identifiers.
create table if not exists scans (
  id         bigserial primary key,
  code       text references products (code) on delete set null,
  source     text not null default 'card',  -- card | index | alternative
  scanned_at timestamptz not null default now()
);

create index if not exists scans_code_time_idx on scans (code, scanned_at desc);

-- ------------------------------------------------------------ conversations
-- What customers ask the assistant, and what it answered. For the AI salesperson.
create table if not exists conversations (
  id         bigserial primary key,
  code       text references products (code) on delete set null,
  question   text not null,
  answer     text not null default '',
  handed_over boolean not null default false,  -- did it call a salesperson?
  asked_at   timestamptz not null default now()
);

create index if not exists conversations_time_idx on conversations (asked_at desc);

-- ----------------------------------------------------------------- security
-- Row level security is on everywhere. The browser key may read products and
-- add a scan or a question; it can never change a price or read nothing else.
alter table products      enable row level security;
alter table price_history enable row level security;
alter table scans         enable row level security;
alter table conversations enable row level security;

drop policy if exists "anyone may read products"      on products;
drop policy if exists "anyone may read price history" on price_history;
drop policy if exists "anyone may record a scan"      on scans;
drop policy if exists "anyone may ask a question"     on conversations;

create policy "anyone may read products"      on products      for select to anon using (true);
create policy "anyone may read price history" on price_history for select to anon using (true);
create policy "anyone may record a scan"      on scans         for insert to anon with check (true);
create policy "anyone may ask a question"     on conversations for insert to anon with check (true);

-- Writing prices is done by the nightly job with the secret key, which bypasses
-- these policies. No policy grants the browser key insert or update on products.

-- ------------------------------------------------------- freshness for pages
-- A single view the product page can read: current price plus how old it is.
create or replace view product_prices as
  select code, price_cash, price_instalment, in_stock,
         last_price_cash, price_missing_runs, updated_at
  from products;

-- ------------------------------------------------------------ permissions
-- The project was created with "automatically expose new tables" off, so the
-- browser key has no access until it is granted here — deliberately narrow:
-- read prices, add a scan or a question, nothing else.
grant usage on schema public to anon;

grant select on products, price_history, product_prices to anon;
grant insert on scans, conversations to anon;
grant usage, select on sequence scans_id_seq to anon;
grant usage, select on sequence conversations_id_seq to anon;

-- No grant of insert/update/delete on products or price_history to anon:
-- prices can only ever be written by the nightly job holding the secret key.

-- The nightly job's secret key also needs explicit grants, since this project was
-- created with "automatically expose new tables" off.
grant usage on schema public to service_role;
grant all privileges on all tables in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;
alter default privileges in schema public
  grant all privileges on tables to service_role;
alter default privileges in schema public
  grant all privileges on sequences to service_role;
