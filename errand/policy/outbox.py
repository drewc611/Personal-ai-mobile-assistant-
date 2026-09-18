"""The undo window.

An approved tier 2+ action does not go out immediately. It lands here with a
release time, and for the next sixty seconds "undo" pulls it back. The cost is
a minute of latency on sends; the benefit is that the worst class of mistake -
the one you spot the instant after you hit yes - is recoverable without
involving the recipient.

Anything already released is gone. The outbox never claims otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from errand.common import clock, config
from errand.store import backend as backend_mod

HELD = "HELD"
RELEASED = "RELEASED"
CANCELLED = "CANCELLED"
EXECUTED = "EXECUTED"
FAILED = "FAILED"


@dataclass
class OutboxEntry:
    approval_id: str
    task_id: str
    tool: str
    args: dict[str, Any]
    args_digest: str
    summary: str
    status: str = HELD
    release_at: float = 0.0
    created_at: str = ""
    note: str = ""

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": "outbox",
            "sk": f"{self.release_at:015.3f}#{self.approval_id}",
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "tool": self.tool,
            "args": json.dumps(self.args, sort_keys=True, default=str),
            "args_digest": self.args_digest,
            "summary": self.summary,
            "status": self.status,
            "release_at": self.release_at,
            "created_at": self.created_at,
            "note": self.note,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> OutboxEntry:
        raw = item.get("args") or "{}"
        return cls(
            approval_id=item["approval_id"],
            task_id=item["task_id"],
            tool=item["tool"],
            args=json.loads(raw) if isinstance(raw, str) else dict(raw),
            args_digest=item.get("args_digest", ""),
            summary=item.get("summary", ""),
            status=item.get("status", HELD),
            release_at=float(item.get("release_at", 0.0)),
            created_at=item.get("created_at", ""),
            note=item.get("note", ""),
        )

    @property
    def seconds_left(self) -> int:
        return max(0, int(round(self.release_at - clock.now())))


def _table() -> str:
    return config.load().table("approvals")


def hold(
    *,
    approval_id: str,
    task_id: str,
    tool: str,
    args: dict[str, Any],
    args_digest: str,
    summary: str,
    undo_seconds: int | None = None,
) -> OutboxEntry:
    window = config.load().undo_seconds if undo_seconds is None else undo_seconds
    entry = OutboxEntry(
        approval_id=approval_id,
        task_id=task_id,
        tool=tool,
        args=dict(args),
        args_digest=args_digest,
        summary=summary,
        release_at=clock.now() + max(0, window),
        created_at=clock.now_iso(),
    )
    backend_mod.get_backend().put(_table(), entry.to_item())
    return entry


def _all() -> list[OutboxEntry]:
    rows = backend_mod.get_backend().query(_table(), "outbox")
    return [OutboxEntry.from_item(r) for r in rows]


def held() -> list[OutboxEntry]:
    return [e for e in _all() if e.status == HELD]


def held_for_task(task_id: str) -> list[OutboxEntry]:
    return [e for e in held() if e.task_id == task_id]


def get(approval_id: str) -> OutboxEntry | None:
    for entry in _all():
        if entry.approval_id == approval_id:
            return entry
    return None


def save(entry: OutboxEntry) -> OutboxEntry:
    backend_mod.get_backend().put(_table(), entry.to_item())
    return entry


def due() -> list[OutboxEntry]:
    """Entries whose window has closed. The releaser calls this."""
    now = clock.now()
    return sorted((e for e in held() if e.release_at <= now), key=lambda e: e.release_at)


def cancel(entry: OutboxEntry, *, reason: str = "undo") -> OutboxEntry:
    """Only a held entry can be cancelled. Once released, it is out."""
    if entry.status != HELD:
        raise RuntimeError(f"{entry.task_id} already {entry.status.lower()}; too late to undo.")
    entry.status = CANCELLED
    entry.note = reason
    return save(entry)


def cancel_all(*, reason: str = "STOP ALL") -> list[OutboxEntry]:
    cancelled = []
    for entry in held():
        entry.status = CANCELLED
        entry.note = reason
        save(entry)
        cancelled.append(entry)
    return cancelled


def mark(entry: OutboxEntry, status: str, note: str = "") -> OutboxEntry:
    entry.status = status
    entry.note = note[:500]
    return save(entry)
