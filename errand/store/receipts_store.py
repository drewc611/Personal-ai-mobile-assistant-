"""Receipts.

A receipt is the durable answer to "what actually happened". v0 writes one for
every deletion and every executed tier 2+ action; v1 adds confirmation numbers
and screenshots from the browser, which is why `file_path` points at S3 rather
than holding content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from errand.common import clock, config, ids
from errand.store import backend as backend_mod

DELETION = "deletion"
ACTION = "action"
PURCHASE = "purchase"


@dataclass
class Receipt:
    receipt_id: str
    task_id: str
    kind: str
    ref: str = ""
    file_path: str = ""
    summary: str = ""
    amount_cents: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": f"task#{self.task_id}",
            "sk": f"receipt#{self.receipt_id}",
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "ref": self.ref,
            "file_path": self.file_path,
            "summary": self.summary,
            "amount_cents": self.amount_cents,
            "detail": json.dumps(self.detail, sort_keys=True, default=str),
            "created_at": self.created_at or clock.now_iso(),
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Receipt:
        raw = item.get("detail") or "{}"
        amount = item.get("amount_cents")
        return cls(
            receipt_id=item["receipt_id"],
            task_id=item["task_id"],
            kind=item.get("kind", ACTION),
            ref=item.get("ref", ""),
            file_path=item.get("file_path", ""),
            summary=item.get("summary", ""),
            amount_cents=int(amount) if amount is not None else None,
            detail=json.loads(raw) if isinstance(raw, str) else dict(raw),
            created_at=item.get("created_at", ""),
        )


def _table() -> str:
    return config.load().table("receipts")


def write(
    *,
    task_id: str,
    kind: str,
    summary: str,
    ref: str = "",
    file_path: str = "",
    amount_cents: int | None = None,
    detail: dict[str, Any] | None = None,
) -> Receipt:
    receipt = Receipt(
        receipt_id=ids.new_approval_id().replace("ap_", "rc_"),
        task_id=task_id,
        kind=kind,
        ref=ref,
        file_path=file_path,
        summary=summary,
        amount_cents=amount_cents,
        detail=detail or {},
        created_at=clock.now_iso(),
    )
    backend_mod.get_backend().put(_table(), receipt.to_item())
    return receipt


def for_task(task_id: str) -> list[Receipt]:
    rows = backend_mod.get_backend().query(_table(), f"task#{task_id}", "receipt#")
    return [Receipt.from_item(r) for r in rows]


def all_receipts() -> list[Receipt]:
    rows = [
        r for r in backend_mod.get_backend().scan(_table())
        if str(r.get("sk", "")).startswith("receipt#")
    ]
    return [Receipt.from_item(r) for r in rows]
