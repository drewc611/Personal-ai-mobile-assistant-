"""Action tiers and the tool registry.

The tier of a call is decided here, from the tool name and its arguments -
never from anything the model says about its own intent. A model that writes
"this is only a draft" in its reasoning still gets tier 2 when it calls
gmail_send.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Tier(IntEnum):
    READ = 0
    DRAFT = 1
    SEND = 2
    SPEND = 3
    IRREVERSIBLE = 4


TIER_NAMES = {
    Tier.READ: "read",
    Tier.DRAFT: "draft",
    Tier.SEND: "send",
    Tier.SPEND: "spend",
    Tier.IRREVERSIBLE: "irreversible",
}

# Tiers 0 and 1 run freely. Everything at 2 and above needs "T# yes".
FREE_TIERS = {Tier.READ, Tier.DRAFT}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    summary: str
    # Optional escalation: a tool can be pushed higher by its own arguments.
    # A calendar delete of a single event is tier 2; a recurring series is 4.
    escalate: Callable[[dict[str, Any]], Tier | None] | None = None
    # Which standing-rule domain this tool belongs to ("dining", "email", ...).
    domain: str = "general"


class UnknownTool(KeyError):
    """A tool that is not in the registry. Unknown means refused, not allowed."""


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool already registered: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def spec_for(name: str) -> ToolSpec:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownTool(name) from exc


def all_specs() -> list[ToolSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.tier, s.name))


def classify(name: str, args: dict[str, Any]) -> Tier:
    """The tier for this specific call."""
    spec = spec_for(name)
    tier = spec.tier
    if spec.escalate is not None:
        escalated = spec.escalate(args)
        if escalated is not None and escalated > tier:
            return escalated
    return tier


def reset_registry_for_tests() -> None:
    _REGISTRY.clear()
