"""The Channel interface.

Every message in or out goes through this. Twilio SMS is the only v0
implementation; WhatsApp is the same Twilio API with a prefix on the number,
and a richer channel with real tappable buttons would be another
implementation and no change to the agent, the gate, or the conversation
handler. The way to keep that true is that nothing outside `channels/`
mentions Twilio.

`Button` is the piece that makes a channel-agnostic approval possible. It
carries two things: a `token`, for a channel that can render something
tappable, and a `reply`, which is the text Andrew sends instead on a channel
that cannot. SMS uses the second. Neither is ever trusted on its own - a
tapped token and a typed reply both land on the same approval record, and the
gate does not care which arrived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Button:
    """One action offered to Andrew.

    `token` is what comes back from a tap. It stays under 64 bytes because
    that is the tightest limit among the channels this will plug into, and a
    token that fits everywhere is one less thing to rediscover later.
    """

    label: str
    token: str
    reply: str = ""          # what to type on a channel without buttons

    def __post_init__(self) -> None:
        if not self.token or len(self.token.encode()) > 64:
            raise ValueError(f"button token must be 1-64 bytes, got {len(self.token.encode())}")

    @property
    def hint(self) -> str:
        """How a text-only channel offers this."""
        return self.reply or self.label


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

    def send_file(self, url: str, caption: str = "") -> str: ...

    def acknowledge(self, inbound: Inbound, note: str = "") -> None: ...

    def retire_buttons(self, message_id: str) -> None: ...

    def fetch_voice(self, voice_ref: str) -> bytes: ...


# Hard rule 9: SMS is not encrypted end to end, and a carrier, a phone on a
# table, and a lock screen preview are all places these end up. Nothing that
# would be damaging in a leaked message goes out over one.
_CARD = re.compile(r"\b(?:\d[ -]?){12,15}\d\b")
_OTP = re.compile(r"\b(?:code|otp|pin|2fa|verification code)\b[^\d]{0,12}(\d{4,8})\b", re.I)
_PASSWORD = re.compile(r"\b(?:password|passcode|passphrase)\b\s*[:=]\s*\S+", re.I)


def mask_sensitive(text: str) -> str:
    """Mask card numbers, one-time codes, and anything labelled a password.

    This is a backstop, not a licence. The rule is that Errand does not handle
    these at all; this catches the case where one arrives inside content it is
    summarising and would otherwise be repeated back into the thread.
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


def render_buttons(text: str, buttons: list[Button]) -> str:
    """Turn a set of buttons into the line a text-only channel sends.

    Deduplicated and ordered, because "Reply T7 yes to send, T7 no to drop" is
    read once at a glance and a repeated hint makes it ambiguous.
    """
    hints: list[str] = []
    for button in buttons:
        hint = button.hint
        if hint and hint not in hints:
            hints.append(hint)
    if not hints:
        return text
    quoted = ", ".join(f'"{h}"' for h in hints)
    return f"{text}\nReply {quoted}.".strip()


def split_message(text: str, limit: int, max_parts: int) -> list[str]:
    """Split a reply into numbered parts on line boundaries.

    Carriers reorder concatenated segments often enough that a long reply
    arrives scrambled, so parts are numbered and there is a hard ceiling on
    how many go out at once.
    """
    body = text.strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]

    room = limit - 8  # space for the "(1/3) " prefix
    parts: list[str] = []
    current = ""
    for line in body.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= room:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(line) > room:
            parts.append(line[:room])
            line = line[room:]
        current = line
    if current:
        parts.append(current)

    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + [
            parts[max_parts - 1][: room - 40].rstrip() + '\n...reply "tasks" for the rest.'
        ]

    total = len(parts)
    if total == 1:
        return parts
    return [f"({i}/{total}) {p}" for i, p in enumerate(parts, 1)]


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
        for part in split_message(mask_sensitive(text), 300, 4):
            self._seq += 1
            self.texts.append(part)
            sent.append(str(self._seq))
        return sent

    def send_buttons(self, text: str, buttons: list[Button]) -> str:
        self._seq += 1
        self.button_messages.append((mask_sensitive(text), list(buttons)))
        self.send_text(render_buttons(text, buttons))
        return str(self._seq)

    def send_file(self, url: str, caption: str = "") -> str:
        self._seq += 1
        self.files.append((url, caption))
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
    def all_output(self) -> str:
        return "\n".join(self.texts)


_channel: Channel | None = None


def set_channel(channel: Channel | None) -> None:
    global _channel
    _channel = channel


def get_channel() -> Channel:
    global _channel
    if _channel is None:
        from errand.channels import twilio

        _channel = twilio.build_from_secrets()
    return _channel
