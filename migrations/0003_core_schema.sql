-- ============================================================================
-- Zimma AI — Migration 0003: Core schema + PostGIS + RPCs (version-controlled)
-- ============================================================================
--
-- WHY
--   The base tables and the find_providers_nearby PostGIS function existed
--   only in the live Supabase project, never in source control. This makes
--   the system reproducible and adds the atomic trace-seq RPC.
--
-- SAFETY
--   Fully idempotent: `create extension/table/index if not exists`,
--   `create or replace function`, and `add column if not exists` for columns
--   the code requires. Running it against the existing live DB will NOT drop
--   or rewrite data — it only adds what's missing. Apply via Supabase SQL
--   Editor (or psql -f). Run AFTER 0001/0002.
-- ============================================================================

begin;

create extension if not exists postgis;

-- ---------------------------------------------------------------------------
-- users (also created by 0002's assumptions; keep idempotent here)
-- ---------------------------------------------------------------------------
create table if not exists public.users (
  id           uuid primary key,
  display_name text,
  lang_pref    text default 'en',
  created_at   timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- service_requests
-- ---------------------------------------------------------------------------
create table if not exists public.service_requests (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references public.users(id),
  raw_message text not null,
  audio_url   text,
  state       text not null default 'NEW',
  intent      jsonb,
  result      jsonb,
  trace_count int default 0,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
alter table public.service_requests add column if not exists intent      jsonb;
alter table public.service_requests add column if not exists result      jsonb;
alter table public.service_requests add column if not exists trace_count int default 0;
alter table public.service_requests add column if not exists updated_at  timestamptz default now();

-- ---------------------------------------------------------------------------
-- providers
-- ---------------------------------------------------------------------------
create table if not exists public.providers (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  category      text not null,
  geo           geography(Point, 4326),
  rating        numeric,
  price_band    text,
  languages     jsonb default '[]'::jsonb,
  working_hours jsonb default '{}'::jsonb,
  phone         text,
  is_synthetic  boolean default true,
  created_at    timestamptz default now()
);
create index if not exists providers_geo_gix      on public.providers using gist (geo);
create index if not exists providers_category_idx on public.providers (category);

-- ---------------------------------------------------------------------------
-- provider_availability  (book_slot updates by integer id)
-- ---------------------------------------------------------------------------
create table if not exists public.provider_availability (
  id          bigint generated always as identity primary key,
  provider_id uuid references public.providers(id) on delete cascade,
  slot_start  timestamptz not null,
  slot_end    timestamptz not null,
  is_booked   boolean default false
);
create index if not exists pa_provider_start_idx
  on public.provider_availability (provider_id, slot_start);
create index if not exists pa_open_idx
  on public.provider_availability (provider_id, is_booked, slot_start);

-- ---------------------------------------------------------------------------
-- bookings  (user_id kept as text — booking agent may pass a non-uuid)
-- ---------------------------------------------------------------------------
create table if not exists public.bookings (
  id             uuid primary key default gen_random_uuid(),
  request_id     uuid references public.service_requests(id) on delete cascade,
  provider_id    uuid references public.providers(id),
  user_id        text,
  slot_start     timestamptz,
  slot_end       timestamptz,
  status         text default 'confirmed',
  price_estimate text,
  confirmation   jsonb default '{}'::jsonb,
  slot_id        bigint,
  created_at     timestamptz default now()
);
alter table public.bookings add column if not exists slot_id bigint;

-- ---------------------------------------------------------------------------
-- follow_ups
-- ---------------------------------------------------------------------------
create table if not exists public.follow_ups (
  id         uuid primary key default gen_random_uuid(),
  booking_id uuid references public.bookings(id) on delete cascade,
  kind       text not null,
  fire_at    timestamptz not null,
  status     text default 'scheduled',
  message    text,
  simulated  boolean default true,
  created_at timestamptz default now()
);
create index if not exists fu_booking_fire_idx
  on public.follow_ups (booking_id, fire_at);

-- ---------------------------------------------------------------------------
-- agent_traces  (shape = TraceEvent.model_dump)
-- ---------------------------------------------------------------------------
create table if not exists public.agent_traces (
  id         bigint generated always as identity primary key,
  request_id uuid references public.service_requests(id) on delete cascade,
  seq        int  not null,
  agent      text not null,
  step       text not null,
  input      jsonb default '{}'::jsonb,
  reasoning  text,
  tool_calls jsonb default '[]'::jsonb,
  output     jsonb default '{}'::jsonb,
  latency_ms int  default 0,
  degraded   boolean default false,
  simulated  boolean default false,
  model      text,
  ts         timestamptz default now(),
  unique (request_id, seq)
);
alter table public.agent_traces add column if not exists model text;
create index if not exists agent_traces_req_seq_idx
  on public.agent_traces (request_id, seq);

-- ---------------------------------------------------------------------------
-- RPC: find_providers_nearby  (PostGIS radius search, distance-sorted)
--   Signature MUST match app/services/supabase.find_providers_within:
--   (cat, lat, lng, radius_m, max_results)
--   Drop the prior version first: CREATE OR REPLACE cannot change a
--   function's return type (Postgres error 42P13).
-- ---------------------------------------------------------------------------
drop function if exists public.find_providers_nearby(
  text, double precision, double precision, double precision, integer
);

create or replace function public.find_providers_nearby(
  cat         text,
  lat         double precision,
  lng         double precision,
  radius_m    double precision,
  max_results integer
)
returns table (
  id            uuid,
  name          text,
  category      text,
  rating        numeric,
  price_band    text,
  languages     jsonb,
  working_hours jsonb,
  phone         text,
  lat           double precision,
  lng           double precision,
  distance_km   double precision
)
language sql
stable
as $$
  -- Explicit casts so the function works regardless of the providers
  -- table's actual column types (e.g. languages may be text[] not jsonb).
  select
    p.id,
    p.name::text,
    p.category::text,
    p.rating::numeric,
    p.price_band::text,
    to_jsonb(p.languages) as languages,
    to_jsonb(p.working_hours) as working_hours,
    p.phone::text,
    st_y(p.geo::geometry)::double precision as lat,
    st_x(p.geo::geometry)::double precision as lng,
    round((st_distance(
      p.geo,
      st_setsrid(st_makepoint(lng, lat), 4326)::geography
    ) / 1000.0)::numeric, 3)::double precision as distance_km
  from public.providers p
  where p.category = cat
    and p.geo is not null
    and st_dwithin(
      p.geo,
      st_setsrid(st_makepoint(lng, lat), 4326)::geography,
      radius_m
    )
  order by distance_km asc
  limit max_results;
$$;

-- ---------------------------------------------------------------------------
-- RPC: next_trace_seq  (atomic, gap-free per request — fixes seq race)
-- ---------------------------------------------------------------------------
create table if not exists public.trace_seq (
  request_id uuid primary key,
  last_seq   int  not null default 0
);

drop function if exists public.next_trace_seq(uuid);

create or replace function public.next_trace_seq(p_request_id uuid)
returns int
language plpgsql
as $$
declare
  n int;
begin
  insert into public.trace_seq (request_id, last_seq)
  values (p_request_id, 1)
  on conflict (request_id)
  do update set last_seq = public.trace_seq.last_seq + 1
  returning last_seq into n;
  return n;
end;
$$;

commit;

-- ============================================================================
-- ROLLBACK (only the additions unique to 0003 — does NOT drop core data)
-- ============================================================================
-- begin;
-- drop function if exists public.find_providers_nearby(text,double precision,double precision,double precision,integer);
-- drop function if exists public.next_trace_seq(uuid);
-- drop table if exists public.trace_seq;
-- commit;
