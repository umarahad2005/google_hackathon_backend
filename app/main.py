"""
Zimma AI — FastAPI Main Application.

REST + SSE for live agent trace. Wraps the ADK pipeline.

Owner: Backend Engineer (03)
Source: agents/skills/fastapi-service.md
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from app.auth import get_current_user_id

from app.models import (
    CreateServiceRequest,
    ServiceRequestResponse,
    RequestState,
    TraceEvent,
)
from app.services.supabase import (
    create_service_request,
    get_service_request,
    list_service_requests,
    get_user_profile,
    upsert_user_profile,
    update_service_request,
    get_traces,
    get_booking,
    get_follow_ups,
    resolve_user_id,
)
from app.agents.orchestrator import run_orchestrator
from app.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zimma AI",
    description="Agentic AI Service Orchestrator for the Informal Economy",
    version="1.0.0",
)

# CORS for the Flutter app (incl. Flutter web in a browser).
# allow_credentials MUST be False when allow_origins=["*"], otherwise the
# spec forbids the wildcard and browsers reject every request. We auth via
# the Authorization: Bearer header (not cookies), so credentials mode is
# not needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================================================================
# POST /api/requests — Create a service request
# ======================================================================


async def _run_pipeline(request_id: str, message: str, user_id: str, audio_url: str | None):
    """Background task: run the ADK orchestrator pipeline."""
    try:
        await run_orchestrator(
            request_id=request_id,
            raw_message=message,
            user_id=user_id,
            audio_url=audio_url,
            auto_confirm=True,
        )
    except Exception as e:
        logger.error(f"Pipeline failed for {request_id}: {e}")
        from app.services.supabase import update_service_request
        from app.agents.trace_observer import emit_trace
        await emit_trace(
            request_id=request_id,
            agent="orchestrator",
            step="orchestrator.error",
            reasoning=f"Pipeline failed with error: {str(e)}",
            output_data={"error": str(e)},
        )
        await update_service_request(request_id, {"state": "FAILED"})


@app.post("/api/requests", status_code=202)
async def create_request(
    body: CreateServiceRequest,
    background_tasks: BackgroundTasks,
    current_user: str | None = Depends(get_current_user_id),
):
    """
    Create a new service request from a natural-language message.
    Kicks the ADK Orchestrator as a background task.
    Returns immediately with request_id so the app can subscribe to trace.

    Identity precedence: authenticated user (Bearer token) > body.user_id
    > demo user (the supabase layer maps non-UUIDs to the demo user).
    """
    user_id = current_user or body.user_id

    # Create the service request in DB
    sr = await create_service_request(
        raw_message=body.message,
        user_id=user_id,
        audio_url=body.audio_url,
    )
    request_id = sr["id"]

    # Start pipeline in background
    background_tasks.add_task(
        _run_pipeline, request_id, body.message, user_id, body.audio_url
    )

    logger.info(f"Request created: {request_id} — pipeline started in background")
    return {"request_id": request_id, "state": "NEW"}


# ======================================================================
# GET /api/requests/{id} — Get request status + result
# ======================================================================


@app.get("/api/requests/{request_id}")
async def get_request(
    request_id: str,
    current_user: str | None = Depends(get_current_user_id),
):
    """Get the current state and accumulated result of a service request.

    When the caller is authenticated, the request must belong to them —
    otherwise it is reported as not found (no existence leak). Unauthenticated
    callers are allowed through during the auth rollout.
    """
    sr = await get_service_request(request_id)
    if not sr:
        return JSONResponse(
            status_code=404,
            content={"error": "Request not found", "request_id": request_id},
        )

    if current_user is not None and sr.get("user_id") != current_user:
        return JSONResponse(
            status_code=404,
            content={"error": "Request not found", "request_id": request_id},
        )

    traces = await get_traces(request_id)

    response = {
        "request_id": request_id,
        "state": sr.get("state", "UNKNOWN"),
        "intent": sr.get("intent"),
        "result": sr.get("result"),
        "trace_count": len(traces),
        "created_at": sr.get("created_at"),
    }

    # Add booking + follow-up info if available
    result = sr.get("result")
    if result and isinstance(result, dict):
        booking_data = result.get("booking")
        if booking_data and booking_data.get("booking_id"):
            fups = await get_follow_ups(booking_data["booking_id"])
            response["followups"] = fups

    return response


# ======================================================================
# GET /api/requests — list the caller's past requests (History)
# ======================================================================


@app.get("/api/requests")
async def list_requests(
    current_user: str | None = Depends(get_current_user_id),
    limit: int = 50,
):
    """History feed: the authenticated user's past service requests."""
    rows = await list_service_requests(
        current_user or "demo-user", limit=limit
    )
    items = []
    for r in rows:
        result = r.get("result") if isinstance(r.get("result"), dict) else {}
        rec = (result or {}).get("recommended") or {}
        intent = r.get("intent") if isinstance(r.get("intent"), dict) else {}
        items.append({
            "request_id": r.get("id"),
            "state": r.get("state", "UNKNOWN"),
            "raw_message": r.get("raw_message"),
            "service_type": (intent or {}).get("service_type"),
            "location": (intent or {}).get("location_text"),
            "provider_name": rec.get("name"),
            "created_at": r.get("created_at"),
        })
    return {"items": items, "count": len(items)}


# ======================================================================
# GET / PATCH /api/profile — user profile (Settings)
# ======================================================================


@app.get("/api/profile")
async def get_profile(
    current_user: str | None = Depends(get_current_user_id),
):
    """Return the caller's profile (creates a default row if missing)."""
    uid = current_user or "demo-user"
    prof = await get_user_profile(uid)
    if not prof:
        prof = await upsert_user_profile(
            uid, {"display_name": "User", "lang_pref": "en"}
        )
    return prof


@app.patch("/api/profile")
async def patch_profile(
    body: dict,
    current_user: str | None = Depends(get_current_user_id),
):
    """Update display_name and/or lang_pref."""
    allowed = {
        k: v for k, v in (body or {}).items()
        if k in ("display_name", "lang_pref") and v is not None
    }
    if not allowed:
        return JSONResponse(
            status_code=400,
            content={"error": "No updatable fields (display_name, lang_pref)"},
        )
    updated = await upsert_user_profile(current_user or "demo-user", allowed)
    return updated


# ======================================================================
# GET /api/requests/{id}/trace — SSE trace stream
# ======================================================================


@app.get("/api/requests/{request_id}/trace")
async def trace_stream(
    request_id: str,
    current_user: str | None = Depends(get_current_user_id),
):
    """
    SSE stream of agent trace events.
    Streams existing events immediately, then tails for new ones.
    """
    import json

    # Authenticated callers may only stream their own request's trace.
    if current_user is not None:
        owner = await get_service_request(request_id)
        if not owner or owner.get("user_id") != current_user:
            return JSONResponse(
                status_code=404,
                content={"error": "Request not found",
                         "request_id": request_id},
            )

    async def event_generator():
        last_seq = 0
        terminal_states = {"COMPLETED", "FAILED", "NO_PROVIDER"}

        while True:
            traces = await get_traces(request_id)

            for t in traces:
                if t["seq"] > last_seq:
                    last_seq = t["seq"]
                    yield f"data: {json.dumps(t, default=str)}\n\n"

            # Check if request is in terminal state
            sr = await get_service_request(request_id)
            if sr and sr.get("state") in terminal_states:
                # Send any remaining traces
                final_traces = await get_traces(request_id)
                for t in final_traces:
                    if t["seq"] > last_seq:
                        last_seq = t["seq"]
                        yield f"data: {json.dumps(t, default=str)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'state': sr.get('state')})}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ======================================================================
# POST /api/requests/{id}/confirm — Confirm recommendation
# ======================================================================


@app.post("/api/requests/{request_id}/confirm")
async def confirm_request(
    request_id: str,
    body: dict | None = None,
    current_user: str | None = Depends(get_current_user_id),
):
    """
    Confirm the recommendation (or pick an alternative).

    The agentic pipeline auto-confirms + reserves a real slot, so by the
    time the user taps "confirm" the booking already exists. This endpoint
    returns the real recommended provider + booking + alternatives and
    records an explicit user confirmation/selection on the request.
    """
    sr = await get_service_request(request_id)
    if not sr:
        return JSONResponse(
            status_code=404,
            content={"error": "Request not found", "request_id": request_id},
        )
    if current_user is not None and sr.get("user_id") != current_user:
        return JSONResponse(
            status_code=404,
            content={"error": "Request not found", "request_id": request_id},
        )

    body = body or {}
    action = body.get("action", "accept")
    chosen_provider_id = body.get("provider_id")

    result = sr.get("result") or {}
    recommended = result.get("recommended")
    booking = result.get("booking")

    confirmation = {
        "action": action,
        "selected_provider_id": chosen_provider_id
        or (recommended or {}).get("provider_id"),
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "by": current_user or "demo",
    }
    try:
        await update_service_request(request_id, {
            "result": {**result, "user_confirmation": confirmation},
        })
    except Exception as e:
        logger.warning(f"Failed to persist user confirmation: {e}")

    return {
        "request_id": request_id,
        "state": sr.get("state"),
        "recommended": recommended,
        "booking": booking,
        "alternatives": result.get("alternatives", []),
        "user_confirmation": confirmation,
    }


# ======================================================================
# GET /api/bookings/{id}/receipt — Get receipt
# ======================================================================


@app.get("/api/bookings/{booking_id}/receipt")
async def get_receipt(booking_id: str):
    """Get booking receipt."""
    booking = await get_booking(booking_id)
    if not booking:
        return JSONResponse(
            status_code=404,
            content={"error": "Booking not found"},
        )
    return booking


# ======================================================================
# Health check
# ======================================================================


@app.get("/health")
async def health():
    return {"status": "ok", "service": "zimma-ai"}


@app.get("/")
async def root():
    return {
        "service": "Zimma AI",
        "description": "Agentic AI Service Orchestrator for the Informal Economy",
        "version": "1.0.0",
        "docs": "/docs",
    }
