"""Browser actions (v1).

The browser itself is not wired yet -- AgentCore Browser arrives with the rest
of v1. What exists now is the *gate* for browser actions, because the recipe
replay engine already needs something to ask permission from, and a submit
that reaches a real page before its tier is decided is the bug this whole
system exists to prevent.

So `browser_submit` is registered with a tier that escalates from its
arguments, and its implementation refuses until a browser is configured. A
recipe that reaches a submit therefore returns PENDING_APPROVAL today and
will return PENDING_APPROVAL when the browser lands: nothing about wiring the
browser changes what may happen without an approval.
"""

from __future__ import annotations

from typing import Any

from errand.common import money
from errand.policy.tiers import Tier
from errand.tools.registry import tool

# Intents that cannot be taken back. A cancellation confirmed on a page is not
# a send, it is the end of a service.
IRREVERSIBLE_INTENTS = (
    "cancel", "close_account", "delete_account", "terminate", "nonrefundable",
)


def _escalate(args: dict[str, Any]) -> Tier | None:
    """Decide the tier from what the call is actually doing.

    Read from the arguments, never from the recipe. The same recorded steps
    are tier 2 when they update an address and tier 4 when they close an
    account, and it is today's intent that says which.
    """
    intent = str(args.get("intent", "")).lower()
    if any(word in intent for word in IRREVERSIBLE_INTENTS):
        return Tier.IRREVERSIBLE

    amount = args.get("amount_cents")
    if amount:
        try:
            if int(amount) > 0:
                return Tier.SPEND
        except (TypeError, ValueError):
            # An unreadable amount is treated as spend rather than ignored.
            return Tier.SPEND
    return None


def _amount(args: dict[str, Any]) -> int | None:
    raw = args.get("amount_cents")
    if raw in (None, "", 0):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _describe(args: dict[str, Any]) -> str:
    provider = args.get("provider", "a site")
    intent = str(args.get("intent", "submit a form")).replace("_", " ")
    amount = _amount(args)
    if amount:
        return f"{intent} at {provider} for {money.format_amount(amount)}"
    return f"{intent} at {provider}"


@tool(
    "browser_submit",
    tier=Tier.SEND,
    summary="Submit a form on a website",
    domain="browser",
    escalate=_escalate,
    amount_from=_amount,
    describe=_describe,
)
def browser_submit(
    provider: str,
    intent: str = "",
    selector: str = "",
    amount_cents: int | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    """Reaching this means an approval exists and the undo window closed.

    It still refuses, because there is no browser yet. That is the correct
    failure: the gate works, the execution does not exist.
    """
    raise NotImplementedError(
        "the browser is not configured yet; AgentCore Browser lands with the "
        "rest of v1"
    )
