"""
Zimma AI — Canonical Data Models (FROZEN at Phase 1).

These are the shared pydantic v2 models used across the entire system:
- ADK sub-agent I/O schemas
- FastAPI request/response bodies
- Supabase persistence shapes

DO NOT modify after Phase 1 freeze without Architect (02) approval.
Source of truth: agents/subagents/*.md
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ======================================================================
# Enums
# ======================================================================


class RequestState(str, Enum):
    """State machine states per orchestration/workflow-state-machine.md."""
    NEW = "NEW"
    UNDERSTANDING = "UNDERSTANDING"
    CLARIFY = "CLARIFY"
    DISCOVERING = "DISCOVERING"
    NO_PROVIDER = "NO_PROVIDER"
    RANKING = "RANKING"
    RECOMMENDED = "RECOMMENDED"
    BOOKING = "BOOKING"
    CONFIRMED = "CONFIRMED"
    FOLLOW_UP_SCHEDULED = "FOLLOW_UP_SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Urgency(str, Enum):
    NOW = "now"
    TODAY = "today"
    SCHEDULED = "scheduled"
    FLEXIBLE = "flexible"


class Language(str, Enum):
    URDU = "ur"
    ROMAN_URDU = "roman_ur"
    ENGLISH = "en"
    MIXED = "mixed"


class ProviderSource(str, Enum):
    PLACES = "places"
    DB = "db"


class PriceBand(str, Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class FollowUpKind(str, Enum):
    REMINDER = "reminder"
    STATUS = "status"
    COMPLETION = "completion"
    RATING_REQUEST = "rating_request"


class FollowUpStatus(str, Enum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    DONE = "done"


# ======================================================================
# Intent / NLU Agent Output
# Source: agents/subagents/intent-nlu-agent.md
# ======================================================================


class ServiceIntent(BaseModel):
    """Structured extraction from a natural-language service request."""
    service_type: str = Field(
        ..., description="Normalized canonical type: ac_technician, electrician, plumber, tutor, beautician, carpenter, appliance_repair"
    )
    service_raw: str = Field(..., description="What the user actually said")
    location_text: str = Field(..., description="Raw location text, e.g. 'G-13'")
    location_resolved: str | None = Field(
        None, description="Resolved full address (set later by geo tool)"
    )
    time_text: str = Field(..., description="Raw time text, e.g. 'kal subah'")
    time_resolved: datetime | None = Field(
        None, description="Normalized datetime (e.g. tomorrow 09:00)"
    )
    time_window_end: datetime | None = Field(
        None, description="End of time window (e.g. tomorrow 12:00)"
    )
    urgency: Urgency = Field(..., description="Urgency level")
    language: Language = Field(..., description="Detected language")
    confidence: float = Field(
        ..., ge=0, le=1, description="Confidence over the 3 required slots"
    )
    reasoning: str = Field(
        ..., min_length=1, description="Why this parse — non-empty, non-generic"
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Slots with low confidence, e.g. ['time']"
    )


# ======================================================================
# Provider Discovery Agent Output
# Source: agents/subagents/provider-discovery-agent.md
# ======================================================================


class ProviderCandidate(BaseModel):
    """A single provider found by discovery."""
    provider_id: str
    name: str
    category: str
    lat: float
    lng: float
    distance_km: float
    rating: float | None = None
    price_band: PriceBand | None = None
    open_now: bool | None = None
    languages: list[str] = Field(default_factory=list)
    source: ProviderSource
    working_hours: dict | None = None
    phone: str | None = None


class DiscoveryResult(BaseModel):
    """Output of the Provider Discovery Agent."""
    candidates: list[ProviderCandidate] = Field(default_factory=list)
    radius_used_km: float
    reasoning: str = Field(
        ..., min_length=1, description="Search strategy + why this radius"
    )
    degraded: bool = Field(
        False, description="True if Places API failed → DB-only"
    )


# ======================================================================
# Ranking & Decision Agent Output
# Source: agents/subagents/ranking-decision-agent.md
# ======================================================================


class RankedProvider(ProviderCandidate):
    """A provider with scoring applied."""
    score: float = Field(..., description="Composite score 0–1")
    score_breakdown: dict = Field(
        ...,
        description="Component scores: {distance, availability, rating, price_fit}"
    )
    rank: int


class RankingResult(BaseModel):
    """Output of the Ranking & Decision Agent."""
    ranked: list[RankedProvider] = Field(
        ..., description="Full ordered list"
    )
    recommended: RankedProvider = Field(
        ..., description="Rank 1 provider"
    )
    alternatives: list[RankedProvider] = Field(
        default_factory=list, description="Ranks 2–3"
    )
    reasoning: str = Field(
        ..., min_length=1,
        description="Cites actual numbers and contrasts winner vs runner-up"
    )


# ======================================================================
# Booking Agent Output
# Source: agents/subagents/booking-agent.md
# ======================================================================


class Booking(BaseModel):
    """A confirmed (simulated) booking."""
    booking_id: str
    provider_id: str
    user_id: str
    slot_start: datetime
    slot_end: datetime
    status: Literal["confirmed", "conflict"]
    price_estimate: str
    receipt_url: str | None = None
    confirmation_message: str = ""
    reasoning: str = Field(
        ..., min_length=1, description="Why this slot, how confirmed"
    )


# ======================================================================
# Follow-up Agent Output
# Source: agents/subagents/followup-agent.md
# ======================================================================


class FollowUp(BaseModel):
    """A scheduled follow-up job."""
    followup_id: str
    booking_id: str
    kind: FollowUpKind
    fire_at: datetime
    status: FollowUpStatus = FollowUpStatus.SCHEDULED
    message: str = ""
    simulated: bool = True
    reasoning: str = Field(
        ..., min_length=1, description="Why this follow-up was scheduled"
    )


# ======================================================================
# Trace Event (emitted by Trace/Observer)
# Source: agents/subagents/trace-observer-agent.md
# ======================================================================


class TraceEvent(BaseModel):
    """A single agentic trace event — the gradable audit trail."""
    request_id: str
    seq: int = Field(..., description="Gap-free, strictly increasing per request")
    agent: str
    step: str = Field(..., description='e.g. "intent.extract", "ranking.score"')
    input: dict = Field(default_factory=dict)
    reasoning: str = Field(
        ..., min_length=1, description="Empty is a defect"
    )
    tool_calls: list[dict] = Field(default_factory=list)
    output: dict = Field(default_factory=dict)
    latency_ms: int = 0
    degraded: bool = False
    simulated: bool = False
    model: str | None = None
    ts: datetime = Field(default_factory=datetime.utcnow)


# ======================================================================
# Request Context (ADK session state — the spine of the pipeline)
# Source: agents/subagents/README.md
# ======================================================================


class RequestContext(BaseModel):
    """
    Passed through ADK session state. The Orchestrator owns this;
    each sub-agent reads and enriches it.
    """
    request_id: str
    raw_message: str
    audio_url: str | None = None
    language: Language | None = None
    intent: ServiceIntent | None = None
    candidates: list[ProviderCandidate] = Field(default_factory=list)
    ranked: list[RankedProvider] = Field(default_factory=list)
    selected: RankedProvider | None = None
    booking: Booking | None = None
    followups: list[FollowUp] = Field(default_factory=list)
    state: RequestState = RequestState.NEW
    user_id: str = "demo-user"


# ======================================================================
# API Request/Response Models
# ======================================================================


class CreateServiceRequest(BaseModel):
    """POST /api/requests body."""
    message: str = Field(
        ..., min_length=1, description="Natural-language service request"
    )
    audio_url: str | None = None
    user_id: str = "demo-user"


class ServiceRequestResponse(BaseModel):
    """GET /api/requests/{id} response."""
    request_id: str
    state: RequestState
    intent: ServiceIntent | None = None
    recommended: RankedProvider | None = None
    alternatives: list[RankedProvider] = Field(default_factory=list)
    booking: Booking | None = None
    followups: list[FollowUp] = Field(default_factory=list)
    trace_count: int = 0
