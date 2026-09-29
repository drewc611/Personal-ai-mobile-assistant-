"""The Twilio webhook: signature first, allowlist second, nothing else.

Telegram is the live channel, so these run with the channel switched over --
which is itself worth testing: the adapter is supposed to make that a config
change rather than a code change.
"""

from __future__ import annotations

import json

import pytest

from errand.channels import twilio
from errand.ingress import handler
from errand.store import audit_store
from errand.tests.conftest import (
    AUTH_TOKEN,
    OWNER_NUMBER,
    WEBHOOK_URL,
    mms_params,
    signed_event,
    sms_params,
)


@pytest.fixture(autouse=True)
def _twilio_channel(monkeypatch):
    monkeypatch.setenv("ERRAND_CHANNEL", "twilio")


@pytest.fixture(autouse=True)
def _no_sqs(monkeypatch):
    queued = []
    monkeypatch.setattr(handler, "_enqueue", lambda url, message: queued.append(message))
    return queued


def test_a_valid_signed_message_from_andrew_is_queued(_no_sqs):
    response = handler.handler(signed_event(sms_params("what's on my calendar Tuesday")))

    assert response["statusCode"] == 200
    assert len(_no_sqs) == 1
    assert _no_sqs[0]["text"] == "what's on my calendar Tuesday"
    assert _no_sqs[0]["kind"] == "text"


def test_a_bad_signature_is_rejected(_no_sqs):
    response = handler.handler(
        signed_event(sms_params("hello"), signature="not-the-signature")
    )
    assert response["statusCode"] == 403
    assert _no_sqs == []


def test_a_missing_signature_is_rejected(_no_sqs):
    response = handler.handler(signed_event(sms_params("hello"), signature=""))
    assert response["statusCode"] == 403


def test_a_signature_from_the_wrong_token_is_rejected(_no_sqs):
    response = handler.handler(signed_event(sms_params("hello"), token="someone-elses-token"))
    assert response["statusCode"] == 403


def test_a_signature_over_a_different_url_is_rejected(_no_sqs):
    """The URL is configured rather than rebuilt from the Lambda event,
    because rebuilding it gives the internal hostname."""
    response = handler.handler(
        signed_event(sms_params("hello"), url="https://someone-else.example.com/sms")
    )
    assert response["statusCode"] == 403


def test_a_rejected_signature_writes_an_audit_row(_no_sqs):
    handler.handler(signed_event(sms_params("hello"), signature="wrong"))
    events = [r["event"] for r in audit_store.for_task("system")]
    assert "WEBHOOK_SIGNATURE_REJECTED" in events


def test_the_signature_is_order_independent():
    params = {"B": "2", "A": "1", "C": "3"}
    reordered = {"C": "3", "A": "1", "B": "2"}
    assert twilio.expected_signature(AUTH_TOKEN, WEBHOOK_URL, params) == (
        twilio.expected_signature(AUTH_TOKEN, WEBHOOK_URL, reordered)
    )


def test_a_stranger_gets_no_reply_and_one_audit_row(_no_sqs, channel):
    response = handler.handler(signed_event(sms_params("hey what are you", sender="+15555559999")))

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert _no_sqs == []
    assert channel.texts == []

    events = audit_store.security_events()
    assert len(events) == 1
    assert events[0]["event"] == "INBOUND_REJECTED"


def test_a_stranger_number_is_hashed_not_stored(_no_sqs):
    handler.handler(signed_event(sms_params("hi", sender="+15555559999")))
    assert "5555559999" not in json.dumps(audit_store.security_events())


def test_the_allowlist_is_exact_not_a_prefix(_no_sqs):
    handler.handler(signed_event(sms_params("hi", sender=OWNER_NUMBER + "9")))
    assert _no_sqs == []


def test_number_formatting_differences_still_match(_no_sqs):
    handler.handler(signed_event(sms_params("hi", sender="+1 (555) 555-0123")))
    assert len(_no_sqs) == 1


def test_a_voice_memo_is_carried_through(_no_sqs):
    handler.handler(signed_event(mms_params()))
    assert _no_sqs[0]["kind"] == "voice"
    assert _no_sqs[0]["voice_ref"] == "https://api.twilio.com/media/ME1"
    assert _no_sqs[0]["raw"]["mime_type"] == "audio/mpeg"


def test_a_caption_on_an_mms_is_treated_as_text(_no_sqs):
    handler.handler(signed_event(mms_params(body="check my calendar")))
    assert _no_sqs[0]["kind"] == "text"
    assert _no_sqs[0]["text"] == "check my calendar"


def test_a_malformed_media_count_means_no_media_not_a_crash(_no_sqs):
    params = sms_params("hi")
    params["NumMedia"] = "lots"
    params["MediaUrl0"] = "https://x/ME"
    response = handler.handler(signed_event(params))
    assert response["statusCode"] == 200
    assert _no_sqs[0]["kind"] == "text"


def test_an_empty_message_with_no_media_is_acknowledged_not_queued(_no_sqs):
    """Twilio retries anything not answered with a 2xx."""
    response = handler.handler(signed_event(sms_params("")))
    assert response["statusCode"] == 200
    assert _no_sqs == []


def test_the_message_sid_is_the_dedup_key(_no_sqs):
    handler.handler(signed_event(sms_params("hi", sid="SM42")))
    assert _no_sqs[0]["update_id"] == "SM42"
