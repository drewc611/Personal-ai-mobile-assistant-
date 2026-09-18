"""Cached third-party content.

Anything we pull out of Gmail or Calendar and keep lands here, tagged with the
connection it came from. That tag is the whole point: "disconnect gmail" has
to be able to enumerate and delete exactly what was kept, and then tell Andrew
what it deleted. An untagged cache somewhere else in the system is a deletion
receipt that lies.
"""

from __future__ import annotations

import json
from typing import Any

from errand.common import clock, config
from errand.store import backend as backend_mod

GMAIL = "gmail"
CALENDAR = "calendar"
SEARCH = "search"

# A backstop, not the mechanism. "disconnect gmail" is what deletes this
# content on purpose; the TTL catches anything a disconnect never covered,
# such as a connection Andrew stopped using without disconnecting it.
RETENTION_DAYS = 30


def _table() -> str:
    return config.load().content_table


def store(
    *,
    connection: str,
    external_id: str,
    kind: str,
    payload: dict[str, Any],
    task_id: str = "",
) -> dict[str, Any]:
    row = {
        "pk": f"conn#{connection}",
        "sk": f"{kind}#{external_id}",
        "connection": connection,
        "kind": kind,
        "external_id": external_id,
        "task_id": task_id,
        "at": clock.now_iso(),
        "payload": json.dumps(payload, sort_keys=True, default=str),
        "expires_at": int(clock.now() + RETENTION_DAYS * 86400),
    }
    backend_mod.get_backend().put(_table(), row)
    return row


def get(connection: str, kind: str, external_id: str) -> dict[str, Any] | None:
    return backend_mod.get_backend().get(_table(), f"conn#{connection}", f"{kind}#{external_id}")


def inventory(connection: str) -> dict[str, int]:
    """Counts by kind, for the deletion receipt."""
    counts: dict[str, int] = {}
    for row in backend_mod.get_backend().query(_table(), f"conn#{connection}"):
        counts[row.get("kind", "unknown")] = counts.get(row.get("kind", "unknown"), 0) + 1
    return counts


def purge(connection: str) -> dict[str, int]:
    """Delete everything cached for a connection. Returns what was deleted so
    the caller can text a receipt rather than an assurance."""
    counts = inventory(connection)
    backend_mod.get_backend().delete_partition(_table(), f"conn#{connection}")
    return counts
