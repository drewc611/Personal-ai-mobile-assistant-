"""The inbound webhook Lambda.

This function does four things and nothing else: verify the request came from
the messaging provider, check the sender is Andrew, drop the message on a
durable queue, and answer. It does not call a model, touch Gmail, or decide
anything. Keeping it this small means the internet-facing surface of Errand is
a couple of hundred lines that are entirely about saying no.

Which provider it is talking to comes from config. Everything provider-shaped
-- how a request is verified, how a body is parsed, what an "is this Andrew"
check means -- lives in `channels/`, so adding a provider is a file there and
three lines here rather than a second webhook.

An unrecognised sender gets a bare acknowledgement and an audit row. No reply,
not even an error: telling a stranger their message was rejected confirms the
endpoint is live.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from typing import Any

from errand.channels import telegram, twilio
from errand.common import clock, config, secrets
from errand.store import audit_store

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in (event.get("headers") or {}).items()}


def _raw_body(event: dict[str, Any]) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8", errors="replace")
    return body


def sender_hash(sender_id: str) -> str:
    """An unrecognised sender is logged as a hash. Telling two of them apart is
    worth something; keeping either one's number or account id is not."""
    salt = os.environ.get("ERRAND_SENDER_SALT") or os.environ.get("ERRAND_NUMBER_SALT", "errand")
    return hashlib.sha256((salt + str(sender_id)).encode()).hexdigest()


def _response(status: int, body: str = "", content_type: str = "text/plain") -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


# --------------------------------------------------------------------------
# Per-channel adapters. Each returns (verified, inbound_payload_or_None).
# --------------------------------------------------------------------------


def _handle_telegram(event: dict[str, Any], cfg) -> tuple[bool, Any, dict[str, Any]]:
    secret = secrets.telegram_credentials()["webhook_secret"]
    if not telegram.verify_webhook(_headers(event), secret):
        return False, None, _response(403, "forbidden")

    update = telegram.parse_body(_raw_body(event))
    parser = telegram.TelegramChannel(bot_token="", chat_id="")
    inbound = parser.receive(update)

    if inbound is None:
        # A sticker, a channel post, an edit we do not handle. Acknowledged so
        # Telegram stops resending it.
        return True, None, _response(200)

    if not telegram.owner_matches(inbound.sender_id, cfg.owner_telegram_id):
        audit_store.write_rejected_inbound(
            from_number_hash=sender_hash(inbound.sender_id), reason="not allowlisted"
        )
        return True, None, _response(200)

    return True, inbound, _response(200)


def _handle_twilio(event: dict[str, Any], cfg) -> tuple[bool, Any, dict[str, Any]]:
    auth_token = secrets.twilio_credentials()["auth_token"]
    params = {
        k: v[0]
        for k, v in urllib.parse.parse_qs(_raw_body(event), keep_blank_values=True).items()
    }
    lowered = {k.lower(): v for k, v in _headers(event).items()}
    signature = lowered.get(twilio.SIGNATURE_HEADER.lower(), "")
    webhook_url = os.environ.get("ERRAND_WEBHOOK_URL", "").strip()

    if not twilio.signature_is_valid(auth_token, webhook_url, params, signature):
        return False, None, _response(403, "forbidden")

    owner = twilio.normalise_number(cfg.owner_number)
    if not owner or twilio.normalise_number(params.get("From", "")) != owner:
        audit_store.write_rejected_inbound(
            from_number_hash=sender_hash(params.get("From", "")), reason="not allowlisted"
        )
        return True, None, _response(204)

    parser = twilio.TwilioChannel(account_sid="", auth_token="", from_number="", to_number="")
    inbound = parser.receive(params)
    ok = _response(200, EMPTY_TWIML, "application/xml")
    return True, inbound, ok


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    cfg = config.load()

    if cfg.is_telegram:
        verified, inbound, response = _handle_telegram(event, cfg)
        rejected_event = "WEBHOOK_SECRET_REJECTED"
    else:
        verified, inbound, response = _handle_twilio(event, cfg)
        rejected_event = "WEBHOOK_SIGNATURE_REJECTED"

    if not verified:
        audit_store.write(
            task_id="system",
            phase=audit_store.PHASE_AFTER,
            event=rejected_event,
            outcome="DENIED",
            detail={"channel": cfg.channel},
        )
        return response

    if inbound is None:
        return response

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
        detail={"channel": cfg.channel, "kind": inbound.kind, "chars": len(inbound.text)},
    )
    return response


def _enqueue(queue_url: str, message: dict[str, Any]) -> None:
    import boto3

    client = boto3.client("sqs", region_name=config.load().region)
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # One conversation, one FIFO group: an approval must never overtake
        # the message that created it, and nothing may overtake a STOP ALL.
        MessageGroupId="andrew",
        MessageDeduplicationId=message["update_id"] or message["received_at"],
    )
