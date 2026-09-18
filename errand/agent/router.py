"""Model routing.

Haiku by default, Sonnet when the task turns out to be harder than it looked.
PLAN.md gives three escalation triggers and they are all evidence from this
task rather than guesses about it:

  - three or more tool steps: a job that needs that many calls has structure
    worth planning properly
  - Haiku's plan failed validation: it produced something the tool layer
    would not accept
  - a tool call failed twice: the first failure is a fact about the world, the
    second is a fact about the plan

Escalation is one-way within a task. Dropping back to Haiku mid-task after
escalating would re-introduce whatever caused the escalation.

Model ids come from config. CLAUDE.md is explicit that they are looked up in
the Bedrock console and never hardcoded, so there is no fallback here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from errand.common import config

DEFAULT = "default"
ESCALATED = "escalated"

TOOL_STEP_THRESHOLD = 3
TOOL_FAILURE_THRESHOLD = 2


@dataclass
class RouterState:
    """What this task has done so far. The planner updates it as it goes."""

    tool_steps: int = 0
    tool_failures: int = 0
    plan_invalid: bool = False
    escalated: bool = False
    reasons: list[str] = field(default_factory=list)

    def record_tool_call(self, *, failed: bool) -> None:
        self.tool_steps += 1
        if failed:
            self.tool_failures += 1

    def record_invalid_plan(self) -> None:
        self.plan_invalid = True


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    tier: str
    temperature: float
    max_tokens: int
    reason: str


def should_escalate(state: RouterState) -> str:
    """The reason to escalate, or "" to stay on the default model."""
    if state.escalated:
        return "already escalated"
    if state.plan_invalid:
        return "the first plan failed validation"
    if state.tool_failures >= TOOL_FAILURE_THRESHOLD:
        return f"{state.tool_failures} tool calls failed"
    if state.tool_steps >= TOOL_STEP_THRESHOLD:
        return f"{state.tool_steps} tool steps"
    return ""


def choose(state: RouterState | None = None) -> ModelChoice:
    state = state or RouterState()
    reason = should_escalate(state)

    if reason:
        if not state.escalated:
            state.escalated = True
            state.reasons.append(reason)
        return ModelChoice(
            model_id=config.escalation_model_id(),
            tier=ESCALATED,
            temperature=0.3,
            max_tokens=1600,
            reason=reason,
        )

    return ModelChoice(
        model_id=config.default_model_id(),
        tier=DEFAULT,
        temperature=0.2,
        max_tokens=900,
        reason="default",
    )


def describe() -> list[str]:
    """What `/budget` says about routing."""
    return [
        f"default: {config.default_model_id()}",
        f"escalation: {config.escalation_model_id()}",
        f"escalates at {TOOL_STEP_THRESHOLD} tool steps, "
        f"{TOOL_FAILURE_THRESHOLD} tool failures, or a plan that fails validation",
    ]


def describe_safely() -> list[str]:
    """Same, but a missing model id is reported rather than raised.

    /budget is the command Andrew reaches for when something looks wrong, so
    it is the last command that should fail because configuration is missing.
    """
    try:
        return describe()
    except config.ConfigError as exc:
        return [f"Model routing is not configured: {exc}"]
