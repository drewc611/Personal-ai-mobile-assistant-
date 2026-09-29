"""The tool layer.

Every tool in Errand is registered here with its tier, and every call goes
through `call()`, which consults the gate first. The planner model is handed
wrappers around `call()` - it never receives a reference to an underlying
function, so "just send it, I have permission" reaches exactly the same code
path as any other request and gets the same answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from errand.common import clock
from errand.policy import approvals, gate, outbox
from errand.policy.tiers import Tier, ToolSpec, register, spec_for
from errand.store import audit_store, tasks_store

AmountExtractor = Callable[[dict[str, Any]], int | None]


@dataclass
class ToolResult:
    status: str                      # OK | PENDING_APPROVAL | DENIED | HELD | ERROR
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    task_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    def to_model_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "tool": self.tool}
        if self.message:
            payload["message"] = self.message
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.status == "OK":
            payload["data"] = self.data
        return payload


_IMPLS: dict[str, Callable[..., Any]] = {}
_AMOUNTS: dict[str, AmountExtractor] = {}
_SUMMARIES: dict[str, Callable[[dict[str, Any]], str]] = {}


def tool(
    name: str,
    *,
    tier: Tier,
    summary: str,
    domain: str = "general",
    escalate: Callable[[dict[str, Any]], Tier | None] | None = None,
    amount_from: AmountExtractor | None = None,
    describe: Callable[[dict[str, Any]], str] | None = None,
):
    """Register a tool implementation.

    `amount_from` is how a tier 3 tool tells the gate what it is about to
    spend. A spend tool without it is a spend tool that cannot be approved,
    which is the safe failure.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        register(ToolSpec(name=name, tier=tier, summary=summary, escalate=escalate, domain=domain))
        _IMPLS[name] = fn
        if amount_from is not None:
            _AMOUNTS[name] = amount_from
        if describe is not None:
            _SUMMARIES[name] = describe
        fn.errand_tool_name = name  # type: ignore[attr-defined]
        return fn

    return decorator


def implementation(name: str) -> Callable[..., Any]:
    return _IMPLS[name]


def describe_call(name: str, args: dict[str, Any]) -> str:
    describer = _SUMMARIES.get(name)
    if describer is not None:
        try:
            return describer(args)
        except Exception:  # a bad describer must not block the gate
            pass
    return spec_for(name).summary


def amount_for(name: str, args: dict[str, Any]) -> int | None:
    extractor = _AMOUNTS.get(name)
    if extractor is None:
        return None
    return extractor(args)


def call(task_id: str, name: str, args: dict[str, Any] | None = None) -> ToolResult:
    """The single entry point. Nothing else may invoke a tool implementation."""
    args = dict(args or {})

    if name not in _IMPLS:
        decision = gate.decide(task_id=task_id, tool=name, args=args)
        return ToolResult(
            status="DENIED", tool=name, message=decision.message, task_id=task_id
        )

    decision = gate.decide(
        task_id=task_id,
        tool=name,
        args=args,
        summary=describe_call(name, args),
        amount_cents=amount_for(name, args),
    )

    if decision.status == gate.ALLOW:
        return _run(task_id, name, decision.args)

    if decision.status == gate.HELD:
        tasks_store.set_status(task_id, tasks_store.HOLDING, note=decision.message)
        return ToolResult(
            status="HELD", tool=name, message=decision.message, task_id=task_id
        )

    if decision.status == gate.PENDING_APPROVAL:
        tasks_store.set_status(task_id, tasks_store.WAITING_APPROVAL, note=decision.message)
        return ToolResult(
            status="PENDING_APPROVAL", tool=name, message=decision.message, task_id=task_id
        )

    return ToolResult(status="DENIED", tool=name, message=decision.message, task_id=task_id)


def _run(task_id: str, name: str, args: dict[str, Any]) -> ToolResult:
    started = clock.now()
    try:
        data = _IMPLS[name](**args)
    except Exception as exc:  # noqa: BLE001 - the audit row matters more than the type
        audit_store.write(
            task_id=task_id,
            phase=audit_store.PHASE_AFTER,
            event="TOOL_CALL_FAILED",
            tool=name,
            tier=int(spec_for(name).tier),
            outcome="ERROR",
            detail={"error": type(exc).__name__, "message": str(exc)[:300]},
            sequence=1,
        )
        return ToolResult(
            status="ERROR", tool=name, message=f"{name} failed: {exc}", task_id=task_id
        )

    payload = data if isinstance(data, dict) else {"value": data}
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="TOOL_CALL_COMPLETED",
        tool=name,
        tier=int(spec_for(name).tier),
        outcome="OK",
        detail={"ms": int((clock.now() - started) * 1000), "keys": sorted(payload)},
        sequence=1,
    )
    return ToolResult(status="OK", tool=name, data=payload, task_id=task_id)


def release(entry: outbox.OutboxEntry) -> ToolResult:
    """Run an outbox entry whose undo window has closed.

    The stored arguments are executed verbatim and checked against the digest
    recorded when the approval was granted. If they do not match, nothing runs:
    a mismatch means something rewrote an approved action after the fact.
    """
    approval = approvals.get(entry.task_id, entry.approval_id)
    if approval is None:
        outbox.mark(entry, outbox.FAILED, "approval record missing")
        return ToolResult(status="ERROR", tool=entry.tool, message="approval missing",
                          task_id=entry.task_id)

    if approval.status != approvals.APPROVED:
        outbox.mark(entry, outbox.CANCELLED, f"approval was {approval.status}")
        return ToolResult(status="DENIED", tool=entry.tool,
                          message=f"{entry.task_id} was {approval.status.lower()}.",
                          task_id=entry.task_id)

    if approvals.digest_args(entry.tool, entry.args) != approval.args_digest:
        outbox.mark(entry, outbox.FAILED, "argument digest mismatch")
        approvals.mark_failed(approval, "argument digest mismatch")
        audit_store.write(
            task_id=entry.task_id,
            phase=audit_store.PHASE_AFTER,
            event="OUTBOX_DIGEST_MISMATCH",
            tool=entry.tool,
            outcome="DENIED",
        )
        return ToolResult(status="DENIED", tool=entry.tool,
                          message=f"{entry.task_id} changed after you approved it. Nothing sent.",
                          task_id=entry.task_id)

    audit_store.write(
        task_id=entry.task_id,
        phase=audit_store.PHASE_BEFORE,
        event="OUTBOX_RELEASE",
        tool=entry.tool,
        tier=approval.tier,
        detail={"approval_id": approval.approval_id},
    )
    result = _run(entry.task_id, entry.tool, entry.args)
    if result.ok:
        outbox.mark(entry, outbox.EXECUTED, json.dumps(result.data, default=str)[:400])
        approvals.mark_executed(approval, result.message or "done")
        task = tasks_store.set_status(entry.task_id, tasks_store.DONE, note=entry.summary)
        if task is not None:
            tasks_store.record_outcome(task, "approved_and_executed")
    else:
        outbox.mark(entry, outbox.FAILED, result.message)
        approvals.mark_failed(approval, result.message)
        task = tasks_store.set_status(entry.task_id, tasks_store.FAILED, note=result.message)
        if task is not None:
            tasks_store.record_outcome(task, "failed")
    return result


def release_due() -> list[tuple[outbox.OutboxEntry, ToolResult]]:
    """Called on a schedule. Anything past its undo window goes out now."""
    return [(entry, release(entry)) for entry in outbox.due()]
