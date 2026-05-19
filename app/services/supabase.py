"""
Zimma AI — Supabase Data Access Layer.

Provides async CRUD operations for all tables.
Uses the service-role key server-side (FastAPI).
Source: agents/skills/supabase-data-layer.md

Owner: Backend Engineer (03)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

from app.settings import get_settings

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase() -> Client:
    """Get or create the Supabase client (service role)."""
    global _client
    if _client is None:
        s = get_settings()
        _client = create_client(s.supabase_url, s.supabase_service_key)
    return _client


# service_requests.user_id is a Postgres `uuid` column. The Flutter client
# sends a friendly id ("demo-user") by default, which Postgres rejects with
# 22P02. Map any non-UUID id to the seeded demo user UUID.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
DEMO_USER_UUID = "00000000-0000-0000-0000-000000000001"


def resolve_user_id(user_id: str | None) -> str:
    """Return a valid UUID for the user, falling back to the demo user."""
    return user_id if _UUID_RE.match(user_id or "") else DEMO_USER_UUID


# ======================================================================
# Service Requests
# ======================================================================


async def create_service_request(
    raw_message: str,
    user_id: str = "demo-user",
    audio_url: str | None = None,
) -> dict:
    """INSERT a new service_request, state=NEW (auto-generates UUID)."""
    sb = get_supabase()
    row = {
        "raw_message": raw_message,
        "user_id": resolve_user_id(user_id),
        "audio_url": audio_url,
        "state": "NEW",
    }
    result = sb.table("service_requests").insert(row).execute()
    return result.data[0]


async def create_service_request_with_id(
    request_id: str,
    raw_message: str,
    user_id: str = "demo-user",
    audio_url: str | None = None,
) -> dict:
    """INSERT a service_request with a pre-determined UUID.
    Required by the orchestrator so agent_traces FK constraint can be satisfied
    before the pipeline emits its first trace.
    """
    resolved_user_id = resolve_user_id(user_id)

    sb = get_supabase()
    row = {
        "id": request_id,
        "raw_message": raw_message,
        "user_id": resolved_user_id,
        "audio_url": audio_url,
        "state": "NEW",
    }
    result = sb.table("service_requests").insert(row).execute()
    return result.data[0]


async def update_service_request(
    request_id: str,
    updates: dict,
) -> dict:
    """UPDATE a service_request row."""
    sb = get_supabase()
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (
        sb.table("service_requests")
        .update(updates)
        .eq("id", request_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def get_service_request(request_id: str) -> dict | None:
    """GET a service_request by id."""
    sb = get_supabase()
    result = (
        sb.table("service_requests")
        .select("*")
        .eq("id", request_id)
        .execute()
    )
    return result.data[0] if result.data else None


async def list_service_requests(user_id: str, limit: int = 50) -> list[dict]:
    """List a user's past service requests, newest first (for History)."""
    sb = get_supabase()
    result = (
        sb.table("service_requests")
        .select("id, state, raw_message, intent, result, created_at")
        .eq("user_id", resolve_user_id(user_id))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data if result.data else []


# ======================================================================
# User profile
# ======================================================================


async def get_user_profile(user_id: str) -> dict | None:
    """GET the public.users profile row for a user."""
    sb = get_supabase()
    result = (
        sb.table("users")
        .select("*")
        .eq("id", resolve_user_id(user_id))
        .execute()
    )
    return result.data[0] if result.data else None


async def upsert_user_profile(user_id: str, fields: dict) -> dict:
    """Create/update a user's profile (display_name, lang_pref)."""
    sb = get_supabase()
    row = {"id": resolve_user_id(user_id), **fields}
    result = sb.table("users").upsert(row).execute()
    return result.data[0] if result.data else {}


# ======================================================================
# Providers (PostGIS queries)
# ======================================================================


async def find_providers_within(
    category: str,
    lat: float,
    lng: float,
    radius_m: float,
    limit: int = 10,
) -> list[dict]:
    """
    Find providers within radius using PostGIS ST_DWithin.
    Returns providers sorted by distance.
    """
    sb = get_supabase()
    # Use RPC for PostGIS spatial query
    result = sb.rpc(
        "find_providers_nearby",
        {
            "cat": category,
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m,
            "max_results": limit,
        },
    ).execute()
    return result.data if result.data else []


async def find_providers_by_category(
    category: str,
    limit: int = 20,
) -> list[dict]:
    """Fallback: find providers by category without geo filter."""
    sb = get_supabase()
    result = (
        sb.table("providers")
        .select("*")
        .eq("category", category)
        .limit(limit)
        .execute()
    )
    return result.data if result.data else []


# ======================================================================
# Provider Availability
# ======================================================================


async def get_availability(
    provider_id: str,
    from_time: datetime,
    to_time: datetime,
) -> list[dict]:
    """Get available (not booked) slots for a provider in a time window."""
    sb = get_supabase()
    result = (
        sb.table("provider_availability")
        .select("*")
        .eq("provider_id", provider_id)
        .eq("is_booked", False)
        .gte("slot_start", from_time.isoformat())
        .lte("slot_end", to_time.isoformat())
        .order("slot_start")
        .execute()
    )
    return result.data if result.data else []


async def book_slot(slot_id: int) -> dict:
    """Mark a slot as booked."""
    sb = get_supabase()
    result = (
        sb.table("provider_availability")
        .update({"is_booked": True})
        .eq("id", slot_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def unbook_slot(slot_id: int) -> dict:
    """Release a previously-reserved slot (e.g. provider declined)."""
    sb = get_supabase()
    result = (
        sb.table("provider_availability")
        .update({"is_booked": False})
        .eq("id", slot_id)
        .execute()
    )
    return result.data[0] if result.data else {}


# ======================================================================
# Bookings
# ======================================================================


async def create_booking(booking_data: dict) -> dict:
    """INSERT a booking row — the CRITICAL state change."""
    sb = get_supabase()
    result = sb.table("bookings").insert(booking_data).execute()
    logger.info(f"Booking created: {result.data[0]['id']}")
    return result.data[0]


async def get_booking(booking_id: str) -> dict | None:
    """GET a booking by id."""
    sb = get_supabase()
    result = (
        sb.table("bookings").select("*").eq("id", booking_id).execute()
    )
    return result.data[0] if result.data else None


# ======================================================================
# Follow-ups
# ======================================================================


async def create_follow_up(followup_data: dict) -> dict:
    """INSERT a follow_up row."""
    sb = get_supabase()
    result = sb.table("follow_ups").insert(followup_data).execute()
    return result.data[0]


async def update_follow_up(followup_id: str, updates: dict) -> dict:
    """UPDATE a follow_up row."""
    sb = get_supabase()
    result = (
        sb.table("follow_ups")
        .update(updates)
        .eq("id", followup_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def get_follow_ups(booking_id: str) -> list[dict]:
    """GET all follow_ups for a booking."""
    sb = get_supabase()
    result = (
        sb.table("follow_ups")
        .select("*")
        .eq("booking_id", booking_id)
        .order("fire_at")
        .execute()
    )
    return result.data if result.data else []


# ======================================================================
# Agent Traces
# ======================================================================


async def insert_trace(trace_data: dict) -> dict:
    """INSERT a trace event — streams to Realtime automatically."""
    sb = get_supabase()
    result = sb.table("agent_traces").insert(trace_data).execute()
    return result.data[0]


async def get_traces(request_id: str) -> list[dict]:
    """GET all trace events for a request, ordered by seq."""
    sb = get_supabase()
    result = (
        sb.table("agent_traces")
        .select("*")
        .eq("request_id", request_id)
        .order("seq")
        .execute()
    )
    return result.data if result.data else []


async def get_next_seq(request_id: str) -> int:
    """
    Atomically allocate the next gap-free seq for a request via the
    next_trace_seq() RPC (migration 0003). This is correct across multiple
    backend replicas; the old SELECT max+1 raced and collided. Falls back
    to max+1 only if the RPC is missing (migration not yet applied).
    """
    sb = get_supabase()
    try:
        res = sb.rpc("next_trace_seq", {"p_request_id": request_id}).execute()
        val = res.data
        if isinstance(val, list):
            val = val[0] if val else None
        if val is not None:
            return int(val)
    except Exception as e:
        logger.warning(f"next_trace_seq RPC unavailable, using fallback: {e}")

    result = (
        sb.table("agent_traces")
        .select("seq")
        .eq("request_id", request_id)
        .order("seq", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["seq"] + 1
    return 1
