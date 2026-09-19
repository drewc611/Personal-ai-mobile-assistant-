"""API first, browser last.

PLAN.md v1 item 1. Driving a browser is the least reliable way to do anything:
pages change, sessions expire, and every extra page is another chance to meet
a CAPTCHA. So a task picks the most boring route that can work, and the
browser is what is left when nothing else can.

The order is fixed and the reason is recorded, so "why did it use the browser
for this?" has an answer in the audit log rather than a shrug.

    API      an official endpoint exists and is connected
    EMAIL    the provider accepts the request in writing
    RECIPE   a previously recorded flow that still replays
    BROWSER  the model drives the page, one step at a time

This module chooses. It does not execute: every approach still calls tools
through the gate, so choosing BROWSER does not widen what may happen without
an approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Approach(IntEnum):
    """Lower is preferred. The ordering is the policy."""

    API = 0
    EMAIL = 1
    RECIPE = 2
    BROWSER = 3


APPROACH_NAMES = {
    Approach.API: "official API",
    Approach.EMAIL: "email",
    Approach.RECIPE: "recorded recipe",
    Approach.BROWSER: "browser",
}


@dataclass(frozen=True)
class Capability:
    """What is known to be possible with one provider.

    `api_tool` names a registered tool. It is a name rather than a callable so
    that a capability cannot smuggle in a function that skips the registry.
    """

    provider: str
    intents: tuple[str, ...] = ()
    api_tool: str = ""
    email_address: str = ""
    # Some providers accept a written cancellation but not a written booking.
    email_intents: tuple[str, ...] = ()
    notes: str = ""

    def handles(self, intent: str) -> bool:
        return not self.intents or intent in self.intents


@dataclass
class Step:
    """One considered approach, and why it was or was not taken."""

    approach: Approach
    available: bool
    reason: str
    detail: str = ""

    @property
    def name(self) -> str:
        return APPROACH_NAMES[self.approach]


@dataclass
class Plan:
    provider: str
    intent: str
    chosen: Approach
    considered: list[Step] = field(default_factory=list)

    @property
    def reason(self) -> str:
        for step in self.considered:
            if step.approach == self.chosen:
                return step.reason
        return ""

    def explain(self) -> str:
        """One line for the task thread, and for the audit row."""
        skipped = [f"{s.name} ({s.reason})" for s in self.considered if not s.available]
        line = f"Using the {APPROACH_NAMES[self.chosen]}: {self.reason}."
        if skipped:
            line += " Skipped " + "; ".join(skipped) + "."
        return line


_CAPABILITIES: dict[str, Capability] = {}


def register(capability: Capability) -> Capability:
    _CAPABILITIES[capability.provider.lower()] = capability
    return capability


def capability_for(provider: str) -> Capability | None:
    return _CAPABILITIES.get(provider.lower())


def known_providers() -> list[str]:
    return sorted(_CAPABILITIES)


def reset_for_tests() -> None:
    _CAPABILITIES.clear()


def resolve(
    provider: str,
    intent: str,
    *,
    has_recipe: bool = False,
    recipe_is_healthy: bool = True,
    browser_available: bool = True,
) -> Plan:
    """Pick the approach. Pure: the caller supplies what it knows."""
    from errand.policy.tiers import UnknownTool, spec_for

    capability = capability_for(provider)
    considered: list[Step] = []

    # 1. An official API.
    if capability is None:
        considered.append(
            Step(Approach.API, False, "no capability recorded for this provider")
        )
    elif not capability.api_tool:
        considered.append(Step(Approach.API, False, "provider has no API we can use"))
    elif not capability.handles(intent):
        considered.append(
            Step(Approach.API, False, f"API does not cover {intent}")
        )
    else:
        try:
            spec_for(capability.api_tool)
        except UnknownTool:
            # A capability naming a tool that is not registered is a
            # configuration error, not a reason to fall through silently.
            considered.append(
                Step(Approach.API, False,
                     f"tool {capability.api_tool} is not registered")
            )
        else:
            considered.append(
                Step(Approach.API, True, "an official API covers this",
                     detail=capability.api_tool)
            )
            return Plan(provider, intent, Approach.API, considered)

    # 2. Ask in writing. Slower than an API, far more reliable than a page.
    if capability is not None and capability.email_address and (
        not capability.email_intents or intent in capability.email_intents
    ):
        considered.append(
            Step(Approach.EMAIL, True, "the provider accepts this in writing",
                 detail=capability.email_address)
        )
        return Plan(provider, intent, Approach.EMAIL, considered)
    considered.append(Step(Approach.EMAIL, False, "no written route for this"))

    # 3. A recipe we recorded earlier.
    if has_recipe and recipe_is_healthy:
        considered.append(
            Step(Approach.RECIPE, True, "a recorded flow for this still replays")
        )
        return Plan(provider, intent, Approach.RECIPE, considered)
    if has_recipe:
        considered.append(
            Step(Approach.RECIPE, False, "the recorded flow stopped matching the page")
        )
    else:
        considered.append(Step(Approach.RECIPE, False, "nothing recorded yet"))

    # 4. The browser, which is where we did not want to end up.
    if not browser_available:
        considered.append(Step(Approach.BROWSER, False, "browser is not configured"))
        raise NoApproach(
            f"Nothing can do {intent} at {provider}: "
            + "; ".join(f"{s.name} — {s.reason}" for s in considered)
        )

    considered.append(
        Step(Approach.BROWSER, True, "nothing cheaper is available")
    )
    return Plan(provider, intent, Approach.BROWSER, considered)


class NoApproach(RuntimeError):
    """Every route is closed. Better to say so than to invent one."""
