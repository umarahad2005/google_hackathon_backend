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
import re
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


def _parse_llm_dt(value) -> datetime | None:
    """Parse an LLM-provided ISO datetime; assume PKT if no tzinfo."""
    if not value:
        return None
    try:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=PKT)
    except (ValueError, TypeError):
        return None


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


# Keyword → canonical service map (Urdu / Roman Urdu / English).
_SERVICE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("ac_technician", ["ac ", "a.c", "air cond", "aircon", "اے سی", "ac technician",
                        "cooling", "fridge", "refrigerat"]),
    ("electrician", ["electric", "bijli", "بجلی", "wiring", "الیکٹریشن", "switch"]),
    ("plumber", ["plumb", "nalka", "نلکا", "pipe", "پلمبر", "leak", "water"]),
    ("tutor", ["tutor", "tuition", "teacher", "ٹیوٹر", "ustaad", "study", "academy"]),
    ("beautician", ["beautic", "salon", "parlor", "parlour", "makeup", "بیوٹیشن",
                    "bridal"]),
    ("carpenter", ["carpenter", "lakkar", "لکڑی", "wood", "furniture", "ترکھان"]),
    ("appliance_repair", ["appliance", "washing machine", "geyser", "microwave",
                          "repair", "مرمت", "electronics"]),
]

# Islamabad/Rawalpindi sector codes + named areas.
_SECTOR_RE = re.compile(r"\b([A-Ia-i]-\d{1,2}(?:/\d)?)\b")
_NAMED_AREAS = ["blue area", "saddar", "bahria town", "bahria", "dha", "pwd",
                "satellite town", "chaklala", "westridge", "g-", "f-", "i-"]


def _heuristic_intent(raw_message: str) -> ServiceIntent:
    """
    Deterministic, no-LLM extractor. Resilience path so the pipeline keeps
    working (and still finds REAL vendors) when Gemini is unavailable or
    rate-limited. Lower confidence than the LLM, but real data — not
    'unknown'.
    """
    msg = raw_message.strip()
    low = msg.lower()

    service = "ac_technician"
    found_service = False
    for canonical, kws in _SERVICE_KEYWORDS:
        if any(k in low for k in kws):
            service, found_service = canonical, True
            break

    m = _SECTOR_RE.search(msg)
    if m:
        location = m.group(1).upper()
    else:
        location = ""
        for area in _NAMED_AREAS:
            if area in low and not area.endswith("-"):
                location = area.title()
                break

    has_arabic = bool(re.search(r"[؀-ۿ]", msg))
    roman_words = ["mujhe", "chahiye", "kal", "aaj", "abhi", "mein", "subah",
                   "shaam", "dopahar"]
    is_roman = any(w in low for w in roman_words)
    language = (Language.URDU if has_arabic
                else Language.ROMAN_URDU if is_roman else Language.ENGLISH)

    if any(w in low for w in ["abhi", "urgent", "now", "foran", "فوری", "ابھی"]):
        urgency = "now"
    elif any(w in low for w in ["aaj", "today", "آج"]):
        urgency = "today"
    elif any(w in low for w in ["kal", "tomorrow", "کل"]):
        urgency = "scheduled"
    else:
        urgency = "flexible"

    time_text = ""
    for w in ["kal subah", "aaj shaam", "kal", "aaj", "abhi", "subah",
              "shaam", "dopahar", "tomorrow", "today", "morning", "evening"]:
        if w in low:
            time_text = w
            break
    t_start, t_end = _resolve_time(time_text, urgency)

    missing = []
    if not found_service:
        missing.append("service_type")
    if not location:
        missing.append("location")

    return ServiceIntent(
        service_type=service,
        service_raw=raw_message,
        location_text=location or "Islamabad",
        time_text=time_text or "as soon as possible",
        time_resolved=t_start,
        time_window_end=t_end,
        urgency=Urgency(urgency),
        language=language,
        confidence=0.55 if found_service and location else 0.4,
        reasoning=(
            "Deterministic fallback extractor used (Gemini unavailable). "
            f"Matched service='{service}', location='{location or 'n/a'}', "
            f"urgency='{urgency}' from keywords/sector regex."
        ),
        missing=missing,
    )


async def run_intent_agent(
    request_id: str,
    raw_message: str,
    audio_url: str | None = None,
    image_url: str | None = None,
    history: list[dict] | None = None,
) -> ServiceIntent:
    """
    Run the Intent/NLU Agent on a raw message.
    Returns a structured ServiceIntent with confidence scoring.
    """
    async with TraceContext(
        request_id=request_id,
        agent="intent_nlu",
        step="intent.extract",
        input_data={"raw_message": raw_message, "audio_url": audio_url, "image_url": image_url},
        model=MODELS.flash,
    ) as trace:
        try:
            # Call Gemini for intent extraction
            client = genai.Client(api_key=get_settings().gemini_api_key)

            # Build the user prompt
            history_text = ""
            if history:
                history_text = "Previous User Requests:\n"
                for h in reversed(history[:3]): # Last 3 requests
                    intent_data = h.get("intent") or {}
                    history_text += f"- Request: \"{h.get('raw_message')}\" -> Extracted Service: {intent_data.get('service_type')}\n"
                history_text += "\n"

            user_prompt = (
                f"Extract the service intent from this message. "
                f"Current time: {datetime.now(PKT).isoformat()}\n\n"
                f"{history_text}"
                f"Message: \"{raw_message}\"\n\n"
                f"Return ONLY valid JSON matching the output format."
            )
            
            contents = [user_prompt]
            if image_url:
                try:
                    import httpx
                    import mimetypes
                    async with httpx.AsyncClient() as http_client:
                        resp = await http_client.get(image_url)
                        resp.raise_for_status()
                        mime_type = mimetypes.guess_type(image_url)[0] or "image/jpeg"
                        contents.append(
                            types.Part.from_bytes(data=resp.content, mime_type=mime_type)
                        )
                except Exception as img_err:
                    logger.warning(f"Failed to load image for intent extraction: {img_err}")

            response = client.models.generate_content(
                model=MODELS.flash,
                contents=contents,
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

            # Trust the LLM's resolved datetime first (it sees full context
            # incl. the current time in the prompt). Only fall back to the
            # deterministic keyword heuristic if the model gave nothing usable.
            llm_start = _parse_llm_dt(parsed.get("time_resolved"))
            llm_end = _parse_llm_dt(parsed.get("time_window_end"))
            if llm_start is not None:
                time_start, time_end = llm_start, llm_end
                time_source = "llm"
            else:
                time_start, time_end = _resolve_time(
                    parsed.get("time_text", ""),
                    parsed.get("urgency", "scheduled"),
                )
                time_source = "heuristic_fallback"

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
                               f"time={intent.time_text}→"
                               f"{time_start.isoformat() if time_start else 'none'} "
                               f"({time_source}), conf={intent.confidence:.2f}, "
                               f"lang={intent.language.value}",
            )

            logger.info(
                f"Intent extracted: {intent.service_type} / {intent.location_text} / "
                f"{intent.time_text} (conf={intent.confidence:.2f}, lang={intent.language.value})"
            )

            return intent

        except Exception as e:
            err = str(e)
            is_quota = any(
                s in err.lower()
                for s in ["quota", "exhausted", "429", "rate limit",
                          "resource_exhausted"]
            )
            logger.error(f"Intent LLM failed ({'quota' if is_quota else 'error'}): {e}")

            # Resilience: parse it deterministically so the pipeline still
            # discovers REAL vendors instead of dead-ending on 'unknown'.
            heuristic = _heuristic_intent(raw_message)
            trace.degraded = True
            trace.simulated = False
            trace.reasoning = (
                ("Gemini quota exhausted — " if is_quota
                 else f"Gemini call failed ({err[:80]}) — ")
                + "switched to the deterministic fallback extractor. "
                + heuristic.reasoning
            )
            trace.output_data = heuristic.model_dump(mode="json")
            trace.add_tool_call(
                name="fallback_intent_extractor",
                args={"reason": "quota" if is_quota else "llm_error"},
                result_summary=(
                    f"service={heuristic.service_type}, "
                    f"location={heuristic.location_text}, "
                    f"conf={heuristic.confidence:.2f}"
                ),
                degraded=True,
            )
            return heuristic
