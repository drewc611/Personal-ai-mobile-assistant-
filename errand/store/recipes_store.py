"""Recorded site flows.

PLAN.md v1 item 2: when a browser task succeeds, save the steps so the next
run replays them instead of asking a model to rediscover the page. Cancelling
the same gym twice should not depend on luck.

Two things are deliberately impossible here.

A step never holds a secret. A `fill` step that types a password stores
`secret_ref`, the name of a Secrets Manager entry, and the value is fetched at
replay time. A recipe is a durable, readable record of how to operate an
account; putting a password in one would be writing a credential to a database
under a different name.

A step never holds an amount or a recipient it can act on unilaterally.
Replaying is not an approval: `tools/recipes.py` runs each step through the
gate exactly as a model-driven run would.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from errand.common import clock, config, ids
from errand.store import backend as backend_mod

# Step actions. Deliberately small: anything not expressible here is a job for
# the model, and falling back to the model is the designed behaviour.
GOTO = "goto"
CLICK = "click"
FILL = "fill"
SELECT = "select"
EXPECT = "expect"
SUBMIT = "submit"

ACTIONS = (GOTO, CLICK, FILL, SELECT, EXPECT, SUBMIT)

HEALTHY = "HEALTHY"
STALE = "STALE"      # stopped matching the page
RETIRED = "RETIRED"  # failed enough times that we stopped trying

# Two consecutive failures is enough. A page that changed once will not change
# back, and retrying a broken recipe costs a browser session each time.
FAILURES_BEFORE_RETIRED = 2


class RecipeError(ValueError):
    """A recipe we refuse to store."""


@dataclass
class Step:
    action: str
    selector: str = ""
    value: str = ""
    # For a field that needs a credential: the name of the secret, never the
    # secret. Resolved at replay time.
    secret_ref: str = ""
    secret_key: str = ""
    # What must be true afterwards for the step to count as having worked.
    # This is how a stale recipe is detected rather than guessed at.
    expect: str = ""
    note: str = ""

    def validate(self) -> Step:
        if self.action not in ACTIONS:
            raise RecipeError(f"unknown step action {self.action!r}")
        if self.action in (CLICK, FILL, SELECT, EXPECT) and not self.selector:
            raise RecipeError(f"{self.action} needs a selector")
        if self.action == GOTO and not self.value:
            raise RecipeError("goto needs a url")
        if self.secret_ref and self.value:
            raise RecipeError(
                "a step carries either a literal value or a secret reference, never both"
            )
        if self.action == FILL and not (self.value or self.secret_ref):
            raise RecipeError("fill needs a value or a secret reference")
        return self

    @property
    def needs_secret(self) -> bool:
        return bool(self.secret_ref)

    def describe(self) -> str:
        """Readable, and never reveals a value that came from a secret."""
        if self.needs_secret:
            return f"{self.action} {self.selector} <from {self.secret_ref}>"
        if self.action == GOTO:
            return f"goto {self.value}"
        if self.value:
            return f"{self.action} {self.selector} = {self.value}"
        return f"{self.action} {self.selector}".strip()


@dataclass
class Recipe:
    recipe_id: str
    provider: str
    intent: str
    steps: list[Step] = field(default_factory=list)
    status: str = HEALTHY
    version: int = 1
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_ok: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_healthy(self) -> bool:
        return self.status == HEALTHY

    @property
    def key(self) -> str:
        return f"{self.provider.lower()}#{self.intent}"

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": f"provider#{self.provider.lower()}",
            "sk": f"intent#{self.intent}",
            "recipe_id": self.recipe_id,
            "provider": self.provider,
            "intent": self.intent,
            "steps": json.dumps([asdict(s) for s in self.steps]),
            "status": self.status,
            "version": self.version,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Recipe:
        raw = item.get("steps") or "[]"
        steps = json.loads(raw) if isinstance(raw, str) else list(raw)
        return cls(
            recipe_id=item["recipe_id"],
            provider=item["provider"],
            intent=item["intent"],
            steps=[Step(**s) for s in steps],
            status=item.get("status", HEALTHY),
            version=int(item.get("version", 1)),
            successes=int(item.get("successes", 0)),
            failures=int(item.get("failures", 0)),
            consecutive_failures=int(item.get("consecutive_failures", 0)),
            last_ok=item.get("last_ok", ""),
            last_error=item.get("last_error", ""),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
        )


def _table() -> str:
    return config.load().table("recipes")


def record(provider: str, intent: str, steps: list[Step]) -> Recipe:
    """Save a flow that just worked. Replaces any earlier one, bumping the
    version -- the page changed, so the old steps are not worth keeping."""
    if not steps:
        raise RecipeError("a recipe with no steps records nothing")
    for step in steps:
        step.validate()

    existing = get(provider, intent)
    recipe = Recipe(
        recipe_id=existing.recipe_id if existing else ids.new_approval_id().replace("ap_", "rx_"),
        provider=provider,
        intent=intent,
        steps=list(steps),
        status=HEALTHY,
        version=(existing.version + 1) if existing else 1,
        successes=existing.successes if existing else 0,
        failures=existing.failures if existing else 0,
        consecutive_failures=0,
        last_ok=clock.now_iso(),
        created_at=existing.created_at if existing else clock.now_iso(),
        updated_at=clock.now_iso(),
    )
    backend_mod.get_backend().put(_table(), recipe.to_item())
    return recipe


def get(provider: str, intent: str) -> Recipe | None:
    item = backend_mod.get_backend().get(
        _table(), f"provider#{provider.lower()}", f"intent#{intent}"
    )
    return Recipe.from_item(item) if item else None


def save(recipe: Recipe) -> Recipe:
    recipe.updated_at = clock.now_iso()
    backend_mod.get_backend().put(_table(), recipe.to_item())
    return recipe


def mark_success(recipe: Recipe) -> Recipe:
    recipe.successes += 1
    recipe.consecutive_failures = 0
    recipe.status = HEALTHY
    recipe.last_ok = clock.now_iso()
    recipe.last_error = ""
    return save(recipe)


def mark_failure(recipe: Recipe, error: str) -> Recipe:
    recipe.failures += 1
    recipe.consecutive_failures += 1
    recipe.last_error = error[:300]
    recipe.status = (
        RETIRED if recipe.consecutive_failures >= FAILURES_BEFORE_RETIRED else STALE
    )
    return save(recipe)


def for_provider(provider: str) -> list[Recipe]:
    rows = backend_mod.get_backend().query(_table(), f"provider#{provider.lower()}", "intent#")
    return [Recipe.from_item(r) for r in rows]


def all_recipes() -> list[Recipe]:
    rows = [
        r for r in backend_mod.get_backend().scan(_table())
        if str(r.get("sk", "")).startswith("intent#")
    ]
    return sorted((Recipe.from_item(r) for r in rows), key=lambda r: (r.provider, r.intent))
