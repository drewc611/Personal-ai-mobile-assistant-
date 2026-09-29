"""Approvals, the undo window, batching, and the kill switch."""

from __future__ import annotations

from errand.common import clock
from errand.dispatcher import commands, conversation
from errand.policy import approvals, outbox
from errand.store import tasks_store
from errand.tests.conftest import FIXED_NOW, hint_for, say, tap, token_for
from errand.tools import registry


def _drafted_send(gmail, to="landlord@example.com"):
    task = tasks_store.create("email the landlord")
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": to, "subject": "Rent", "body": "Going out Friday."}
    )
    registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"], "to": to})
    return task


def test_a_pending_approval_tells_him_exactly_what_to_text(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} status")
    assert hint_for(reply, "Approve") == f"{task.task_id} yes"
    assert hint_for(reply, "Reject") == f"{task.task_id} no"


def test_yes_holds_then_releases(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} yes")

    assert "goes out in 60s" in reply.text
    assert gmail.sent == []

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 1
    assert gmail.sent[0]["to"] == "landlord@example.com"


def test_approving_offers_an_undo(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} yes")
    assert hint_for(reply, "Undo") == f"{task.task_id} undo"


def test_undo_inside_the_window_stops_the_send(gmail):
    task = _drafted_send(gmail)
    say(f"{task.task_id} yes")

    clock.freeze(FIXED_NOW + 20)
    reply = say(f"{task.task_id} undo")
    assert "pulled back" in reply.text

    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_undo_after_the_window_says_so(gmail):
    task = _drafted_send(gmail)
    say(f"{task.task_id} yes")

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()

    reply = say(f"{task.task_id} undo")
    assert "nothing to undo" in reply.text.lower()
    assert len(gmail.sent) == 1


def test_bare_undo_catches_the_most_recent_hold(gmail):
    task = _drafted_send(gmail)
    say(f"{task.task_id} yes")

    say("undo")
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_no_drops_the_task(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} no")

    assert "dropped" in reply.text.lower()
    assert tasks_store.get(task.task_id).status == tasks_store.STOPPED
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_edit_drops_it_and_asks_for_the_change(gmail):
    task = _drafted_send(gmail)
    reply = say(f"{task.task_id} edit")

    assert "nothing sent" in reply.text.lower()
    assert "what to change" in reply.text.lower()
    assert approvals.pending() == []


def test_yes_with_trailing_words_is_not_an_approval(gmail, scripted):
    task = _drafted_send(gmail)
    scripted(text="Noted.")

    say(f"{task.task_id} yes but change the subject line")

    assert len(approvals.open_for_task(task.task_id)) == 1
    assert gmail.sent == []


def test_approving_twice_says_so_rather_than_acting_twice(gmail):
    task = _drafted_send(gmail)
    say(f"{task.task_id} yes")

    again = say(f"{task.task_id} yes")
    assert "nothing waiting" in again.text.lower()
    assert len(outbox.held_for_task(task.task_id)) == 1


def test_expired_approvals_cannot_be_granted(gmail):
    task = _drafted_send(gmail)
    clock.freeze(FIXED_NOW + 3601)

    reply = say(f"{task.task_id} yes")
    assert "nothing waiting" in reply.text.lower()
    assert gmail.sent == []


# ---------------------------------------------------------------- batching


def test_pending_is_numbered_so_yes_1_3_means_something(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")

    reply = say("pending")
    assert f"1. {first.task_id}" in reply.text
    assert f"2. {second.task_id}" in reply.text
    assert hint_for(reply, "Approve all") == "yes all"


def test_yes_all_clears_the_ordinary_ones(gmail):
    _drafted_send(gmail, "a@example.com")
    _drafted_send(gmail, "b@example.com")

    say("yes all")
    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert {m["to"] for m in gmail.sent} == {"a@example.com", "b@example.com"}


def test_yes_by_index_leaves_the_others(gmail):
    _drafted_send(gmail, "a@example.com")
    _drafted_send(gmail, "b@example.com")
    _drafted_send(gmail, "c@example.com")

    say("pending")
    say("yes 1,3")

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert {m["to"] for m in gmail.sent} == {"a@example.com", "c@example.com"}
    assert len(approvals.pending()) == 1


def test_an_out_of_range_index_is_reported_not_guessed(gmail):
    _drafted_send(gmail, "a@example.com")
    reply = say("yes 1,9")
    assert "no item 9" in reply.text


def test_yes_all_leaves_tier_four_alone():
    """One message must never satisfy a second confirm."""
    ordinary = tasks_store.create("respond to an invite")
    registry.call(ordinary.task_id, "calendar_respond",
                  {"event_id": "e1", "response": "accepted"})
    risky = tasks_store.create("disconnect everything")
    registry.call(risky.task_id, "gmail_disconnect", {})

    reply = say("yes all")

    assert ordinary.task_id in reply.text
    assert "One at a time" in reply.text
    assert risky.task_id in reply.text
    assert outbox.held_for_task(risky.task_id) == []


def test_yes_all_leaves_anything_with_a_charge_alone(gmail):
    """An amount has to be seen and echoed, so it never rides along."""
    task = _drafted_send(gmail)
    approval = approvals.open_for_task(task.task_id)[0]
    approval.amount_cents = 4200
    approval.tier = 3
    approvals.save(approval)

    reply = say("yes all")
    assert "One at a time" in reply.text
    assert "$42.00" in reply.text
    assert outbox.held_for_task(task.task_id) == []
    # And the hint it leaves him with is one that will actually work.
    assert hint_for(reply, "Approve") == f"{task.task_id} yes $42.00"


def test_yes_all_with_nothing_waiting():
    assert say("yes all").text == "Nothing waiting on you."


# ---------------------------------------------------------------- tiers


def test_tier_four_needs_a_second_confirm():
    task = tasks_store.create("disconnect everything")
    registry.call(task.task_id, "gmail_disconnect", {})

    first = say(f"{task.task_id} yes")
    assert "cannot be undone" in first.text
    assert not outbox.held_for_task(task.task_id)
    assert [b.label for b in first.buttons][0].startswith("Confirm")

    second = say(f"{task.task_id} yes")
    assert "goes out" in second.text
    assert outbox.held_for_task(task.task_id)


def test_a_spend_needs_the_amount_echoed(gmail):
    task = _drafted_send(gmail)
    approval = approvals.open_for_task(task.task_id)[0]
    approval.amount_cents = 4250
    approval.tier = 3
    approvals.save(approval)

    bare = say(f"{task.task_id} yes")
    assert "$42.50" in bare.text
    assert not outbox.held_for_task(task.task_id)

    wrong = say(f"{task.task_id} yes $40.00")
    assert "Nothing was charged" in wrong.text

    right = say(f"{task.task_id} yes $42.50")
    assert "goes out" in right.text


def test_the_approve_hint_carries_the_amount(gmail):
    task = _drafted_send(gmail)
    approval = approvals.open_for_task(task.task_id)[0]
    approval.amount_cents = 4250
    approval.tier = 3
    approvals.save(approval)

    reply = say(f"{task.task_id} status")
    assert hint_for(reply, "Approve") == f"{task.task_id} yes $42.50"


# ---------------------------------------------------------------- kill switch


def test_stop_all_halts_tasks_revokes_approvals_and_reports(gmail):
    first = _drafted_send(gmail, "a@example.com")
    second = _drafted_send(gmail, "b@example.com")
    say(f"{first.task_id} yes")

    reply = say("STOP ALL")

    assert "stopped everything" in reply.text.lower()
    assert first.task_id in reply.text and second.task_id in reply.text
    clock.freeze(FIXED_NOW + 120)
    conversation.release_due()
    assert gmail.sent == []


def test_stop_all_on_a_quiet_system_says_so():
    assert say("stop all").text == "Nothing was running."


# ------------------------------------------- the token path, for later channels


def test_a_button_token_resolves_to_the_same_approval(gmail):
    """SMS has no buttons, but the token path is what a richer channel uses,
    and it must land on the same approval record."""
    task = _drafted_send(gmail)
    token = token_for(say(f"{task.task_id} status"), "Approve")

    reply = tap(token, message_id="777")

    assert "goes out in 60s" in reply.text
    assert reply.retire_message == "777"
    assert reply.acknowledge == "Approved"


def test_an_unparseable_token_does_nothing():
    assert tap("garbage").text == ""


def test_the_approve_all_token_matches_yes_all(gmail):
    _drafted_send(gmail, "a@example.com")
    _drafted_send(gmail, "b@example.com")

    tap(commands.APPROVE_ALL_TOKEN)
    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 2
