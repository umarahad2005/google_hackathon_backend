"""
Zimma AI — Headless Reference Run.

Runs the reference scenario end-to-end and prints the full ordered trace.
This is the AI Engineer's Definition of Done proof.

Usage: python -m scripts.run_reference

Reference: "Mujhe kal subah G-13 mein AC technician chahiye"
Expected: ac_technician / G-13 / tomorrow morning / >=3 ranked / booking / follow-ups
"""

import asyncio
import json
import sys
import os

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agents.orchestrator import run_orchestrator
from app.services.supabase import get_traces


REFERENCE_MESSAGES = [
    # Roman Urdu (primary reference)
    "Mujhe kal subah G-13 mein AC technician chahiye",
    # Urdu script
    "\u0645\u062c\u06be\u06d2 \u06a9\u0644 \u0635\u0628\u062d G-13 \u0645\u06cc\u06ba AC \u0679\u06cc\u06a9\u0646\u06cc\u0634\u0646 \u0686\u0627\u06c1\u06cc\u06d2",
    # English
    "I need an AC technician in G-13 tomorrow morning",
]


async def run_reference(message: str | None = None):
    """Run a reference scenario and print results."""
    msg = message or REFERENCE_MESSAGES[0]

    print("=" * 70)
    print("ZIMMA AI -- HEADLESS REFERENCE RUN")
    print("=" * 70)
    print(f'\nInput: "{msg}"')
    print(f'Time: {__import__("datetime").datetime.now().isoformat()}')
    print("-" * 70)

    import uuid as _uuid
    # Run the orchestrator
    ctx = await run_orchestrator(
        request_id=str(_uuid.uuid4()),
        raw_message=msg,
        user_id="demo-user",
        auto_confirm=True,
    )

    # Print results
    print(f"\nRESULTS")
    print(f"   State: {ctx.state.value}")

    if ctx.intent:
        print(f"\n   [Intent]")
        print(f"      Service:    {ctx.intent.service_type}")
        print(f"      Location:   {ctx.intent.location_text}")
        print(f"      Time:       {ctx.intent.time_text}")
        print(f"      Urgency:    {ctx.intent.urgency.value}")
        print(f"      Language:   {ctx.intent.language.value}")
        print(f"      Confidence: {ctx.intent.confidence:.2f}")
        print(f"      Reasoning:  {ctx.intent.reasoning[:100]}")

    if ctx.ranked:
        print(f"\n   [Ranked Providers ({len(ctx.ranked)})]")
        for r in ctx.ranked[:5]:
            print(f"      #{r.rank} {r.name} -- {r.distance_km}km, "
                  f"score={r.score:.3f}, rating={r.rating}")

    if ctx.selected:
        print(f"\n   [Recommended] {ctx.selected.name}")
        print(f"      Distance: {ctx.selected.distance_km}km")
        print(f"      Score: {ctx.selected.score:.3f}")
        print(f"      Breakdown: {json.dumps(ctx.selected.score_breakdown, indent=2)}")

    if ctx.booking:
        print(f"\n   [Booking]")
        print(f"      ID: {ctx.booking.booking_id}")
        print(f"      Status: {ctx.booking.status}")
        print(f"      Slot: {ctx.booking.slot_start} -- {ctx.booking.slot_end}")
        print(f"      Price: {ctx.booking.price_estimate}")
        print(f"      Reasoning: {ctx.booking.reasoning[:150]}")

    if ctx.followups:
        print(f"\n   [Follow-ups ({len(ctx.followups)})]")
        for f in ctx.followups:
            print(f"      {f.kind.value}: {f.fire_at.strftime('%H:%M')} ({f.status.value})")

    # Print full trace
    print(f"\n{'=' * 70}")
    print(f"FULL AGENT TRACE (ordered by seq)")
    print(f"{'=' * 70}")

    try:
        traces = await get_traces(ctx.request_id)
        for t in traces:
            print(f"\n   [{t['seq']:02d}] {t['agent']}.{t['step']} ({t.get('latency_ms', '?')}ms)")
            print(f"       Reasoning: {t.get('reasoning', '')[:200]}")
            if t.get("tool_calls"):
                for tc in t["tool_calls"]:
                    flags = []
                    if tc.get("degraded"):
                        flags.append("[DEGRADED]")
                    if tc.get("simulated"):
                        flags.append("[SIMULATED]")
                    print(f"       TOOL {tc.get('name', '?')}: {tc.get('result', '')[:100]} {' '.join(flags)}")
    except Exception as e:
        print(f"   WARNING: Could not fetch traces: {e}")

    print(f"\n{'=' * 70}")
    print(f"DONE. Reference run complete. Final state: {ctx.state.value}")
    print(f"{'=' * 70}")

    return ctx


async def run_all_references():
    """Run all 3 reference messages."""
    for msg in REFERENCE_MESSAGES:
        await run_reference(msg)
        print("\n\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        asyncio.run(run_all_references())
    else:
        asyncio.run(run_reference())
