-- Cairo Sales QR — conversation logging for supervisors
-- Run in the Supabase dashboard: SQL Editor → New query → paste → Run.
-- Extends the conversations table created by schema.sql and adds the views
-- a supervisor reads. No personal data is stored anywhere here.

-- ------------------------------------------------------------- the columns
alter table conversations add column if not exists session_id   uuid;        -- groups one customer's visit
alter table conversations add column if not exists turn         smallint not null default 1;
alter table conversations add column if not exists language     text not null default 'ar';
alter table conversations add column if not exists voice_used   boolean not null default false;
alter table conversations add column if not exists intent       text;        -- price | fit | compare | stock | warranty | discount | other
alter table conversations add column if not exists answered     boolean not null default true;  -- false when it had to refuse
alter table conversations add column if not exists refusal_reason text;      -- outside_data | discount | personal | unsafe
alter table conversations add column if not exists answer_ms    integer;     -- how long the answer took
alter table conversations add column if not exists model        text;
alter table conversations add column if not exists cost_usd     numeric(10,5);

create index if not exists conversations_session_idx on conversations (session_id, turn);
create index if not exists conversations_code_idx    on conversations (code, asked_at desc);
create index if not exists conversations_intent_idx  on conversations (intent);

-- A customer's phone may add rows, and must never read them back:
-- one shopper can never see another shopper's questions.
drop policy if exists "anyone may ask a question" on conversations;
create policy "anyone may ask a question" on conversations for insert to anon with check (true);

-- --------------------------------------------------------------- the views
-- Supervisors read these in the dashboard (or a protected page later).

-- What customers ask most, and how often we fail to answer it
create or replace view question_themes as
  select coalesce(intent, 'other')                            as intent,
         count(*)                                             as questions,
         count(*) filter (where not answered)                 as refused,
         round(100.0 * count(*) filter (where not answered) / count(*), 1) as refused_pct,
         count(*) filter (where handed_over)                  as handed_to_salesperson,
         round(avg(answer_ms))                                as avg_ms
  from conversations
  group by 1
  order by questions desc;

-- Which products draw questions — interest that may not show up as sales
create or replace view product_interest as
  select c.code,
         p.title,
         p.price_cash,
         count(*)                                   as questions,
         count(distinct c.session_id)               as visits,
         count(*) filter (where c.handed_over)      as handovers,
         max(c.asked_at)                            as last_asked
  from conversations c
  left join products p on p.code = c.code
  group by c.code, p.title, p.price_cash
  order by questions desc;

-- Everything the assistant could not answer — the list to act on weekly
create or replace view unanswered_questions as
  select asked_at, code, question, refusal_reason
  from conversations
  where not answered
  order by asked_at desc;

-- Day by day, to see whether use is growing
create or replace view assistant_daily as
  select date_trunc('day', asked_at)::date          as day,
         count(*)                                   as questions,
         count(distinct session_id)                 as conversations,
         count(*) filter (where voice_used)         as spoken,
         count(*) filter (where handed_over)        as handovers,
         round(sum(cost_usd)::numeric, 3)           as cost_usd
  from conversations
  group by 1
  order by 1 desc;

grant select on question_themes, product_interest, unanswered_questions, assistant_daily to service_role;

-- ------------------------------------------------------------- housekeeping
-- Questions are kept for a year, then dropped. Call this from the nightly job.
create or replace function prune_conversations() returns void language sql as $$
  delete from conversations where asked_at < now() - interval '12 months';
$$;
