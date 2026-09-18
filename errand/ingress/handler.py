"""The Twilio webhook Lambda.

This function does four things and nothing else: validate the signature, check
the allowlist, drop the message on a durable queue, and answer Twilio. It does
not call a model, touch Gmail, or decide anything. Keeping it this small means
the internet-facing surface of Errand is about two hundred lines that are
entirely about saying no.

An unknown number gets HTTP 204 and an audit row. No reply, not even an error:
telling a stranger that their text was rejected confirms the number is live.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from typing import Any

from errand.common import clock, config, secrets
from errand.ingress import twilio_signature
from errand.store import audit_store

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalise_number(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits and not digits.startswith("+"):
        digits = "+" + digits
    return digits


def number_hash(number: str) -> str:
    """Unknown numbers are logged as a hash. We want to be able to tell two
    rejected senders apart without keeping either one's number."""
    salt = os.environ.get("ERRAND_NUMBER_SALT", "errand")
    return hashlib.sha256((salt + normalise_number(number)).encode()).hexdigest()


def is_allowlisted(number: str) -> bool:
    """Hard rule 4: exactly one number. Not a prefix, not a pattern, not a
    list that someone can append to by accident."""
    owner = normalise_number(config.load().owner_number)
    if not _E164.match(owner):
        return False
    import hmac

    return hmac.compare_digest(owner, normalise_number(number))


def _form_params(event: dict[str, Any]) -> dict[str, str]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8", errors="replace")
    parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def _header(event: dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name.lower(), "")


def _response(status: int, body: str = "", content_type: str = "text/plain") -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type},
        "body": body,
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    cfg = config.load()
    params = _form_params(event)
    signature = _header(event, "X-Twilio-Signature")
    webhook_url = os.environ.get("ERRAND_WEBHOOK_URL", "").strip()

    auth_token = secrets.twilio_credentials()["auth_token"]
    if not twilio_signature.is_valid(auth_token, webhook_url, params, signature):
        audit_store.write(
            task_id="system",
            phase=audit_store.PHASE_AFTER,
            event="WEBHOOK_SIGNATURE_REJECTED",
            outcome="DENIED",
            detail={"has_signature": bool(signature), "param_count": len(params)},
        )
        return _response(403, "forbidden")

    from_number = params.get("From", "")
    if not is_allowlisted(from_number):
        audit_store.write_rejected_inbound(
            from_number_hash=number_hash(from_number), reason="not allowlisted"
        )
        return _response(204)

    message = {
        "body": params.get("Body", ""),
        "from": normalise_number(from_number),
        "message_sid": params.get("MessageSid", ""),
        "received_at": clock.now_iso(),
        "media": _media(params),
    }
    _enqueue(cfg.queue_url, message)

    audit_store.write(
        task_id="system",
        phase=audit_store.PHASE_AFTER,
        event="INBOUND_ACCEPTED",
        outcome="QUEUED",
        detail={
            "message_sid": message["message_sid"],
            "chars": len(message["body"]),
            "media": len(message["media"]),
        },
    )
    # Twilio wants TwiML. The real reply comes from the dispatcher as a
    # separate outbound message, so this one is empty.
    return _response(200, EMPTY_TWIML, "application/xml")


def _media(params: dict[str, str]) -> list[dict[str, str]]:
    """Voice memos arrive as MediaUrl0 / MediaContentType0.

    NumMedia arrives signed by Twilio, but a signed request is not the same
    as a well-formed one, so a value that is not a number means no media
    rather than a 500.
    """
    try:
        count = int(params.get("NumMedia", "0") or 0)
    except ValueError:
        return []
    media = []
    for index in range(min(count, 5)):
        url = params.get(f"MediaUrl{index}", "")
        if url:
            media.append(
                {"url": url, "content_type": params.get(f"MediaContentType{index}", "")}
            )
    return media


def _enqueue(queue_url: str, message: dict[str, Any]) -> None:
    import boto3

    client = boto3.client("sqs", region_name=config.load().region)
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # One conversation, one FIFO group: "T7 yes" must never overtake the
        # message that created T7.
        MessageGroupId="andrew",
        MessageDeduplicationId=message["message_sid"] or message["received_at"],
    )
