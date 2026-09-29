"""Monthly token and cost accounting.

One row per month. Every model call adds to it before the call is made, so a
crash mid-call costs the budget rather than escaping it - overcounting a
failed call is the safe direction when the alternative is a cap that can be
walked past by dying at the right moment.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from errand.common import clock, config
from errand.store import backend as backend_mod


@dataclass
class MonthlyUsage:
    month: str
    tokens_in: int = 0
    tokens_out: int = 0
    usd_estimate: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": f"month#{self.month}",
            "sk": "usage",
            "month": self.month,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usd_estimate": round(self.usd_estimate, 6),
            "calls": self.calls,
            "unpriced_calls": self.unpriced_calls,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> MonthlyUsage:
        return cls(
            month=item["month"],
            tokens_in=int(item.get("tokens_in", 0)),
            tokens_out=int(item.get("tokens_out", 0)),
            usd_estimate=float(item.get("usd_estimate", 0.0)),
            calls=int(item.get("calls", 0)),
            unpriced_calls=int(item.get("unpriced_calls", 0)),
        )


def current_month() -> str:
    return dt.datetime.fromtimestamp(clock.now(), tz=dt.UTC).strftime("%Y-%m")


def _table() -> str:
    return config.load().table("budget")


def get(month: str | None = None) -> MonthlyUsage:
    month = month or current_month()
    item = backend_mod.get_backend().get(_table(), f"month#{month}", "usage")
    return MonthlyUsage.from_item(item) if item else MonthlyUsage(month=month)


def add(
    *,
    tokens_in: int,
    tokens_out: int,
    usd: float | None,
    month: str | None = None,
) -> MonthlyUsage:
    usage = get(month)
    usage.tokens_in += max(0, tokens_in)
    usage.tokens_out += max(0, tokens_out)
    usage.calls += 1
    if usd is None:
        usage.unpriced_calls += 1
    else:
        usage.usd_estimate += usd
    backend_mod.get_backend().put(_table(), usage.to_item())
    return usage


def note_warned(month: str | None = None) -> bool:
    """Record that the 80 percent warning went out. Returns False if it
    already had - the warning is worth sending once, not on every call."""
    month = month or current_month()
    return backend_mod.get_backend().put_if_absent(
        _table(), {"pk": f"month#{month}", "sk": "warned", "at": clock.now_iso()}
    )


def history(limit: int = 12) -> list[MonthlyUsage]:
    rows = [r for r in backend_mod.get_backend().scan(_table()) if r.get("sk") == "usage"]
    months = sorted((MonthlyUsage.from_item(r) for r in rows), key=lambda m: m.month)
    return months[-limit:]
