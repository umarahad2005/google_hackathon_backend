"""
Zimma AI — Provider Discovery Agent.

Finds candidate providers using real geo data + seeded DB.
Uses the find_candidates() tool from Maps/Geo (06).

Owner: AI/Agent Engineer (05)
Source: agents/subagents/provider-discovery-agent.md
        agents/workflows/wf-02-provider-discovery.md
"""

from __future__ import annotations

import logging

from app.models import ServiceIntent, DiscoveryResult, Urgency
from app.services.maps import find_candidates
from app.agents.trace_observer import TraceContext

logger = logging.getLogger(__name__)

# Radius by urgency (km)
RADIUS_BY_URGENCY = {
    Urgency.NOW: 3.0,
    Urgency.TODAY: 6.0,
    Urgency.SCHEDULED: 10.0,
    Urgency.FLEXIBLE: 10.0,
}


async def run_discovery_agent(
    request_id: str,
    intent: ServiceIntent,
) -> DiscoveryResult:
    """
    Run the Provider Discovery Agent.
    Strategy: start radius by urgency → find_candidates → retry once if empty.
    """
    radius_km = RADIUS_BY_URGENCY.get(intent.urgency, 10.0)

    async with TraceContext(
        request_id=request_id,
        agent="provider_discovery",
        step="discovery.search",
        input_data={
            "service_type": intent.service_type,
            "location": intent.location_text,
            "urgency": intent.urgency.value,
            "start_radius_km": radius_km,
        },
    ) as trace:
        # First attempt
        result = await find_candidates(
            category=intent.service_type,
            location_text=intent.location_text,
            radius_km=radius_km,
        )

        trace.add_tool_call(
            name="find_candidates",
            args={
                "category": intent.service_type,
                "location": intent.location_text,
                "radius_km": radius_km,
            },
            result_summary=f"{len(result.candidates)} candidates found",
            degraded=result.degraded,
        )

        # Retry with doubled radius if empty
        if not result.candidates:
            doubled_radius = radius_km * 2
            logger.info(f"No candidates at {radius_km}km, retrying at {doubled_radius}km")

            result = await find_candidates(
                category=intent.service_type,
                location_text=intent.location_text,
                radius_km=doubled_radius,
            )

            trace.add_tool_call(
                name="find_candidates_retry",
                args={
                    "category": intent.service_type,
                    "location": intent.location_text,
                    "radius_km": doubled_radius,
                },
                result_summary=f"{len(result.candidates)} candidates found after radius doubling",
                degraded=result.degraded,
            )

            result.reasoning += (
                f" Initial search at {radius_km}km returned 0 — "
                f"doubled to {doubled_radius}km."
            )
            result.radius_used_km = doubled_radius

        # Set trace data
        trace.reasoning = result.reasoning
        trace.output_data = {
            "candidate_count": len(result.candidates),
            "radius_used_km": result.radius_used_km,
            "degraded": result.degraded,
            "candidates": [
                {"name": c.name, "distance_km": c.distance_km, "source": c.source.value}
                for c in result.candidates[:5]  # summary for trace
            ],
        }
        trace.degraded = result.degraded

        logger.info(
            f"Discovery: {len(result.candidates)} candidates within "
            f"{result.radius_used_km}km (degraded={result.degraded})"
        )

        return result
