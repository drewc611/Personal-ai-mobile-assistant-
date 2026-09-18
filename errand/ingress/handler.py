"""The Telegram webhook Lambda.

This function does four things and nothing else: check the secret token
header, check the sender, drop the update on a durable queue, and answer
Telegram. It does not call a model, touch Gmail, or decide anything. Keeping
it this small means the internet-facing surface of Errand is a couple of
hundred lines that are entirely about saying no.

An unknown sender gets HTTP 200 with an empty body and one audit row. Two
hundred rather than an error because Telegram retries on failure and there is
nothing to retry; no reply because telling a stranger their message was
rejected confirms the bot is live.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from errand.channels import telegram
from errand.common import clock, config, secrets
from errand.store import audit_store


def _header(event: dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    lowered = {str(k).lower(): v for k, v in headers.items()}
    return str(lowered.get(name.lower(), ""))


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def secret_ok(event: dict[str, Any], expected: str) -> bool:
    """Hard rule 4.

    Telegram sends the value of `secret_token` back in the
    X-Telegram-Bot-Api-Secret-Token header on every update. The comparison is
    constant time; a fast reject leaks the secret one byte at a time.
    """
    presented = _header(event, telegram.SECRET_HEADER)
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def is_allowlisted(sender_id: str) -> bool:
    """Hard rule 3: exactly one Telegram user id. Not a list, not a pattern."""
    owner = config.load().owner_telegram_id.strip()
    if not owner or not str(sender_id).strip():
        return False
    return hmac.compare_digest(owner, str(sender_id).strip())


def sender_hash(sender_id: str) -> str:
    """Unknown senders are logged as a hash. Telling two of them apart is
    worth something; keeping either one's account id is not."""
    salt = os.environ.get("ERRAND_SENDER_SALT", "errand")
    return hashlib.sha256((salt + str(sender_id)).encode()).hexdigest()


def _response(status: int, body: str = "") -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": body}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    cfg = config.load()
    expected = secrets.telegram_credentials()["webhook_secret"]

    if not secret_ok(event, expected):
        audit_store.write(
            task_id="system",
            phase=audit_store.PHASE_AFTER,
            event="WEBHOOK_SECRET_REJECTED",
            outcome="DENIED",
            detail={"had_header": bool(_header(event, telegram.SECRET_HEADER))},
        )
        return _response(403, "forbidden")

    update = _body(event)
    inbound = _reader().receive(update)
    if inbound is None:
        # A sticker, a channel post, an edit we do not handle. Acknowledged so
        # Telegram stops resending it.
        return _response(200)

    if not is_allowlisted(inbound.sender_id):
        audit_store.write_rejected_inbound(
            from_number_hash=sender_hash(inbound.sender_id), reason="not allowlisted"
        )
        return _response(200)

    message = {
        "kind": inbound.kind,
        "text": inbound.text,
        "token": inbound.token,
        "sender_id": inbound.sender_id,
        "message_id": inbound.message_id,
        "update_id": inbound.update_id,
        "voice_ref": inbound.voice_ref,
        "raw": inbound.raw,
        "received_at": clock.now_iso(),
    }
    _enqueue(cfg.queue_url, message)

    audit_store.write(
        task_id="system",
        phase=audit_store.PHASE_AFTER,
        event="INBOUND_ACCEPTED",
        outcome="QUEUED",
        detail={
            "kind": inbound.kind,
            "update_id": inbound.update_id,
            "chars": len(inbound.text),
        },
    )
    return _response(200)


def _reader() -> telegram.TelegramChannel:
    """Parsing an update needs no credentials, so the webhook does not fetch
    the bot token to do it."""
    return telegram.TelegramChannel(bot_token="", chat_id="")


def _enqueue(queue_url: str, message: dict[str, Any]) -> None:
    import boto3

    client = boto3.client("sqs", region_name=config.load().region)
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # One conversation, one FIFO group: an Approve must never overtake the
        # message that created the approval, and nothing may overtake STOP ALL.
        MessageGroupId="andrew",
        MessageDeduplicationId=message["update_id"] or message["received_at"],
    )
