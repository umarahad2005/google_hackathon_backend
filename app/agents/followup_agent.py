"""
Zimma AI — Follow-up Agent.

Closes the lifecycle loop: schedules reminders, status updates,
and completion confirmation. Proves this is automation, not a list app.

Owner: AI/Agent Engineer (05)
Source: agents/subagents/followup-agent.md
        agents/workflows/wf-05-followup-automation.md
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.models import (
    Booking,
    FollowUp,
    FollowUpKind,
    FollowUpStatus,
    ServiceIntent,
)
from app.services.supabase import create_follow_up, update_follow_up, update_service_request
from app.agents.trace_observer import TraceContext, emit_trace
from app.settings import get_settings

logger = logging.getLogger(__name__)

PKT = timezone(timedelta(hours=5))


def _generate_reminder_message(
    provider_name: str,
    slot_start: datetime,
) -> str:
    """Bilingual reminder message."""
    time_str = slot_start.strftime("%I:%M %p")
    return (
        f"⏰ یاد دہانی | Reminder\n\n"
        f"آپ کی {provider_name} کے ساتھ اپائنٹمنٹ {time_str} پر ہے۔\n"
        f"Your appointment with {provider_name} is at {time_str}.\n\n"
        f"براہ کرم تیار رہیں۔ Please be ready."
    )


def _generate_status_message(status: str, provider_name: str) -> str:
    """Bilingual status update message."""
    messages = {
        "en_route": (
            f"🚗 {provider_name} آپ کی طرف آ رہے ہیں | is on the way\n"
            f"تخمینی وقت: 15 منٹ | ETA: 15 minutes"
        ),
        "in_progress": (
            f"🔧 کام شروع | Work Started\n"
            f"{provider_name} نے کام شروع کر دیا ہے۔\n"
            f"{provider_name} has started working."
        ),
        "completed": (
            f"✅ کام مکمل | Work Completed\n"
            f"{provider_name} نے کام مکمل کر دیا ہے۔\n"
            f"{provider_name} has completed the work.\n\n"
            f"⭐ براہ کرم ریٹنگ دیں | Please rate the service"
        ),
    }
    return messages.get(status, f"Status update: {status}")


async def _fire_demo_followups(
    request_id: str,
    followups: list[FollowUp],
    provider_name: str,
) -> None:
    """
    In demo mode, compress time so all follow-ups fire within seconds.
    Uses DEMO_CLOCK_MULTIPLIER from settings.
    """
    multiplier = get_settings().demo_clock_multiplier
    base_delay = 2.0  # seconds between events in demo

    for i, fu in enumerate(followups):
        await asyncio.sleep(base_delay)

        # Update follow-up status to 'sent'
        try:
            await update_follow_up(fu.followup_id, {"status": "sent"})
        except Exception as e:
            logger.warning(f"Failed to update follow-up status: {e}")

        # Emit trace for the status update
        await emit_trace(
            request_id=request_id,
            agent="followup",
            step=f"followup.{fu.kind.value}",
            input_data={"followup_id": fu.followup_id, "kind": fu.kind.value},
            reasoning=fu.reasoning,
            output_data={"status": "sent", "message": fu.message},
            simulated=True,
        )

        # Update to 'done'
        try:
            await update_follow_up(fu.followup_id, {"status": "done"})
        except Exception as e:
            logger.warning(f"Failed to update follow-up done status: {e}")

    # Mark service request as COMPLETED
    try:
        await update_service_request(request_id, {"state": "COMPLETED"})
        await emit_trace(
            request_id=request_id,
            agent="orchestrator",
            step="request.completed",
            reasoning=(
                f"All follow-ups processed. Service by {provider_name} completed. "
                f"Request lifecycle is now closed."
            ),
            output_data={"state": "COMPLETED"},
        )
    except Exception as e:
        logger.error(f"Failed to mark request completed: {e}")


async def run_followup_agent(
    request_id: str,
    booking: Booking,
    intent: ServiceIntent,
    provider_name: str = "Provider",
) -> list[FollowUp]:
    """
    Run the Follow-up Agent.
    1. Schedule reminder at slot_start - 1h
    2. Schedule status: en_route (T-15m), in_progress (T+0), completed (T+est)
    3. Each job writes follow_ups row + pushes to Realtime
    4. On completion: generate rating request, mark COMPLETED
    """
    async with TraceContext(
        request_id=request_id,
        agent="followup",
        step="followup.schedule",
        input_data={
            "booking_id": booking.booking_id,
            "slot_start": booking.slot_start.isoformat(),
        },
    ) as trace:
        followups: list[FollowUp] = []
        slot = booking.slot_start

        # 1. Reminder (T - 1h)
        reminder = FollowUp(
            followup_id=str(uuid.uuid4()),
            booking_id=booking.booking_id,
            kind=FollowUpKind.REMINDER,
            fire_at=slot - timedelta(hours=1),
            status=FollowUpStatus.SCHEDULED,
            message=_generate_reminder_message(provider_name, slot),
            simulated=True,
            reasoning=(
                f"Scheduled reminder 1 hour before appointment at "
                f"{slot.strftime('%I:%M %p')} with {provider_name}."
            ),
        )
        followups.append(reminder)

        # 2. Status: en_route (T - 15m)
        en_route = FollowUp(
            followup_id=str(uuid.uuid4()),
            booking_id=booking.booking_id,
            kind=FollowUpKind.STATUS,
            fire_at=slot - timedelta(minutes=15),
            status=FollowUpStatus.SCHEDULED,
            message=_generate_status_message("en_route", provider_name),
            simulated=True,
            reasoning=f"Status update: {provider_name} is en route, ETA 15 minutes.",
        )
        followups.append(en_route)

        # 3. Status: in_progress (T + 0)
        in_progress = FollowUp(
            followup_id=str(uuid.uuid4()),
            booking_id=booking.booking_id,
            kind=FollowUpKind.STATUS,
            fire_at=slot,
            status=FollowUpStatus.SCHEDULED,
            message=_generate_status_message("in_progress", provider_name),
            simulated=True,
            reasoning=f"Status update: {provider_name} has started work at the scheduled time.",
        )
        followups.append(in_progress)

        # 4. Completion (T + estimated duration ~1h)
        completion = FollowUp(
            followup_id=str(uuid.uuid4()),
            booking_id=booking.booking_id,
            kind=FollowUpKind.COMPLETION,
            fire_at=slot + timedelta(hours=1),
            status=FollowUpStatus.SCHEDULED,
            message=_generate_status_message("completed", provider_name),
            simulated=True,
            reasoning=(
                f"Service completion confirmation. {provider_name} has finished. "
                f"Rating prompt generated."
            ),
        )
        followups.append(completion)

        # 5. Rating request
        rating = FollowUp(
            followup_id=str(uuid.uuid4()),
            booking_id=booking.booking_id,
            kind=FollowUpKind.RATING_REQUEST,
            fire_at=slot + timedelta(hours=1, minutes=5),
            status=FollowUpStatus.SCHEDULED,
            message=(
                f"⭐ {provider_name} کی سروس کیسی رہی?\n"
                f"How was {provider_name}'s service?\n"
                f"براہ کرم 1-5 ⭐ ریٹنگ دیں | Please rate 1-5 ⭐"
            ),
            simulated=True,
            reasoning="Rating request sent to close the feedback loop.",
        )
        followups.append(rating)

        # Persist all follow-ups to Supabase
        for fu in followups:
            try:
                await create_follow_up({
                    "id": fu.followup_id,
                    "booking_id": fu.booking_id,
                    "kind": fu.kind.value,
                    "fire_at": fu.fire_at.isoformat(),
                    "status": fu.status.value,
                    "message": fu.message,
                    "simulated": fu.simulated,
                })
            except Exception as e:
                logger.warning(f"Failed to persist follow-up: {e}")

        trace.reasoning = (
            f"Scheduled {len(followups)} follow-ups for booking {booking.booking_id}: "
            f"1 reminder (T-1h), 2 status updates (en_route + in_progress), "
            f"1 completion confirmation, 1 rating request. "
            f"All notifications simulated and flagged."
        )
        trace.output_data = {
            "followup_count": len(followups),
            "kinds": [f.kind.value for f in followups],
            "fire_times": [f.fire_at.isoformat() for f in followups],
        }

        for fu in followups:
            trace.add_tool_call(
                name="schedule_job",
                args={"kind": fu.kind.value, "fire_at": fu.fire_at.isoformat()},
                result_summary=f"{fu.kind.value} scheduled at {fu.fire_at.strftime('%H:%M')}",
                simulated=True,
            )

        # Fire demo follow-ups in background (compressed time)
        asyncio.create_task(
            _fire_demo_followups(request_id, followups, provider_name)
        )

        logger.info(f"Follow-ups scheduled: {len(followups)} for booking {booking.booking_id}")
        return followups
