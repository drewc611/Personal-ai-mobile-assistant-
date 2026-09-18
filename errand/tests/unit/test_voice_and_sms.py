"""Voice memos in, and how replies get onto a phone."""

from __future__ import annotations

import pytest

from errand.dispatcher import handler as dispatcher_handler
from errand.dispatcher import sms, voice
from errand.store import audit_store, tasks_store


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(voice, "fetch_twilio_media", lambda url: b"fake-audio-bytes")
    deleted = []
    monkeypatch.setattr(voice, "delete_twilio_media", lambda url: deleted.append(url) or True)
    return deleted


def test_a_voice_memo_becomes_a_task(transcriber, sender, scripted):
    transcriber.transcripts.append("email the landlord that rent goes out Friday")
    scripted(text="Drafted it.")

    dispatcher_handler.handle_one({
        "body": "",
        "media": [{"url": "https://api.twilio.com/media/ME1", "content_type": "audio/mpeg"}],
    })

    assert tasks_store.all_tasks()[0].title.startswith("email the landlord")
    assert "Heard:" in sender.last()


def test_the_twilio_copy_of_the_memo_is_deleted(_no_network, transcriber, scripted):
    transcriber.transcripts.append("remind me about the gym")
    scripted(text="Noted.")

    dispatcher_handler.handle_one({
        "body": "",
        "media": [{"url": "https://api.twilio.com/media/ME2", "content_type": "audio/mpeg"}],
    })
    assert _no_network == ["https://api.twilio.com/media/ME2"]


def test_a_memo_that_says_stop_all_still_hits_the_kill_switch(transcriber, sender):
    tasks_store.create("something running")
    transcriber.transcripts.append("stop all")

    dispatcher_handler.handle_one({
        "body": "",
        "media": [{"url": "https://x/ME3", "content_type": "audio/mpeg"}],
    })
    assert "Stopped everything" in sender.last()


def test_a_failed_transcription_says_so_and_starts_nothing(sender):
    dispatcher_handler.handle_one({
        "body": "",
        "media": [{"url": "https://x/ME4", "content_type": "audio/mpeg"}],
    })
    assert "no transcript queued" in sender.last() or "could not" in sender.last().lower()
    assert tasks_store.all_tasks() == []


def test_a_caption_beats_the_audio(transcriber, scripted, sender):
    scripted(text="Got it.")
    dispatcher_handler.handle_one({
        "body": "actually just check my calendar",
        "media": [{"url": "https://x/ME5", "content_type": "audio/mpeg"}],
    })
    assert transcriber.calls == []
    assert tasks_store.all_tasks()[0].title == "actually just check my calendar"


def test_the_memo_is_logged_before_and_after(transcriber, scripted):
    transcriber.transcripts.append("book the gym")
    scripted(text="ok")
    dispatcher_handler.handle_one({
        "body": "",
        "media": [{"url": "https://x/ME6", "content_type": "audio/mpeg"}],
    })
    events = [r["event"] for r in audit_store.for_task("system")]
    assert "VOICE_MEMO_RECEIVED" in events
    assert "VOICE_MEMO_TRANSCRIBED" in events


def test_a_short_reply_is_one_message():
    assert sms.split_message("Nothing open.") == ["Nothing open."]


def test_a_long_reply_is_numbered():
    body = "\n".join(f"T{i} something reasonably long to read on a phone" for i in range(1, 30))
    parts = sms.split_message(body)
    assert len(parts) > 1
    assert parts[0].startswith("(1/")
    assert all(len(p) <= 320 for p in parts)


def test_a_very_long_reply_is_truncated_not_spammed():
    body = "\n".join(f"line {i} " + "x" * 200 for i in range(200))
    parts = sms.split_message(body)
    assert len(parts) <= 4
    assert "reply" in parts[-1].lower()


def test_an_empty_reply_sends_nothing():
    assert sms.split_message("   ") == []
