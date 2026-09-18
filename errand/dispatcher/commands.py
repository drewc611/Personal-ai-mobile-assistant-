"""Parsing what Andrew texts.

Command parsing is deliberately dumb and deterministic. Approving a send is
not a place for a model to interpret intent: "T7 yes" means yes to T7 and
nothing else means yes to T7. Anything unrecognised falls through to the agent
as free text, which is the safe direction - an unrecognised message starts a
conversation, it does not authorise an action.

Two ways in, one meaning. Over SMS Andrew types; over a channel with tappable
buttons a token comes back instead. Both land on the same approval record, and
`parse_button` exists now so that adding such a channel is a new file in
`channels/` rather than a second command language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from errand.common import ids

# Kinds
FREE_TEXT = "free_text"
APPROVE = "approve"
APPROVE_ALL = "approve_all"
REJECT = "reject"
EDIT = "edit"
CONFIRM = "confirm"
UNDO = "undo"
STATUS = "status"
LIST_TASKS = "list_tasks"
PENDING = "pending"
RULES = "rules"
BUDGET = "budget"
DISCONNECT = "disconnect"
TIGHTEN = "tighten"
STOP_ALL = "stop_all"
HELP = "help"
IGNORE = "ignore"

# Button verbs, one character so a token stays far inside the 64-byte limit
# the tightest channel imposes.
VERBS = {"a": APPROVE, "r": REJECT, "e": EDIT, "c": CONFIRM, "u": UNDO}
VERB_FOR = {kind: verb for verb, kind in VERBS.items()}
APPROVE_ALL_TOKEN = "A:all"


@dataclass
class Command:
    kind: str
    task_id: str = ""
    approval_id: str = ""
    indices: list[int] = field(default_factory=list)
    all_pending: bool = False
    argument: str = ""
    amount_text: str = ""
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def button_token(kind: str, approval_id: str) -> str:
    verb = VERB_FOR.get(kind)
    if verb is None:
        raise ValueError(f"no button verb for {kind!r}")
    token = f"{verb}:{approval_id}"
    if len(token.encode()) > 64:
        raise ValueError(f"callback token is {len(token.encode())} bytes, over the 64 limit")
    return token


def parse_button(token: str) -> Command:
    if token == APPROVE_ALL_TOKEN:
        return Command(kind=APPROVE_ALL, all_pending=True, raw=token)
    verb, _, approval_id = token.partition(":")
    kind = VERBS.get(verb)
    if kind is None or not approval_id:
        return Command(kind=IGNORE, raw=token)
    return Command(kind=kind, approval_id=approval_id, raw=token)


_YES = {"yes", "y", "ok", "okay", "go", "send", "approve", "do it", "confirm"}
_NO = {"no", "n", "nope", "cancel", "reject", "decline", "don't", "dont"}
_AMOUNT = re.compile(r"\$?\s*\d{1,7}(?:\.\d{1,2})?$")
_INDEX_LIST = re.compile(r"^\d+(?:\s*[,&]\s*\d+)*$")

_WORD_COMMANDS = {
    LIST_TASKS: {"tasks", "task", "open", "open tasks", "list"},
    PENDING: {"pending", "approvals", "waiting", "whats waiting", "what's waiting"},
    RULES: {"rules", "my rules", "standing rules"},
    BUDGET: {"budget", "spend", "cost"},
    HELP: {"help", "?", "commands"},
}


def parse(text: str) -> Command:
    raw = (text or "").strip()
    collapsed = re.sub(r"\s+", " ", raw.lower()).strip()

    if not collapsed:
        return Command(kind=FREE_TEXT, raw=raw)

    # The kill switch is checked first and matched loosely. If Andrew is
    # typing STOP ALL he is in a hurry and should not have to get it exact.
    if collapsed.replace("!", "").replace(".", "").lstrip("/") in {
        "stop all", "stopall", "halt", "halt all", "stop everything", "abort all", "stop"
    }:
        return Command(kind=STOP_ALL, raw=raw)

    # Slash forms are accepted as aliases. They cost nothing and some phones
    # autocomplete them.
    bare = collapsed.lstrip("/")
    bare = re.sub(r"^([a-z_]+)@\S+", r"\1", bare)

    for kind, words in _WORD_COMMANDS.items():
        if bare in words:
            return Command(kind=kind, raw=raw)

    if bare == "undo":
        return Command(kind=UNDO, raw=raw)

    status = re.match(r"^status\s+(\S+)$", bare)
    if status:
        task_id = ids.parse_task_id(status.group(1))
        if task_id:
            return Command(kind=STATUS, task_id=task_id, raw=raw)
    if bare == "status":
        return Command(kind=LIST_TASKS, raw=raw)

    undo = re.match(r"^undo\s+(\S+)$", bare)
    if undo:
        return Command(kind=UNDO, task_id=ids.parse_task_id(undo.group(1)) or "", raw=raw)

    disconnect = re.match(r"^disconnect\s+(gmail|calendar|google)$", bare)
    if disconnect:
        target = disconnect.group(1)
        return Command(kind=DISCONNECT, argument="gmail" if target == "google" else target, raw=raw)

    tighten = re.match(r"^tighten\s+(?:up\s+)?([a-z0-9_-]+)$", bare)
    if tighten:
        return Command(kind=TIGHTEN, argument=tighten.group(1), raw=raw)

    batched = _parse_batch(bare, raw)
    if batched is not None:
        return batched

    targeted = _parse_task_text(collapsed, raw)
    if targeted is not None:
        return targeted

    return Command(kind=FREE_TEXT, raw=raw)


def _parse_batch(bare: str, raw: str) -> Command | None:
    """"yes all", "yes 1,3", "no 2" - the morning batch over SMS.

    This is what a channel with buttons does with an "Approve all" button, and
    it is the reason `pending` prints a numbered list: the numbers Andrew
    replies with have to be the numbers he was shown.
    """
    match = re.match(r"^(yes|approve|ok|no|deny|decline|reject)\s+(all|[\d,&\s]+)$", bare)
    if not match:
        return None

    verb, target = match.group(1), match.group(2).strip()
    kind = APPROVE if verb in {"yes", "approve", "ok"} else REJECT

    if target == "all":
        return Command(kind=APPROVE_ALL if kind == APPROVE else kind,
                       all_pending=True, raw=raw)

    if not _INDEX_LIST.match(target):
        return None
    indices = sorted({int(part) for part in re.split(r"[,&\s]+", target) if part})
    if not indices or any(i < 1 for i in indices):
        return None
    return Command(kind=kind, indices=indices, raw=raw)


def _parse_task_text(collapsed: str, raw: str) -> Command | None:
    """"T7 yes", "T7 yes $42.50", "T7 no", "T7 status", "T7 undo", "T7 edit"."""
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
    if rest == "edit":
        return Command(kind=EDIT, task_id=task_id, raw=raw)

    words = rest.split()
    head, tail = words[0], " ".join(words[1:]).strip()

    if rest in _YES or head in _YES:
        if tail and _AMOUNT.match(tail):
            return Command(kind=APPROVE, task_id=task_id, amount_text=tail, raw=raw)
        if tail:
            # "T7 yes but change the subject" is not an approval. The stored
            # arguments are what was approved, and this message changes them.
            return Command(kind=FREE_TEXT, task_id=task_id, raw=raw)
        return Command(kind=APPROVE, task_id=task_id, raw=raw)

    if rest in _NO or head in _NO:
        return Command(kind=REJECT, task_id=task_id, raw=raw)

    return Command(kind=FREE_TEXT, task_id=task_id, raw=raw)
