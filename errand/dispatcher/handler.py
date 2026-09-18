"""The dispatcher Lambda.

Reads one SMS off the FIFO queue, turns it into a reply, sends the reply.
Voice memos are transcribed first and then handled as if Andrew had typed
them.

The queue is FIFO with a single message group, so messages are processed in
the order they were sent. That ordering is not a nicety: "T7 yes" arriving
before the task that created T7 would approve nothing, and worse, an approval
arriving out of order after a "STOP ALL" would be a real problem.
"""

from __future__ import annotations

import json
from typing import Any

from errand.dispatcher import conversation, sms, voice
from errand.store import audit_store
from errand.tools import providers


def _cold_start() -> None:
    """Build the provider wiring once per container."""
    try:
        providers.get_providers()
        return
    except providers.ProviderError:
        pass

    import os

    from errand.common import config

    cfg = config.load()
    identity_provider = os.environ.get("ERRAND_IDENTITY_PROVIDER", "").strip()
    if not identity_provider:
        raise RuntimeError("ERRAND_IDENTITY_PROVIDER is not set")

    from errand.tools.providers import FakeSearch

    providers.set_providers(
        providers.build_live_providers(cfg.region, identity_provider, FakeSearch())
    )


def handle_one(message: dict[str, Any]) -> str:
    """One inbound message to one reply body. Returns what was sent."""
    body = (message.get("body") or "").strip()
    media = message.get("media") or []

    if media and not body:
        try:
            body = voice.transcribe_memo(media)
        except voice.TranscriptionError as exc:
            sms.reply(str(exc))
            return str(exc)
        prefix = f'Heard: "{body[:120]}"\n'
    elif media and body:
        # Text wins when both are present; a caption is what he meant to say.
        prefix = ""
    else:
        prefix = ""

    if not body:
        return ""

    reply = conversation.handle(body, source="voice" if media and prefix else "sms")
    text = (prefix + reply.text).strip()
    if text:
        sms.reply(text)
    return text


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    _cold_start()
    failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            message = json.loads(record.get("body") or "{}")
            handle_one(message)
        except Exception as exc:  # noqa: BLE001
            audit_store.write(
                task_id="system",
                phase=audit_store.PHASE_AFTER,
                event="DISPATCH_FAILED",
                outcome="ERROR",
                detail={"error": type(exc).__name__, "message": str(exc)[:300]},
            )
            failures.append({"itemIdentifier": message_id})

    # Partial batch failure: only the messages that actually failed come back
    # for redelivery. Returning the whole batch would replay approvals.
    return {"batchItemFailures": failures}


def releaser(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge, once a minute. Anything past its undo window goes out."""
    _cold_start()
    sent = 0
    for reply in conversation.release_due():
        if reply.text:
            sms.reply(reply.text)
            sent += 1
    return {"released": sent}


def digest(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge, once a morning. The batched-approval text."""
    _cold_start()
    reply = conversation.morning_digest()
    if reply.text:
        sms.reply(reply.text)
    return {"sent": bool(reply.text)}
