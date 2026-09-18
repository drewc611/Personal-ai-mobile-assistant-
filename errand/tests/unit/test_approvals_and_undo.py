"""Approvals, the undo window, batching, and the kill switch."""

from __future__ import annotations

from errand.common import clock
from errand.dispatcher import conversation
from errand.policy import approvals, outbox
from errand.store import tasks_store
from errand.tests.conftest import FIXED_NOW
from errand.tools import registry


def _drafted_send(gmail, to="landlord@example.com"):
    task = tasks_store.create("email the landlord")
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": to, "subject": "Rent", "body": "Going out Friday."}
    )
    registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"], "to": to})
    return task


def test_yes_holds_then_releases(gmail):
    task = _drafted_send(gmail)

    reply = conversation.handle(f"{task.task_id} yes")
    assert "goes out in 60s" in reply.text
    assert gmail.sent == []

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 1
    assert gmail.sent[0]["to"] == "landlord@example.com"


def test_undo_inside_the_window_stops_the_send(gmail):
    task = _drafted_send(gmail)
    conversation.handle(f"{task.task_id} yes")

    clock.freeze(FIXED_NOW + 20)
    reply = conversation.handle(f"{task.task_id} undo")
    assert "pulled back" in reply.text

    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_undo_after_the_window_says_so(gmail):
    task = _drafted_send(gmail)
    conversation.handle(f"{task.task_id} yes")

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()

    reply = conversation.handle(f"{task.task_id} undo")
    assert "nothing to undo" in reply.text.lower()
    assert len(gmail.sent) == 1


def test_bare_undo_catches_the_most_recent_hold(gmail):
    task = _drafted_send(gmail)
    conversation.handle(f"{task.task_id} yes")

    conversation.handle("undo")
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_no_drops_the_task(gmail):
    task = _drafted_send(gmail)
    reply = conversation.handle(f"{task.task_id} no")

    assert "dropped" in reply.text.lower()
    assert tasks_store.get(task.task_id).status == tasks_store.STOPPED
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_batched_yes_all(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")

    listing = conversation.handle("pending")
    assert f"1. {first.task_id}" in listing.text
    assert f"2. {second.task_id}" in listing.text

    conversation.handle("yes all")
    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert {m["to"] for m in gmail.sent} == {"a@example.com", "b@example.com"}


def test_batched_yes_by_index_leaves_the_others(gmail):
    _drafted_send(gmail, "a@example.com")
    _drafted_send(gmail, "b@example.com")
    _drafted_send(gmail, "c@example.com")

    conversation.handle("pending")
    conversation.handle("yes 1,3")

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert {m["to"] for m in gmail.sent} == {"a@example.com", "c@example.com"}
    assert len(approvals.pending()) == 1


def test_an_out_of_range_index_is_reported_not_guessed(gmail):
    _drafted_send(gmail, "a@example.com")
    reply = conversation.handle("yes 1,9")
    assert "no item 9" in reply.text


def test_yes_with_trailing_words_is_not_an_approval(gmail, scripted):
    task = _drafted_send(gmail)
    scripted(text="Noted.")

    conversation.handle(f"{task.task_id} yes but change the subject line")

    assert len(approvals.open_for_task(task.task_id)) == 1
    assert gmail.sent == []


def test_expired_approvals_cannot_be_granted(gmail):
    task = _drafted_send(gmail)
    clock.freeze(FIXED_NOW + 3601)

    reply = conversation.handle(f"{task.task_id} yes")
    assert "nothing waiting" in reply.text.lower()
    assert gmail.sent == []


def test_stop_all_halts_tasks_revokes_approvals_and_reports(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")
    conversation.handle(f"{first.task_id} yes")

    reply = conversation.handle("STOP ALL")

    assert "stopped everything" in reply.text.lower()
    assert first.task_id in reply.text and second.task_id in reply.text
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_stop_all_on_a_quiet_system_says_so():
    assert conversation.handle("stop all").text == "Nothing was running."


def test_tier_four_needs_two_confirms():
    task = tasks_store.create("disconnect everything")
    registry.call(task.task_id, "gmail_disconnect", {})

    first = conversation.handle(f"{task.task_id} yes")
    assert "once more" in first.text
    assert not outbox.held_for_task(task.task_id)

    second = conversation.handle(f"{task.task_id} yes")
    assert "goes out" in second.text
    assert outbox.held_for_task(task.task_id)
