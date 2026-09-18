"""Append-only audit log.

Hard rule 6: every tool call writes a row before it runs and a row after it
finishes. The "before" row is what makes an interrupted Lambda visible - if
there is a BEFORE with no matching AFTER, something died mid-call and the
receipt is incomplete. Never write only on success.
"""

from __future__ import annotations

import json
from typing import Any

from errand.common import clock, config
from errand.store import backend as backend_mod

PHASE_BEFORE = "BEFORE"
PHASE_AFTER = "AFTER"


def _table() -> str:
    return config.load().audit_table


def _sk(phase: str, sequence: int) -> str:
    return f"ts#{clock.now():015.3f}#{sequence:04d}#{phase}"


def write(
    *,
    task_id: str,
    phase: str,
    event: str,
    tool: str = "",
    tier: int | None = None,
    outcome: str = "",
    detail: dict[str, Any] | None = None,
    sequence: int = 0,
) -> dict[str, Any]:
    row = {
        "pk": f"task#{task_id}",
        "sk": _sk(phase, sequence),
        "task_id": task_id,
        "phase": phase,
        "event": event,
        "tool": tool,
        "tier": tier,
        "outcome": outcome,
        "at": clock.now_iso(),
        "detail": json.dumps(detail or {}, sort_keys=True, default=str),
    }
    backend_mod.get_backend().put(_table(), row)
    return row


def write_rejected_inbound(*, from_number_hash: str, reason: str) -> dict[str, Any]:
    """Hard rule 4: a text from an unknown number gets no reply, but it is
    never silent in the log. We store a hash, not the number - keeping a
    stranger's phone number is the sort of collection this project exists to
    avoid."""
    row = {
        "pk": "security#inbound",
        "sk": f"ts#{clock.now():015.3f}#{from_number_hash[:12]}",
        "event": "INBOUND_REJECTED",
        "reason": reason,
        "from_number_hash": from_number_hash,
        "at": clock.now_iso(),
    }
    backend_mod.get_backend().put(_table(), row)
    return row


def for_task(task_id: str) -> list[dict[str, Any]]:
    return backend_mod.get_backend().query(_table(), f"task#{task_id}")


def security_events() -> list[dict[str, Any]]:
    return backend_mod.get_backend().query(_table(), "security#inbound")


def delete_for_task(task_id: str) -> int:
    """Only used by data deletion; the audit row for the deletion itself is
    written to a partition that is not deleted."""
    return backend_mod.get_backend().delete_partition(_table(), f"task#{task_id}")
