"""Durable monotonic counters.

Two things in Errand need numbers that only ever go up and never repeat: task
ids, which Andrew types back, and the approval sequence that the numbered
"yes 1,3" list is ordered by. Both are read and written by concurrent Lambdas,
so the increment is a compare-and-set against a claim marker rather than a
read-then-write.
"""

from __future__ import annotations

from errand.store import backend as backend_mod

MAX_ATTEMPTS = 32


def next_value(table: str, name: str) -> int:
    """Allocate the next value of a named counter.

    The claim row is what makes this safe: two callers can read the same
    current value, but only one of them can create `claim#<name>#<n>`, so only
    one of them returns n.
    """
    backend = backend_mod.get_backend()
    counter_pk = "meta#counter"

    for _ in range(MAX_ATTEMPTS):
        current = backend.get(table, counter_pk, name)
        value = int(current["value"]) if current else 0
        candidate = value + 1

        if backend.put_if_absent(table, {"pk": "meta#claim", "sk": f"{name}#{candidate}"}):
            backend.put(table, {"pk": counter_pk, "sk": name, "value": candidate})
            return candidate

    raise RuntimeError(f"could not allocate a value for counter {name!r}")
