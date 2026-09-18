"""Twilio implementation of the Channel interface.

SMS has no buttons, so `send_buttons` renders the hints as a line of text:
"Reply "T7 yes", "T7 no"." Everything above this file builds the same Button
objects either way, which is what makes WhatsApp (the same API with a prefix
on the number) or a richer channel later a new file rather than a rewrite.

Signature validation lives here too, next to the auth token it needs, so the
webhook Lambda imports one function rather than reimplementing HMAC.

Nothing outside this file knows Twilio exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from errand.channels.base import Button, Inbound, mask_sensitive, render_buttons, split_message
from errand.common import config

API_ROOT = "https://api.twilio.com/2010-04-01"
SIGNATURE_HEADER = "X-Twilio-Signature"
MAX_MEDIA_BYTES = 8 * 1024 * 1024


class TwilioError(RuntimeError):
    """A Twilio REST call failed."""


# --------------------------------------------------------------------------
# Signature validation (hard rule 5)
# --------------------------------------------------------------------------


def expected_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Twilio signs the full request URL concatenated with every POST
    parameter, sorted by name, HMAC-SHA1 with the account auth token."""
    payload = url
    for key in sorted(params):
        payload += key + str(params[key])
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def signature_is_valid(
    auth_token: str, url: str, params: Mapping[str, str], signature: str
) -> bool:
    """Two details that are easy to get wrong behind API Gateway, and both of
    which turn into a webhook that accepts anything:

    The URL must be the one Twilio called, scheme and host included, exactly
    as configured on the number. Rebuilding it from Lambda event fields gives
    the internal hostname and every signature fails, so it is configured.

    The comparison must be constant time. A fast reject leaks the signature
    one byte at a time.
    """
    if not signature or not auth_token or not url:
        return False
    return hmac.compare_digest(expected_signature(auth_token, url, params), signature)


# --------------------------------------------------------------------------
# The channel
# --------------------------------------------------------------------------


class TwilioChannel:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        segment_chars: int = 300,
        max_segments: int = 4,
    ) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._to = to_number
        self._limit = segment_chars
        self._max_parts = max_segments

    # ------------------------------------------------------------- inbound

    def receive(self, payload: dict[str, Any]) -> Inbound | None:
        """Turn Twilio's form-encoded webhook body into an Inbound."""
        sender = normalise_number(str(payload.get("From", "")))
        message_sid = str(payload.get("MessageSid", "") or payload.get("SmsSid", ""))
        body = str(payload.get("Body", "") or "").strip()

        media = _media(payload)
        audio = [m for m in media if str(m.get("content_type", "")).startswith("audio/")]
        if audio and not body:
            return Inbound(
                kind="voice",
                sender_id=sender,
                message_id=message_sid,
                update_id=message_sid,
                voice_ref=audio[0]["url"],
                raw={"mime_type": audio[0].get("content_type", "audio/mpeg")},
            )

        if not body:
            return None

        return Inbound(
            kind="text",
            text=body,
            sender_id=sender,
            message_id=message_sid,
            update_id=message_sid,
            raw={"media": len(media)},
        )

    # ------------------------------------------------------------ outbound

    def send_text(self, text: str) -> list[str]:
        parts = split_message(mask_sensitive(text), self._limit, self._max_parts)
        return [self._send(part) for part in parts]

    def send_buttons(self, text: str, buttons: list[Button]) -> str:
        """SMS has nothing tappable, so the buttons become a reply hint."""
        sids = self.send_text(render_buttons(text, buttons))
        return sids[0] if sids else ""

    def send_file(self, url: str, caption: str = "") -> str:
        """MMS. Used from v1 for receipts and confirmation screenshots; the
        file has to already be reachable by URL, which is what the presigned
        S3 link is for."""
        return self._send(mask_sensitive(caption), media_url=url)

    def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        """Nothing to acknowledge on SMS - there is no spinner to stop. A
        channel with real buttons overrides this."""
        return None

    def retire_buttons(self, message_id: str) -> None:
        """Nothing to retire on SMS. An old message stays on the phone, which
        is why an already-decided approval is refused by the approval record
        rather than by taking the option away."""
        return None

    def fetch_voice(self, voice_ref: str) -> bytes:
        """Twilio media needs the account credentials; a media URL alone
        should not be enough, and on a correctly configured account it is
        not."""
        request = urllib.request.Request(voice_ref)
        request.add_header("Authorization", f"Basic {self._basic()}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read(MAX_MEDIA_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise TwilioError(f"could not fetch the memo: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise TwilioError(f"could not fetch the memo: {exc.reason}") from exc

    def delete_media(self, voice_ref: str) -> bool:
        """Once the audio is in Andrew's own bucket, Twilio should not keep a
        copy. Best effort: a failure here is worth logging, not worth failing
        the task over."""
        request = urllib.request.Request(voice_ref, method="DELETE")
        request.add_header("Authorization", f"Basic {self._basic()}")
        try:
            urllib.request.urlopen(request, timeout=15).close()
            return True
        except (urllib.error.HTTPError, urllib.error.URLError):
            return False

    # -------------------------------------------------------------- plumbing

    def _basic(self) -> str:
        return base64.b64encode(f"{self._sid}:{self._token}".encode()).decode()

    def _send(self, body: str, media_url: str = "") -> str:
        fields = {"To": self._to, "From": self._from, "Body": body}
        if media_url:
            fields["MediaUrl"] = media_url
        data = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(
            f"{API_ROOT}/Accounts/{self._sid}/Messages.json", data=data, method="POST"
        )
        request.add_header("Authorization", f"Basic {self._basic()}")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise TwilioError(f"{exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TwilioError(f"could not reach Twilio: {exc.reason}") from exc
        return str(payload.get("sid", ""))


def normalise_number(raw: str) -> str:
    """E.164, tolerant of how a number was typed or displayed."""
    import re

    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits and not digits.startswith("+"):
        digits = "+" + digits
    return digits


def _media(params: dict[str, Any]) -> list[dict[str, str]]:
    """Voice memos arrive as MediaUrl0 / MediaContentType0.

    NumMedia arrives signed by Twilio, but a signed request is not the same as
    a well-formed one, so a value that is not a number means no media rather
    than a 500.
    """
    try:
        count = int(params.get("NumMedia", "0") or 0)
    except (TypeError, ValueError):
        return []

    media = []
    for index in range(min(count, 5)):
        url = str(params.get(f"MediaUrl{index}", "") or "")
        if url:
            media.append(
                {"url": url, "content_type": str(params.get(f"MediaContentType{index}", "") or "")}
            )
    return media


def build_from_secrets() -> TwilioChannel:
    from errand.common import secrets

    cfg = config.load()
    creds = secrets.twilio_credentials()
    return TwilioChannel(
        account_sid=creds["account_sid"],
        auth_token=creds["auth_token"],
        from_number=cfg.twilio_from_number,
        to_number=cfg.owner_number,
        segment_chars=cfg.segment_chars,
        max_segments=cfg.max_segments,
    )
