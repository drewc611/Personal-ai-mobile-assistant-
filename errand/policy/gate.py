"""The approval gate.

Hard rule 1 lives here. Enforcement is in the tool layer, not in the prompt: a
call above the allowed tier returns PENDING_APPROVAL and does nothing. There is
no phrasing, no role-play, and no chain of reasoning that routes around this
function, because the model never holds a reference to the underlying tool -
it only ever reaches `tools.registry.call`, which comes through here first.

The order matters:

  unknown tool        -> DENIED   (an unregistered tool is refused, not guessed)
  standing deny rule  -> DENIED
  constrain rules     -> arguments rewritten, then denies re-checked
  tier 0 or 1         -> ALLOW
  standing allow rule -> HELD     (auto-approved, still inside the undo window)
  otherwise           -> PENDING_APPROVAL
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from errand.common import money
from errand.policy import approvals, outbox, rules
from errand.policy.approvals import Approval
from errand.policy.tiers import FREE_TIERS, Tier, UnknownTool, classify, spec_for
from errand.store import audit_store

ALLOW = "ALLOW"
PENDING_APPROVAL = "PENDING_APPROVAL"
DENIED = "DENIED"
HELD = "HELD"


@dataclass
class GateDecision:
    status: str
    tier: Tier
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    approval: Approval | None = None
    entry: outbox.OutboxEntry | None = None
    message: str = ""
    rule_id: str = ""

    @property
    def allowed(self) -> bool:
        return self.status == ALLOW

    def to_model_payload(self) -> dict[str, Any]:
        """What the planner model is told when a call does not run. It gets the
        status and a sentence, never a hint about how to retry at a lower
        tier."""
        return {
            "status": self.status,
            "tool": self.tool,
            "tier": int(self.tier),
            "task_id": self.approval.task_id if self.approval else "",
            "message": self.message,
        }


def decide(
    *,
    task_id: str,
    tool: str,
    args: dict[str, Any],
    summary: str = "",
    amount_cents: int | None = None,
) -> GateDecision:
    try:
        spec = spec_for(tool)
    except UnknownTool:
        audit_store.write(
            task_id=task_id,
            phase=audit_store.PHASE_BEFORE,
            event="TOOL_REFUSED_UNKNOWN",
            tool=tool,
            outcome=DENIED,
            detail={"args_keys": sorted(args)},
        )
        return GateDecision(
            status=DENIED,
            tier=Tier.IRREVERSIBLE,
            tool=tool,
            args=dict(args),
            message=f"{tool} is not a tool Errand has.",
        )

    tier = classify(tool, args)
    decision = rules.evaluate(
        tool=tool, domain=spec.domain, tier=tier, args=args, amount_cents=amount_cents
    )

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="TOOL_CALL_REQUESTED",
        tool=tool,
        tier=int(tier),
        detail={
            "args": _redact(decision.args),
            "amount_cents": amount_cents,
            "constraints": [r.rule_id for r in decision.constraints],
        },
    )

    if decision.denied:
        rule = decision.denied_by
        message = f"Refused by your standing rule: {rule.text}" if rule else "Refused."
        audit_store.write(
            task_id=task_id,
            phase=audit_store.PHASE_AFTER,
            event="TOOL_CALL_DENIED",
            tool=tool,
            tier=int(tier),
            outcome=DENIED,
            detail={"rule_id": rule.rule_id if rule else ""},
        )
        return GateDecision(
            status=DENIED,
            tier=tier,
            tool=tool,
            args=decision.args,
            message=message,
            rule_id=rule.rule_id if rule else "",
        )

    if tier in FREE_TIERS:
        return GateDecision(status=ALLOW, tier=tier, tool=tool, args=decision.args)

    approval = approvals.request(
        task_id=task_id,
        tool=tool,
        tier=tier,
        args=decision.args,
        summary=summary or spec.summary,
        amount_cents=amount_cents,
    )

    if decision.auto_approved and decision.allowed_by is not None:
        rule = decision.allowed_by
        approvals.auto_approve(approval, rule_id=rule.rule_id)
        entry = outbox.hold(
            approval_id=approval.approval_id,
            task_id=task_id,
            tool=tool,
            args=decision.args,
            args_digest=approval.args_digest,
            summary=approval.summary,
        )
        audit_store.write(
            task_id=task_id,
            phase=audit_store.PHASE_AFTER,
            event="TOOL_CALL_AUTO_APPROVED",
            tool=tool,
            tier=int(tier),
            outcome=HELD,
            detail={"rule_id": rule.rule_id, "approval_id": approval.approval_id},
        )
        return GateDecision(
            status=HELD,
            tier=tier,
            tool=tool,
            args=decision.args,
            approval=approval,
            entry=entry,
            rule_id=rule.rule_id,
            message=(
                f"{task_id}: {approval.summary} — going out in {entry.seconds_left}s under your "
                f"rule \"{rule.text}\". Reply \"{task_id} undo\" to stop it."
            ),
        )

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="TOOL_CALL_PENDING_APPROVAL",
        tool=tool,
        tier=int(tier),
        outcome=PENDING_APPROVAL,
        detail={"approval_id": approval.approval_id},
    )
    return GateDecision(
        status=PENDING_APPROVAL,
        tier=tier,
        tool=tool,
        args=decision.args,
        approval=approval,
        message=_approval_prompt(approval),
    )


def _approval_prompt(approval: Approval) -> str:
    """What Andrew reads. It has to say what it costs, whether it can be
    undone, and exactly what to type back."""
    irreversible = approval.tier >= int(Tier.IRREVERSIBLE)

    if approval.amount_cents:
        amount = money.format_amount(approval.amount_cents)
        warning = " This cannot be undone." if irreversible else ""
        twice = " twice" if irreversible else ""
        return (
            f"{approval.task_id}: {approval.summary} - {amount}.{warning} "
            f'Reply "{approval.task_id} yes {amount}"{twice} to go ahead.'
        )

    if irreversible:
        return (
            f"{approval.task_id}: {approval.summary} - this cannot be undone. "
            f'Reply "{approval.task_id} yes" twice to confirm.'
        )

    return f'{approval.task_id}: {approval.summary} - reply "{approval.task_id} yes" to send.'


_SENSITIVE_KEYS = {"body", "text", "message", "content", "html", "snippet"}


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    """The audit log records that a body existed and how long it was, not what
    it said. Audit rows outlive the task; message bodies should not."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in _SENSITIVE_KEYS and isinstance(value, str):
            out[key] = f"<{len(value)} chars>"
        else:
            out[key] = value
    return out
