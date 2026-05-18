-- ============================================================================
-- Zimma AI — Migration 0001: Enable Row-Level Security (multi-tenancy)
-- ============================================================================
--
-- WHAT THIS DOES
--   Turns on RLS and adds owner-scoped policies so a signed-in user can only
--   see their own service requests (and the traces/bookings/follow-ups that
--   hang off them). The providers catalog stays publicly readable.
--
-- IMPORTANT — READ BEFORE APPLYING
--   * The FastAPI backend connects with the Supabase SERVICE ROLE key, which
--     BYPASSES RLS. So enabling this does NOT break the backend, and the
--     backend stays responsible for per-user scoping in code (already done in
--     app/main.py via the Bearer token). RLS here is defense-in-depth for any
--     direct PostgREST / client access.
--   * `service_requests.user_id` is assumed to hold the Supabase Auth user id
--     (auth.users.id). Verify that FK/semantics before applying.
--   * The child-table policies assume these columns exist:
--         agent_traces.request_id  -> service_requests.id
--         bookings.request_id      -> service_requests.id
--         follow_ups.booking_id    -> bookings.id
--     If your bookings table links to the request differently, adjust the
--     EXISTS sub-queries below (they are the only thing you'd change).
--   * Run in a transaction; a rollback section is provided at the bottom.
--
-- HOW TO APPLY
--   Supabase Dashboard → SQL Editor → paste this file → Run.
--   (Or `psql "$DATABASE_URL" -f migrations/0001_enable_rls.sql`.)
-- ============================================================================

begin;

-- ----------------------------------------------------------------------------
-- service_requests — the tenancy root
-- ----------------------------------------------------------------------------
alter table public.service_requests enable row level security;

drop policy if exists sr_select_own on public.service_requests;
create policy sr_select_own on public.service_requests
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists sr_insert_own on public.service_requests;
create policy sr_insert_own on public.service_requests
  for insert to authenticated
  with check (user_id = auth.uid());

drop policy if exists sr_update_own on public.service_requests;
create policy sr_update_own on public.service_requests
  for update to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ----------------------------------------------------------------------------
-- agent_traces — scoped through the parent request
-- ----------------------------------------------------------------------------
alter table public.agent_traces enable row level security;

drop policy if exists at_select_own on public.agent_traces;
create policy at_select_own on public.agent_traces
  for select to authenticated
  using (
    exists (
      select 1 from public.service_requests sr
      where sr.id = agent_traces.request_id
        and sr.user_id = auth.uid()
    )
  );

-- ----------------------------------------------------------------------------
-- bookings — scoped through the parent request
-- ----------------------------------------------------------------------------
alter table public.bookings enable row level security;

drop policy if exists bk_select_own on public.bookings;
create policy bk_select_own on public.bookings
  for select to authenticated
  using (
    exists (
      select 1 from public.service_requests sr
      where sr.id = bookings.request_id
        and sr.user_id = auth.uid()
    )
  );

-- ----------------------------------------------------------------------------
-- follow_ups — scoped through booking -> request
-- ----------------------------------------------------------------------------
alter table public.follow_ups enable row level security;

drop policy if exists fu_select_own on public.follow_ups;
create policy fu_select_own on public.follow_ups
  for select to authenticated
  using (
    exists (
      select 1
      from public.bookings b
      join public.service_requests sr on sr.id = b.request_id
      where b.id = follow_ups.booking_id
        and sr.user_id = auth.uid()
    )
  );

-- ----------------------------------------------------------------------------
-- users — a user sees only their own row
-- ----------------------------------------------------------------------------
alter table public.users enable row level security;

drop policy if exists usr_select_self on public.users;
create policy usr_select_self on public.users
  for select to authenticated
  using (id = auth.uid());

-- ----------------------------------------------------------------------------
-- providers / provider_availability — public catalog (read-only to everyone)
-- ----------------------------------------------------------------------------
alter table public.providers enable row level security;

drop policy if exists prov_read_all on public.providers;
create policy prov_read_all on public.providers
  for select to anon, authenticated
  using (true);

alter table public.provider_availability enable row level security;

drop policy if exists pa_read_all on public.provider_availability;
create policy pa_read_all on public.provider_availability
  for select to anon, authenticated
  using (true);

commit;

-- ============================================================================
-- ROLLBACK (run this block to fully revert migration 0001)
-- ============================================================================
-- begin;
-- drop policy if exists sr_select_own  on public.service_requests;
-- drop policy if exists sr_insert_own  on public.service_requests;
-- drop policy if exists sr_update_own  on public.service_requests;
-- drop policy if exists at_select_own  on public.agent_traces;
-- drop policy if exists bk_select_own  on public.bookings;
-- drop policy if exists fu_select_own  on public.follow_ups;
-- drop policy if exists usr_select_self on public.users;
-- drop policy if exists prov_read_all  on public.providers;
-- drop policy if exists pa_read_all    on public.provider_availability;
-- alter table public.service_requests      disable row level security;
-- alter table public.agent_traces          disable row level security;
-- alter table public.bookings              disable row level security;
-- alter table public.follow_ups            disable row level security;
-- alter table public.users                 disable row level security;
-- alter table public.providers             disable row level security;
-- alter table public.provider_availability disable row level security;
-- commit;
