"""Outbound SMS.

Two things worth knowing about SMS that shape this file. A message over 160
GSM-7 characters is billed and delivered as multiple segments, and carriers
reorder segments often enough that a long reply arrives scrambled. So replies
are split on line boundaries into numbered parts with a hard ceiling, and
anything over the ceiling is truncated rather than sent as a wall.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from errand.common import config

SEGMENT_CHARS = 300  # comfortably inside a 4-segment GSM-7 concatenation


class Sender(Protocol):
    def send(self, to: str, body: str) -> str: ...


@dataclass
class RecordingSender:
    """Used by tests and acceptance scripts. Every reply is inspectable."""

    messages: list[tuple[str, str]] = field(default_factory=list)

    def send(self, to: str, body: str) -> str:
        self.messages.append((to, body))
        return f"SM{len(self.messages):032d}"

    @property
    def bodies(self) -> list[str]:
        return [body for _, body in self.messages]

    def last(self) -> str:
        return self.messages[-1][1] if self.messages else ""


class TwilioSender:
    """Twilio REST. The auth token is fetched from Secrets Manager per cold
    start and never written to disk or an env file (hard rule 7)."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number

    def send(self, to: str, body: str) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = urllib.parse.urlencode({"To": to, "From": self._from, "Body": body}).encode()
        request = urllib.request.Request(url, data=data, method="POST")
        import base64

        basic = base64.b64encode(f"{self._sid}:{self._token}".encode()).decode()
        request.add_header("Authorization", f"Basic {basic}")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
        return payload.get("sid", "")


def split_message(body: str, *, segment_chars: int = SEGMENT_CHARS,
                  max_parts: int | None = None) -> list[str]:
    """Split a reply into numbered parts on line boundaries."""
    limit = max_parts or config.load().sms_segment_limit
    text = body.strip()
    if len(text) <= segment_chars:
        return [text] if text else []

    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= segment_chars - 8:  # room for the "(1/3) " prefix
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(line) > segment_chars - 8:
            parts.append(line[: segment_chars - 8])
            line = line[segment_chars - 8 :]
        current = line
    if current:
        parts.append(current)

    if len(parts) > limit:
        parts = parts[: limit - 1] + [
            parts[limit - 1][: segment_chars - 40].rstrip() + "\n...reply \"tasks\" for the rest."
        ]

    total = len(parts)
    return [f"({i}/{total}) {p}" for i, p in enumerate(parts, 1)]


_sender: Sender | None = None


def set_sender(sender: Sender | None) -> None:
    global _sender
    _sender = sender


def get_sender() -> Sender:
    global _sender
    if _sender is None:
        from errand.common import secrets

        cfg = config.load()
        creds = secrets.twilio_credentials()
        _sender = TwilioSender(creds["account_sid"], creds["auth_token"], cfg.twilio_from_number)
    return _sender


def reply(body: str, *, to: str = "") -> list[str]:
    """Send a reply, split into parts. Returns the message sids."""
    cfg = config.load()
    destination = to or cfg.owner_number
    if not destination:
        raise RuntimeError("no destination number configured")
    sender = get_sender()
    return [sender.send(destination, part) for part in split_message(body)]
