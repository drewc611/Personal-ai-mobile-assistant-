"""The v0 acceptance criteria over Telegram.

Same guarantees as the SMS run, different channel. That they pass unchanged
above the `channels/` boundary is the point of having the boundary.
"""

from __future__ import annotations

import json

import pytest

from errand.agent import planner as planner_mod
from errand.common import clock
from errand.dispatcher import conversation
from errand.dispatcher import handler as dispatcher_handler
from errand.ingress import handler as ingress
from errand.store import tasks_store
from errand.tests.conftest import (
    FIXED_NOW,
    say,
    telegram_event,
    telegram_text,
    telegram_voice,
)
from errand.tools import registry

pytestmark = pytest.mark.acceptance


@pytest.fixture(autouse=True)
def _telegram_channel(monkeypatch):
    monkeypatch.setenv("ERRAND_CHANNEL", "telegram")


def _run_through(monkeypatch, update):
    queued = []
    monkeypatch.setattr(ingress, "_enqueue", lambda url, message: queued.append(message))
    monkeypatch.setattr(dispatcher_handler, "_cold_start", lambda: None)

    assert ingress.handler(telegram_event(update))["statusCode"] == 200
    dispatcher_handler.handler({"Records": [{"messageId": "1", "body": json.dumps(queued[0])}]})
    return queued


def test_calendar_question_answers_over_telegram(monkeypatch, calendar, channel):
    calendar.add_event(
        event_id="e1", title="Dentist", start="2025-12-02T09:00:00+00:00",
        end="2025-12-02T10:00:00+00:00", location="Market St",
    )

    def script(task, message):
        return planner_mod.PlannerReply(
            text="Tuesday: Dentist 9-10am, Market St.",
            tool_calls=[("calendar_day", {"day": "2025-12-02"})],
        )

    planner_mod.set_planner(planner_mod.ScriptedPlanner(script=script))
    _run_through(monkeypatch, telegram_text("what's on my calendar Tuesday"))

    assert "Dentist" in channel.all_output
    assert "T1" in channel.all_output


def test_an_approval_still_gates_the_send(gmail):
    task = tasks_store.create("email the landlord")
    draft = registry.call(
        task.task_id, "gmail_draft",
        {"to": "landlord@example.com", "subject": "Rent", "body": "Friday"},
    )
    result = registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"]})

    assert result.status == "PENDING_APPROVAL"
    assert gmail.sent == []

    say(f"{task.task_id} yes")
    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 1


def test_a_stranger_gets_nothing(monkeypatch, channel):
    queued = []
    monkeypatch.setattr(ingress, "_enqueue", lambda url, message: queued.append(message))

    response = ingress.handler(telegram_event(telegram_text("hello?", sender="99999")))

    assert response["statusCode"] == 200
    assert response["body"] == ""
    assert queued == []
    assert channel.texts == []


def test_a_voice_note_becomes_a_task(monkeypatch, transcriber, channel, scripted):
    transcriber.transcripts.append("check my calendar for Tuesday")
    scripted(text="Nothing on Tuesday.")

    _run_through(monkeypatch, telegram_voice())

    assert tasks_store.all_tasks()[0].title == "check my calendar for Tuesday"
    assert "Heard:" in channel.all_output


def test_switching_channel_is_config_only(monkeypatch):
    """The adapter's whole claim: nothing above channels/ changes."""
    from errand.common import config

    monkeypatch.setenv("ERRAND_CHANNEL", "twilio")
    assert not config.load().is_telegram
    monkeypatch.setenv("ERRAND_CHANNEL", "telegram")
    assert config.load().is_telegram
