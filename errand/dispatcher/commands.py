"""Parsing what Andrew texts.

Command parsing is deliberately dumb and deterministic. Approving a send is
not a place for a model to interpret intent: "T7 yes" means yes to T7 and
nothing else means yes to T7. Anything this parser does not recognise falls
through to the agent as free text, which is the safe direction - an
unrecognised message starts a conversation, it does not authorise an action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from errand.common import ids

# Kinds
FREE_TEXT = "free_text"
APPROVE = "approve"
DENY = "deny"
STATUS = "status"
LIST_TASKS = "list_tasks"
PENDING = "pending"
STOP_ALL = "stop_all"
UNDO = "undo"
DISCONNECT = "disconnect"
TIGHTEN = "tighten"
RULES = "rules"
HELP = "help"


@dataclass
class Command:
    kind: str
    task_id: str = ""
    indices: list[int] = field(default_factory=list)
    all_pending: bool = False
    amount_text: str = ""
    argument: str = ""
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


_YES = {"yes", "y", "ok", "okay", "go", "send", "do it", "confirm", "approve"}
_NO = {"no", "n", "nope", "stop", "cancel", "don't", "dont", "decline", "deny"}

_AMOUNT = re.compile(r"\$?\s*\d{1,7}(?:\.\d{1,2})?$")
_INDEX_LIST = re.compile(r"^\d+(?:\s*[,&]\s*\d+)*$")


def parse(text: str) -> Command:
    raw = (text or "").strip()
    lowered = raw.lower()
    collapsed = re.sub(r"\s+", " ", lowered).strip()

    if not collapsed:
        return Command(kind=FREE_TEXT, raw=raw)

    # The kill switch is checked first and matched loosely. If Andrew is
    # typing STOP ALL he is in a hurry and should not have to get it exact.
    if collapsed.replace("!", "").replace(".", "") in {
        "stop all", "stopall", "halt", "halt all", "stop everything", "abort all"
    }:
        return Command(kind=STOP_ALL, raw=raw)

    if collapsed in {"tasks", "task", "open", "open tasks", "list", "status"}:
        return Command(kind=LIST_TASKS, raw=raw)

    if collapsed in {"pending", "approvals", "waiting", "what's waiting", "whats waiting"}:
        return Command(kind=PENDING, raw=raw)

    if collapsed in {"rules", "my rules", "standing rules"}:
        return Command(kind=RULES, raw=raw)

    if collapsed in {"help", "?", "commands"}:
        return Command(kind=HELP, raw=raw)

    if collapsed == "undo":
        return Command(kind=UNDO, raw=raw)

    disconnect = re.match(r"^disconnect (gmail|calendar|google)$", collapsed)
    if disconnect:
        target = disconnect.group(1)
        return Command(kind=DISCONNECT, argument="gmail" if target == "google" else target, raw=raw)

    tighten = re.match(r"^tighten (?:up )?([a-z0-9_-]+)$", collapsed)
    if tighten:
        return Command(kind=TIGHTEN, argument=tighten.group(1), raw=raw)

    batched = _parse_batch(collapsed, raw)
    if batched is not None:
        return batched

    targeted = _parse_task_command(collapsed, raw)
    if targeted is not None:
        return targeted

    return Command(kind=FREE_TEXT, raw=raw)


def _parse_batch(collapsed: str, raw: str) -> Command | None:
    """"yes all", "yes 1,3", "no 2" - the morning batch."""
    match = re.match(r"^(yes|approve|ok|no|deny|decline)\s+(all|[\d,&\s]+)$", collapsed)
    if not match:
        return None
    verb, target = match.group(1), match.group(2).strip()
    kind = APPROVE if verb in {"yes", "approve", "ok"} else DENY

    if target == "all":
        return Command(kind=kind, all_pending=True, raw=raw)

    if not _INDEX_LIST.match(target):
        return None
    indices = sorted({int(part) for part in re.split(r"[,&\s]+", target) if part})
    if not indices or any(i < 1 for i in indices):
        return None
    return Command(kind=kind, indices=indices, raw=raw)


def _parse_task_command(collapsed: str, raw: str) -> Command | None:
    """"T7 yes", "T7 yes $42.50", "T7 no", "T7 status", "T7 undo"."""
    match = re.match(r"^(t\d{1,6})\b\s*(.*)$", collapsed)
    if not match:
        return None

    task_id = ids.parse_task_id(match.group(1))
    if task_id is None:
        return None
    rest = match.group(2).strip()

    if rest in {"", "status", "?"}:
        return Command(kind=STATUS, task_id=task_id, raw=raw)

    if rest == "undo":
        return Command(kind=UNDO, task_id=task_id, raw=raw)

    words = rest.split()
    head = words[0]
    tail = " ".join(words[1:]).strip()

    if rest in _YES or head in _YES:
        if tail and _AMOUNT.match(tail):
            return Command(kind=APPROVE, task_id=task_id, amount_text=tail, raw=raw)
        if tail:
            # "T7 yes but change the subject" is not an approval. The stored
            # arguments are what was approved, and this message changes them.
            return Command(kind=FREE_TEXT, task_id=task_id, raw=raw)
        return Command(kind=APPROVE, task_id=task_id, raw=raw)

    if rest in _NO or head in _NO:
        return Command(kind=DENY, task_id=task_id, raw=raw)

    return Command(kind=FREE_TEXT, task_id=task_id, raw=raw)
