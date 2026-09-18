"""The Twilio webhook Lambda.

This function does four things and nothing else: validate the signature, check
the allowlist, drop the message on a durable queue, and answer Twilio. It does
not call a model, touch Gmail, or decide anything. Keeping it this small means
the internet-facing surface of Errand is a couple of hundred lines that are
entirely about saying no.

An unknown number gets HTTP 204 and an audit row. No reply, not even an error:
telling a stranger their text was rejected confirms the number is live.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.parse
from typing import Any

from errand.channels import twilio
from errand.common import clock, config, secrets
from errand.store import audit_store

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def _form_params(event: dict[str, Any]) -> dict[str, str]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8", errors="replace")
    parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def _header(event: dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    lowered = {str(k).lower(): v for k, v in headers.items()}
    return str(lowered.get(name.lower(), ""))


def is_allowlisted(number: str) -> bool:
    """Hard rule 4: exactly one number. Not a prefix, not a pattern, not a
    list that someone can append to by accident."""
    owner = twilio.normalise_number(config.load().owner_number)
    if not _E164.match(owner):
        return False
    return hmac.compare_digest(owner, twilio.normalise_number(number))


def number_hash(number: str) -> str:
    """Unknown numbers are logged as a hash. Telling two rejected senders
    apart is worth something; keeping either one's number is not."""
    salt = os.environ.get("ERRAND_NUMBER_SALT", "errand")
    return hashlib.sha256((salt + twilio.normalise_number(number)).encode()).hexdigest()


def _response(status: int, body: str = "", content_type: str = "text/plain") -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    cfg = config.load()
    params = _form_params(event)
    signature = _header(event, twilio.SIGNATURE_HEADER)
    webhook_url = os.environ.get("ERRAND_WEBHOOK_URL", "").strip()
    auth_token = secrets.twilio_credentials()["auth_token"]

    if not twilio.signature_is_valid(auth_token, webhook_url, params, signature):
        audit_store.write(
            task_id="system",
            phase=audit_store.PHASE_AFTER,
            event="WEBHOOK_SIGNATURE_REJECTED",
            outcome="DENIED",
            detail={"had_signature": bool(signature), "param_count": len(params)},
        )
        return _response(403, "forbidden")

    from_number = params.get("From", "")
    if not is_allowlisted(from_number):
        audit_store.write_rejected_inbound(
            from_number_hash=number_hash(from_number), reason="not allowlisted"
        )
        return _response(204)

    inbound = _parser().receive(params)
    if inbound is None:
        # An empty body with no audio. Acknowledged so Twilio stops retrying.
        return _response(200, EMPTY_TWIML, "application/xml")

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
            "message_sid": inbound.message_id,
            "chars": len(inbound.text),
        },
    )
    # Twilio wants TwiML. The real reply comes from the dispatcher as a
    # separate outbound message, so this one is empty.
    return _response(200, EMPTY_TWIML, "application/xml")


def _parser() -> twilio.TwilioChannel:
    """Parsing a webhook body needs no credentials, so the ingress function
    does not fetch the account sid to do it."""
    return twilio.TwilioChannel(
        account_sid="", auth_token="", from_number="", to_number=""
    )


def _enqueue(queue_url: str, message: dict[str, Any]) -> None:
    import boto3

    client = boto3.client("sqs", region_name=config.load().region)
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # One conversation, one FIFO group: "T7 yes" must never overtake the
        # message that created T7, and nothing may overtake a STOP ALL.
        MessageGroupId="andrew",
        MessageDeduplicationId=message["update_id"] or message["received_at"],
    )
