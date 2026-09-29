"""API first, browser last."""

from __future__ import annotations

import pytest

from errand.tools import strategy
from errand.tools.strategy import Approach, Capability


@pytest.fixture(autouse=True)
def _clean_registry():
    strategy.reset_for_tests()
    yield
    strategy.reset_for_tests()


def test_an_official_api_wins():
    strategy.register(Capability(provider="gymco", api_tool="gmail_send"))
    plan = strategy.resolve("gymco", "cancel")
    assert plan.chosen is Approach.API


def test_email_beats_a_recipe():
    strategy.register(Capability(provider="gymco", email_address="support@gymco.example"))
    plan = strategy.resolve("gymco", "cancel", has_recipe=True)
    assert plan.chosen is Approach.EMAIL


def test_a_healthy_recipe_beats_the_browser():
    plan = strategy.resolve("gymco", "cancel", has_recipe=True, recipe_is_healthy=True)
    assert plan.chosen is Approach.RECIPE


def test_a_stale_recipe_falls_through_to_the_browser():
    plan = strategy.resolve("gymco", "cancel", has_recipe=True, recipe_is_healthy=False)
    assert plan.chosen is Approach.BROWSER
    assert "stopped matching" in plan.reason or "stopped matching" in plan.explain()


def test_the_browser_is_the_last_resort():
    plan = strategy.resolve("unknown-co", "cancel")
    assert plan.chosen is Approach.BROWSER
    assert plan.reason == "nothing cheaper is available"


def test_an_api_that_does_not_cover_the_intent_is_skipped():
    strategy.register(
        Capability(provider="gymco", api_tool="gmail_send", intents=("book",))
    )
    plan = strategy.resolve("gymco", "cancel")
    assert plan.chosen is not Approach.API


def test_a_capability_naming_an_unregistered_tool_is_a_config_error_not_a_silent_skip():
    """Otherwise a typo in a capability quietly downgrades every task for that
    provider to the browser, and nothing says why."""
    strategy.register(Capability(provider="gymco", api_tool="not_a_real_tool"))
    plan = strategy.resolve("gymco", "cancel")

    assert plan.chosen is Approach.BROWSER
    assert any("not registered" in s.reason for s in plan.considered)


def test_email_can_be_limited_to_certain_intents():
    strategy.register(
        Capability(
            provider="gymco",
            email_address="support@gymco.example",
            email_intents=("cancel",),
        )
    )
    assert strategy.resolve("gymco", "cancel").chosen is Approach.EMAIL
    assert strategy.resolve("gymco", "book").chosen is Approach.BROWSER


def test_no_browser_and_nothing_else_raises_rather_than_inventing_a_route():
    with pytest.raises(strategy.NoApproach):
        strategy.resolve("unknown-co", "cancel", browser_available=False)


def test_the_plan_explains_itself_for_the_audit_row():
    strategy.register(Capability(provider="gymco", email_address="support@gymco.example"))
    text = strategy.resolve("gymco", "cancel").explain()

    assert "Using the email" in text
    assert "Skipped" in text and "official API" in text


def test_every_considered_approach_is_recorded():
    plan = strategy.resolve("unknown-co", "cancel")
    assert [s.approach for s in plan.considered] == [
        Approach.API, Approach.EMAIL, Approach.RECIPE, Approach.BROWSER
    ]
