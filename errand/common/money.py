"""Money handling for tier 3.

Dollars never touch a float. Everything is integer cents, and the only way in
is a string Andrew actually typed.
"""

from __future__ import annotations

import re

_AMOUNT_RE = re.compile(r"^\$?\s*(\d{1,7})(?:\.(\d{1,2}))?$")


class AmountError(ValueError):
    """The text is not an amount we are willing to act on."""


def parse_amount(text: str) -> int:
    """Parse "$42.50" / "42.5" / "42" into cents. Raises AmountError."""
    match = _AMOUNT_RE.match(text.strip())
    if not match:
        raise AmountError(f"not an amount: {text!r}")
    whole, frac = match.group(1), match.group(2) or "0"
    return int(whole) * 100 + int(frac.ljust(2, "0"))


def format_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"
