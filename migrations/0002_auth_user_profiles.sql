-- ============================================================================
-- Zimma AI — Migration 0002: Auto-provision public.users from auth.users
-- ============================================================================
--
-- PROBLEM
--   service_requests.user_id has a FK to public.users(id). Supabase Auth
--   creates rows in auth.users, NOT public.users, so the first request from
--   any signed-in user fails with:
--     23503 ... violates foreign key constraint "service_requests_user_id_fkey"
--     Key (user_id)=(<auth uid>) is not present in table "users".
--
-- FIX
--   1. Backfill: create a public.users row for every existing auth user
--      (so the account you already signed up with works immediately).
--   2. Trigger: create the public.users row automatically on every future
--      sign-up. This is the standard Supabase pattern.
--
-- public.users columns (from scripts/seed.py): id (uuid PK),
-- display_name (text), lang_pref (text). Adjust below if your schema differs.
--
-- No backend redeploy needed — this is a pure DB change. Apply via the
-- Supabase Dashboard → SQL Editor (or psql -f). Rollback at the bottom.
-- ============================================================================

begin;

-- 1) Backfill existing auth users -------------------------------------------
insert into public.users (id, display_name, lang_pref)
select
  u.id,
  coalesce(
    u.raw_user_meta_data ->> 'full_name',
    split_part(u.email, '@', 1),
    'User'
  ),
  'en'
from auth.users u
on conflict (id) do nothing;

-- 2) Trigger function: provision on sign-up ---------------------------------
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.users (id, display_name, lang_pref)
  values (
    new.id,
    coalesce(
      new.raw_user_meta_data ->> 'full_name',
      split_part(new.email, '@', 1),
      'User'
    ),
    'en'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

-- 3) Attach the trigger to auth.users ---------------------------------------
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row
  execute function public.handle_new_auth_user();

commit;

-- ============================================================================
-- ROLLBACK (run to revert migration 0002)
-- ============================================================================
-- begin;
-- drop trigger if exists on_auth_user_created on auth.users;
-- drop function if exists public.handle_new_auth_user();
-- -- (backfilled public.users rows are left in place on purpose;
-- --  delete them manually only if you really want to.)
-- commit;
