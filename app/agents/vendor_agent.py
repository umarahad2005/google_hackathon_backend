"""
Zimma AI — Vendor Calling / Outreach Agent.

A distinct hub-and-spoke sub-agent (invoked by the Orchestrator before the
Booking Agent). It SIMULATES a real outbound voice call to the selected
provider to confirm the assignment — a true two-sided, provider-assignment
state change rather than blind auto-confirm.

Per Challenge 2 the contact is simulated ("Use mock data if real APIs are
unavailable"; booking/assignment must be *simulated* end-to-end), but the
decision and the resulting state change are real and fully traced. The call
flow is deterministic (no RNG) so demos are reproducible.

Owner: AI/Agent Engineer (05)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models import RankedProvider, ServiceIntent
from app.agents.trace_observer import TraceContext

logger = logging.getLogger(__name__)

PKT = timezone(timedelta(hours=5))


async def run_vendor_agent(
    request_id: str,
    provider: RankedProvider,
    intent: ServiceIntent,
) -> dict:
    """
    Place a simulated call to the provider and return the outcome dict:
    {channel, requested_at, responded_at, response_seconds, decision,
     note, provider_id, provider_name, transcript}.

    decision ∈ {"accepted", "declined"}. A provider declines only on a
    genuine red flag (rating < 3.0), which exercises the orchestrator's
    re-rank path; otherwise it accepts.
    """
    async with TraceContext(
        request_id=request_id,
        agent="vendor_outreach",
        step="vendor.call",
        input_data={
            "provider": provider.name,
            "provider_id": provider.provider_id,
            "phone": provider.phone,
            "service": intent.service_type,
            "location": intent.location_text,
        },
    ) as trace:
        now = datetime.now(PKT)
        resp_s = 2 + (abs(hash(provider.provider_id)) % 8)  # 2–9s, stable
        rating = provider.rating
        when_txt = (
            intent.time_resolved.strftime("%d %b %I:%M %p")
            if intent.time_resolved else (intent.time_text or "the requested time")
        )

        transcript: list[str] = []

        # --- Simulated multi-step call flow ---
        trace.add_tool_call(
            name="dial_provider",
            args={"name": provider.name, "phone": provider.phone or "n/a"},
            result_summary=f"Dialing {provider.name} ({provider.phone or 'no number on file'})…",
            simulated=True,
        )
        transcript.append(f"📞 Dialing {provider.name}…")

        trace.add_tool_call(
            name="call_ringing",
            args={},
            result_summary="Ringing…",
            simulated=True,
        )
        transcript.append("… ringing …")

        trace.add_tool_call(
            name="call_connected",
            args={"latency_s": resp_s},
            result_summary=f"Connected to {provider.name} after {resp_s}s",
            simulated=True,
        )
        transcript.append(f"✅ Connected (after {resp_s}s)")

        trace.add_tool_call(
            name="propose_job",
            args={
                "service": intent.service_type,
                "location": intent.location_text,
                "when": when_txt,
            },
            result_summary=(
                f"Proposed: {intent.service_type.replace('_', ' ')} job in "
                f"{intent.location_text} at {when_txt}"
            ),
            simulated=True,
        )
        transcript.append(
            f"🤖 “Salaam, {intent.service_type.replace('_',' ')} chahiye "
            f"{intent.location_text} mein, {when_txt} — available ho?”"
        )

        if rating is not None and rating < 3.0:
            decision = "declined"
            note = (
                f"provider declined — flagged unreliable "
                f"(rating {rating:.1f}/5)"
            )
            trace.add_tool_call(
                name="negotiate",
                args={},
                result_summary="Provider unavailable / declined the job",
                simulated=True,
            )
            transcript.append("👷 “Maaf kijiye, main available nahi hoon.”")
        else:
            decision = "accepted"
            rtxt = f"rating {rating:.1f}/5" if rating is not None else "unrated"
            note = f"provider accepted the assignment ({rtxt})"
            trace.add_tool_call(
                name="negotiate",
                args={},
                result_summary="Provider confirmed availability and rate",
                simulated=True,
            )
            transcript.append("👷 “Ji haan, main aa jaonga. Confirm.”")

        trace.add_tool_call(
            name="hang_up",
            args={"duration_s": resp_s},
            result_summary=f"Call ended — outcome: {decision.upper()}",
            simulated=True,
        )
        transcript.append(f"📴 Call ended — {decision.upper()}")

        confirmation = {
            "channel": "simulated voice call",
            "requested_at": now.isoformat(),
            "responded_at": (now + timedelta(seconds=resp_s)).isoformat(),
            "response_seconds": resp_s,
            "decision": decision,
            "note": note,
            "provider_id": provider.provider_id,
            "provider_name": provider.name,
            "transcript": transcript,
        }

        trace.simulated = True
        trace.reasoning = (
            f"Placed a simulated confirmation call to {provider.name} for the "
            f"{intent.service_type.replace('_',' ')} assignment at {when_txt}. "
            f"Outcome: {decision.upper()} — {note}."
        )
        trace.output_data = confirmation

        logger.info(
            f"Vendor call → {provider.name}: {decision} ({note})"
        )
        return confirmation
