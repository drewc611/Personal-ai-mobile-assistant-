"""Task and approval identifiers.

Task ids are short on purpose: Andrew types them back over SMS. "T7 yes" has
to be typeable one-handed while walking.
"""

from __future__ import annotations

import re
import secrets
import string

TASK_ID_RE = re.compile(r"^[Tt](\d{1,6})$")
_ALPHABET = string.ascii_lowercase + string.digits


def format_task_id(number: int) -> str:
    if number < 1:
        raise ValueError("task numbers start at 1")
    return f"T{number}"


def parse_task_id(text: str) -> str | None:
    """Return the canonical task id ("T7") for a user-typed token, or None."""
    match = TASK_ID_RE.match(text.strip())
    if not match:
        return None
    number = int(match.group(1))
    if number < 1:
        return None
    return format_task_id(number)


def new_approval_id() -> str:
    return "ap_" + "".join(secrets.choice(_ALPHABET) for _ in range(12))


def new_run_id() -> str:
    return "run_" + "".join(secrets.choice(_ALPHABET) for _ in range(12))
