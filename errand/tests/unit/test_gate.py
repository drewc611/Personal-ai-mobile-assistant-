"""The gate is the load-bearing part, so it gets the most tests."""

from __future__ import annotations

from errand.policy import approvals, gate, outbox
from errand.policy.tiers import Tier, classify
from errand.store import audit_store, tasks_store
from errand.tools import registry


def _task(title="test task"):
    return tasks_store.create(title)


def test_read_and_draft_run_freely(gmail):
    gmail.add_message(sender="a@b.com", subject="Rent", body="hello")
    task = _task()

    assert registry.call(task.task_id, "gmail_search", {"query": "rent"}).ok
    assert registry.call(
        task.task_id, "gmail_draft", {"to": "x@y.com", "subject": "s", "body": "b"}
    ).ok


def test_send_returns_pending_and_does_nothing(gmail):
    task = _task()
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": "landlord@x.com", "subject": "Rent", "body": "Friday"}
    )

    result = registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"]})

    assert result.status == "PENDING_APPROVAL"
    assert gmail.sent == []
    assert "yes" in result.message.lower()


def test_pending_approval_records_the_exact_arguments(gmail):
    task = _task()
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": "a@b.com", "subject": "s", "body": "b"}
    )
    registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"]})

    pending = approvals.open_for_task(task.task_id)
    assert len(pending) == 1
    assert pending[0].args == {"draft_id": draft.data["draft_id"]}
    assert pending[0].tier == int(Tier.SEND)


def test_an_unregistered_tool_is_denied_not_guessed():
    task = _task()
    result = registry.call(task.task_id, "gmail_delete_everything", {})
    assert result.status == "DENIED"


def test_tier_four_is_never_reachable_by_the_model():
    task = _task()
    result = registry.call(task.task_id, "gmail_disconnect", {})
    assert result.status == "PENDING_APPROVAL"

    approval = approvals.open_for_task(task.task_id)[0]
    # Two confirms, so one stray "yes" cannot do it.
    assert approval.confirms_required == 2


def test_every_call_writes_a_before_and_an_after_row(gmail):
    gmail.add_message(sender="a@b.com", subject="Rent", body="x")
    task = _task()
    registry.call(task.task_id, "gmail_search", {"query": "rent"})

    rows = audit_store.for_task(task.task_id)
    phases = [r["phase"] for r in rows]
    assert audit_store.PHASE_BEFORE in phases
    assert audit_store.PHASE_AFTER in phases


def test_a_refused_call_still_writes_both_rows():
    task = _task()
    registry.call(task.task_id, "gmail_send", {"draft_id": "d1"})

    rows = audit_store.for_task(task.task_id)
    events = [r["event"] for r in rows]
    assert "TOOL_CALL_REQUESTED" in events
    assert "TOOL_CALL_PENDING_APPROVAL" in events


def test_audit_rows_do_not_keep_message_bodies():
    task = _task()
    secret = "the rent is 2200 and the door code is 4417"
    registry.call(task.task_id, "gmail_draft", {"to": "a@b", "subject": "s", "body": secret})

    blob = "".join(str(r) for r in audit_store.for_task(task.task_id))
    assert "4417" not in blob
    assert "chars>" in blob


def test_a_failing_tool_writes_an_after_row_and_reports():
    task = _task()
    result = registry.call(task.task_id, "gmail_read", {"message_id": "nope"})

    assert result.status == "ERROR"
    events = [r["event"] for r in audit_store.for_task(task.task_id)]
    assert "TOOL_CALL_FAILED" in events


def test_calendar_respond_is_tier_two():
    assert classify("calendar_respond", {}) == Tier.SEND
    task = _task()
    result = registry.call(
        task.task_id, "calendar_respond", {"event_id": "e1", "response": "accepted"}
    )
    assert result.status == "PENDING_APPROVAL"


def test_the_gate_decision_payload_tells_the_model_nothing_useful_for_retrying():
    task = _task()
    decision = gate.decide(task_id=task.task_id, tool="gmail_send", args={"draft_id": "d"})
    payload = decision.to_model_payload()
    assert payload["status"] == "PENDING_APPROVAL"
    assert "args" not in payload
    assert "approval_id" not in payload


def test_released_call_checks_the_digest(gmail):
    task = _task()
    draft = registry.call(task.task_id, "gmail_draft", {"to": "a@b", "subject": "s", "body": "b"})
    registry.call(task.task_id, "gmail_send", {"draft_id": draft.data["draft_id"]})

    approval = approvals.open_for_task(task.task_id)[0]
    approvals.approve(approval)
    entry = outbox.hold(
        approval_id=approval.approval_id,
        task_id=task.task_id,
        tool="gmail_send",
        args={"draft_id": "SOMETHING_ELSE"},   # tampered after approval
        args_digest=approval.args_digest,
        summary=approval.summary,
    )

    result = registry.release(entry)
    assert result.status == "DENIED"
    assert gmail.sent == []
