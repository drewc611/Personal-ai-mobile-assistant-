"""Disconnect and deletion.

Instinct's reported failure was keeping data after a disconnect. The answer is
not a promise in a privacy policy, it is this function: revoke the token,
delete every cached row tagged with that connection, count what was deleted,
write a receipt, and send the counts. A receipt with numbers in it can be
checked. "Your data has been removed" cannot.

`gmail_disconnect` is registered at tier 4 so the planner model cannot reach
it. Andrew sending `/disconnect gmail` runs `disconnect()` directly - that
message is the authorisation, and the action only ever removes access and
deletes Errand's own copies, so the failure direction is losing capability,
never losing control.
"""

from __future__ import annotations

from typing import Any

from errand.common import clock
from errand.policy.tiers import Tier
from errand.store import audit_store, connections_store, content_store, receipts_store
from errand.tools import providers
from errand.tools.registry import tool

CONNECTIONS = {
    "gmail": content_store.GMAIL,
    "calendar": content_store.CALENDAR,
}


def disconnect(connection: str, *, task_id: str = "system") -> dict[str, Any]:
    """Revoke, delete, receipt, report."""
    if connection not in CONNECTIONS:
        raise ValueError(f"unknown connection {connection!r}")

    inventory = content_store.inventory(connection)
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="DISCONNECT_REQUESTED",
        tool=f"{connection}_disconnect",
        tier=int(Tier.IRREVERSIBLE),
        detail={"connection": connection, "inventory": inventory},
    )

    revoked = False
    revoke_error = ""
    try:
        revoked = providers.get_providers().tokens.revoke(connection)
    except Exception as exc:  # noqa: BLE001
        # A failed revoke must not stop the deletion. Deleting our copies is
        # the part we control; the receipt says plainly when the revoke failed.
        revoke_error = str(exc)[:200]

    deleted = content_store.purge(connection)
    connections_store.record_disconnected(connection)

    result = {
        "connection": connection,
        "token_revoked": revoked,
        "revoke_error": revoke_error,
        "deleted": deleted,
        "deleted_total": sum(deleted.values()),
        "at": clock.now_iso(),
    }

    receipt = receipts_store.write(
        task_id=task_id,
        kind=receipts_store.DELETION,
        summary=f"disconnected {connection}, deleted {result['deleted_total']} stored items",
        ref=connection,
        detail=result,
    )
    result["receipt_id"] = receipt.receipt_id

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="DISCONNECT_COMPLETED",
        tool=f"{connection}_disconnect",
        tier=int(Tier.IRREVERSIBLE),
        outcome="OK" if revoked and not revoke_error else "PARTIAL",
        detail={"connection": connection, "deleted": deleted, "revoke_error": revoke_error,
                "receipt_id": receipt.receipt_id},
        sequence=1,
    )
    return result


def receipt_text(result: dict[str, Any]) -> str:
    """The deletion receipt as Andrew reads it."""
    lines = [f"Disconnected {result['connection']}."]

    if result["token_revoked"]:
        lines.append("OAuth token revoked.")
    else:
        detail = result.get("revoke_error") or "no token was held"
        lines.append(
            f"Token not revoked ({detail}); revoke it yourself at "
            f"myaccount.google.com/permissions."
        )

    deleted = result.get("deleted") or {}
    if deleted:
        parts = ", ".join(
            f"{count} {kind.replace('_', ' ')}" for kind, count in sorted(deleted.items())
        )
        lines.append(f"Deleted {parts}.")
    else:
        lines.append("Nothing was stored, so nothing to delete.")

    lines.append("Audit rows are kept; they record actions, not message content.")
    if result.get("receipt_id"):
        lines.append(f"Receipt {result['receipt_id'][:8]}.")
    return " ".join(lines)


@tool(
    "gmail_disconnect",
    tier=Tier.IRREVERSIBLE,
    summary="Disconnect Gmail and delete stored content",
    domain="email",
)
def gmail_disconnect() -> dict[str, Any]:
    return disconnect("gmail")
