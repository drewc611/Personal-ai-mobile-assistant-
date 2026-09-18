"""The dispatcher Lambda.

Reads one update off the FIFO queue, turns it into a reply, sends the reply
through the Channel. Voice notes are transcribed first and then handled as if
Andrew had typed them.

The queue is FIFO with a single message group, so updates are processed in the
order they arrived. That ordering is not a nicety: an Approve arriving before
the task that created it would approve nothing, and an Approve arriving after
a STOP ALL would be a real problem.
"""

from __future__ import annotations

import json
from typing import Any

from errand.channels import base as channels
from errand.channels.base import Inbound
from errand.dispatcher import conversation, voice
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
    from errand.tools.providers import FakeSearch

    identity_provider = os.environ.get("ERRAND_IDENTITY_PROVIDER", "").strip()
    if not identity_provider:
        raise RuntimeError("ERRAND_IDENTITY_PROVIDER is not set")

    cfg = config.load()
    providers.set_providers(
        providers.build_live_providers(cfg.region, identity_provider, FakeSearch())
    )


def _inbound_from(message: dict[str, Any]) -> Inbound:
    return Inbound(
        kind=message.get("kind", "text"),
        text=message.get("text", "") or "",
        token=message.get("token", "") or "",
        sender_id=message.get("sender_id", "") or "",
        message_id=message.get("message_id", "") or "",
        update_id=message.get("update_id", "") or "",
        voice_ref=message.get("voice_ref", "") or "",
        raw=message.get("raw") or {},
    )


def handle_one(message: dict[str, Any]) -> conversation.Reply:
    """One queued update to one delivered reply."""
    channel = channels.get_channel()
    inbound = _inbound_from(message)
    prefix = ""

    if inbound.kind == "voice":
        if inbound.text:
            # A caption beats the audio: it is what he meant to say.
            inbound = Inbound(kind="text", text=inbound.text, sender_id=inbound.sender_id,
                              message_id=inbound.message_id, update_id=inbound.update_id)
        else:
            try:
                audio = channel.fetch_voice(inbound.voice_ref)
                spoken = voice.transcribe_note(
                    audio, str(inbound.raw.get("mime_type") or "audio/ogg")
                )
            except Exception as exc:  # noqa: BLE001
                note = str(exc) if isinstance(exc, voice.TranscriptionError) else (
                    "I could not fetch that voice note."
                )
                channel.send_text(note)
                return conversation.Reply(note)
            prefix = f'Heard: "{spoken[:150]}"\n'
            inbound = Inbound(kind="text", text=spoken, sender_id=inbound.sender_id,
                              message_id=inbound.message_id, update_id=inbound.update_id)

    reply = conversation.respond(inbound)

    if reply.retire_message:
        channel.retire_buttons(reply.retire_message)
    if reply.acknowledge or message.get("kind") == "button":
        channel.acknowledge(_inbound_from(message), reply.acknowledge)

    text = (prefix + reply.text).strip()
    if reply.buttons:
        channel.send_buttons(text or "What next?", reply.buttons)
    elif text:
        channel.send_text(text)

    return reply


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    _cold_start()
    failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            handle_one(json.loads(record.get("body") or "{}"))
        except Exception as exc:  # noqa: BLE001
            audit_store.write(
                task_id="system",
                phase=audit_store.PHASE_AFTER,
                event="DISPATCH_FAILED",
                outcome="ERROR",
                detail={"error": type(exc).__name__, "message": str(exc)[:300]},
            )
            failures.append({"itemIdentifier": message_id})

    # Partial batch failure: only the updates that actually failed come back
    # for redelivery. Returning the whole batch would replay approvals.
    return {"batchItemFailures": failures}


def releaser(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge, once a minute. Anything past its undo window goes out."""
    _cold_start()
    channel = channels.get_channel()
    sent = 0
    for reply in conversation.release_due():
        if reply.text:
            channel.send_text(reply.text)
            sent += 1
    return {"released": sent}


def digest(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge Scheduler, once a day. The batched approval message."""
    _cold_start()
    channel = channels.get_channel()
    reply = conversation.daily_digest()
    if reply.buttons:
        channel.send_buttons(reply.text, reply.buttons)
    elif reply.text:
        channel.send_text(reply.text)
    return {"sent": bool(reply.text)}
