"""Replaying a recorded flow.

The model only steps in when the page has changed. That is decided by the
recipe's own expectations failing, not by a guess: each step says what must be
true afterwards, and a step whose expectation fails stops the replay and hands
back the index it stopped at, so the model resumes from there rather than
starting over.

Two properties this file exists to hold:

**Replay is not an approval.** Every step that would change something goes
through `tools.registry.call` exactly as a model-driven step does. A recipe
that ends in a payment still returns PENDING_APPROVAL on that step. If
replaying could execute a tier 3 action because "it was approved last time",
a recorded flow would be a way to launder an approval, which is precisely the
failure this whole system is built against.

**A secret never enters a step.** A step naming `secret_ref` has its value
fetched at replay time and passed to the executor, and the value is never
written back into the recipe, the audit row, or the reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from errand.policy.tiers import Tier
from errand.store import audit_store, recipes_store
from errand.store.recipes_store import EXPECT, FILL, GOTO, Recipe, Step

# Which step actions change something on the far side, and therefore are not
# merely reading a page.
MUTATING = {"submit", "click"}


class StepFailed(RuntimeError):
    """A step did not do what the recipe says it does."""

    def __init__(self, index: int, step: Step, message: str) -> None:
        super().__init__(message)
        self.index = index
        self.step = step
        self.message = message


class StepExecutor(Protocol):
    """Whatever actually drives the page.

    v1 fills this in with AgentCore Browser. It is a protocol so the replay
    engine is testable without one, and so the engine never holds a browser
    handle of its own.
    """

    def perform(self, step: Step, secret_value: str = "") -> str: ...

    def check(self, expectation: str) -> bool: ...


@dataclass
class ReplayResult:
    recipe: Recipe
    completed: int = 0
    total: int = 0
    stopped_at: int | None = None
    reason: str = ""
    pending_approval: bool = False
    performed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stopped_at is None and not self.pending_approval

    @property
    def needs_model(self) -> bool:
        """The page changed. Hand the rest to the model, from here."""
        return self.stopped_at is not None and not self.pending_approval

    def summary(self) -> str:
        if self.ok:
            return f"Replayed {self.completed}/{self.total} steps."
        if self.pending_approval:
            return f"Stopped at step {self.stopped_at + 1}: {self.reason}"
        return (
            f"Recipe stopped matching at step {self.stopped_at + 1} of {self.total}: "
            f"{self.reason}"
        )


def _resolve_secret(step: Step) -> str:
    from errand.common import secrets

    value = secrets.get_secret(step.secret_ref)
    if step.secret_key:
        if step.secret_key not in value:
            raise StepFailed(-1, step, f"{step.secret_ref} has no key {step.secret_key}")
        return str(value[step.secret_key])
    if isinstance(value, dict) and len(value) == 1:
        return str(next(iter(value.values())))
    raise StepFailed(-1, step, f"{step.secret_ref} needs a secret_key to pick a field")


def replay(
    task_id: str,
    recipe: Recipe,
    executor: StepExecutor,
    *,
    tier_of_submit: Tier = Tier.SEND,
) -> ReplayResult:
    """Run a recorded flow. Stops at the first step that does not behave.

    `tier_of_submit` is what a submitting step counts as for this task -- a
    cancellation is tier 4, a booking with a charge is tier 3. It is passed in
    rather than stored on the recipe, because the tier is a property of what
    the task is doing today, not of the steps recorded last time.
    """
    result = ReplayResult(recipe=recipe, total=len(recipe.steps))

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="RECIPE_REPLAY_STARTED",
        detail={
            "provider": recipe.provider,
            "intent": recipe.intent,
            "version": recipe.version,
            "steps": len(recipe.steps),
        },
    )

    for index, step in enumerate(recipe.steps):
        # A step that changes something is gated, every time. Being in a
        # recipe is not a permission.
        if step.action in MUTATING and _is_consequential(step):
            from errand.tools import registry

            gate_result = registry.call(
                task_id,
                "browser_submit",
                {"provider": recipe.provider, "intent": recipe.intent,
                 "selector": step.selector},
            )
            if gate_result.status != "OK":
                result.stopped_at = index
                result.pending_approval = gate_result.status == "PENDING_APPROVAL"
                result.reason = gate_result.message or gate_result.status
                _finish(task_id, recipe, result)
                return result

        try:
            secret_value = _resolve_secret(step) if step.needs_secret else ""
            observed = executor.perform(step, secret_value)

            if step.expect and not executor.check(step.expect):
                raise StepFailed(
                    index, step, f"expected {step.expect!r}, page did not match"
                )
        except StepFailed as exc:
            result.stopped_at = index
            result.reason = exc.message
            recipes_store.mark_failure(recipe, f"step {index + 1}: {exc.message}")
            _finish(task_id, recipe, result)
            return result
        except Exception as exc:  # noqa: BLE001
            result.stopped_at = index
            result.reason = f"{type(exc).__name__}: {exc}"
            recipes_store.mark_failure(recipe, f"step {index + 1}: {result.reason}")
            _finish(task_id, recipe, result)
            return result

        result.completed += 1
        # Never the value -- a filled password would otherwise land here.
        result.performed.append(step.describe())
        del observed

    recipes_store.mark_success(recipe)
    _finish(task_id, recipe, result)
    return result


def _is_consequential(step: Step) -> bool:
    """A submit always is. A click is only when the recipe says it commits
    something, which the recorder marks."""
    if step.action == "submit":
        return True
    return "commit" in (step.note or "").lower()


def _finish(task_id: str, recipe: Recipe, result: ReplayResult) -> None:
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="RECIPE_REPLAY_FINISHED",
        outcome="OK" if result.ok else ("PENDING_APPROVAL" if result.pending_approval
                                        else "STALE"),
        detail={
            "provider": recipe.provider,
            "intent": recipe.intent,
            "completed": result.completed,
            "total": result.total,
            "stopped_at": result.stopped_at,
            "reason": result.reason,
        },
        sequence=1,
    )


def record_from_run(
    task_id: str, provider: str, intent: str, steps: list[Step]
) -> Recipe:
    """Save the flow a model just drove successfully.

    Refuses a step carrying a literal that looks like a credential: the model
    saw a real password to type it, and the temptation to write it into the
    recipe is exactly what needs designing out rather than trusting.
    """
    for step in steps:
        step.validate()
        if step.action == FILL and step.value and _looks_secret(step):
            raise recipes_store.RecipeError(
                f"step {step.describe()} looks like a credential; "
                f"record it as a secret_ref instead of a literal"
            )

    recipe = recipes_store.record(provider, intent, steps)
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="RECIPE_RECORDED",
        outcome="OK",
        detail={"provider": provider, "intent": intent,
                "version": recipe.version, "steps": len(steps)},
    )
    return recipe


_SECRET_HINTS = ("password", "passwd", "secret", "token", "otp", "cvv", "pin", "ssn")


def _looks_secret(step: Step) -> bool:
    haystack = f"{step.selector} {step.note}".lower()
    return any(hint in haystack for hint in _SECRET_HINTS)


@dataclass
class RecordingExecutor:
    """Test double: performs nothing, answers expectations from a script."""

    expectations: dict[str, bool] = field(default_factory=dict)
    performed: list[tuple[str, str]] = field(default_factory=list)
    fail_on: int | None = None
    _count: int = 0

    def perform(self, step: Step, secret_value: str = "") -> str:
        self._count += 1
        if self.fail_on is not None and self._count == self.fail_on:
            raise StepFailed(self._count - 1, step, "element not found")
        # Records that a secret arrived, never what it was.
        self.performed.append((step.action, "<secret>" if secret_value else step.value))
        return "ok"

    def check(self, expectation: str) -> bool:
        return self.expectations.get(expectation, True)


__all__ = [
    "EXPECT",
    "FILL",
    "GOTO",
    "RecordingExecutor",
    "ReplayResult",
    "Step",
    "StepExecutor",
    "StepFailed",
    "record_from_run",
    "replay",
]
