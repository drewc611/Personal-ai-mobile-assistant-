"""Telegram implementation of the Channel interface.

Two details from the Bot API shape this file:

  - `callback_data` is 1 to 64 bytes. Exceeding it fails the whole
    sendMessage with BUTTON_DATA_INVALID, so tokens stay short and carry an
    id rather than a description.
  - After a button press, the Telegram client shows a spinner until the bot
    calls `answerCallbackQuery`. Answering is not optional politeness; a bot
    that skips it looks hung.

Nothing outside this file knows Telegram exists.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from errand.channels.base import Button, Inbound, mask_sensitive, split_message
from errand.common import config

API_ROOT = "https://api.telegram.org"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class TelegramError(RuntimeError):
    """A Bot API call failed."""


class TelegramChannel:
    def __init__(self, bot_token: str, chat_id: str, char_limit: int = 3500) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._limit = char_limit

    # ------------------------------------------------------------- inbound

    def receive(self, payload: dict[str, Any]) -> Inbound | None:
        """Turn one Update into an Inbound, or None if it is nothing we handle."""
        update_id = str(payload.get("update_id", ""))

        callback = payload.get("callback_query")
        if isinstance(callback, dict):
            message = callback.get("message") or {}
            return Inbound(
                kind="button",
                token=str(callback.get("data", "")),
                sender_id=str((callback.get("from") or {}).get("id", "")),
                message_id=str(message.get("message_id", "")),
                update_id=update_id,
                raw={"callback_query_id": str(callback.get("id", ""))},
            )

        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict):
            return None

        sender_id = str((message.get("from") or {}).get("id", ""))
        message_id = str(message.get("message_id", ""))

        voice = message.get("voice") or message.get("audio")
        if isinstance(voice, dict) and voice.get("file_id"):
            return Inbound(
                kind="voice",
                text=str(message.get("caption", "")),
                sender_id=sender_id,
                message_id=message_id,
                update_id=update_id,
                voice_ref=str(voice["file_id"]),
                raw={"duration": voice.get("duration"), "mime_type": voice.get("mime_type", "")},
            )

        text = message.get("text") or message.get("caption")
        if not text:
            return None
        return Inbound(
            kind="text",
            text=str(text),
            sender_id=sender_id,
            message_id=message_id,
            update_id=update_id,
        )

    # ------------------------------------------------------------ outbound

    def send_text(self, text: str) -> list[str]:
        sent = []
        for part in split_message(mask_sensitive(text), self._limit):
            response = self._call("sendMessage", {"chat_id": self._chat_id, "text": part})
            sent.append(str(response.get("message_id", "")))
        return sent

    def send_buttons(self, text: str, buttons: list[Button]) -> str:
        keyboard = [
            [{"text": button.label, "callback_data": button.token}]
            for button in buttons
        ]
        response = self._call(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": mask_sensitive(text)[: self._limit],
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )
        return str(response.get("message_id", ""))

    def send_file(self, path: str, caption: str = "") -> str:
        """Used from v1 for receipts and confirmation screenshots."""
        with open(path, "rb") as handle:
            data = handle.read()
        boundary = "errandboundary"
        filename = path.rsplit("/", 1)[-1]
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n"
            f"{self._chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n"
            f"{mask_sensitive(caption)}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
            f"filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode(),
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(
            f"{API_ROOT}/bot{self._token}/sendDocument", data=body, method="POST"
        )
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        payload = self._open(request)
        return str(payload.get("message_id", ""))

    def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        """Stop the spinner on the button Andrew just pressed."""
        callback_id = inbound.raw.get("callback_query_id")
        if not callback_id:
            return
        body: dict[str, Any] = {"callback_query_id": callback_id}
        if note:
            body["text"] = note[:200]
        self._call("answerCallbackQuery", body)

    def retire_buttons(self, message_id: str) -> None:
        """Take the buttons off a message that has been acted on.

        Without this, Approve stays tappable after the send has gone, which
        invites a second tap on something that already happened.
        """
        if not message_id:
            return
        self._call(
            "editMessageReplyMarkup",
            {"chat_id": self._chat_id, "message_id": message_id,
             "reply_markup": {"inline_keyboard": []}},
        )

    def fetch_voice(self, voice_ref: str) -> bytes:
        info = self._call("getFile", {"file_id": voice_ref})
        file_path = info.get("file_path")
        if not file_path:
            raise TelegramError("getFile returned no file_path")
        url = f"{API_ROOT}/file/bot{self._token}/{file_path}"
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read(20 * 1024 * 1024)

    # -------------------------------------------------------------- plumbing

    def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            f"{API_ROOT}/bot{self._token}/{method}", data=data, method="POST"
        )
        request.add_header("Content-Type", "application/json")
        return self._open(request)

    def _open(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise TelegramError(f"{exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"could not reach Telegram: {exc.reason}") from exc
        if not payload.get("ok"):
            raise TelegramError(str(payload.get("description", "unknown error")))
        return payload.get("result", {})


def build_from_secrets() -> TelegramChannel:
    from errand.common import secrets

    cfg = config.load()
    creds = secrets.telegram_credentials()
    return TelegramChannel(
        bot_token=creds["bot_token"],
        chat_id=cfg.owner_telegram_id,
        char_limit=cfg.message_char_limit,
    )
