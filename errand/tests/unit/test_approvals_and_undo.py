"""Approvals, buttons, the undo window, batching, and the kill switch."""

from __future__ import annotations

from errand.common import clock
from errand.dispatcher import commands, conversation
from errand.policy import approvals, outbox
from errand.store import tasks_store
from errand.tests.conftest import FIXED_NOW, approve_token, button_labelled, say, tap
from errand.tools import registry


def _drafted_send(gmail, to="landlord@example.com"):
    task = tasks_store.create("email the landlord")
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": to, "subject": "Rent", "body": "Going out Friday."}
    )
    registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"], "to": to})
    return task


def test_a_pending_approval_offers_approve_reject_edit(gmail):
    task = _drafted_send(gmail)
    reply = say(f"/status {task.task_id}")
    labels = [b.label for b in reply.buttons]
    assert labels == ["Approve", "Reject", "Edit"]


def test_approve_holds_then_releases(gmail):
    task = _drafted_send(gmail)
    reply = tap(approve_token(say(f"/status {task.task_id}")))

    assert "goes out in 60s" in reply.text
    assert gmail.sent == []

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 1
    assert gmail.sent[0]["to"] == "landlord@example.com"


def test_approving_offers_an_undo_button(gmail):
    task = _drafted_send(gmail)
    reply = tap(approve_token(say(f"/status {task.task_id}")))
    assert [b.label for b in reply.buttons] == ["Undo"]


def test_undo_inside_the_window_stops_the_send(gmail):
    task = _drafted_send(gmail)
    approved = tap(approve_token(say(f"/status {task.task_id}")))

    clock.freeze(FIXED_NOW + 20)
    reply = tap(button_labelled(approved, "Undo"))
    assert "pulled back" in reply.text

    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_undo_after_the_window_says_so(gmail):
    task = _drafted_send(gmail)
    approved = tap(approve_token(say(f"/status {task.task_id}")))

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()

    reply = tap(button_labelled(approved, "Undo"))
    assert "nothing to undo" in reply.text.lower()
    assert len(gmail.sent) == 1


def test_a_tapped_button_retires_the_message_it_came_from(gmail):
    task = _drafted_send(gmail)
    reply = tap(approve_token(say(f"/status {task.task_id}")), message_id="777")
    assert reply.retire_message == "777"
    assert reply.acknowledge == "Approved"


def test_reject_drops_the_task(gmail):
    task = _drafted_send(gmail)
    reply = tap(button_labelled(say(f"/status {task.task_id}"), "Reject"))

    assert "dropped" in reply.text.lower()
    assert tasks_store.get(task.task_id).status == tasks_store.STOPPED
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_edit_drops_it_and_asks_for_the_change(gmail):
    task = _drafted_send(gmail)
    reply = tap(button_labelled(say(f"/status {task.task_id}"), "Edit"))

    assert "nothing sent" in reply.text.lower()
    assert "what to change" in reply.text.lower()
    assert approvals.pending() == []


def test_a_stale_button_says_so_rather_than_acting_twice(gmail):
    task = _drafted_send(gmail)
    token = approve_token(say(f"/status {task.task_id}"))
    tap(token)

    again = tap(token)
    assert "already" in again.text.lower()
    assert len(outbox.held_for_task(task.task_id)) == 1


def test_an_unparseable_button_token_does_nothing():
    assert tap("garbage").text == ""


def test_typed_approval_still_works_when_buttons_scrolled_away(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} yes")
    assert "goes out in 60s" in reply.text


def test_yes_with_trailing_words_is_not_an_approval(gmail, scripted):
    task = _drafted_send(gmail)
    scripted(text="Noted.")

    say(f"{task.task_id} yes but change the subject line")

    assert len(approvals.open_for_task(task.task_id)) == 1
    assert gmail.sent == []


def test_expired_approvals_cannot_be_granted(gmail):
    task = _drafted_send(gmail)
    clock.freeze(FIXED_NOW + 3601)

    reply = say(f"{task.task_id} yes")
    assert "nothing waiting" in reply.text.lower()
    assert gmail.sent == []


def test_pending_lists_everything_with_an_approve_all(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")

    reply = say("/pending")
    assert first.task_id in reply.text and second.task_id in reply.text
    assert button_labelled(reply, "Approve all") == commands.APPROVE_ALL_TOKEN


def test_approve_all_clears_the_ordinary_ones(gmail):
    _drafted_send(gmail, "a@example.com")
    _drafted_send(gmail, "b@example.com")

    tap(commands.APPROVE_ALL_TOKEN)
    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()

    assert {m["to"] for m in gmail.sent} == {"a@example.com", "b@example.com"}


def test_approve_all_leaves_tier_four_alone():
    """One tap must never satisfy a second confirm."""
    ordinary = tasks_store.create("send something")
    registry.call(ordinary.task_id, "calendar_respond",
                  {"event_id": "e1", "response": "accepted"})
    risky = tasks_store.create("disconnect everything")
    registry.call(risky.task_id, "gmail_disconnect", {})

    reply = tap(commands.APPROVE_ALL_TOKEN)

    assert ordinary.task_id in reply.text
    assert "one at a time" in reply.text
    assert risky.task_id in reply.text
    assert outbox.held_for_task(risky.task_id) == []


def test_approve_all_leaves_anything_with_a_charge_alone(gmail):
    """An amount has to be seen before it is approved, so it never rides along
    in a bulk tap."""
    task = _drafted_send(gmail)
    approval = approvals.open_for_task(task.task_id)[0]
    approval.amount_cents = 4200
    approval.tier = 3
    approvals.save(approval)

    reply = tap(commands.APPROVE_ALL_TOKEN)
    assert "one at a time" in reply.text
    assert outbox.held_for_task(task.task_id) == []


def test_approve_all_with_nothing_waiting():
    assert tap(commands.APPROVE_ALL_TOKEN).text == "Nothing waiting on you."


def test_stop_all_halts_tasks_voids_approvals_and_reports(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")
    tap(approve_token(say(f"/status {first.task_id}")))

    reply = say("STOP ALL")

    assert "stopped everything" in reply.text.lower()
    assert first.task_id in reply.text and second.task_id in reply.text
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_stop_all_on_a_quiet_system_says_so():
    assert say("stop all").text == "Nothing was running."


def test_tier_four_needs_a_second_confirm():
    task = tasks_store.create("disconnect everything")
    registry.call(task.task_id, "gmail_disconnect", {})

    first = tap(approve_token(say(f"/status {task.task_id}")))
    assert "cannot be undone" in first.text
    assert not outbox.held_for_task(task.task_id)
    assert [b.label for b in first.buttons][0].startswith("Confirm")

    second = tap(button_labelled(first, "Confirm"))
    assert "goes out" in second.text
    assert outbox.held_for_task(task.task_id)
