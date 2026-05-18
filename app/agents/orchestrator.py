"""
Zimma AI — Orchestrator Agent (ADK root).

The brain stem. Owns RequestContext and the state machine.
Plans the run, routes to one sub-agent at a time, handles failures,
decides when the request is complete.

Owner: AI/Agent Engineer (05)
Source: agents/subagents/orchestrator-agent.md
        agents/orchestration/workflow-state-machine.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models import (
    RequestContext,
    RequestState,
    ServiceIntent,
)
from app.agents.intent_agent import run_intent_agent
from app.agents.discovery_agent import run_discovery_agent
from app.agents.ranking_agent import run_ranking_agent
from app.agents.booking_agent import run_booking_agent
from app.agents.followup_agent import run_followup_agent
from app.agents.trace_observer import TraceContext, emit_trace
from app.services.supabase import update_service_request, create_service_request_with_id

logger = logging.getLogger(__name__)


async def _transition(
    ctx: RequestContext,
    new_state: RequestState,
    reasoning: str,
) -> None:
    """
    Execute a state-machine transition.
    Updates the context, persists to Supabase, and emits a trace.
    """
    old_state = ctx.state
    ctx.state = new_state

    # Persist state change
    try:
        await update_service_request(ctx.request_id, {
            "state": new_state.value,
        })
    except Exception as e:
        logger.error(f"Failed to persist state transition: {e}")

    logger.info(f"STATE: {old_state.value} → {new_state.value} | {reasoning}")


async def run_orchestrator(
    request_id: str,
    raw_message: str,
    user_id: str = "demo-user",
    audio_url: str | None = None,
    auto_confirm: bool = True,
) -> RequestContext:
    """
    Run the full agentic pipeline for a service request.

    State machine: NEW → UNDERSTANDING → DISCOVERING → RANKING →
    RECOMMENDED → BOOKING → CONFIRMED → FOLLOW_UP_SCHEDULED → COMPLETED

    Hub-and-spoke: the Orchestrator calls each sub-agent in sequence.
    No sub-agent calls another sub-agent directly.
    """
    ctx = RequestContext(
        request_id=request_id,
        raw_message=raw_message,
        audio_url=audio_url,
        user_id=user_id,
        state=RequestState.NEW,
    )

    # ── Pre-insert the service_request row (all FK tables depend on this) ──
    try:
        await create_service_request_with_id(
            request_id=request_id,
            raw_message=raw_message,
            user_id=user_id,
            audio_url=audio_url,
        )
    except Exception as e:
        logger.warning(f"Could not pre-create service_request row: {e}")

    # ──────────────────────────────────────────────────────────────────
    # STEP 1: NEW → UNDERSTANDING (Intent/NLU Agent)
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.plan",
        reasoning=(
            f"New service request received: \"{raw_message}\". "
            f"Planning run: Intent extraction → Provider discovery → "
            f"Ranking with reasoning → Booking simulation → Follow-up scheduling. "
            f"Routing to Intent/NLU Agent first."
        ),
        output_data={"state": "NEW", "plan": "intent → discovery → ranking → booking → followup"},
    )

    await _transition(ctx, RequestState.UNDERSTANDING, "Routing to Intent/NLU Agent for extraction")

    intent = await run_intent_agent(request_id, raw_message, audio_url)
    ctx.intent = intent
    ctx.language = intent.language

    # Check if clarification is needed
    if intent.confidence < 0.6 or intent.missing:
        await _transition(
            ctx, RequestState.CLARIFY,
            f"Low confidence ({intent.confidence:.2f}) or missing slots: {intent.missing}. "
            f"Would ask a clarifying question, but in demo mode proceeding with best-effort extraction."
        )
        # In demo mode, proceed anyway (real app would return a question)
        await _transition(ctx, RequestState.UNDERSTANDING, "Demo mode: proceeding with best-effort extraction")

    # Update service request with intent
    try:
        await update_service_request(request_id, {
            "intent": intent.model_dump(mode="json"),
            "state": RequestState.DISCOVERING.value,
        })
    except Exception as e:
        logger.warning(f"Failed to update intent: {e}")

    # ──────────────────────────────────────────────────────────────────
    # STEP 2: UNDERSTANDING → DISCOVERING (Provider Discovery Agent)
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.route",
        reasoning=(
            f"Intent extracted: {intent.service_type} in {intent.location_text} "
            f"at {intent.time_text} (confidence={intent.confidence:.2f}, "
            f"language={intent.language.value}). "
            f"Routing to Provider Discovery Agent."
        ),
        output_data={
            "service": intent.service_type,
            "location": intent.location_text,
            "time": intent.time_text,
            "confidence": intent.confidence,
        },
    )

    await _transition(ctx, RequestState.DISCOVERING, "Intent extracted → routing to Provider Discovery")

    discovery_result = await run_discovery_agent(request_id, intent)
    ctx.candidates = discovery_result.candidates

    # Check for empty results
    if not discovery_result.candidates:
        await _transition(
            ctx, RequestState.NO_PROVIDER,
            f"No providers found for {intent.service_type} near {intent.location_text} "
            f"(radius={discovery_result.radius_used_km}km, degraded={discovery_result.degraded})"
        )
        await emit_trace(
            request_id=request_id,
            agent="orchestrator",
            step="discovery.empty",
            reasoning=(
                f"No providers found. Searched {discovery_result.radius_used_km}km radius. "
                f"Generating graceful message for user."
            ),
            output_data={"state": "NO_PROVIDER"},
        )
        return ctx

    # ──────────────────────────────────────────────────────────────────
    # STEP 3: DISCOVERING → RANKING (Ranking & Decision Agent)
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.route",
        reasoning=(
            f"Discovery returned {len(discovery_result.candidates)} candidates "
            f"(radius={discovery_result.radius_used_km}km). "
            f"Routing to Ranking & Decision Agent for scoring and recommendation."
        ),
        output_data={"candidate_count": len(discovery_result.candidates)},
    )

    await _transition(ctx, RequestState.RANKING, f"{len(discovery_result.candidates)} candidates found → routing to Ranking")

    ranking_result = await run_ranking_agent(request_id, discovery_result, intent)
    ctx.ranked = ranking_result.ranked
    ctx.selected = ranking_result.recommended

    # ──────────────────────────────────────────────────────────────────
    # STEP 4: RANKING → RECOMMENDED → BOOKING
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="decision.recommend",
        reasoning=(
            f"Recommendation: {ranking_result.recommended.name} "
            f"(rank #1, score={ranking_result.recommended.score:.3f}, "
            f"distance={ranking_result.recommended.distance_km}km). "
            f"{ranking_result.reasoning[:200]}"
        ),
        output_data={
            "recommended": ranking_result.recommended.name,
            "score": ranking_result.recommended.score,
            "alternatives": [a.name for a in ranking_result.alternatives],
        },
    )

    await _transition(ctx, RequestState.RECOMMENDED, f"Recommended: {ranking_result.recommended.name}")

    if auto_confirm:
        await emit_trace(
            request_id=request_id,
            agent="orchestrator",
            step="orchestrator.auto_confirm",
            reasoning=(
                f"Demo mode: auto-confirming top provider {ranking_result.recommended.name}. "
                f"In production, user would confirm or pick an alternative."
            ),
            output_data={"action": "auto_confirm", "provider": ranking_result.recommended.name},
        )

    await _transition(ctx, RequestState.BOOKING, "Provider confirmed → routing to Booking Agent")

    # ──────────────────────────────────────────────────────────────────
    # STEP 5: BOOKING (Booking Agent)
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.route",
        reasoning=(
            f"Routing to Booking Agent. Provider: {ranking_result.recommended.name}, "
            f"time window: {intent.time_text}. Will simulate slot reservation, "
            f"write booking to Supabase, generate receipt and bilingual confirmation."
        ),
    )

    booking = await run_booking_agent(
        request_id, ranking_result.recommended, intent, user_id
    )
    ctx.booking = booking

    if booking.status == "conflict":
        # Slot conflict → would re-rank, but for demo proceed
        await _transition(ctx, RequestState.RANKING, "Slot conflict → re-ranking")
        # Simplified: in a full implementation, re-rank excluding the conflicting slot

    await _transition(ctx, RequestState.CONFIRMED, f"Booking confirmed: {booking.booking_id}")

    # Update service request with result
    try:
        await update_service_request(request_id, {
            "state": RequestState.CONFIRMED.value,
            "result": {
                "recommended": ranking_result.recommended.model_dump(mode="json"),
                "booking": booking.model_dump(mode="json"),
            },
        })
    except Exception as e:
        logger.warning(f"Failed to update result: {e}")

    # ──────────────────────────────────────────────────────────────────
    # STEP 6: CONFIRMED → FOLLOW_UP_SCHEDULED (Follow-up Agent)
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.route",
        reasoning=(
            f"Booking confirmed ({booking.booking_id}). "
            f"Routing to Follow-up Agent to schedule reminder (T-1h), "
            f"status updates (en_route, in_progress, completed), and rating request."
        ),
    )

    await _transition(ctx, RequestState.FOLLOW_UP_SCHEDULED, "Booking confirmed → scheduling follow-ups")

    followups = await run_followup_agent(
        request_id, booking, intent,
        provider_name=ranking_result.recommended.name,
    )
    ctx.followups = followups

    # ──────────────────────────────────────────────────────────────────
    # DONE: The request lifecycle is now managed by the Follow-up Agent
    # which will mark it COMPLETED after all follow-ups fire.
    # ──────────────────────────────────────────────────────────────────
    await emit_trace(
        request_id=request_id,
        agent="orchestrator",
        step="orchestrator.lifecycle_handoff",
        reasoning=(
            f"Pipeline complete. {len(followups)} follow-ups scheduled. "
            f"Request lifecycle is now managed by the Follow-up Agent. "
            f"Summary: {intent.service_type} in {intent.location_text} → "
            f"{ranking_result.recommended.name} ({ranking_result.recommended.distance_km}km) → "
            f"booked {booking.slot_start.strftime('%d %b %I:%M %p')} → "
            f"follow-ups will fire via demo clock compression."
        ),
        output_data={
            "final_state": ctx.state.value,
            "provider": ranking_result.recommended.name,
            "booking_id": booking.booking_id,
            "followup_count": len(followups),
        },
    )

    logger.info(f"Orchestrator complete for {request_id}: state={ctx.state.value}")
    return ctx
