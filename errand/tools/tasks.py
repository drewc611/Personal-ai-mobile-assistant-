"""Task tools and the standing-rule tool.

`rule_add` is tier 2 on purpose. A standing rule hands out authority for
actions that have not happened yet, so it needs the same yes that a single
send needs. A model that could quietly write "allow everything under $5,000"
has just written itself a blank cheque.
"""

from __future__ import annotations

from typing import Any

from errand.common import clock, ids
from errand.policy import rules as rules_mod
from errand.policy.tiers import Tier
from errand.store import tasks_store
from errand.tools.registry import tool


@tool("task_note", tier=Tier.READ, summary="Add a note to the task thread")
def task_note(task_id: str, note: str) -> dict[str, Any]:
    task = tasks_store.get(task_id)
    if task is None:
        raise ValueError(f"no task {task_id}")
    task.notes.append(f"{clock.now_iso()} {note[:280]}")
    tasks_store.save(task)
    return {"task_id": task_id, "notes": len(task.notes)}


@tool("task_list", tier=Tier.READ, summary="List open tasks")
def task_list() -> dict[str, Any]:
    tasks = tasks_store.open_tasks()
    return {
        "count": len(tasks),
        "tasks": [
            {"task_id": t.task_id, "title": t.title, "status": t.status} for t in tasks
        ],
    }


@tool("rule_list", tier=Tier.READ, summary="List standing rules")
def rule_list() -> dict[str, Any]:
    active = rules_mod.active_rules()
    return {
        "count": len(active),
        "rules": [
            {"rule_id": r.rule_id, "effect": r.effect, "text": r.text, "domain": r.domain}
            for r in active
        ],
    }


@tool(
    "rule_add",
    tier=Tier.SEND,
    summary="Add a standing rule",
    describe=lambda a: f"add the standing rule: {a.get('text', '')}",
)
def rule_add(
    effect: str,
    text: str,
    domain: str = "general",
    tools: list[str] | None = None,
    match: dict[str, str] | None = None,
    set_args: dict[str, Any] | None = None,
    max_amount_cents: int = 0,
    max_tier: int = int(Tier.SEND),
) -> dict[str, Any]:
    rule = rules_mod.Rule(
        rule_id=ids.new_approval_id().replace("ap_", "r_"),
        effect=effect,
        text=text[:200],
        domain=domain,
        tools=list(tools or []),
        match=dict(match or {}),
        set_args=dict(set_args or {}),
        max_amount_cents=int(max_amount_cents),
        max_tier=int(max_tier),
    )
    rules_mod.save(rule)
    return {"rule_id": rule.rule_id, "effect": rule.effect, "text": rule.text}
