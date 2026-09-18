"""The Channel interface.

Every message in or out goes through this. Telegram is the only v0
implementation; Twilio SMS and voice arrive in v2 and should need no change to
the agent, the gate, or the conversation handler. The way to keep that true is
to make sure nothing outside `channels/` ever mentions Telegram.

`send_buttons` is the one method that is not obviously channel-agnostic.
Telegram renders it as an inline keyboard; SMS will render the same buttons as
"reply T7 yes". The caller supplies labels and tokens and does not care which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Button:
    """One action offered to Andrew.

    `token` is what comes back when he taps it. Telegram caps callback data at
    64 bytes, so it stays short and is resolved server side rather than
    carrying a description.
    """

    label: str
    token: str

    def __post_init__(self) -> None:
        if not self.token or len(self.token.encode()) > 64:
            raise ValueError(f"button token must be 1-64 bytes, got {len(self.token.encode())}")


@dataclass
class Inbound:
    """One message from Andrew, in whatever form the channel delivered it."""

    kind: str                       # "text" | "voice" | "button"
    text: str = ""
    token: str = ""                 # set when kind == "button"
    sender_id: str = ""
    message_id: str = ""
    update_id: str = ""
    voice_ref: str = ""             # a channel-specific handle for the audio
    raw: dict[str, Any] = field(default_factory=dict)


class Channel(Protocol):
    def receive(self, payload: dict[str, Any]) -> Inbound | None: ...

    def send_text(self, text: str) -> list[str]: ...

    def send_buttons(self, text: str, buttons: list[Button]) -> str: ...

    def send_file(self, path: str, caption: str = "") -> str: ...

    def acknowledge(self, inbound: Inbound, note: str = "") -> None: ...

    def retire_buttons(self, message_id: str) -> None: ...

    def fetch_voice(self, voice_ref: str) -> bytes: ...


# Hard rule 9: Telegram bot chats are not end to end encrypted. Nothing that
# would be damaging in a leaked chat log goes out over one.
_CARD = re.compile(r"\b(?:\d[ -]?){12,15}\d\b")
_OTP = re.compile(r"\b(?:code|otp|pin|2fa|verification code)\b[^\d]{0,12}(\d{4,8})\b", re.I)
_PASSWORD = re.compile(r"\b(?:password|passcode|passphrase)\b\s*[:=]\s*\S+", re.I)


def mask_sensitive(text: str) -> str:
    """Mask card numbers, one-time codes, and anything labelled a password.

    This is a backstop, not a licence. The rule is that Errand does not handle
    these at all; this catches the case where one arrives inside content it is
    summarising and would otherwise be repeated back into the chat.
    """

    def mask_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 13:
            return match.group(0)
        return f"card ending {digits[-4:]}"

    masked = _CARD.sub(mask_card, text)
    masked = _OTP.sub(lambda m: m.group(0).replace(m.group(1), "*" * len(m.group(1))), masked)
    masked = _PASSWORD.sub("password [not shown]", masked)
    return masked


def split_message(text: str, limit: int) -> list[str]:
    """Split on line boundaries so a long reply is readable in order."""
    body = text.strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]

    parts: list[str] = []
    current = ""
    for line in body.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        parts.append(current)
    return parts


@dataclass
class RecordingChannel:
    """The test double. Every message and button set is inspectable."""

    texts: list[str] = field(default_factory=list)
    button_messages: list[tuple[str, list[Button]]] = field(default_factory=list)
    files: list[tuple[str, str]] = field(default_factory=list)
    acknowledged: list[tuple[str, str]] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    voice_bytes: bytes = b"fake-audio"
    _seq: int = 0

    def receive(self, payload: dict[str, Any]) -> Inbound | None:
        return payload.get("inbound")

    def send_text(self, text: str) -> list[str]:
        sent = []
        for part in split_message(mask_sensitive(text), 3500):
            self._seq += 1
            self.texts.append(part)
            sent.append(str(self._seq))
        return sent

    def send_buttons(self, text: str, buttons: list[Button]) -> str:
        self._seq += 1
        self.button_messages.append((mask_sensitive(text), list(buttons)))
        return str(self._seq)

    def send_file(self, path: str, caption: str = "") -> str:
        self._seq += 1
        self.files.append((path, caption))
        return str(self._seq)

    def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        self.acknowledged.append((inbound.token, note))

    def retire_buttons(self, message_id: str) -> None:
        self.retired.append(message_id)

    def fetch_voice(self, voice_ref: str) -> bytes:
        return self.voice_bytes

    # Conveniences for tests
    @property
    def last_text(self) -> str:
        return self.texts[-1] if self.texts else ""

    @property
    def last_buttons(self) -> list[Button]:
        return self.button_messages[-1][1] if self.button_messages else []

    @property
    def all_output(self) -> str:
        return "\n".join(self.texts + [t for t, _ in self.button_messages])

    def tokens(self) -> list[str]:
        return [b.token for _, buttons in self.button_messages for b in buttons]


_channel: Channel | None = None


def set_channel(channel: Channel | None) -> None:
    global _channel
    _channel = channel


def get_channel() -> Channel:
    global _channel
    if _channel is None:
        from errand.channels import telegram

        _channel = telegram.build_from_secrets()
    return _channel
