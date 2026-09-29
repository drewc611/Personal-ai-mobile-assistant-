"""Calendar tools.

Reading the week is free. Responding to an invite is tier 2: an accept is a
message to the organiser, and third parties finding out where Andrew will be
is exactly the kind of thing that should need a yes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from errand.policy.tiers import Tier
from errand.store import content_store
from errand.tools import providers
from errand.tools.registry import tool

VALID_RESPONSES = ("accepted", "declined", "tentative")


def _day_bounds(day_iso: str) -> tuple[str, str]:
    day = dt.date.fromisoformat(day_iso)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    return start.isoformat(), (start + dt.timedelta(days=1)).isoformat()


@tool(
    "calendar_day",
    tier=Tier.READ,
    summary="List a day's events",
    domain="calendar",
    describe=lambda a: f"look at {a.get('day', 'the calendar')}",
)
def calendar_day(day: str, task_id: str = "") -> dict[str, Any]:
    """Events for one date, as YYYY-MM-DD."""
    from errand.reader.schemas import clean_text

    start, end = _day_bounds(day)
    events = providers.get_providers().calendar.events_between(start, end)
    cleaned = [
        {
            "id": e.get("id", ""),
            "title": clean_text(e.get("title", ""), 160),
            "start": clean_text(e.get("start", ""), 40),
            "end": clean_text(e.get("end", ""), 40),
            "location": clean_text(e.get("location", ""), 160),
            "attendees": int(e.get("attendees", 0) or 0),
        }
        for e in events
    ]
    for event in cleaned:
        if event["id"]:
            content_store.store(
                connection=content_store.CALENDAR,
                external_id=event["id"],
                kind="event",
                payload=event,
                task_id=task_id,
            )
    return {"day": day, "count": len(cleaned), "events": cleaned}


@tool(
    "calendar_range",
    tier=Tier.READ,
    summary="List events between two timestamps",
    domain="calendar",
)
def calendar_range(start_iso: str, end_iso: str) -> dict[str, Any]:
    events = providers.get_providers().calendar.events_between(start_iso, end_iso)
    return {"count": len(events), "events": events}


@tool(
    "calendar_respond",
    tier=Tier.SEND,
    summary="Reply to a calendar invitation",
    domain="calendar",
    describe=lambda a: f"{a.get('response', 'respond')} the invite {a.get('event_id', '')}",
)
def calendar_respond(event_id: str, response: str) -> dict[str, Any]:
    if response not in VALID_RESPONSES:
        raise ValueError(f"response must be one of {', '.join(VALID_RESPONSES)}")
    return providers.get_providers().calendar.respond(event_id, response)
