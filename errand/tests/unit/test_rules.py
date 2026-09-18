"""Standing rules: what they can grant, and what they can never grant."""

from __future__ import annotations

import pytest

from errand.common import clock
from errand.dispatcher import conversation
from errand.policy import rules
from errand.policy.tiers import Tier
from errand.store import tasks_store
from errand.tests.conftest import FIXED_NOW
from errand.tools import registry


def _allow_email_rule():
    return rules.save(
        rules.Rule(
            rule_id="r_email",
            effect=rules.ALLOW,
            text="emails to my landlord go without asking",
            domain="email",
            tools=["gmail_send"],
            match={"to": "landlord"},
            max_tier=int(Tier.SEND),
        )
    )


def test_an_allow_rule_skips_the_prompt_but_not_the_undo_window(gmail):
    _allow_email_rule()
    task = tasks_store.create("email the landlord")
    draft = registry.call(
        task.task_id, "gmail_draft",
        {"to": "landlord@example.com", "subject": "Rent", "body": "Friday"},
    )

    result = registry.call(
        task.task_id, "gmail_send",
        {"draft_id": draft.data["draft_id"], "to": "landlord@example.com"},
    )

    assert result.status == "HELD"
    assert gmail.sent == []            # the window is still open

    clock.freeze(FIXED_NOW + 61)
    conversation.release_due()
    assert len(gmail.sent) == 1


def test_an_allow_rule_does_not_match_a_different_recipient(gmail):
    _allow_email_rule()
    task = tasks_store.create("email someone else")
    draft = registry.call(
        task.task_id, "gmail_draft", {"to": "boss@example.com", "subject": "s", "body": "b"}
    )

    result = registry.call(
        task.task_id, "gmail_send",
        {"draft_id": draft.data["draft_id"], "to": "boss@example.com"},
    )
    assert result.status == "PENDING_APPROVAL"


def test_a_deny_rule_beats_an_allow_rule(gmail):
    _allow_email_rule()
    rules.save(
        rules.Rule(
            rule_id="r_no_spirit",
            effect=rules.DENY,
            text="never Spirit",
            tools=["gmail_send"],
            match={"to": "spirit"},
        )
    )
    task = tasks_store.create("email Spirit")
    draft = registry.call(
        task.task_id, "gmail_draft",
        {"to": "landlord@example.com", "subject": "s", "body": "b"},
    )

    result = registry.call(
        task.task_id, "gmail_send",
        {"draft_id": draft.data["draft_id"], "to": "help@spirit.com"},
    )
    assert result.status == "DENIED"
    assert "never Spirit" in result.message
    assert gmail.sent == []


def test_a_deny_rule_stops_a_free_tier_call_too(gmail):
    """Tier 0 and 1 run without asking, but not without checking. "Never
    Spirit" should stop it drafting the Spirit email, not just sending it."""
    rules.save(
        rules.Rule(
            rule_id="r_no_spirit",
            effect=rules.DENY,
            text="never Spirit",
            match={"to": "spirit"},
        )
    )
    task = tasks_store.create("email Spirit")

    result = registry.call(
        task.task_id, "gmail_draft",
        {"to": "help@spirit.com", "subject": "s", "body": "b"},
    )
    assert result.status == "DENIED"
    assert gmail.drafts == {}


def test_a_rule_cannot_pre_approve_tier_four():
    with pytest.raises(rules.RuleError):
        rules.save(
            rules.Rule(
                rule_id="r_bad",
                effect=rules.ALLOW,
                text="let it cancel whatever it likes",
                max_tier=int(Tier.IRREVERSIBLE),
                max_amount_cents=100,
            )
        )


def test_a_spend_rule_needs_an_amount():
    with pytest.raises(rules.RuleError):
        rules.save(
            rules.Rule(
                rule_id="r_bad_spend",
                effect=rules.ALLOW,
                text="buy whatever",
                max_tier=int(Tier.SPEND),
            )
        )


def test_a_deny_rule_that_matches_everything_is_refused():
    with pytest.raises(rules.RuleError):
        rules.save(rules.Rule(rule_id="r_all", effect=rules.DENY, text="no"))


def test_a_constrain_rule_rewrites_arguments_before_the_gate_sees_them():
    rules.save(
        rules.Rule(
            rule_id="r_aisle",
            effect=rules.CONSTRAIN,
            text="always aisle seats",
            set_args={"seat": "aisle"},
        )
    )
    decision = rules.evaluate(
        tool="gmail_send", domain="email", tier=Tier.SEND,
        args={"seat": "window"}, amount_cents=None,
    )
    assert decision.args["seat"] == "aisle"


def test_a_constraint_cannot_steer_a_call_into_something_denied():
    rules.save(
        rules.Rule(
            rule_id="r_set_spirit",
            effect=rules.CONSTRAIN,
            text="prefer the cheapest carrier",
            set_args={"airline": "Spirit"},
        )
    )
    rules.save(
        rules.Rule(
            rule_id="r_never_spirit",
            effect=rules.DENY,
            text="never Spirit",
            match={"airline": "spirit"},
        )
    )
    decision = rules.evaluate(
        tool="gmail_send", domain="email", tier=Tier.SEND, args={}, amount_cents=None
    )
    assert decision.denied
    assert decision.denied_by.rule_id == "r_never_spirit"


def test_tighten_drops_allow_rules_and_keeps_restrictions(gmail):
    _allow_email_rule()
    rules.save(
        rules.Rule(rule_id="r_deny", effect=rules.DENY, text="never Spirit",
                   domain="email", match={"subject": "spirit"})
    )

    reply = conversation.handle("tighten email")
    assert "needs your approval again" in reply.text

    remaining = {r.rule_id for r in rules.active_rules()}
    assert remaining == {"r_deny"}


def test_tighten_a_quiet_domain_says_so():
    assert "No standing rules" in conversation.handle("tighten dining").text


def test_adding_a_rule_itself_needs_approval():
    task = tasks_store.create("set a rule")
    result = registry.call(
        task.task_id, "rule_add",
        {"effect": "allow", "text": "anything under $40 is fine", "max_amount_cents": 4000},
    )
    assert result.status == "PENDING_APPROVAL"
    assert rules.active_rules() == []


def test_rules_listing_reads_cleanly_on_a_phone():
    _allow_email_rule()
    text = conversation.handle("rules").text
    assert "landlord" in text
    assert len(text) < 300
