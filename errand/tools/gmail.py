"""Gmail tools.

Read is tier 0, draft is tier 1, send is tier 2. The tier boundary between
draft and send is the whole disagreement with Instinct: writing the email is
free, putting it in someone else's inbox is not.

Nothing here returns a raw message body to the caller. `gmail_read` hands the
bytes to the quarantined reader and returns only the extracted fields.
"""

from __future__ import annotations

from typing import Any

from errand.policy.tiers import Tier
from errand.reader import quarantine
from errand.store import content_store
from errand.tools import providers
from errand.tools.registry import tool


@tool(
    "gmail_search",
    tier=Tier.READ,
    summary="Search the inbox",
    domain="email",
    describe=lambda a: f"search inbox for {a.get('query', '')!r}",
)
def gmail_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the inbox. Returns senders and subjects only - never bodies.

    Subjects are third-party text too, so they are cleaned before they travel
    any further.
    """
    from errand.reader.schemas import clean_text

    hits = providers.get_providers().gmail.search(query, max(1, min(int(limit), 10)))
    return {
        "count": len(hits),
        "messages": [
            {
                "id": h["id"],
                "from": clean_text(h.get("from", ""), 120),
                "subject": clean_text(h.get("subject", ""), 160),
                "received": clean_text(h.get("received", ""), 40),
            }
            for h in hits
        ],
    }


@tool(
    "gmail_read",
    tier=Tier.READ,
    summary="Read one email through the quarantined reader",
    domain="email",
    describe=lambda a: f"read message {a.get('message_id', '')}",
)
def gmail_read(message_id: str, task_id: str = "") -> dict[str, Any]:
    """Hard rule 2 in practice: the raw body goes to the reader model and the
    caller gets the extract."""
    raw = providers.get_providers().gmail.get_raw(message_id)
    result = quarantine.read_email(raw)
    content_store.store(
        connection=content_store.GMAIL,
        external_id=message_id,
        kind="email_extract",
        payload=result.data,
        task_id=task_id,
    )
    return {
        "message_id": message_id,
        "extract": result.data,
        "dropped_keys": result.dropped_keys,
        "flagged_as_instructions": result.flagged,
    }


@tool(
    "gmail_draft",
    tier=Tier.DRAFT,
    summary="Write a draft email",
    domain="email",
    describe=lambda a: f"draft to {a.get('to', '')}: {a.get('subject', '')}",
)
def gmail_draft(to: str, subject: str, body: str, task_id: str = "") -> dict[str, Any]:
    """Runs freely and shows Andrew the draft. Creating it sends nothing."""
    draft = providers.get_providers().gmail.create_draft(to, subject, body)
    content_store.store(
        connection=content_store.GMAIL,
        external_id=draft["id"],
        kind="draft",
        payload={"to": to, "subject": subject},
        task_id=task_id,
    )
    return {"draft_id": draft["id"], "to": to, "subject": subject, "body": body}


@tool(
    "gmail_send",
    tier=Tier.SEND,
    summary="Send a drafted email",
    domain="email",
    describe=lambda a: f"send the draft to {a.get('to', 'the recipient')}",
)
def gmail_send(draft_id: str, to: str = "", task_id: str = "") -> dict[str, Any]:
    """Tier 2. Reaching this function means an approval exists and the undo
    window closed - `registry.call` returns PENDING_APPROVAL otherwise."""
    return providers.get_providers().gmail.send_draft(draft_id)
