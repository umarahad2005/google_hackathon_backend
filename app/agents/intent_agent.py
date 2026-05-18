"""
Zimma AI — Intent / NLU Agent.

Extracts structured ServiceIntent from a multilingual natural-language message.
Supports: Urdu, Roman Urdu, English, and mixed.

Owner: AI/Agent Engineer (05)
Source: agents/subagents/intent-nlu-agent.md
        agents/workflows/wf-01-intent-understanding.md
        agents/skills/roman-urdu-nlu.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google import genai
from google.genai import types

from app.agents.config import MODELS
from app.models import ServiceIntent, Language, Urgency
from app.agents.trace_observer import TraceContext
from app.settings import get_settings

logger = logging.getLogger(__name__)

# Timezone for Islamabad
PKT = timezone(timedelta(hours=5))

# Load system prompt
PROMPT_PATH = Path(__file__).parent / "prompts" / "intent.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""


def _resolve_time(time_text: str, urgency: str) -> tuple[datetime | None, datetime | None]:
    """Resolve relative time references to actual datetimes."""
    now = datetime.now(PKT)

    if urgency == "now":
        return now, None

    # Tomorrow
    tomorrow = now + timedelta(days=1)
    time_lower = time_text.lower() if time_text else ""

    if urgency == "scheduled" or "kal" in time_lower or "tomorrow" in time_lower:
        base = tomorrow
    elif urgency == "today" or "aaj" in time_lower or "today" in time_lower:
        base = now
    else:
        base = tomorrow

    # Time-of-day windows
    if any(w in time_lower for w in ["subah", "صبح", "morning", "sver", "sba"]):
        start = base.replace(hour=9, minute=0, second=0, microsecond=0)
        end = base.replace(hour=12, minute=0, second=0, microsecond=0)
    elif any(w in time_lower for w in ["dopahar", "دوپہر", "afternoon"]):
        start = base.replace(hour=12, minute=0, second=0, microsecond=0)
        end = base.replace(hour=16, minute=0, second=0, microsecond=0)
    elif any(w in time_lower for w in ["shaam", "شام", "evening", "sham"]):
        start = base.replace(hour=16, minute=0, second=0, microsecond=0)
        end = base.replace(hour=20, minute=0, second=0, microsecond=0)
    else:
        start = base.replace(hour=9, minute=0, second=0, microsecond=0)
        end = base.replace(hour=18, minute=0, second=0, microsecond=0)

    return start, end


async def run_intent_agent(
    request_id: str,
    raw_message: str,
    audio_url: str | None = None,
) -> ServiceIntent:
    """
    Run the Intent/NLU Agent on a raw message.
    Returns a structured ServiceIntent with confidence scoring.
    """
    async with TraceContext(
        request_id=request_id,
        agent="intent_nlu",
        step="intent.extract",
        input_data={"raw_message": raw_message, "audio_url": audio_url},
        model=MODELS.flash,
    ) as trace:
        try:
            # Call Gemini for intent extraction
            client = genai.Client(api_key=get_settings().gemini_api_key)

            # Build the user prompt
            user_prompt = (
                f"Extract the service intent from this message. "
                f"Current time: {datetime.now(PKT).isoformat()}\n\n"
                f"Message: \"{raw_message}\"\n\n"
                f"Return ONLY valid JSON matching the output format."
            )

            response = client.models.generate_content(
                model=MODELS.flash,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )

            # Parse the response
            response_text = response.text.strip()
            # Clean potential markdown wrapping
            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[1]
                response_text = response_text.rsplit("```", 1)[0].strip()

            parsed = json.loads(response_text)

            # Resolve actual datetimes
            time_start, time_end = _resolve_time(
                parsed.get("time_text", ""),
                parsed.get("urgency", "scheduled"),
            )

            intent = ServiceIntent(
                service_type=parsed.get("service_type", "unknown"),
                service_raw=parsed.get("service_raw", raw_message),
                location_text=parsed.get("location_text", ""),
                location_resolved=parsed.get("location_resolved"),
                time_text=parsed.get("time_text", ""),
                time_resolved=time_start,
                time_window_end=time_end,
                urgency=Urgency(parsed.get("urgency", "scheduled")),
                language=Language(parsed.get("language", "en")),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", ""),
                missing=parsed.get("missing", []),
            )

            # Set trace output
            trace.reasoning = intent.reasoning
            trace.output_data = intent.model_dump(mode="json")
            trace.add_tool_call(
                name="gemini_intent_extract",
                args={"message": raw_message},
                result_summary=f"service={intent.service_type}, location={intent.location_text}, "
                               f"time={intent.time_text}, conf={intent.confidence:.2f}, "
                               f"lang={intent.language.value}",
            )

            logger.info(
                f"Intent extracted: {intent.service_type} / {intent.location_text} / "
                f"{intent.time_text} (conf={intent.confidence:.2f}, lang={intent.language.value})"
            )

            return intent

        except Exception as e:
            logger.error(f"Intent extraction failed: {e}")
            trace.reasoning = f"Intent extraction failed: {e}. Returning low-confidence result for clarification."
            trace.output_data = {"error": str(e)}

            return ServiceIntent(
                service_type="unknown",
                service_raw=raw_message,
                location_text="",
                time_text="",
                time_resolved=None,
                urgency=Urgency.FLEXIBLE,
                language=Language.MIXED,
                confidence=0.0,
                reasoning=f"Extraction failed: {e}. All slots need clarification.",
                missing=["service_type", "location_text", "time"],
            )
