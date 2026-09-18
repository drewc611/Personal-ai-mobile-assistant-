"""Voice notes in."""

from __future__ import annotations

from errand.dispatcher import handler as dispatcher_handler
from errand.store import audit_store, tasks_store
from errand.tests.conftest import OWNER_ID


def _voice_queue_message(caption: str = ""):
    return {
        "kind": "voice",
        "text": caption,
        "sender_id": OWNER_ID,
        "message_id": "101",
        "update_id": "1",
        "voice_ref": "AwACAgEAAx",
        "raw": {"mime_type": "audio/ogg"},
    }


def test_a_voice_note_becomes_a_task(transcriber, channel, scripted):
    transcriber.transcripts.append("email the landlord that rent goes out Friday")
    scripted(text="Drafted it.")

    dispatcher_handler.handle_one(_voice_queue_message())

    assert tasks_store.all_tasks()[0].title.startswith("email the landlord")
    assert "Heard:" in channel.all_output
    assert "Drafted it" in channel.all_output


def test_the_transcript_is_shown_back_so_a_misheard_note_is_obvious(transcriber, channel,
                                                                   scripted):
    transcriber.transcripts.append("book the gym for Thursday")
    scripted(text="On it.")

    dispatcher_handler.handle_one(_voice_queue_message())
    assert 'Heard: "book the gym for Thursday"' in channel.all_output


def test_a_spoken_stop_all_still_hits_the_kill_switch(transcriber, channel):
    tasks_store.create("something running")
    transcriber.transcripts.append("stop all")

    dispatcher_handler.handle_one(_voice_queue_message())
    assert "Stopped everything" in channel.all_output


def test_a_failed_transcription_says_so_and_starts_nothing(channel):
    dispatcher_handler.handle_one(_voice_queue_message())

    assert "could not make out" in channel.last_text
    assert tasks_store.all_tasks() == []


def test_a_caption_beats_the_audio(transcriber, channel, scripted):
    scripted(text="Got it.")
    dispatcher_handler.handle_one(_voice_queue_message(caption="actually check my calendar"))

    assert transcriber.calls == []
    assert tasks_store.all_tasks()[0].title == "actually check my calendar"


def test_the_note_is_logged_before_and_after(transcriber, scripted):
    transcriber.transcripts.append("book the gym")
    scripted(text="ok")
    dispatcher_handler.handle_one(_voice_queue_message())

    events = [r["event"] for r in audit_store.for_task("system")]
    assert "VOICE_NOTE_RECEIVED" in events
    assert "VOICE_NOTE_TRANSCRIBED" in events


def test_an_unfetchable_note_reports_rather_than_crashing(channel, monkeypatch):
    def boom(ref):
        raise RuntimeError("telegram said no")

    monkeypatch.setattr(channel, "fetch_voice", boom)
    dispatcher_handler.handle_one(_voice_queue_message())

    assert "could not fetch" in channel.last_text
    assert tasks_store.all_tasks() == []
