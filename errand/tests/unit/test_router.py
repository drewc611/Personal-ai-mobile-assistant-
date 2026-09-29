"""Model routing: Haiku by default, Sonnet when the task earns it."""

from __future__ import annotations

import pytest

from errand.agent import router
from errand.common import config


def test_the_default_is_haiku():
    choice = router.choose(router.RouterState())
    assert choice.model_id == "test-haiku"
    assert choice.tier == router.DEFAULT


def test_three_tool_steps_escalates():
    state = router.RouterState()
    for _ in range(3):
        state.record_tool_call(failed=False)

    choice = router.choose(state)
    assert choice.tier == router.ESCALATED
    assert choice.model_id == "test-sonnet"
    assert "3 tool steps" in choice.reason


def test_two_tool_steps_does_not_escalate():
    state = router.RouterState()
    state.record_tool_call(failed=False)
    state.record_tool_call(failed=False)
    assert router.choose(state).tier == router.DEFAULT


def test_two_tool_failures_escalates():
    state = router.RouterState()
    state.record_tool_call(failed=True)
    state.record_tool_call(failed=True)

    choice = router.choose(state)
    assert choice.tier == router.ESCALATED
    assert "failed" in choice.reason


def test_one_failure_does_not_escalate():
    state = router.RouterState()
    state.record_tool_call(failed=True)
    assert router.choose(state).tier == router.DEFAULT


def test_a_plan_that_fails_validation_escalates():
    state = router.RouterState()
    state.record_invalid_plan()

    choice = router.choose(state)
    assert choice.tier == router.ESCALATED
    assert "validation" in choice.reason


def test_escalation_is_one_way():
    """Dropping back to the default mid-task would re-introduce whatever
    caused the escalation."""
    state = router.RouterState()
    state.record_invalid_plan()
    router.choose(state)

    state.plan_invalid = False
    assert router.choose(state).tier == router.ESCALATED


def test_the_escalation_reason_is_recorded_once():
    state = router.RouterState()
    state.record_tool_call(failed=True)
    state.record_tool_call(failed=True)
    router.choose(state)
    router.choose(state)
    assert len(state.reasons) == 1


def test_a_missing_model_id_raises_rather_than_defaulting(monkeypatch):
    """CLAUDE.md: never hardcode a model id. A missing one is an error, not a
    reason to fall back to something plausible."""
    monkeypatch.delenv("ERRAND_DEFAULT_MODEL_ID", raising=False)
    with pytest.raises(config.ConfigError):
        router.choose(router.RouterState())


def test_describe_safely_reports_missing_config_instead_of_raising(monkeypatch):
    monkeypatch.delenv("ERRAND_ESCALATION_MODEL_ID", raising=False)
    assert any("not configured" in line for line in router.describe_safely())
