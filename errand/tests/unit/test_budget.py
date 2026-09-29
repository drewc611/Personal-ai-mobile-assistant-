"""Hard rule 8: warn at 80 percent, stop at 100."""

from __future__ import annotations

import json

import pytest

from errand.policy import budget
from errand.store import budget_store, tasks_store
from errand.tests.conftest import say


def _spend(usd: float, model: str = "test-haiku"):
    """Put a known dollar figure on the month."""
    budget_store.add(tokens_in=1000, tokens_out=100, usd=usd)


def test_a_fresh_month_is_ok():
    assert budget.check().status == budget.OK


def test_eighty_percent_warns():
    _spend(16.5)  # cap is 20
    state = budget.check()
    assert state.status == budget.WARN
    assert "80%" in state.message or "82%" in state.message


def test_the_warning_is_sent_once(monkeypatch):
    _spend(16.5)
    assert budget.check().newly_warned is True
    assert budget.check().newly_warned is False


def test_the_cap_blocks(monkeypatch):
    _spend(20.0)
    state = budget.check()
    assert state.blocked
    assert "cap hit" in state.message.lower()


def test_guard_raises_at_the_cap():
    _spend(25.0)
    with pytest.raises(budget.BudgetExceeded):
        budget.guard("test-haiku")


def test_a_blocked_budget_stops_the_model_call_and_says_so(scripted, gmail):
    planner = scripted(("gmail_search", {"query": "rent"}), text="Here you go.")
    _spend(20.0)

    reply = say("what's in my inbox")

    assert "cap hit" in reply.text.lower()
    assert planner.calls == []
    assert tasks_store.all_tasks() == []


def test_a_cap_of_one_cent_stops_everything(monkeypatch, scripted):
    """Acceptance criterion 7, stated directly."""
    monkeypatch.setenv("ERRAND_MONTHLY_BUDGET_USD", "0.01")
    planner = scripted(text="Sure.")
    _spend(0.02)

    reply = say("what's on my calendar Tuesday")

    assert "stopped making model calls" in reply.text
    assert planner.calls == []


def test_no_budget_configured_refuses_rather_than_running_free(monkeypatch, scripted):
    monkeypatch.setenv("ERRAND_MONTHLY_BUDGET_USD", "0")
    planner = scripted(text="Sure.")

    reply = say("do something")

    assert "No monthly budget is set" in reply.text
    assert planner.calls == []


def test_an_unpriced_model_blocks_rather_than_counting_as_free(monkeypatch):
    monkeypatch.setenv("ERRAND_MODEL_RATES", json.dumps({"test-haiku": {"in": 1.0, "out": 5.0}}))
    state = budget.check("test-sonnet")
    assert state.blocked
    assert "No price is configured" in state.message


def test_usage_is_recorded_per_call(scripted):
    scripted(text="done")
    say("hello")
    say("hello again")

    usage = budget_store.get()
    assert usage.calls == 2
    assert usage.tokens_in == 800
    assert usage.usd_estimate > 0


def test_the_estimate_uses_the_configured_rate():
    # 1,000,000 in at $1/M and 1,000,000 out at $5/M is $6.
    assert budget.estimate_usd("test-haiku", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_an_unknown_model_has_no_estimate():
    assert budget.estimate_usd("some-other-model", 1000, 1000) is None


def test_unpriced_calls_are_counted_and_reported():
    budget_store.add(tokens_in=500, tokens_out=50, usd=None)
    assert budget_store.get().unpriced_calls == 1
    assert "no configured price" in budget.report()


def test_the_budget_command_reports_spend_and_routing():
    _spend(5.0)
    text = say("/budget").text
    assert "$5.00 of $20.00" in text
    assert "test-haiku" in text
    assert "test-sonnet" in text


def test_the_budget_command_works_when_models_are_unconfigured(monkeypatch):
    """/budget is what Andrew reaches for when something looks wrong, so it
    must not itself fail on missing configuration."""
    monkeypatch.delenv("ERRAND_DEFAULT_MODEL_ID", raising=False)
    text = say("/budget").text
    assert "not configured" in text


def test_the_warning_rides_along_with_the_next_reply(scripted):
    _spend(16.5)
    scripted(text="Tuesday is clear.")
    reply = say("what's on Tuesday")
    assert "Tuesday is clear" in reply.text
    assert "% of this month's budget used" in reply.text
