"""Turning one inbound message into one reply.

Everything Andrew sends lands here. Approvals, rejections, the undo window,
the kill switch, the budget, and disconnect are all handled deterministically
in this file; only free text reaches the planner. That split is the point -
the actions with consequences are decided by a parser, not by a model reading
intent.

Replies are built as text plus a set of Buttons and handed to the Channel.
Over SMS the buttons come out as a "Reply "T7 yes"" line; on a channel with
something tappable they come out as buttons. Nothing in this file knows which.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from errand.agent import planner as planner_mod
from errand.agent import router
from errand.channels.base import Button, Inbound
from errand.common import clock, money
from errand.dispatcher import commands
from errand.policy import approvals, budget, outbox
from errand.policy import rules as rules_mod
from errand.policy.approvals import Approval
from errand.policy.tiers import Tier
from errand.store import audit_store, receipts_store, tasks_store
from errand.tools import connections, registry

HELP_TEXT = (
    "Text me anything to start a task.\n"
    'T7 yes / T7 no / T7 status / T7 undo / T7 edit\n'
    '"pending" what needs a yes, then "yes all" or "yes 1,3"\n'
    '"tasks" open tasks, "rules" standing rules, "budget" this month\n'
    '"tighten dining" makes a type ask again\n'
    '"disconnect gmail" revokes and deletes, with a receipt\n'
    '"STOP ALL" halts everything'
)


@dataclass
class Reply:
    text: str
    buttons: list[Button] = field(default_factory=list)
    task_id: str = ""
    retire_message: str = ""      # a button message that should stop being tappable
    acknowledge: str = ""         # short toast, for channels that have one

    @property
    def empty(self) -> bool:
        return not self.text and not self.buttons


def respond(inbound: Inbound) -> Reply:
    """One inbound message to one reply. No I/O beyond the stores."""
    if inbound.kind == "button":
        command = commands.parse_button(inbound.token)
        command.raw = inbound.token
        reply = _dispatch(command)
        reply.retire_message = inbound.message_id
        if not reply.acknowledge:
            reply.acknowledge = _toast(command.kind)
        return reply

    return _dispatch(commands.parse(inbound.text))


def _toast(kind: str) -> str:
    return {
        commands.APPROVE: "Approved",
        commands.APPROVE_ALL: "Approved",
        commands.REJECT: "Dropped",
        commands.EDIT: "Dropped so you can change it",
        commands.CONFIRM: "Confirmed",
        commands.UNDO: "Pulled back",
    }.get(kind, "")


def _dispatch(command: commands.Command) -> Reply:
    simple = {
        commands.HELP: lambda: Reply(HELP_TEXT),
        commands.IGNORE: lambda: Reply(""),
        commands.LIST_TASKS: _list_tasks,
        commands.PENDING: _pending,
        commands.RULES: _rules,
        commands.BUDGET: _budget,
        commands.STOP_ALL: _stop_all,
    }
    if command.kind in simple:
        return simple[command.kind]()

    if command.kind == commands.STATUS:
        return _status(command.task_id)
    if command.kind in (commands.APPROVE, commands.CONFIRM):
        return _approve(command)
    if command.kind == commands.APPROVE_ALL:
        return _approve_all()
    if command.kind in (commands.REJECT, commands.EDIT):
        return _reject(command, edit=command.kind == commands.EDIT)
    if command.kind == commands.UNDO:
        return _undo(command)
    if command.kind == commands.DISCONNECT:
        return _disconnect(command.argument)
    if command.kind == commands.TIGHTEN:
        return _tighten(command.argument)
    return _free_text(command.raw)


# ---------------------------------------------------------------- buttons


def _approval_buttons(approval: Approval) -> list[Button]:
    """Approve and Reject, and for tier 4 a Confirm that only appears once the
    first approval is in, so one message can never finish it."""
    task = approval.task_id
    if approval.confirms_received and approval.confirms_outstanding:
        return [
            Button("Confirm - cannot be undone",
                   commands.button_token(commands.CONFIRM, approval.approval_id),
                   reply=f"{task} yes"),
            Button("Reject", commands.button_token(commands.REJECT, approval.approval_id),
                   reply=f"{task} no"),
        ]

    if approval.amount_cents:
        amount = money.format_amount(approval.amount_cents)
        approve = Button("Approve",
                         commands.button_token(commands.APPROVE, approval.approval_id),
                         reply=f"{task} yes {amount}")
    else:
        approve = Button("Approve",
                         commands.button_token(commands.APPROVE, approval.approval_id),
                         reply=f"{task} yes")

    return [
        approve,
        Button("Reject", commands.button_token(commands.REJECT, approval.approval_id),
               reply=f"{task} no"),
    ]


def _undo_button(task_id: str, approval_id: str) -> Button:
    return Button("Undo", commands.button_token(commands.UNDO, approval_id),
                  reply=f"{task_id} undo")


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
    return Reply(_digest_text(waiting), buttons=_digest_buttons(waiting))


def _digest_text(waiting: list[Approval]) -> str:
    lines = [f"{len(waiting)} waiting on you:"]
    for index, approval in enumerate(waiting, 1):
        suffix = ""
        if approval.amount_cents:
            suffix = f" [{money.format_amount(approval.amount_cents)}]"
        lines.append(f"{index}. {approval.task_id} {approval.summary}{suffix}")
    return "\n".join(lines)


def _digest_buttons(waiting: list[Approval]) -> list[Button]:
    """One Approve per item, plus Approve all when there is more than one.

    Over SMS these render as the reply hints, which is why the hint for the
    batch is "yes all" and not a token: that is what Andrew actually types.
    """
    buttons = [
        Button(f"Approve {a.task_id}",
               commands.button_token(commands.APPROVE, a.approval_id),
               reply=f"{a.task_id} yes")
        for a in waiting
    ]
    if len(waiting) > 1:
        buttons.append(Button("Approve all", commands.APPROVE_ALL_TOKEN, reply="yes all"))
    return buttons


def _rules() -> Reply:
    active = rules_mod.active_rules()
    if not active:
        return Reply("No standing rules. Everything at tier 2 and up asks you first.")
    lines = [f"[{r.effect}] {r.text}" for r in active]
    lines.append('"tighten <type>" drops the allow rules for a type.')
    return Reply("\n".join(lines))


def _budget() -> Reply:
    lines = [budget.report(), ""]
    lines.extend(router.describe_safely())
    return Reply("\n".join(line for line in lines if line is not None))


def _status(task_id: str) -> Reply:
    task = tasks_store.get(task_id)
    if task is None:
        return Reply(f"No task {task_id}.")

    lines = [f"{task.task_id} {task.status.lower().replace('_', ' ')}: {task.title[:80]}"]
    buttons: list[Button] = []

    for entry in outbox.held_for_task(task_id):
        lines.append(f"Going out in {entry.seconds_left}s: {entry.summary}")
        buttons.append(_undo_button(task_id, entry.approval_id))

    for approval in approvals.open_for_task(task_id):
        lines.append(f"Waiting on you: {approval.summary}")
        buttons.extend(_approval_buttons(approval))

    for receipt in receipts_store.for_task(task_id):
        lines.append(f"Receipt: {receipt.summary}")

    if task.last_message and len(lines) == 1:
        lines.append(task.last_message[:160])

    return Reply("\n".join(lines), buttons=buttons, task_id=task_id)


# ---------------------------------------------------------------- approvals


def _resolve(command: commands.Command) -> tuple[list[Approval], str]:
    """Which approvals this message is about, and a note about any it could
    not match."""
    approvals.expire_stale()
    waiting = approvals.pending()

    if command.approval_id:
        for approval in approvals.for_all():
            if approval.approval_id == command.approval_id:
                if not approval.is_open:
                    return [], f"{approval.task_id} is already {approval.status.lower()}."
                return [approval], ""
        return [], "That one is gone."

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
        note = f"(no item {', '.join(str(i) for i in missing)})" if missing else ""
        return picked, note

    if len(waiting) == 1:
        return waiting, ""
    if not waiting:
        return [], "Nothing waiting on you."
    return [], 'Say which one: "T7 yes", "yes 1,3", or "yes all".'


def _approve(command: commands.Command) -> Reply:
    targets, note = _resolve(command)
    if not targets:
        return Reply(note)

    lines: list[str] = []
    buttons: list[Button] = []

    for approval in targets:
        try:
            updated = approvals.approve(approval, amount_text=command.amount_text or None)
        except approvals.ApprovalError as exc:
            lines.append(str(exc))
            buttons.extend(_approval_buttons(approval))
            continue

        if updated.confirms_outstanding > 0:
            lines.append(
                f"{updated.task_id}: {updated.summary} cannot be undone. Confirm to go ahead."
            )
            buttons.extend(_approval_buttons(updated))
            continue

        entry = _hold(updated)
        lines.append(f"{updated.task_id}: {updated.summary} goes out in {entry.seconds_left}s.")
        buttons.append(_undo_button(updated.task_id, updated.approval_id))

    if note:
        lines.append(note)
    return Reply("\n".join(lines), buttons=buttons,
                 task_id=targets[0].task_id if len(targets) == 1 else "")


def _hold(approval: Approval) -> outbox.OutboxEntry:
    entry = outbox.hold(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        tool=approval.tool,
        args=approval.args,
        args_digest=approval.args_digest,
        summary=approval.summary,
    )
    tasks_store.set_status(approval.task_id, tasks_store.HOLDING, note=approval.summary)
    audit_store.write(
        task_id=approval.task_id,
        phase=audit_store.PHASE_AFTER,
        event="APPROVAL_GRANTED",
        tool=approval.tool,
        tier=approval.tier,
        outcome="APPROVED",
        detail={"approval_id": approval.approval_id, "undo_seconds": entry.seconds_left},
    )
    return entry


def _approve_all() -> Reply:
    approvals.expire_stale()
    waiting = approvals.pending()
    if not waiting:
        return Reply("Nothing waiting on you.")

    done: list[str] = []
    problems: list[str] = []
    held_back: list[Approval] = []

    for approval in waiting:
        # Tier 4 and anything with a charge are never part of a bulk approve.
        # "yes all" is for clearing a morning of ordinary sends, and a second
        # confirm that one message can satisfy is not a second confirm.
        if approval.tier >= int(Tier.IRREVERSIBLE) or approval.amount_cents:
            held_back.append(approval)
            continue
        try:
            updated = approvals.approve(approval)
        except approvals.ApprovalError as exc:
            problems.append(str(exc))
            continue
        _hold(updated)
        done.append(updated.task_id)

    lines = []
    buttons: list[Button] = []
    if done:
        lines.append(f"Approved {', '.join(done)}. They go out in a minute.")
        buttons.append(Button("Undo all", commands.button_token(commands.UNDO, "all"),
                              reply="undo"))
    if held_back:
        lines.append(
            "One at a time, these need you to look: "
            + "; ".join(
                f"{a.task_id} {a.summary}"
                + (f" [{money.format_amount(a.amount_cents)}]" if a.amount_cents else "")
                for a in held_back
            )
            + "."
        )
        # Built through _approval_buttons so a spend carries the amount in its
        # hint. Telling him to text "T7 yes" for a charge would send him
        # straight into the refusal for not echoing the amount.
        for approval in held_back:
            buttons.extend(_approval_buttons(approval))
    lines.extend(problems)
    if not lines:
        lines.append("Nothing could be approved.")

    return Reply("\n".join(lines), buttons=buttons)


def _reject(command: commands.Command, *, edit: bool) -> Reply:
    targets, note = _resolve(command)
    if not targets:
        return Reply(note)

    lines = []
    for approval in targets:
        approvals.deny(approval, reason="edit requested" if edit else "rejected by Andrew")
        task = tasks_store.set_status(
            approval.task_id, tasks_store.STOPPED,
            note="edit requested" if edit else "rejected",
        )
        if task is not None:
            tasks_store.record_outcome(task, "edited" if edit else "rejected")
        audit_store.write(
            task_id=approval.task_id,
            phase=audit_store.PHASE_AFTER,
            event="APPROVAL_EDIT" if edit else "APPROVAL_REJECTED",
            tool=approval.tool,
            tier=approval.tier,
            outcome="DENIED",
        )
        if edit:
            lines.append(
                f"{approval.task_id} dropped, nothing sent. Tell me what to change."
            )
        else:
            lines.append(f"{approval.task_id} dropped. Nothing sent.")

    if note:
        lines.append(note)
    return Reply("\n".join(lines))


def _undo(command: commands.Command) -> Reply:
    if command.approval_id == "all":
        held = outbox.held()
    elif command.approval_id:
        entry = outbox.get(command.approval_id)
        held = [entry] if entry and entry.status == outbox.HELD else []
    elif command.task_id:
        held = outbox.held_for_task(command.task_id)
    else:
        held = outbox.held()

    if not held:
        target = command.task_id or "that"
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


def _free_text(text: str) -> Reply:
    state = budget.check()
    if state.blocked:
        # Hard rule 8: at the cap the only model call that still happens is
        # none, and the reply says so.
        return Reply(state.message)

    task = tasks_store.create(text, task_type=_guess_type(text))
    audit_store.write(
        task_id=task.task_id,
        phase=audit_store.PHASE_BEFORE,
        event="TASK_STARTED",
        detail={"chars": len(text), "task_type": task.task_type},
    )

    try:
        answer = planner_mod.get_planner().run(task, text)
    except budget.BudgetExceeded as exc:
        tasks_store.set_status(task.task_id, tasks_store.STOPPED, note="budget cap")
        return Reply(str(exc), task_id=task.task_id)
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

    buttons: list[Button] = []
    for approval in approvals.open_for_task(task.task_id):
        buttons.extend(_approval_buttons(approval))

    prefix = "" if answer.startswith(task.task_id) else f"{task.task_id}: "
    text_out = f"{prefix}{answer}".strip()

    if state.status == budget.WARN and state.newly_warned:
        text_out = f"{text_out}\n{state.message}"

    return Reply(text_out, buttons=buttons, task_id=task.task_id)


_TYPE_HINTS = {
    "email": ("email", "inbox", "reply to", "write to", "landlord", "message"),
    "calendar": ("calendar", "schedule", "meeting", "free", "busy", "invite",
                 "monday", "tuesday", "wednesday", "thursday", "friday"),
    "research": ("find", "look up", "compare", "search", "how much", "who is"),
}


def _guess_type(text: str) -> str:
    """A rough label used for routing and, later, earned autonomy. It is a
    hint, never a permission: the gate does not read it."""
    lowered = text.lower()
    for task_type, hints in _TYPE_HINTS.items():
        if any(hint in lowered for hint in hints):
            return task_type
    return "general"


def release_due() -> list[Reply]:
    """Run once a minute: anything past its undo window goes out, and Andrew
    is told what happened."""
    replies = []
    for entry, result in registry.release_due():
        if result.ok:
            receipt = receipts_store.write(
                task_id=entry.task_id,
                kind=receipts_store.ACTION,
                summary=entry.summary,
                ref=str(result.data.get("id", "")),
                detail={"tool": entry.tool},
            )
            replies.append(
                Reply(f"{entry.task_id} done: {entry.summary}. Receipt {receipt.receipt_id[:8]}.",
                      task_id=entry.task_id)
            )
        elif result.status == "DENIED":
            replies.append(Reply(result.message, task_id=entry.task_id))
        else:
            replies.append(
                Reply(f"{entry.task_id} failed: {result.message}", task_id=entry.task_id)
            )
    return replies


def daily_digest() -> Reply:
    """The batched-approval message. One a day, numbered so it can be answered
    with "yes all" or "yes 1,3"."""
    approvals.expire_stale()
    waiting = approvals.pending()
    tasks = tasks_store.open_tasks()
    if not waiting and not tasks:
        return Reply("")

    lines = [f"Morning. {clock.now_iso()[:10]}"]
    buttons: list[Button] = []
    if waiting:
        lines.append(_digest_text(waiting))
        buttons = _digest_buttons(waiting)
    if tasks:
        lines.append(f"{len(tasks)} open: {', '.join(t.task_id for t in tasks)}")

    state = budget.check()
    if state.status in (budget.WARN, budget.BLOCKED):
        lines.append(state.message)

    return Reply("\n".join(lines), buttons=buttons)
