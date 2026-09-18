"""Turning one inbound text into one outbound reply.

Everything Andrew can type lands here. Approvals, denials, the batch list, the
undo window, the kill switch, and disconnect are all handled deterministically
in this file; only free text reaches the planner. That split is the point -
the actions with consequences are decided by a parser, not by a model reading
intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from errand.agent import planner as planner_mod
from errand.common import clock, money
from errand.dispatcher import commands
from errand.policy import approvals, outbox
from errand.policy import rules as rules_mod
from errand.store import audit_store, tasks_store
from errand.tools import connections, registry

HELP_TEXT = (
    "Text me anything to start a task.\n"
    'T7 yes / T7 no / T7 status\n'
    '"yes all" or "yes 1,3" for what is waiting\n'
    '"tasks" open tasks, "pending" what needs a yes\n'
    '"undo" or "T7 undo" inside the 60s window\n'
    '"rules" your standing rules, "tighten dining" to require approval again\n'
    '"STOP ALL" halts everything, "disconnect gmail" revokes and deletes'
)


@dataclass
class Reply:
    text: str
    task_id: str = ""
    actions: list[str] = field(default_factory=list)


def handle(text: str, *, source: str = "sms") -> Reply:
    command = commands.parse(text)

    handlers = {
        commands.HELP: lambda: Reply(HELP_TEXT),
        commands.LIST_TASKS: _list_tasks,
        commands.PENDING: _pending,
        commands.RULES: _rules,
        commands.STOP_ALL: _stop_all,
    }
    if command.kind in handlers:
        return handlers[command.kind]()

    if command.kind == commands.STATUS:
        return _status(command.task_id)
    if command.kind == commands.APPROVE:
        return _approve(command)
    if command.kind == commands.DENY:
        return _deny(command)
    if command.kind == commands.UNDO:
        return _undo(command.task_id)
    if command.kind == commands.DISCONNECT:
        return _disconnect(command.argument)
    if command.kind == commands.TIGHTEN:
        return _tighten(command.argument)
    return _free_text(command.raw or text, source=source)


# ---------------------------------------------------------------- listings


def _list_tasks() -> Reply:
    tasks = tasks_store.open_tasks()
    if not tasks:
        return Reply("Nothing open.")
    lines = [f"{t.task_id} {t.status.lower().replace('_', ' ')}: {t.title[:60]}" for t in tasks]
    return Reply("\n".join(lines))


def _pending() -> Reply:
    """The numbered list "yes 1,3" indexes into."""
    approvals.expire_stale()
    waiting = approvals.pending()
    if not waiting:
        return Reply("Nothing waiting on you.")
    lines = []
    for index, approval in enumerate(waiting, 1):
        suffix = ""
        if approval.amount_cents:
            suffix = f" [{money.format_amount(approval.amount_cents)}]"
        lines.append(f"{index}. {approval.task_id} {approval.summary}{suffix}")
    lines.append('Reply "yes all", "yes 1,3", or "T7 yes".')
    return Reply("\n".join(lines))


def _rules() -> Reply:
    active = rules_mod.active_rules()
    if not active:
        return Reply("No standing rules. Everything at tier 2 and up asks you first.")
    lines = [f"{r.rule_id[:8]} [{r.effect}] {r.text}" for r in active]
    lines.append('"tighten <domain>" to drop the allow rules for a domain.')
    return Reply("\n".join(lines))


def _status(task_id: str) -> Reply:
    task = tasks_store.get(task_id)
    if task is None:
        return Reply(f"No task {task_id}.")
    lines = [f"{task.task_id} {task.status.lower().replace('_', ' ')}: {task.title[:80]}"]

    for entry in outbox.held_for_task(task_id):
        lines.append(f"Going out in {entry.seconds_left}s: {entry.summary}")
    for approval in approvals.open_for_task(task_id):
        lines.append(f"Waiting on you: {approval.summary}")
    if task.last_message and len(lines) == 1:
        lines.append(task.last_message[:160])
    return Reply("\n".join(lines), task_id=task_id)


# ---------------------------------------------------------------- approvals


def _resolve_targets(command: commands.Command) -> tuple[list[approvals.Approval], str]:
    approvals.expire_stale()
    waiting = approvals.pending()

    if command.task_id:
        matches = [a for a in waiting if a.task_id == command.task_id]
        if not matches:
            return [], f"Nothing waiting on {command.task_id}."
        return matches[:1], ""

    if command.all_pending:
        if not waiting:
            return [], "Nothing waiting on you."
        return waiting, ""

    if command.indices:
        picked, missing = [], []
        for index in command.indices:
            if 1 <= index <= len(waiting):
                picked.append(waiting[index - 1])
            else:
                missing.append(index)
        if not picked:
            return [], f"No pending item {', '.join(str(i) for i in command.indices)}."
        if missing:
            note = f"(no item {', '.join(str(i) for i in missing)})"
            return picked, note
        return picked, ""

    return [], "Say which one: \"T7 yes\", \"yes 1,3\", or \"yes all\"."


def _approve(command: commands.Command) -> Reply:
    targets, note = _resolve_targets(command)
    if not targets:
        return Reply(note)

    lines: list[str] = []
    actions: list[str] = []
    for approval in targets:
        try:
            updated = approvals.approve(approval, amount_text=command.amount_text or None)
        except approvals.ApprovalError as exc:
            lines.append(str(exc))
            continue

        if updated.confirms_outstanding > 0:
            lines.append(
                f"{updated.task_id}: {updated.summary} cannot be undone. "
                f'Reply "{updated.task_id} yes" once more to confirm.'
            )
            continue

        entry = outbox.hold(
            approval_id=updated.approval_id,
            task_id=updated.task_id,
            tool=updated.tool,
            args=updated.args,
            args_digest=updated.args_digest,
            summary=updated.summary,
        )
        tasks_store.set_status(updated.task_id, tasks_store.HOLDING, note=updated.summary)
        audit_store.write(
            task_id=updated.task_id,
            phase=audit_store.PHASE_AFTER,
            event="APPROVAL_GRANTED",
            tool=updated.tool,
            tier=updated.tier,
            outcome="APPROVED",
            detail={"approval_id": updated.approval_id, "undo_seconds": entry.seconds_left},
        )
        actions.append(updated.approval_id)
        lines.append(
            f"{updated.task_id}: {updated.summary} goes out in {entry.seconds_left}s. "
            f'"{updated.task_id} undo" to stop it.'
        )

    if note:
        lines.append(note)
    return Reply("\n".join(lines), actions=actions)


def _deny(command: commands.Command) -> Reply:
    targets, note = _resolve_targets(command)
    if not targets:
        return Reply(note)

    lines = []
    for approval in targets:
        approvals.deny(approval)
        task = tasks_store.set_status(
            approval.task_id, tasks_store.STOPPED, note="declined by Andrew"
        )
        if task is not None:
            tasks_store.record_outcome(task, "declined")
        audit_store.write(
            task_id=approval.task_id,
            phase=audit_store.PHASE_AFTER,
            event="APPROVAL_DENIED",
            tool=approval.tool,
            tier=approval.tier,
            outcome="DENIED",
        )
        lines.append(f"{approval.task_id} dropped. Nothing sent.")
    if note:
        lines.append(note)
    return Reply("\n".join(lines))


def _undo(task_id: str = "") -> Reply:
    held = outbox.held_for_task(task_id) if task_id else outbox.held()
    if not held:
        target = task_id or "anything"
        return Reply(f"Nothing to undo for {target}. Anything already sent is out.")

    lines = []
    for entry in held:
        try:
            outbox.cancel(entry)
        except RuntimeError as exc:
            lines.append(str(exc))
            continue
        approval = approvals.get(entry.task_id, entry.approval_id)
        if approval is not None:
            approvals.deny(approval, reason="undone inside the window")
        task = tasks_store.set_status(entry.task_id, tasks_store.STOPPED, note="undone")
        if task is not None:
            tasks_store.record_outcome(task, "undone")
        audit_store.write(
            task_id=entry.task_id,
            phase=audit_store.PHASE_AFTER,
            event="OUTBOX_UNDONE",
            tool=entry.tool,
            outcome="CANCELLED",
        )
        lines.append(f"{entry.task_id} pulled back. {entry.summary} did not go out.")
    return Reply("\n".join(lines))


# ---------------------------------------------------------------- the rest


def _stop_all() -> Reply:
    """The kill switch. Halts running tasks, revokes pending approvals, and
    says what it stopped - a kill switch that reports nothing is one you
    cannot trust in the moment you need it."""
    cancelled = outbox.cancel_all()
    revoked = approvals.revoke_all()
    stopped = []
    for task in tasks_store.open_tasks():
        tasks_store.set_status(task.task_id, tasks_store.STOPPED, note="STOP ALL")
        tasks_store.record_outcome(task, "stopped")
        stopped.append(task.task_id)

    audit_store.write(
        task_id="system",
        phase=audit_store.PHASE_AFTER,
        event="STOP_ALL",
        outcome="STOPPED",
        detail={
            "tasks": stopped,
            "approvals_revoked": len(revoked),
            "outbox_cancelled": len(cancelled),
        },
    )

    if not (stopped or revoked or cancelled):
        return Reply("Nothing was running.")
    lines = ["Stopped everything."]
    if stopped:
        lines.append(f"Tasks halted: {', '.join(stopped)}.")
    if cancelled:
        lines.append(
            "Pulled back before sending: "
            + "; ".join(f"{e.task_id} {e.summary}" for e in cancelled)
            + "."
        )
    if revoked:
        lines.append(f"Approvals revoked: {len(revoked)}.")
    return Reply(" ".join(lines))


def _disconnect(connection: str) -> Reply:
    result = connections.disconnect(connection)
    return Reply(connections.receipt_text(result))


def _tighten(domain: str) -> Reply:
    dropped = rules_mod.tighten(domain)
    if not dropped:
        return Reply(f"No standing rules were letting {domain} through.")
    listed = "; ".join(r.text for r in dropped)
    return Reply(f"{domain} needs your approval again. Dropped: {listed}.")


def _free_text(text: str, *, source: str) -> Reply:
    task = tasks_store.create(text, task_type=_guess_type(text), source=source)
    audit_store.write(
        task_id=task.task_id,
        phase=audit_store.PHASE_BEFORE,
        event="TASK_STARTED",
        detail={"source": source, "chars": len(text)},
    )
    try:
        answer = planner_mod.get_planner().run(task, text)
    except Exception as exc:  # noqa: BLE001
        audit_store.write(
            task_id=task.task_id,
            phase=audit_store.PHASE_AFTER,
            event="TASK_FAILED",
            outcome="ERROR",
            detail={"error": type(exc).__name__, "message": str(exc)[:300]},
            sequence=1,
        )
        tasks_store.set_status(task.task_id, tasks_store.FAILED, note=str(exc)[:200])
        return Reply(f"{task.task_id} failed: {exc}", task_id=task.task_id)

    refreshed = tasks_store.get(task.task_id)
    if refreshed is not None and refreshed.status == tasks_store.OPEN:
        tasks_store.set_status(task.task_id, tasks_store.DONE, note=answer[:200])
        tasks_store.record_outcome(refreshed, "answered")

    audit_store.write(
        task_id=task.task_id,
        phase=audit_store.PHASE_AFTER,
        event="TASK_ANSWERED",
        outcome="OK",
        detail={"reply_chars": len(answer)},
        sequence=1,
    )
    prefix = f"{task.task_id}: " if not answer.startswith(task.task_id) else ""
    return Reply(f"{prefix}{answer}".strip(), task_id=task.task_id)


_TYPE_HINTS = {
    "email": ("email", "inbox", "reply to", "write to", "landlord", "message"),
    "calendar": ("calendar", "schedule", "meeting", "free", "busy", "tuesday",
                 "monday", "wednesday", "thursday", "friday", "invite"),
    "research": ("find", "look up", "compare", "search", "how much", "who is"),
}


def _guess_type(text: str) -> str:
    """A rough label used for model routing and, later, earned autonomy.

    It is a hint, never a permission: a task labelled "calendar" gets exactly
    the same gate as one labelled anything else.
    """
    lowered = text.lower()
    for task_type, hints in _TYPE_HINTS.items():
        if any(hint in lowered for hint in hints):
            return task_type
    return "general"


def release_due() -> list[Reply]:
    """Run by the releaser Lambda once a minute: anything past its undo window
    goes out, and Andrew gets told what happened."""
    replies = []
    for entry, result in registry.release_due():
        if result.ok:
            replies.append(Reply(f"{entry.task_id} done: {entry.summary}.", task_id=entry.task_id))
        elif result.status == "DENIED":
            replies.append(Reply(result.message, task_id=entry.task_id))
        else:
            replies.append(
                Reply(f"{entry.task_id} failed: {result.message}", task_id=entry.task_id)
            )
    return replies


def morning_digest() -> Reply:
    """The batched-approval text. One message a morning listing what is
    waiting, answerable with "yes all" or "yes 1,3"."""
    approvals.expire_stale()
    waiting = approvals.pending()
    tasks = tasks_store.open_tasks()
    if not waiting and not tasks:
        return Reply("")
    lines = [f"Morning. {clock.now_iso()[:10]}"]
    if waiting:
        lines.append(f"{len(waiting)} waiting on you:")
        for index, approval in enumerate(waiting, 1):
            suffix = ""
            if approval.amount_cents:
                suffix = f" [{money.format_amount(approval.amount_cents)}]"
            lines.append(f"{index}. {approval.task_id} {approval.summary}{suffix}")
        lines.append('"yes all" or "yes 1,3".')
    if tasks:
        lines.append(f"{len(tasks)} open: {', '.join(t.task_id for t in tasks)}")
    return Reply("\n".join(lines))
