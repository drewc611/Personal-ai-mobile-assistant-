"""The approval store.

An approval record pins down exactly one call: the tool, the arguments, the
tier, and (for spend) the amount. When Andrew texts "T7 yes" we execute the
stored arguments, not whatever the model would produce on a second pass. That
distinction is the difference between approving an action and approving an
intention.

Batched approvals: `pending()` returns the open requests in a stable order, so
"yes 1,3" and "yes all" address the same list Andrew was shown.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from errand.common import clock, config, ids, money
from errand.policy.tiers import Tier
from errand.store import backend as backend_mod
from errand.store import counters

PENDING = "PENDING"
APPROVED = "APPROVED"      # cleared by Andrew, waiting on the undo window
DENIED = "DENIED"
EXPIRED = "EXPIRED"
REVOKED = "REVOKED"        # STOP ALL
EXECUTED = "EXECUTED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"    # pulled back inside the undo window

OPEN_STATUSES = {PENDING}


class ApprovalError(RuntimeError):
    """The approval cannot be granted as asked."""


@dataclass
class Approval:
    approval_id: str
    task_id: str
    tool: str
    tier: int
    args: dict[str, Any]
    args_digest: str
    summary: str
    status: str = PENDING
    amount_cents: int | None = None
    confirms_required: int = 1
    confirms_received: int = 0
    created_at: str = ""
    expires_at: float = 0.0
    decided_at: str = ""
    result_note: str = ""
    rule_id: str = ""          # set when a standing rule granted it
    # Durable order of arrival. The numbered list Andrew replies to with
    # "yes 1,3" is sorted by this, so it must not depend on wall-clock
    # resolution or on scan order.
    seq: int = 0
    notes: list[str] = field(default_factory=list)

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": f"task#{self.task_id}",
            "sk": f"approval#{self.approval_id}",
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "tool": self.tool,
            "tier": self.tier,
            "args": json.dumps(self.args, sort_keys=True, default=str),
            "args_digest": self.args_digest,
            "summary": self.summary,
            "status": self.status,
            "amount_cents": self.amount_cents,
            "confirms_required": self.confirms_required,
            "confirms_received": self.confirms_received,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "result_note": self.result_note,
            "rule_id": self.rule_id,
            "notes": json.dumps(self.notes),
            "seq": self.seq,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Approval:
        raw_args = item.get("args") or "{}"
        raw_notes = item.get("notes") or "[]"
        amount = item.get("amount_cents")
        return cls(
            approval_id=item["approval_id"],
            task_id=item["task_id"],
            tool=item["tool"],
            tier=int(item.get("tier", 2)),
            args=json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args),
            args_digest=item.get("args_digest", ""),
            summary=item.get("summary", ""),
            status=item.get("status", PENDING),
            amount_cents=int(amount) if amount is not None else None,
            confirms_required=int(item.get("confirms_required", 1)),
            confirms_received=int(item.get("confirms_received", 0)),
            created_at=item.get("created_at", ""),
            expires_at=float(item.get("expires_at", 0.0)),
            decided_at=item.get("decided_at", ""),
            result_note=item.get("result_note", ""),
            rule_id=item.get("rule_id", ""),
            notes=json.loads(raw_notes) if isinstance(raw_notes, str) else list(raw_notes),
            seq=int(item.get("seq", 0)),
        )

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES and not self.is_expired

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and clock.now() > self.expires_at

    @property
    def confirms_outstanding(self) -> int:
        return max(0, self.confirms_required - self.confirms_received)


def digest_args(tool: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _table() -> str:
    return config.load().table("approvals")


def request(
    *,
    task_id: str,
    tool: str,
    tier: Tier,
    args: dict[str, Any],
    summary: str,
    amount_cents: int | None = None,
) -> Approval:
    cfg = config.load()
    approval = Approval(
        approval_id=ids.new_approval_id(),
        task_id=task_id,
        tool=tool,
        tier=int(tier),
        args=dict(args),
        args_digest=digest_args(tool, args),
        summary=summary,
        amount_cents=amount_cents,
        # Tier 4 needs a second confirm: one thumb-typed "yes" should not be
        # able to cancel an account.
        confirms_required=2 if tier >= Tier.IRREVERSIBLE else 1,
        created_at=clock.now_iso(),
        expires_at=clock.now() + cfg.approval_ttl_seconds,
        seq=counters.next_value(_table(), "approvals"),
    )
    backend_mod.get_backend().put(_table(), approval.to_item())
    return approval


def get(task_id: str, approval_id: str) -> Approval | None:
    item = backend_mod.get_backend().get(_table(), f"task#{task_id}", f"approval#{approval_id}")
    return Approval.from_item(item) if item else None


def save(approval: Approval) -> Approval:
    backend_mod.get_backend().put(_table(), approval.to_item())
    return approval


def for_task(task_id: str) -> list[Approval]:
    rows = backend_mod.get_backend().query(_table(), f"task#{task_id}", "approval#")
    return [Approval.from_item(r) for r in rows]


def open_for_task(task_id: str) -> list[Approval]:
    return [a for a in for_task(task_id) if a.is_open]


def pending() -> list[Approval]:
    """Every open approval, oldest first. This ordering is what "yes 1,3"
    indexes into, so it must not depend on scan order."""
    rows = [
        r
        for r in backend_mod.get_backend().scan(_table())
        if str(r.get("sk", "")).startswith("approval#")
    ]
    approvals = [Approval.from_item(r) for r in rows]
    open_ones = [a for a in approvals if a.is_open]
    return sorted(open_ones, key=lambda a: (a.seq, a.created_at))


def expire_stale() -> list[Approval]:
    expired = []
    for approval in for_all():
        if approval.status == PENDING and approval.is_expired:
            approval.status = EXPIRED
            approval.decided_at = clock.now_iso()
            save(approval)
            expired.append(approval)
    return expired


def for_all() -> list[Approval]:
    rows = [
        r
        for r in backend_mod.get_backend().scan(_table())
        if str(r.get("sk", "")).startswith("approval#")
    ]
    return [Approval.from_item(r) for r in rows]


def approve(
    approval: Approval,
    *,
    amount_text: str | None = None,
    granted_by: str = "button",
) -> Approval:
    """Grant one approval. Raises ApprovalError with a message meant to be
    texted verbatim - Andrew needs to know what to type next, not that
    something went wrong."""
    cfg = config.load()

    if approval.status != PENDING:
        raise ApprovalError(f"{approval.task_id} is already {approval.status.lower()}.")
    if approval.is_expired:
        approval.status = EXPIRED
        save(approval)
        raise ApprovalError(f"{approval.task_id} expired. Start it again if you still want it.")

    # A tier 3 call must carry an amount. Tier 4 need not - cancelling an
    # account costs nothing - but when it does carry one, the money checks
    # below apply to it exactly as they do to tier 3.
    if approval.tier == int(Tier.SPEND) and approval.amount_cents is None:
        raise ApprovalError(f"{approval.task_id} has no amount recorded; refusing to spend.")

    if approval.amount_cents is not None:
        if not cfg.tier3_cap_configured:
            raise ApprovalError(
                "No per-task spend cap is set, so spending is refused. "
                "Set ERRAND_TIER3_CAP_CENTS before approving a charge."
            )
        if approval.amount_cents > cfg.tier3_cap_cents:
            raise ApprovalError(
                f"{approval.task_id} is {money.format_amount(approval.amount_cents)}, over the "
                f"{money.format_amount(cfg.tier3_cap_cents)} cap. Raise the cap or do it yourself."
            )
        if amount_text is None:
            raise ApprovalError(
                f"{approval.task_id} costs {money.format_amount(approval.amount_cents)}. "
                f"Reply \"{approval.task_id} yes {money.format_amount(approval.amount_cents)}\"."
            )
        try:
            echoed = money.parse_amount(amount_text)
        except money.AmountError as exc:
            raise ApprovalError(f"Could not read {amount_text!r} as an amount.") from exc
        if echoed != approval.amount_cents:
            raise ApprovalError(
                f"You typed {money.format_amount(echoed)} but {approval.task_id} is "
                f"{money.format_amount(approval.amount_cents)}. Nothing was charged."
            )

    approval.confirms_received += 1
    approval.notes.append(
        f"{clock.now_iso()} confirm {approval.confirms_received} via {granted_by}"
    )

    if approval.confirms_outstanding > 0:
        save(approval)
        return approval

    approval.status = APPROVED
    approval.decided_at = clock.now_iso()
    return save(approval)


def auto_approve(approval: Approval, *, rule_id: str) -> Approval:
    """Granted by a standing rule rather than by a text message."""
    approval.status = APPROVED
    approval.rule_id = rule_id
    approval.confirms_received = approval.confirms_required
    approval.decided_at = clock.now_iso()
    approval.notes.append(f"{clock.now_iso()} auto-approved by standing rule {rule_id}")
    return save(approval)


def deny(approval: Approval, *, reason: str = "declined by Andrew") -> Approval:
    approval.status = DENIED
    approval.decided_at = clock.now_iso()
    approval.result_note = reason
    return save(approval)


def revoke_all(*, reason: str = "STOP ALL") -> list[Approval]:
    """The kill switch. Every pending approval is revoked, not just paused."""
    revoked = []
    for approval in for_all():
        if approval.status in (PENDING, APPROVED):
            approval.status = REVOKED
            approval.decided_at = clock.now_iso()
            approval.result_note = reason
            save(approval)
            revoked.append(approval)
    return revoked


def mark_executed(approval: Approval, note: str) -> Approval:
    approval.status = EXECUTED
    approval.result_note = note[:500]
    return save(approval)


def mark_failed(approval: Approval, note: str) -> Approval:
    approval.status = FAILED
    approval.result_note = note[:500]
    return save(approval)
