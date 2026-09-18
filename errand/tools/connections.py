"""Disconnect and deletion.

Instinct's reported failure was keeping data after a disconnect. The answer is
not a promise in a privacy policy, it is this function: revoke the token,
delete the cached content, count what was deleted, and text the count back.
A receipt with numbers in it can be checked. "Your data has been removed"
cannot.

`gmail_disconnect` is registered at tier 4 so the planner model cannot reach
it. Andrew texting "disconnect gmail" runs `disconnect()` directly - that text
message is the authorisation, and the action only ever removes access and
deletes Errand's own copies, so the failure direction is losing capability,
never losing control.
"""

from __future__ import annotations

from typing import Any

from errand.common import clock
from errand.policy.tiers import Tier
from errand.store import audit_store, content_store
from errand.tools import providers
from errand.tools.registry import tool

CONNECTIONS = {
    "gmail": content_store.GMAIL,
    "calendar": content_store.CALENDAR,
}


def disconnect(connection: str, *, task_id: str = "system") -> dict[str, Any]:
    """Revoke, delete, and report. Called by the command handler."""
    if connection not in CONNECTIONS:
        raise ValueError(f"unknown connection {connection!r}")

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="DISCONNECT_REQUESTED",
        tool=f"{connection}_disconnect",
        tier=int(Tier.IRREVERSIBLE),
        detail={"connection": connection, "inventory": content_store.inventory(connection)},
    )

    revoked = False
    revoke_error = ""
    try:
        revoked = providers.get_providers().tokens.revoke(connection)
    except Exception as exc:  # noqa: BLE001
        # A failed revoke must not stop the deletion. Deleting our copies is
        # the part we control; the receipt says plainly if the revoke failed.
        revoke_error = str(exc)[:200]

    deleted = content_store.purge(connection)

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="DISCONNECT_COMPLETED",
        tool=f"{connection}_disconnect",
        tier=int(Tier.IRREVERSIBLE),
        outcome="OK" if revoked and not revoke_error else "PARTIAL",
        detail={"connection": connection, "deleted": deleted, "revoke_error": revoke_error},
        sequence=1,
    )

    return {
        "connection": connection,
        "token_revoked": revoked,
        "revoke_error": revoke_error,
        "deleted": deleted,
        "deleted_total": sum(deleted.values()),
        "at": clock.now_iso(),
    }


def receipt_text(result: dict[str, Any]) -> str:
    """The deletion receipt, as Andrew will read it on a phone."""
    lines = [f"Disconnected {result['connection']}."]
    if result["token_revoked"]:
        lines.append("OAuth token revoked.")
    else:
        detail = result.get("revoke_error") or "no token was held"
        lines.append(f"Token not revoked ({detail}) - do it at myaccount.google.com/permissions.")
    deleted = result.get("deleted") or {}
    if deleted:
        parts = ", ".join(
            f"{count} {kind.replace('_', ' ')}" for kind, count in sorted(deleted.items())
        )
        lines.append(f"Deleted {parts}.")
    else:
        lines.append("Nothing was stored, so nothing to delete.")
    lines.append("Audit rows are kept; they record actions, not message content.")
    return " ".join(lines)


@tool(
    "gmail_disconnect",
    tier=Tier.IRREVERSIBLE,
    summary="Disconnect Gmail and delete stored content",
    domain="email",
)
def gmail_disconnect() -> dict[str, Any]:
    return disconnect("gmail")
