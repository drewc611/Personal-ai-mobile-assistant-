"""Recorded flows: replay, staleness, and the two things that must stay true.

A recipe is a durable record of how to operate one of Andrew's accounts. The
two ways that goes wrong are it becoming a place credentials live, and it
becoming a way to skip an approval because "this was approved last time".
Both have tests here rather than comments.
"""

from __future__ import annotations

import pytest

from errand.common import secrets
from errand.policy import approvals
from errand.policy.tiers import Tier, classify
from errand.store import audit_store, recipes_store, tasks_store
from errand.store.recipes_store import CLICK, FILL, GOTO, SUBMIT, Step
from errand.tools import recipes
from errand.tools.recipes import RecordingExecutor


def _login_steps():
    return [
        Step(action=GOTO, value="https://gymco.example/login"),
        Step(action=FILL, selector="#email", value="andrew@example.com"),
        Step(action=FILL, selector="#password", secret_ref="errand/gymco",
             secret_key="password", note="password field"),
        Step(action=CLICK, selector="#signin", expect="dashboard"),
    ]


def _cancel_steps():
    return _login_steps() + [
        Step(action=GOTO, value="https://gymco.example/membership"),
        Step(action=CLICK, selector="#cancel", expect="confirm-dialog"),
        Step(action=SUBMIT, selector="#confirm-cancel", note="commits the cancellation"),
    ]


# ------------------------------------------------------------- recording


def test_a_flow_is_recorded_and_read_back():
    recipes_store.record("gymco", "cancel", _login_steps())
    recipe = recipes_store.get("gymco", "cancel")

    assert recipe is not None
    assert len(recipe.steps) == 4
    assert recipe.version == 1
    assert recipe.is_healthy


def test_recording_again_bumps_the_version():
    recipes_store.record("gymco", "cancel", _login_steps())
    again = recipes_store.record("gymco", "cancel", _login_steps()[:2])
    assert again.version == 2
    assert len(again.steps) == 2


def test_an_empty_recipe_is_refused():
    with pytest.raises(recipes_store.RecipeError):
        recipes_store.record("gymco", "cancel", [])


def test_a_step_with_no_selector_is_refused():
    with pytest.raises(recipes_store.RecipeError):
        recipes_store.record("gymco", "cancel", [Step(action=CLICK)])


def test_a_step_cannot_carry_both_a_literal_and_a_secret():
    with pytest.raises(recipes_store.RecipeError):
        Step(action=FILL, selector="#p", value="hunter2",
             secret_ref="errand/gymco").validate()


# --------------------------------------------------- secrets stay out of it


def test_a_credential_typed_as_a_literal_is_refused_when_recording():
    """The model saw a real password in order to type it. Writing it into the
    recipe is the obvious next step and the one that must not be possible."""
    steps = [Step(action=FILL, selector="#password", value="hunter2seasonal")]
    with pytest.raises(recipes_store.RecipeError) as exc:
        recipes.record_from_run("T1", "gymco", "cancel", steps)
    assert "secret_ref" in str(exc.value)


def test_a_secret_step_never_describes_its_value():
    step = Step(action=FILL, selector="#password", secret_ref="errand/gymco",
                secret_key="password")
    assert "hunter2" not in step.describe()
    assert "errand/gymco" in step.describe()


def test_the_secret_is_fetched_at_replay_and_not_written_anywhere():
    secrets.set_cached("errand/gymco", {"password": "hunter2seasonal"})
    recipe = recipes_store.record("gymco", "login", _login_steps())
    executor = RecordingExecutor()

    result = recipes.replay("T1", recipe, executor)

    assert result.ok
    # The executor was handed the secret, but nothing recorded the value.
    assert ("fill", "<secret>") in executor.performed
    blob = "".join(result.performed) + "".join(str(r) for r in audit_store.for_task("T1"))
    assert "hunter2seasonal" not in blob


def test_a_missing_secret_key_stops_the_replay_rather_than_guessing():
    secrets.set_cached("errand/gymco", {"username": "andrew", "password": "x"})
    steps = [Step(action=FILL, selector="#p", secret_ref="errand/gymco",
                  secret_key="not_there")]
    recipe = recipes_store.record("gymco", "login", steps)

    result = recipes.replay("T1", recipe, RecordingExecutor())
    assert not result.ok
    assert "no key" in result.reason


# ------------------------------------------------------------- replay


def test_a_clean_replay_runs_every_step():
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})

    result = recipes.replay("T1", recipe, RecordingExecutor())

    assert result.ok
    assert result.completed == 4
    assert recipes_store.get("gymco", "login").successes == 1


def test_a_failed_expectation_stops_and_says_where():
    """This is how a changed page is detected -- not by guessing."""
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})
    executor = RecordingExecutor(expectations={"dashboard": False})

    result = recipes.replay("T1", recipe, executor)

    assert result.needs_model
    assert result.stopped_at == 3
    assert "did not match" in result.reason
    assert "step 4" in result.summary()


def test_a_stale_recipe_is_marked_and_then_retired():
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})
    executor = RecordingExecutor(expectations={"dashboard": False})

    recipes.replay("T1", recipe, executor)
    assert recipes_store.get("gymco", "login").status == recipes_store.STALE

    recipes.replay("T2", recipes_store.get("gymco", "login"), executor)
    assert recipes_store.get("gymco", "login").status == recipes_store.RETIRED


def test_a_success_clears_the_failure_streak():
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})

    recipes.replay("T1", recipe, RecordingExecutor(expectations={"dashboard": False}))
    assert recipes_store.get("gymco", "login").consecutive_failures == 1

    recipes.replay("T2", recipes_store.get("gymco", "login"), RecordingExecutor())
    fresh = recipes_store.get("gymco", "login")
    assert fresh.consecutive_failures == 0
    assert fresh.is_healthy


def test_a_missing_element_stops_the_replay():
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})

    result = recipes.replay("T1", recipe, RecordingExecutor(fail_on=2))
    assert result.needs_model
    assert result.stopped_at == 1


def test_replay_is_audited_before_and_after():
    recipe = recipes_store.record("gymco", "login", _login_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})
    recipes.replay("T1", recipe, RecordingExecutor())

    events = [r["event"] for r in audit_store.for_task("T1")]
    assert "RECIPE_REPLAY_STARTED" in events
    assert "RECIPE_REPLAY_FINISHED" in events


# ------------------------------------- replaying is not an approval


def test_a_replayed_submit_still_needs_approval():
    """The whole point. A recorded flow that ends in a cancellation does not
    get to cancel because it was approved the last time it ran."""
    task = tasks_store.create("cancel the gym")
    recipe = recipes_store.record("gymco", "cancel", _cancel_steps())
    secrets.set_cached("errand/gymco", {"password": "x"})

    result = recipes.replay(task.task_id, recipe, RecordingExecutor())

    assert result.pending_approval
    assert not result.ok
    assert approvals.open_for_task(task.task_id), "the submit should be waiting on Andrew"


def test_a_cancellation_submit_is_tier_four():
    assert classify("browser_submit", {"provider": "gymco", "intent": "cancel"}) == (
        Tier.IRREVERSIBLE
    )


def test_a_submit_with_a_charge_is_tier_three():
    assert classify(
        "browser_submit", {"provider": "resto", "intent": "book", "amount_cents": 4200}
    ) == Tier.SPEND


def test_an_ordinary_submit_is_tier_two():
    assert classify(
        "browser_submit", {"provider": "gymco", "intent": "update_address"}
    ) == Tier.SEND


def test_an_unreadable_amount_counts_as_spend_rather_than_free():
    assert classify(
        "browser_submit", {"provider": "resto", "intent": "book", "amount_cents": "lots"}
    ) == Tier.SPEND


def test_the_approval_prompt_names_what_it_is_about_to_do():
    task = tasks_store.create("book a table")
    from errand.tools import registry

    result = registry.call(
        task.task_id,
        "browser_submit",
        {"provider": "resto", "intent": "book", "amount_cents": 4200},
    )
    assert result.status == "PENDING_APPROVAL"
    assert "resto" in result.message
    assert "$42.00" in result.message


def test_the_browser_refuses_cleanly_because_it_does_not_exist_yet():
    """Registered so the gate works; unimplemented because v1 is not built.
    The gate must be the thing that stops it, not the missing browser."""
    from errand.tools import browser

    with pytest.raises(NotImplementedError):
        browser.browser_submit(provider="gymco", intent="cancel")
