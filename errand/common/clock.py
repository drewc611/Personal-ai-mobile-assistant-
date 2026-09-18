"""One source of time so tests can freeze it."""

from __future__ import annotations

import datetime as _dt

_override: float | None = None


def now() -> float:
    """Unix seconds."""
    if _override is not None:
        return _override
    return _dt.datetime.now(tz=_dt.UTC).timestamp()


def now_iso() -> str:
    return _dt.datetime.fromtimestamp(now(), tz=_dt.UTC).isoformat()


def freeze(timestamp: float) -> None:
    global _override
    _override = timestamp


def unfreeze() -> None:
    global _override
    _override = None
