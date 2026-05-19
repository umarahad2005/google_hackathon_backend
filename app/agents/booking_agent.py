"""
Zimma AI — Booking Agent.

Simulates a booking end-to-end with a REAL, VISIBLE system-state change
(Supabase row + receipt + confirmation message).

Owner: AI/Agent Engineer (05)
Source: agents/subagents/booking-agent.md
        agents/workflows/wf-04-booking-simulation.md
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Booking, RankedProvider, ServiceIntent
from app.services.supabase import (
    create_booking,
    get_availability,
    book_slot,
)
from app.agents.trace_observer import TraceContext

logger = logging.getLogger(__name__)

PKT = timezone(timedelta(hours=5))

# A provider_id that is a real UUID came from the Supabase DB; non-UUID ids
# (e.g. Google Places ids) are external and have no availability rows.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _generate_confirmation_message(
    provider_name: str,
    slot_start: datetime,
    slot_end: datetime,
    price_estimate: str,
) -> str:
    """Generate bilingual (Urdu + English) confirmation message."""
    time_str = slot_start.strftime("%I:%M %p")
    date_str = slot_start.strftime("%d %B %Y")

    return (
        f"✅ بکنگ کنفرم | Booking Confirmed\n\n"
        f"📋 سروس: {provider_name}\n"
        f"📅 تاریخ: {date_str}\n"
        f"🕐 وقت: {time_str}\n"
        f"💰 تخمینہ: {price_estimate}\n\n"
        f"آپ کی بکنگ کنفرم ہو گئی ہے۔ سروس فراہم کنندہ وقت پر پہنچیں گے۔\n"
        f"Your booking is confirmed. The service provider will arrive on time.\n\n"
        f"📱 ریمائنڈر آپ کو 1 گھنٹہ پہلے بھیجا جائے گا۔\n"
        f"A reminder will be sent 1 hour before the appointment."
    )


def _generate_receipt(
    booking_id: str,
    provider: RankedProvider,
    slot_start: datetime,
    slot_end: datetime,
    price_estimate: str,
) -> dict:
    """Generate a structured receipt artifact."""
    return {
        "receipt_id": f"REC-{booking_id[:8].upper()}",
        "booking_id": booking_id,
        "provider": {
            "name": provider.name,
            "category": provider.category,
            "rating": provider.rating,
            "distance_km": provider.distance_km,
        },
        "service": {
            "type": provider.category,
            "slot_start": slot_start.isoformat(),
            "slot_end": slot_end.isoformat(),
        },
        "pricing": {
            "estimate": price_estimate,
            "currency": "PKR",
            "note": "Final price may vary based on work scope",
        },
        "status": "confirmed",
        "generated_at": datetime.now(PKT).isoformat(),
    }


async def run_booking_agent(
    request_id: str,
    provider: RankedProvider,
    intent: ServiceIntent,
    user_id: str = "demo-user",
    vendor_confirmation: dict | None = None,
) -> Booking:
    """
    Run the Booking Agent.
    1. reserve_slot → pick first free slot in window
    2. write_booking → INSERT into bookings (the state change)
    3. generate_receipt → receipt artifact
    4. send_confirmation → bilingual message (simulated SMS/WhatsApp)
    """
    async with TraceContext(
        request_id=request_id,
        agent="booking",
        step="booking.confirm",
        input_data={
            "provider": provider.name,
            "provider_id": provider.provider_id,
            "requested_window": {
                "start": intent.time_resolved.isoformat() if intent.time_resolved else None,
                "end": intent.time_window_end.isoformat() if intent.time_window_end else None,
            },
        },
    ) as trace:
        booking_id = str(uuid.uuid4())

        # Requested window
        if intent.time_resolved:
            want_start = intent.time_resolved
            window_end = intent.time_window_end or (want_start + timedelta(hours=3))
        else:
            now = datetime.now(PKT)
            want_start = (now + timedelta(days=1)).replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            window_end = want_start + timedelta(hours=3)

        # Price estimate: category base adjusted by the provider's real
        # price_band (from the provider record), not a flat per-category lookup.
        base_price = {
            "ac_technician": (2000, 3500),
            "electrician": (1000, 2500),
            "plumber": (1500, 3000),
            "tutor": (2000, 5000),
            "beautician": (1500, 4000),
            "carpenter": (2000, 5000),
            "appliance_repair": (1500, 4000),
        }.get(intent.service_type, (1500, 3000))
        band_mult = {"low": 0.85, "mid": 1.0, "high": 1.25}.get(
            provider.price_band.value if provider.price_band else "mid", 1.0
        )
        lo = int(round(base_price[0] * band_mult / 100.0)) * 100
        hi = int(round(base_price[1] * band_mult / 100.0)) * 100
        suffix = "/month" if intent.service_type == "tutor" else ""
        price_estimate = f"PKR {lo:,} – {hi:,}{suffix}"

        # Step 1: REAL slot reservation against provider_availability.
        is_db_provider = _UUID_RE.match(provider.provider_id or "") is not None
        chosen_slot_id: int | None = None
        slot_start = want_start
        slot_end = want_start + timedelta(hours=1)
        booking_status = "confirmed"

        if is_db_provider:
            free = await get_availability(
                provider.provider_id,
                want_start - timedelta(minutes=30),
                window_end + timedelta(minutes=30),
            )
            if free:
                slot = free[0]  # earliest free slot in window
                chosen_slot_id = slot["id"]
                slot_start = datetime.fromisoformat(slot["slot_start"])
                slot_end = datetime.fromisoformat(slot["slot_end"])
                await book_slot(chosen_slot_id)  # transactional: is_booked=true
                trace.add_tool_call(
                    name="book_slot",
                    args={
                        "provider_id": provider.provider_id,
                        "slot_id": chosen_slot_id,
                        "slot_start": slot_start.isoformat(),
                    },
                    result_summary=(
                        f"Reserved real slot #{chosen_slot_id} "
                        f"{slot_start.strftime('%d %b %H:%M')}–"
                        f"{slot_end.strftime('%H:%M')} (is_booked=true)"
                    ),
                )
            else:
                booking_status = "conflict"
                trace.add_tool_call(
                    name="book_slot",
                    args={"provider_id": provider.provider_id},
                    result_summary=(
                        "No free slot in requested window — booking conflict"
                    ),
                )
        else:
            # Google Places provider — no availability table for it.
            trace.add_tool_call(
                name="book_slot",
                args={"provider_id": provider.provider_id},
                result_summary=(
                    "Places provider has no DB availability; slot derived "
                    "from requested time (no row reserved)"
                ),
                degraded=True,
            )

        if booking_status == "conflict":
            trace.reasoning = (
                f"{provider.name} has no free slot in the requested window "
                f"({want_start.strftime('%d %b %H:%M')}–"
                f"{window_end.strftime('%H:%M')}). Returning conflict so the "
                f"orchestrator can pick an alternative."
            )
            trace.output_data = {"status": "conflict",
                                 "provider_id": provider.provider_id}
            return Booking(
                booking_id=booking_id,
                provider_id=provider.provider_id,
                user_id=user_id,
                slot_start=slot_start,
                slot_end=slot_end,
                status="conflict",
                price_estimate=price_estimate,
                confirmation_message="",
                reasoning=(
                    f"No availability for {provider.name} in the requested "
                    f"window — booking not created."
                ),
            )

        # The provider already accepted via the Vendor Calling Agent (run by
        # the orchestrator before this agent). Record its handshake here.
        vc = vendor_confirmation or {}

        # Step 2: Write booking to Supabase (THE CRITICAL STATE CHANGE)
        booking_data = {
            "id": booking_id,
            "request_id": request_id,
            "provider_id": provider.provider_id,
            "user_id": user_id,
            "slot_start": slot_start.isoformat(),
            "slot_end": slot_end.isoformat(),
            "status": "confirmed",
            "price_estimate": price_estimate,
            "slot_id": chosen_slot_id,
            "confirmation": vc,
        }

        try:
            await create_booking(booking_data)
            trace.add_tool_call(
                name="write_booking",
                args={"booking_id": booking_id},
                result_summary=f"Booking {booking_id} written to Supabase, status=confirmed",
            )
        except Exception as e:
            logger.error(f"Booking write failed: {e}")
            trace.add_tool_call(
                name="write_booking",
                args={"booking_id": booking_id},
                result_summary=f"FAILED: {e}",
            )
            raise

        # Step 3: Generate receipt
        receipt = _generate_receipt(
            booking_id, provider, slot_start, slot_end, price_estimate
        )
        receipt_url = f"/api/bookings/{booking_id}/receipt"

        trace.add_tool_call(
            name="generate_receipt",
            args={"booking_id": booking_id},
            result_summary=f"Receipt {receipt['receipt_id']} generated",
        )

        # Step 4: Send confirmation (SIMULATED)
        confirmation_message = _generate_confirmation_message(
            provider.name, slot_start, slot_end, price_estimate
        )

        trace.add_tool_call(
            name="send_confirmation",
            args={
                "channel": "SMS/WhatsApp",
                "user_id": user_id,
            },
            result_summary="Bilingual confirmation generated (Urdu + English)",
            simulated=True,  # Explicitly flagged as simulated
        )

        # Build booking result
        booking = Booking(
            booking_id=booking_id,
            provider_id=provider.provider_id,
            user_id=user_id,
            slot_start=slot_start,
            slot_end=slot_end,
            status="confirmed",
            price_estimate=price_estimate,
            receipt_url=receipt_url,
            confirmation_message=confirmation_message,
            reasoning=(
                f"Booked {provider.name} for {slot_start.strftime('%d %b %I:%M %p')} – "
                f"{slot_end.strftime('%I:%M %p')}. Slot falls within requested "
                f"{intent.time_text} window. Price estimate: {price_estimate}. "
                f"Confirmation sent via simulated SMS/WhatsApp."
            ),
        )

        trace.reasoning = booking.reasoning
        trace.output_data = booking.model_dump(mode="json")

        logger.info(f"Booking confirmed: {booking_id} with {provider.name}")
        return booking
