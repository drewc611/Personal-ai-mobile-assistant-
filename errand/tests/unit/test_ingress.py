"""The Telegram webhook: secret token first, sender second, nothing else."""

from __future__ import annotations

import json

import pytest

from errand.channels import telegram
from errand.ingress import handler
from errand.store import audit_store
from errand.tests.conftest import (
    OWNER_ID,
    WEBHOOK_SECRET,
    button_press,
    text_message,
    voice_message,
)


def _event(update, *, secret=WEBHOOK_SECRET, header=telegram.SECRET_HEADER):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[header] = secret
    return {"body": json.dumps(update), "headers": headers, "isBase64Encoded": False}


@pytest.fixture(autouse=True)
def _no_sqs(monkeypatch):
    queued = []
    monkeypatch.setattr(handler, "_enqueue", lambda url, message: queued.append(message))
    return queued


def test_a_valid_update_from_andrew_is_queued(_no_sqs):
    response = handler.handler(_event(text_message("what's on my calendar Tuesday")))

    assert response["statusCode"] == 200
    assert len(_no_sqs) == 1
    assert _no_sqs[0]["text"] == "what's on my calendar Tuesday"
    assert _no_sqs[0]["kind"] == "text"


def test_a_wrong_secret_token_is_rejected(_no_sqs):
    response = handler.handler(_event(text_message("hello"), secret="not-the-secret"))
    assert response["statusCode"] == 403
    assert _no_sqs == []


def test_a_missing_secret_header_is_rejected(_no_sqs):
    response = handler.handler(_event(text_message("hello"), secret=None))
    assert response["statusCode"] == 403
    assert _no_sqs == []


def test_the_header_name_is_the_one_telegram_sends(_no_sqs):
    """Telegram sends X-Telegram-Bot-Api-Secret-Token. A near-miss on the name
    would mean the check silently never matches, so it is pinned here."""
    assert telegram.SECRET_HEADER == "X-Telegram-Bot-Api-Secret-Token"
    response = handler.handler(_event(text_message("hi"), header="X-Telegram-Secret"))
    assert response["statusCode"] == 403


def test_the_header_check_is_case_insensitive(_no_sqs):
    """API Gateway lowercases header names; the check must still find it."""
    response = handler.handler(
        _event(text_message("hi"), header="x-telegram-bot-api-secret-token")
    )
    assert response["statusCode"] == 200


def test_a_rejected_secret_writes_an_audit_row(_no_sqs):
    handler.handler(_event(text_message("hello"), secret="wrong"))
    events = [r["event"] for r in audit_store.for_task("system")]
    assert "WEBHOOK_SECRET_REJECTED" in events


def test_another_telegram_account_gets_no_reply_and_one_audit_row(_no_sqs):
    response = handler.handler(_event(text_message("hey what are you", sender="99999")))

    assert response["statusCode"] == 200
    assert response["body"] == ""
    assert _no_sqs == []

    events = audit_store.security_events()
    assert len(events) == 1
    assert events[0]["event"] == "INBOUND_REJECTED"


def test_a_rejected_sender_id_is_hashed_not_stored(_no_sqs):
    handler.handler(_event(text_message("hi", sender="99999")))
    assert "99999" not in json.dumps(audit_store.security_events())


def test_the_allowlist_is_exact(_no_sqs):
    handler.handler(_event(text_message("hi", sender=OWNER_ID + "1")))
    assert _no_sqs == []


def test_a_voice_note_is_carried_through(_no_sqs):
    handler.handler(_event(voice_message()))
    assert _no_sqs[0]["kind"] == "voice"
    assert _no_sqs[0]["voice_ref"] == "AwACAgEAAx"


def test_a_button_press_is_carried_through(_no_sqs):
    handler.handler(_event(button_press("a:ap_abc123")))
    assert _no_sqs[0]["kind"] == "button"
    assert _no_sqs[0]["token"] == "a:ap_abc123"
    assert _no_sqs[0]["raw"]["callback_query_id"] == "cbq1"


def test_an_update_we_do_not_handle_is_acknowledged_not_queued(_no_sqs):
    """A sticker. Telegram keeps resending anything not answered with a 200."""
    update = {"update_id": 5, "message": {"message_id": 1, "from": {"id": int(OWNER_ID)},
                                          "sticker": {"file_id": "x"}}}
    assert handler.handler(_event(update))["statusCode"] == 200
    assert _no_sqs == []


def test_a_malformed_body_does_not_crash(_no_sqs):
    event = {
        "body": "not json at all",
        "headers": {telegram.SECRET_HEADER: WEBHOOK_SECRET},
        "isBase64Encoded": False,
    }
    assert handler.handler(event)["statusCode"] == 200
    assert _no_sqs == []


def test_the_update_id_is_the_dedup_key(_no_sqs):
    handler.handler(_event(text_message("hi", update_id="42")))
    assert _no_sqs[0]["update_id"] == "42"
