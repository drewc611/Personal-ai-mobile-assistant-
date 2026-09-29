"""The monthly budget cap.

Hard rule 8: at 80 percent, message Andrew; at 100 percent, stop all model
calls except the reply saying the cap was hit.

Two decisions worth stating plainly.

The check runs *before* the model call. Checking afterwards means the cap is
always exceeded by exactly one call, and on an escalation to a bigger model
that one call is the expensive one.

Pricing comes from config, not from a table in this file. Bedrock prices
change; a stale number here would not fail, it would quietly misreport what
Andrew is spending, which is worse than refusing to guess. A model with no
configured rate blocks rather than being counted as free - the whole point of
the cap is that it cannot be walked past.
"""

from __future__ import annotations

from dataclasses import dataclass

from errand.common import config
from errand.store import budget_store

OK = "OK"
WARN = "WARN"
BLOCKED = "BLOCKED"

WARN_FRACTION = 0.8


class BudgetExceeded(RuntimeError):
    """The cap is spent. The only thing left to do is say so."""


@dataclass(frozen=True)
class BudgetState:
    status: str
    spent_usd: float
    cap_usd: float
    fraction: float
    message: str = ""
    newly_warned: bool = False

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)


def estimate_usd(model_id: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD for one call, or None when this model has no configured rate."""
    rate = config.model_rates().get(model_id)
    if rate is None:
        return None
    return (tokens_in / 1_000_000) * rate["in"] + (tokens_out / 1_000_000) * rate["out"]


def check(model_id: str = "") -> BudgetState:
    """Where the month stands. Call this before spending anything."""
    cfg = config.load()
    usage = budget_store.get()

    if not cfg.budget_configured:
        return BudgetState(
            status=BLOCKED,
            spent_usd=usage.usd_estimate,
            cap_usd=0.0,
            fraction=1.0,
            message=(
                "No monthly budget is set, so I am not making model calls. "
                "Set ERRAND_MONTHLY_BUDGET_USD and I will start."
            ),
        )

    if model_id and model_id not in config.model_rates():
        return BudgetState(
            status=BLOCKED,
            spent_usd=usage.usd_estimate,
            cap_usd=cfg.monthly_budget_usd,
            fraction=usage.usd_estimate / cfg.monthly_budget_usd,
            message=(
                f"No price is configured for {model_id}, so I cannot tell what a call "
                f"would cost and I am not making one. Add it to ERRAND_MODEL_RATES."
            ),
        )

    fraction = usage.usd_estimate / cfg.monthly_budget_usd

    if usage.usd_estimate >= cfg.monthly_budget_usd:
        return BudgetState(
            status=BLOCKED,
            spent_usd=usage.usd_estimate,
            cap_usd=cfg.monthly_budget_usd,
            fraction=fraction,
            message=(
                f"Monthly budget cap hit: ${usage.usd_estimate:.2f} of "
                f"${cfg.monthly_budget_usd:.2f}. I have stopped making model calls. "
                f"Raise the cap or wait for {_next_month_word()}."
            ),
        )

    if fraction >= WARN_FRACTION:
        newly = budget_store.note_warned()
        return BudgetState(
            status=WARN,
            spent_usd=usage.usd_estimate,
            cap_usd=cfg.monthly_budget_usd,
            fraction=fraction,
            newly_warned=newly,
            message=(
                f"{int(fraction * 100)}% of this month's budget used "
                f"(${usage.usd_estimate:.2f} of ${cfg.monthly_budget_usd:.2f})."
            ),
        )

    return BudgetState(
        status=OK,
        spent_usd=usage.usd_estimate,
        cap_usd=cfg.monthly_budget_usd,
        fraction=fraction,
    )


def guard(model_id: str) -> BudgetState:
    """Raise if the cap is spent. Callers that want to report rather than
    raise should use `check` instead."""
    state = check(model_id)
    if state.blocked:
        raise BudgetExceeded(state.message)
    return state


def record(model_id: str, tokens_in: int, tokens_out: int) -> budget_store.MonthlyUsage:
    usd = estimate_usd(model_id, tokens_in, tokens_out)
    return budget_store.add(tokens_in=tokens_in, tokens_out=tokens_out, usd=usd)


def report() -> str:
    """What `/budget` prints."""
    cfg = config.load()
    usage = budget_store.get()

    if not cfg.budget_configured:
        return (
            "No monthly budget is set, so model calls are refused. "
            "Set ERRAND_MONTHLY_BUDGET_USD."
        )

    lines = [
        f"{usage.month}: ${usage.usd_estimate:.2f} of ${cfg.monthly_budget_usd:.2f} "
        f"({int(usage.usd_estimate / cfg.monthly_budget_usd * 100)}%)",
        f"{usage.tokens_in:,} in / {usage.tokens_out:,} out over {usage.calls} calls",
    ]
    if usage.unpriced_calls:
        lines.append(
            f"{usage.unpriced_calls} calls had no configured price and are not in the total."
        )
    state = check()
    if state.blocked:
        lines.append("Cap reached. No model calls until it is raised.")
    elif state.status == WARN:
        lines.append("Past 80%.")
    return "\n".join(lines)


def _next_month_word() -> str:
    import datetime as dt

    from errand.common import clock

    now = dt.datetime.fromtimestamp(clock.now(), tz=dt.UTC)
    following = (now.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return following.strftime("%B")
