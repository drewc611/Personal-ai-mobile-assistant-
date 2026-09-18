"""Standing rules.

"Always book aisle seats, never Spirit, anything under $40 is fine" is a
policy the gate checks before it bothers Andrew. The rules are structured
records, not sentences a model reinterprets each time - a rule that means
something different on Tuesday is not a rule.

Three effects, evaluated in this order:

  DENY      refuse the call outright, whatever the tier
  CONSTRAIN force argument values before the call is considered
  ALLOW     skip the approval prompt for a call that would otherwise need one

A rule can never auto-approve tier 4. Cancelling an account or deleting data
is exactly the class of action a standing rule should not be able to
pre-authorise, however narrowly it is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from errand.common import clock, config
from errand.policy.tiers import Tier
from errand.store import backend as backend_mod

DENY = "deny"
CONSTRAIN = "constrain"
ALLOW = "allow"
EFFECTS = (DENY, CONSTRAIN, ALLOW)

MAX_AUTO_APPROVE_TIER = Tier.SPEND  # tier 4 is never rule-approvable


class RuleError(ValueError):
    """A rule that we refuse to store because it would be unsafe or ambiguous."""


@dataclass
class Rule:
    rule_id: str
    effect: str
    text: str                                   # what Andrew said, for display
    domain: str = "general"                     # "" or "general" matches any
    tools: list[str] = field(default_factory=list)   # empty matches any tool
    match: dict[str, str] = field(default_factory=dict)   # arg -> substring
    set_args: dict[str, Any] = field(default_factory=dict)
    max_amount_cents: int = 0                   # ALLOW only; 0 means no spend allowed
    max_tier: int = int(Tier.SEND)              # ALLOW only
    active: bool = True
    created_at: str = ""

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": "rules",
            "sk": f"rule#{self.rule_id}",
            "rule_id": self.rule_id,
            "effect": self.effect,
            "text": self.text,
            "domain": self.domain,
            "tools": json.dumps(self.tools),
            "match": json.dumps(self.match),
            "set_args": json.dumps(self.set_args),
            "max_amount_cents": self.max_amount_cents,
            "max_tier": self.max_tier,
            "active": self.active,
            "created_at": self.created_at or clock.now_iso(),
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Rule:
        def loads(key: str, fallback: Any) -> Any:
            raw = item.get(key)
            if isinstance(raw, str):
                return json.loads(raw)
            return raw if raw is not None else fallback

        return cls(
            rule_id=item["rule_id"],
            effect=item["effect"],
            text=item.get("text", ""),
            domain=item.get("domain", "general"),
            tools=loads("tools", []),
            match=loads("match", {}),
            set_args=loads("set_args", {}),
            max_amount_cents=int(item.get("max_amount_cents", 0)),
            max_tier=int(item.get("max_tier", int(Tier.SEND))),
            active=bool(item.get("active", True)),
            created_at=item.get("created_at", ""),
        )

    def applies_to(self, tool: str, domain: str, args: dict[str, Any]) -> bool:
        if not self.active:
            return False
        if self.tools and tool not in self.tools:
            return False
        if self.domain not in ("", "general") and self.domain != domain:
            return False
        for key, needle in self.match.items():
            value = args.get(key)
            if value is None or needle.lower() not in str(value).lower():
                return False
        return True


@dataclass
class RuleDecision:
    """What the rule set has to say about one call."""
    denied_by: Rule | None = None
    allowed_by: Rule | None = None
    constraints: list[Rule] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def denied(self) -> bool:
        return self.denied_by is not None

    @property
    def auto_approved(self) -> bool:
        return self.allowed_by is not None


def _table() -> str:
    return config.load().tasks_table


def validate(rule: Rule) -> Rule:
    if rule.effect not in EFFECTS:
        raise RuleError(f"unknown effect {rule.effect!r}")
    if rule.effect == ALLOW:
        if rule.max_tier > int(MAX_AUTO_APPROVE_TIER):
            raise RuleError("a standing rule cannot pre-approve tier 4 actions")
        if rule.max_tier >= int(Tier.SPEND) and rule.max_amount_cents <= 0:
            raise RuleError("a spend rule needs a max amount")
    if rule.effect == CONSTRAIN and not rule.set_args:
        raise RuleError("a constrain rule needs at least one argument to set")
    if rule.effect == DENY and not (rule.match or rule.tools or rule.domain not in ("", "general")):
        raise RuleError("a deny rule that matches everything is a kill switch, not a rule")
    return rule


def save(rule: Rule) -> Rule:
    validate(rule)
    backend_mod.get_backend().put(_table(), rule.to_item())
    return rule


def get(rule_id: str) -> Rule | None:
    item = backend_mod.get_backend().get(_table(), "rules", f"rule#{rule_id}")
    return Rule.from_item(item) if item else None


def active_rules() -> list[Rule]:
    rows = backend_mod.get_backend().query(_table(), "rules", "rule#")
    return [r for r in (Rule.from_item(row) for row in rows) if r.active]


def all_rules() -> list[Rule]:
    rows = backend_mod.get_backend().query(_table(), "rules", "rule#")
    return [Rule.from_item(row) for row in rows]


def deactivate(rule_id: str) -> Rule | None:
    rule = get(rule_id)
    if rule is None:
        return None
    rule.active = False
    backend_mod.get_backend().put(_table(), rule.to_item())
    return rule


def tighten(domain: str) -> list[Rule]:
    """"tighten dining" - drop every ALLOW rule for a domain, so that class of
    action needs approval again. Deny and constrain rules stay: tightening
    should never quietly remove a restriction."""
    tightened = []
    for rule in active_rules():
        if rule.effect == ALLOW and (domain in ("", "all") or rule.domain == domain):
            rule.active = False
            backend_mod.get_backend().put(_table(), rule.to_item())
            tightened.append(rule)
    return tightened


def evaluate(
    *,
    tool: str,
    domain: str,
    tier: Tier,
    args: dict[str, Any],
    amount_cents: int | None,
) -> RuleDecision:
    """Apply the rule set to one call. Pure apart from reading the store."""
    decision = RuleDecision(args=dict(args))
    rules = active_rules()

    for rule in rules:
        if rule.effect == DENY and rule.applies_to(tool, domain, decision.args):
            decision.denied_by = rule
            return decision

    for rule in rules:
        if rule.effect == CONSTRAIN and rule.applies_to(tool, domain, decision.args):
            decision.constraints.append(rule)
            decision.args.update(rule.set_args)

    # Re-check denies against the constrained arguments: a constraint must not
    # be able to steer a call into something a deny rule forbids.
    for rule in rules:
        if rule.effect == DENY and rule.applies_to(tool, domain, decision.args):
            decision.denied_by = rule
            return decision

    if tier in (Tier.READ, Tier.DRAFT):
        return decision

    for rule in rules:
        if rule.effect != ALLOW or not rule.applies_to(tool, domain, decision.args):
            continue
        if int(tier) > rule.max_tier or int(tier) > int(MAX_AUTO_APPROVE_TIER):
            continue
        if tier >= Tier.SPEND:
            if amount_cents is None or amount_cents <= 0:
                continue
            if amount_cents > rule.max_amount_cents:
                continue
        decision.allowed_by = rule
        return decision

    return decision
