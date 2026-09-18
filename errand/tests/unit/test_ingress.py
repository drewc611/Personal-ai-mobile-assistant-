"""The webhook: signature first, allowlist second, nothing else."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from errand.ingress import handler, twilio_signature
from errand.store import audit_store

URL = "https://errand.example.com/sms"
TOKEN = "test_token"
OWNER = "+15555550123"


def _event(params, *, signature=None, token=TOKEN):
    body = urllib.parse.urlencode(params)
    sig = signature if signature is not None else twilio_signature.expected_signature(
        token, URL, params
    )
    return {
        "body": body,
        "headers": {"X-Twilio-Signature": sig, "Content-Type": "application/x-www-form-urlencoded"},
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True)
def _no_sqs(monkeypatch):
    sent = []
    monkeypatch.setattr(handler, "_enqueue", lambda url, message: sent.append(message))
    return sent


def test_a_valid_signed_message_from_andrew_is_queued(_no_sqs):
    params = {"From": OWNER, "Body": "what's on my calendar Tuesday", "MessageSid": "SM1"}
    response = handler.handler(_event(params))

    assert response["statusCode"] == 200
    assert len(_no_sqs) == 1
    assert _no_sqs[0]["body"] == "what's on my calendar Tuesday"


def test_a_bad_signature_is_rejected(_no_sqs):
    params = {"From": OWNER, "Body": "hello", "MessageSid": "SM2"}
    response = handler.handler(_event(params, signature="not-the-signature"))

    assert response["statusCode"] == 403
    assert _no_sqs == []


def test_a_missing_signature_is_rejected(_no_sqs):
    params = {"From": OWNER, "Body": "hello"}
    response = handler.handler(_event(params, signature=""))
    assert response["statusCode"] == 403


def test_a_signature_from_the_wrong_token_is_rejected(_no_sqs):
    params = {"From": OWNER, "Body": "hello"}
    response = handler.handler(_event(params, token="someone-elses-token"))
    assert response["statusCode"] == 403


def test_a_stranger_gets_no_reply_and_an_audit_row(_no_sqs):
    params = {"From": "+15555559999", "Body": "hey what are you", "MessageSid": "SM3"}
    response = handler.handler(_event(params))

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert _no_sqs == []

    events = audit_store.security_events()
    assert len(events) == 1
    assert events[0]["event"] == "INBOUND_REJECTED"


def test_a_stranger_number_is_hashed_not_stored(_no_sqs):
    handler.handler(_event({"From": "+15555559999", "Body": "hi", "MessageSid": "SM4"}))
    blob = json.dumps(audit_store.security_events())
    assert "5555559999" not in blob


def test_the_allowlist_is_exact_not_a_prefix(_no_sqs):
    handler.handler(_event({"From": OWNER + "9", "Body": "hi", "MessageSid": "SM5"}))
    assert _no_sqs == []


def test_number_formatting_differences_still_match(_no_sqs):
    handler.handler(_event({"From": "+1 (555) 555-0123", "Body": "hi", "MessageSid": "SM6"}))
    assert len(_no_sqs) == 1


def test_media_is_carried_through(_no_sqs):
    params = {
        "From": OWNER,
        "Body": "",
        "MessageSid": "SM7",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1",
        "MediaContentType0": "audio/mpeg",
    }
    handler.handler(_event(params))
    assert _no_sqs[0]["media"] == [
        {"url": "https://api.twilio.com/media/ME1", "content_type": "audio/mpeg"}
    ]


def test_signature_check_is_order_independent():
    params = {"B": "2", "A": "1", "C": "3"}
    reordered = {"C": "3", "A": "1", "B": "2"}
    assert twilio_signature.expected_signature(TOKEN, URL, params) == (
        twilio_signature.expected_signature(TOKEN, URL, reordered)
    )


def test_a_malformed_media_count_means_no_media_not_a_crash(_no_sqs):
    params = {
        "From": OWNER,
        "Body": "hi",
        "MessageSid": "SM8",
        "NumMedia": "lots",
        "MediaUrl0": "https://x/ME",
    }
    response = handler.handler(_event(params))
    assert response["statusCode"] == 200
    assert _no_sqs[0]["media"] == []


def test_stored_content_carries_an_expiry(_no_sqs):
    from errand.store import content_store

    content_store.store(
        connection=content_store.GMAIL, external_id="m1", kind="email_extract", payload={}
    )
    row = content_store.get(content_store.GMAIL, "email_extract", "m1")
    assert row["expires_at"] > 0
