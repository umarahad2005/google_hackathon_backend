"""
Zimma AI — Ranking & Decision Agent.

The decision-quality agent (20% of the score). Ranks candidates with
deterministic scoring and produces LLM-written reasoning that cites
actual numbers and contrasts #1 vs #2.

Owner: AI/Agent Engineer (05)
Source: agents/subagents/ranking-decision-agent.md
        agents/workflows/wf-03-matching-ranking.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types

import asyncio
import re

from app.agents.config import MODELS, RANKING_WEIGHTS
from app.models import (
    ProviderCandidate,
    RankedProvider,
    RankingResult,
    ServiceIntent,
    DiscoveryResult,
)
from app.agents.trace_observer import TraceContext
from app.services.supabase import get_availability
from app.settings import get_settings

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

logger = logging.getLogger(__name__)

PKT = timezone(timedelta(hours=5))


def _compute_distance_score(distance_km: float, max_distance: float) -> float:
    """Nearer = higher score (normalized 0–1)."""
    if max_distance <= 0:
        return 1.0
    return max(0, 1.0 - (distance_km / max_distance))


def _availability_window(
    intent: ServiceIntent,
) -> tuple[datetime, datetime]:
    """The time window to check provider_availability against."""
    if intent.time_resolved:
        frm = intent.time_resolved - timedelta(minutes=30)
        to = (intent.time_window_end or
              intent.time_resolved + timedelta(hours=3)) + timedelta(minutes=30)
        return frm, to
    now = datetime.now(PKT)
    return now, now + timedelta(days=7)


async def _free_slot_counts(
    candidates: list[ProviderCandidate],
    intent: ServiceIntent,
) -> dict[str, int]:
    """
    Query provider_availability for REAL free slots in the requested
    window. Only DB-sourced providers (uuid ids) have availability rows.
    """
    frm, to = _availability_window(intent)

    async def _count(c: ProviderCandidate) -> tuple[str, int]:
        if c.source.value != "db" or not _UUID_RE.match(c.provider_id or ""):
            return c.provider_id, -1  # unknown (Places); -1 = no DB data
        try:
            slots = await get_availability(c.provider_id, frm, to)
            return c.provider_id, len(slots)
        except Exception as e:
            logger.warning(f"Availability lookup failed for {c.provider_id}: {e}")
            return c.provider_id, -1

    pairs = await asyncio.gather(*[_count(c) for c in candidates])
    return dict(pairs)


def _compute_availability_score(
    candidate: ProviderCandidate,
    intent: ServiceIntent,
    free_count: int,
) -> float:
    """
    Real availability: score from actual free provider_availability slots
    in the requested window. free_count == -1 means no DB availability data
    (e.g. a Google Places result) → fall back to open_now / neutral.
    """
    if free_count >= 0:
        if free_count == 0:
            return 0.15  # real signal: provider is fully booked then
        # 1 free slot → 0.7, scaling to 1.0 at 4+ free slots
        return round(min(1.0, 0.6 + 0.1 * free_count), 3)

    # No DB availability (Places provider): use open_now if asked "now"
    if intent.urgency.value == "now" and candidate.open_now is not None:
        return 1.0 if candidate.open_now else 0.3
    return 0.55  # neutral — unknown availability, don't over-reward


def _compute_rating_score(rating: float | None) -> float:
    """Rating / 5 (normalized 0–1)."""
    if rating is None:
        return 0.5  # neutral
    return min(max(rating / 5.0, 0), 1.0)


def _compute_price_fit_score(
    price_band: str | None,
    urgency: str,
) -> float:
    """Price fit vs urgency. Urgent = willing to pay more."""
    band_scores = {"low": 0.9, "mid": 0.7, "high": 0.5}
    if urgency in ("now", "today"):
        # Urgency → price matters less
        band_scores = {"low": 0.8, "mid": 0.8, "high": 0.7}

    if price_band:
        return band_scores.get(price_band, 0.6)
    return 0.6  # neutral if unknown


def score_candidates(
    candidates: list[ProviderCandidate],
    intent: ServiceIntent,
    free_counts: dict[str, int] | None = None,
) -> list[RankedProvider]:
    """
    Deterministic scoring: score = 0.40·dist + 0.25·avail + 0.25·rating + 0.10·price.
    Weights live in config (tunable). `free_counts` carries the REAL
    provider_availability free-slot count per provider_id.
    """
    if not candidates:
        return []

    free_counts = free_counts or {}
    max_distance = max(c.distance_km for c in candidates) if candidates else 1.0

    scored = []
    for i, c in enumerate(candidates):
        dist_score = _compute_distance_score(c.distance_km, max_distance)
        avail_score = _compute_availability_score(
            c, intent, free_counts.get(c.provider_id, -1)
        )
        rating_score = _compute_rating_score(c.rating)
        price_score = _compute_price_fit_score(
            c.price_band.value if c.price_band else None,
            intent.urgency.value,
        )

        composite = (
            RANKING_WEIGHTS["distance"] * dist_score
            + RANKING_WEIGHTS["availability"] * avail_score
            + RANKING_WEIGHTS["rating"] * rating_score
            + RANKING_WEIGHTS["price_fit"] * price_score
        )

        scored.append(RankedProvider(
            **c.model_dump(),
            score=round(composite, 4),
            score_breakdown={
                "distance": round(dist_score, 3),
                "availability": round(avail_score, 3),
                "rating": round(rating_score, 3),
                "price_fit": round(price_score, 3),
            },
            rank=0,  # set below
        ))

    # Sort by score descending; break ties by availability, then rating
    scored.sort(key=lambda p: (-p.score, -p.score_breakdown.get("availability", 0), -p.score_breakdown.get("rating", 0)))

    # Assign ranks
    for i, p in enumerate(scored):
        p.rank = i + 1

    return scored


async def _generate_reasoning(
    ranked: list[RankedProvider],
    intent: ServiceIntent,
) -> str:
    """
    Use Gemini Pro to write reasoning that cites actual numbers
    and contrasts the winner vs runner-up.
    """
    if not ranked:
        return "No candidates to rank."

    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None

    # Build a fact sheet for the LLM
    fact_sheet = (
        f"Service requested: {intent.service_type}\n"
        f"Location: {intent.location_text}\n"
        f"Time: {intent.time_text}\n\n"
        f"RECOMMENDED (#1): {top.name}\n"
        f"  Distance: {top.distance_km} km\n"
        f"  Rating: {top.rating or 'N/A'}/5\n"
        f"  Price band: {top.price_band or 'N/A'}\n"
        f"  Score: {top.score:.3f}\n"
        f"  Breakdown: distance={top.score_breakdown['distance']:.2f}, "
        f"availability={top.score_breakdown['availability']:.2f}, "
        f"rating={top.score_breakdown['rating']:.2f}, "
        f"price={top.score_breakdown['price_fit']:.2f}\n"
    )

    if runner:
        fact_sheet += (
            f"\nRUNNER-UP (#2): {runner.name}\n"
            f"  Distance: {runner.distance_km} km\n"
            f"  Rating: {runner.rating or 'N/A'}/5\n"
            f"  Price band: {runner.price_band or 'N/A'}\n"
            f"  Score: {runner.score:.3f}\n"
            f"  Breakdown: distance={runner.score_breakdown['distance']:.2f}, "
            f"availability={runner.score_breakdown['availability']:.2f}, "
            f"rating={runner.score_breakdown['rating']:.2f}, "
            f"price={runner.score_breakdown['price_fit']:.2f}\n"
        )

    try:
        client = genai.Client(api_key=get_settings().gemini_api_key)
        response = client.models.generate_content(
            model=MODELS.pro,
            contents=(
                f"You are the decision-explanation engine of Zimma AI. "
                f"Write 2-3 sentences explaining why provider #1 was recommended. "
                f"You MUST cite the actual numbers (distance, rating, score) "
                f"and explicitly say why #1 beat #2. No generic praise. "
                f"Keep it concise and factual.\n\n{fact_sheet}"
            ),
            config=types.GenerateContentConfig(temperature=0.3),
        )
        return response.text.strip()
    except Exception as e:
        logger.warning(f"LLM reasoning generation failed: {e}")
        # Fallback: deterministic reasoning
        reasoning = (
            f"{top.name} recommended: {top.distance_km}km away "
            f"(score {top.score:.3f}), "
        )
        if top.rating:
            reasoning += f"rated {top.rating}★, "
        if runner:
            reasoning += (
                f"beating {runner.name} ({runner.distance_km}km, "
                f"score {runner.score:.3f}) "
                f"primarily on distance advantage."
            )
        return reasoning


async def run_ranking_agent(
    request_id: str,
    discovery_result: DiscoveryResult,
    intent: ServiceIntent,
) -> RankingResult:
    """
    Run the Ranking & Decision Agent.
    1. Deterministic scoring in code
    2. LLM writes human-readable justification
    """
    async with TraceContext(
        request_id=request_id,
        agent="ranking_decision",
        step="ranking.score",
        input_data={
            "candidate_count": len(discovery_result.candidates),
            "service_type": intent.service_type,
            "urgency": intent.urgency.value,
            "weights": RANKING_WEIGHTS,
        },
        model=MODELS.pro,
    ) as trace:
        # 1. Real availability lookup, then deterministic scoring
        free_counts = await _free_slot_counts(
            discovery_result.candidates, intent
        )
        frm, to = _availability_window(intent)
        checked = sum(1 for v in free_counts.values() if v >= 0)
        trace.add_tool_call(
            name="query_provider_availability",
            args={
                "window_start": frm.isoformat(),
                "window_end": to.isoformat(),
                "providers_checked": checked,
            },
            result_summary=(
                f"Queried provider_availability for {checked} DB providers; "
                f"free-slot counts: "
                f"{ {k[:8]: v for k, v in free_counts.items() if v >= 0} }"
            ),
        )
        ranked = score_candidates(
            discovery_result.candidates, intent, free_counts
        )

        if not ranked:
            trace.reasoning = "No candidates to rank — discovery returned empty."
            trace.output_data = {"ranked_count": 0}
            return RankingResult(
                ranked=[],
                recommended=RankedProvider(
                    provider_id="", name="No Provider", category=intent.service_type,
                    lat=0, lng=0, distance_km=0, source="db",
                    score=0, score_breakdown={}, rank=0,
                ),
                alternatives=[],
                reasoning="No candidates available to rank.",
            )

        # 2. Generate reasoning with LLM
        reasoning = await _generate_reasoning(ranked, intent)

        # 3. Build result
        result = RankingResult(
            ranked=ranked,
            recommended=ranked[0],
            alternatives=ranked[1:3],
            reasoning=reasoning,
        )

        # Emit ranking trace
        trace.reasoning = reasoning
        trace.output_data = {
            "recommended": ranked[0].name,
            "recommended_score": ranked[0].score,
            "alternatives": [r.name for r in ranked[1:3]],
            "full_ranking": [
                {"rank": r.rank, "name": r.name, "score": r.score, "distance_km": r.distance_km}
                for r in ranked
            ],
        }
        trace.add_tool_call(
            name="deterministic_scoring",
            args={"weights": RANKING_WEIGHTS},
            result_summary=f"Ranked {len(ranked)} providers. #1: {ranked[0].name} (score={ranked[0].score:.3f})",
        )
        trace.add_tool_call(
            name="gemini_reasoning",
            args={"model": MODELS.pro},
            result_summary=f"Generated {len(reasoning)} char reasoning contrasting #1 vs #2",
        )

        logger.info(f"Ranking: {ranked[0].name} (score={ranked[0].score:.3f}) recommended")
        return result
