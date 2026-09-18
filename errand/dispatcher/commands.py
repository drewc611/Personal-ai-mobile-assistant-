"""Parsing what Andrew sends.

Command parsing is deliberately dumb and deterministic. Approving a send is
not a place for a model to interpret intent: a tap on Approve means approve
that one approval, and nothing else means it. Anything unrecognised falls
through to the agent as free text, which is the safe direction - an
unrecognised message starts a conversation, it does not authorise an action.

Button tokens have to fit in Telegram's 64-byte `callback_data`, so they are
`<verb>:<approval id>` and the rest is looked up server side.
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

# Button verbs, kept to one character so the token stays well inside 64 bytes.
VERBS = {
    "a": APPROVE,
    "r": REJECT,
    "e": EDIT,
    "c": CONFIRM,
    "u": UNDO,
}
VERB_FOR = {kind: verb for verb, kind in VERBS.items()}
APPROVE_ALL_TOKEN = "A:all"


@dataclass
class Command:
    kind: str
    task_id: str = ""
    approval_id: str = ""
    argument: str = ""
    amount_text: str = ""
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def button_token(kind: str, approval_id: str) -> str:
    """Build a callback token. Raises if it would not fit."""
    verb = VERB_FOR.get(kind)
    if verb is None:
        raise ValueError(f"no button verb for {kind!r}")
    token = f"{verb}:{approval_id}"
    if len(token.encode()) > 64:
        raise ValueError(f"callback token is {len(token.encode())} bytes, over Telegram's 64")
    return token


def parse_button(token: str) -> Command:
    """Parse a callback token from a tapped button."""
    if token == APPROVE_ALL_TOKEN:
        return Command(kind=APPROVE_ALL, raw=token)

    verb, _, approval_id = token.partition(":")
    kind = VERBS.get(verb)
    if kind is None or not approval_id:
        return Command(kind=IGNORE, raw=token)
    return Command(kind=kind, approval_id=approval_id, raw=token)


_YES = {"yes", "y", "ok", "okay", "go", "send", "approve", "do it", "confirm"}
_NO = {"no", "n", "nope", "cancel", "reject", "decline", "don't", "dont"}
_AMOUNT = re.compile(r"\$?\s*\d{1,7}(?:\.\d{1,2})?$")


def parse(text: str) -> Command:
    raw = (text or "").strip()
    collapsed = re.sub(r"\s+", " ", raw.lower()).strip()

    if not collapsed:
        return Command(kind=FREE_TEXT, raw=raw)

    # The kill switch is checked first and matched loosely. If Andrew is
    # typing STOP ALL he is in a hurry and should not have to get it exact.
    if collapsed.replace("!", "").replace(".", "").replace("/", "") in {
        "stop all", "stopall", "halt", "halt all", "stop everything", "abort all"
    }:
        return Command(kind=STOP_ALL, raw=raw)

    if collapsed == "undo":
        return Command(kind=UNDO, raw=raw)

    slash = _parse_slash(collapsed, raw)
    if slash is not None:
        return slash

    targeted = _parse_task_text(collapsed, raw)
    if targeted is not None:
        return targeted

    return Command(kind=FREE_TEXT, raw=raw)


def _parse_slash(collapsed: str, raw: str) -> Command | None:
    if not collapsed.startswith("/"):
        return None

    # Telegram appends @botname to commands in groups. Strip it.
    body = re.sub(r"^/([a-z_]+)(@\S+)?", r"/\1", collapsed)
    parts = body.split()
    command, args = parts[0], parts[1:]
    argument = " ".join(args).strip()

    if command in {"/tasks", "/task"}:
        return Command(kind=LIST_TASKS, raw=raw)
    if command in {"/pending", "/approvals"}:
        return Command(kind=PENDING, raw=raw)
    if command == "/rules":
        return Command(kind=RULES, raw=raw)
    if command == "/budget":
        return Command(kind=BUDGET, raw=raw)
    if command in {"/help", "/start"}:
        return Command(kind=HELP, raw=raw)
    if command == "/undo":
        return Command(kind=UNDO, task_id=ids.parse_task_id(argument) or "", raw=raw)
    if command == "/status":
        task_id = ids.parse_task_id(argument) if argument else None
        if task_id is None:
            # "/status" with nothing after it is the task list, not an error.
            return Command(kind=LIST_TASKS, raw=raw)
        return Command(kind=STATUS, task_id=task_id, raw=raw)
    if command == "/disconnect":
        target = argument.strip()
        if target in {"google", "gmail"}:
            return Command(kind=DISCONNECT, argument="gmail", raw=raw)
        if target == "calendar":
            return Command(kind=DISCONNECT, argument="calendar", raw=raw)
        return Command(kind=HELP, raw=raw)
    if command == "/tighten":
        if not argument:
            return Command(kind=HELP, raw=raw)
        return Command(kind=TIGHTEN, argument=argument.split()[0], raw=raw)
    if command in {"/stop", "/stopall"}:
        return Command(kind=STOP_ALL, raw=raw)

    return Command(kind=HELP, raw=raw)


def _parse_task_text(collapsed: str, raw: str) -> Command | None:
    """The typed forms: "T7 yes", "T7 yes $42.50", "T7 no", "T7 undo".

    Buttons are the intended path, but a button on an old message stops being
    tappable and typing is the fallback.
    """
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
