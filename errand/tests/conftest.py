"""Test wiring.

Every test runs against the memory backend, fake providers, and a recording
channel, with a frozen clock. Nothing here touches AWS or Twilio, which is
deliberate: the properties these tests check - a send never happens without an
approval, an unknown number never gets a reply - are properties of this code,
and a test that needs credentials to prove them is a test nobody runs.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ERRAND_BACKEND", "memory")
os.environ.setdefault("ERRAND_CHANNEL", "telegram")
os.environ.setdefault("ERRAND_OWNER_NUMBER", "+15555550123")
os.environ.setdefault("ERRAND_OWNER_TELEGRAM_ID", "8675309")
os.environ.setdefault("ERRAND_TWILIO_FROM", "+15555550999")
os.environ.setdefault("ERRAND_WEBHOOK_URL", "https://errand.example.com/sms")
os.environ.setdefault("ERRAND_DEFAULT_MODEL_ID", "test-haiku")
os.environ.setdefault("ERRAND_ESCALATION_MODEL_ID", "test-sonnet")
os.environ.setdefault("ERRAND_READER_MODEL_ID", "test-reader")
os.environ.setdefault("ERRAND_UNDO_SECONDS", "60")
os.environ.setdefault("ERRAND_APPROVAL_TTL_SECONDS", "3600")
os.environ.setdefault("ERRAND_TIER3_CAP_CENTS", "15000")
os.environ.setdefault("ERRAND_MONTHLY_BUDGET_USD", "20")
os.environ.setdefault(
    "ERRAND_MODEL_RATES",
    json.dumps({
        "test-haiku": {"in": 1.0, "out": 5.0},
        "test-sonnet": {"in": 3.0, "out": 15.0},
        "test-reader": {"in": 1.0, "out": 5.0},
    }),
)

import errand.tools  # noqa: E402,F401  registers every tool
from errand.agent import planner as planner_mod  # noqa: E402
from errand.channels import base as channels  # noqa: E402
from errand.common import clock, secrets  # noqa: E402
from errand.dispatcher import voice  # noqa: E402
from errand.reader import quarantine  # noqa: E402
from errand.store import backend as backend_mod  # noqa: E402
from errand.tools import providers  # noqa: E402

FIXED_NOW = 1_764_500_000.0  # 2025-11-30T12:13:20Z
OWNER_NUMBER = "+15555550123"
AUTH_TOKEN = "test_auth_token"
OWNER_TELEGRAM_ID = "8675309"
TELEGRAM_SECRET = "a-long-enough-webhook-secret-value-32+"
WEBHOOK_URL = "https://errand.example.com/sms"


@pytest.fixture(autouse=True)
def _isolate():
    backend_mod.set_backend(backend_mod.MemoryBackend())
    clock.freeze(FIXED_NOW)

    secrets.clear_cache()
    secrets.set_cached(
        "errand/twilio", {"account_sid": "AC_test", "auth_token": AUTH_TOKEN}
    )
    secrets.set_cached(
        "errand/telegram",
        {"bot_token": "test-bot-token", "webhook_secret": TELEGRAM_SECRET},
    )

    providers.set_providers(providers.build_fake_providers())
    channels.set_channel(channels.RecordingChannel())
    voice.set_transcriber(voice.FakeTranscriber())
    quarantine.set_client(None)
    planner_mod.set_planner(None)

    yield

    clock.unfreeze()
    backend_mod.set_backend(None)
    providers.set_providers(None)
    channels.set_channel(None)
    voice.set_transcriber(None)
    quarantine.set_client(None)
    planner_mod.set_planner(None)
    secrets.clear_cache()


@pytest.fixture
def backend():
    return backend_mod.get_backend()


@pytest.fixture
def gmail():
    return providers.get_providers().gmail


@pytest.fixture
def calendar():
    return providers.get_providers().calendar


@pytest.fixture
def search():
    return providers.get_providers().search


@pytest.fixture
def channel():
    return channels.get_channel()


@pytest.fixture
def transcriber():
    return voice.get_transcriber()


@pytest.fixture
def scripted():
    """Install a planner that runs a fixed list of tool calls.

    Tests use this to stand in for a model that has already been talked into
    something. The system's guarantees should hold anyway.
    """

    def install(*calls, text: str = ""):
        def script(task, message):
            return planner_mod.PlannerReply(text=text, tool_calls=list(calls))

        planner = planner_mod.ScriptedPlanner(script=script)
        planner_mod.set_planner(planner)
        return planner

    return install


class FakeReader:
    """A reader model that returns whatever the test queues up."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.prompts = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.prompts.append({"system": system, "user": user})
        if not self.responses:
            raise AssertionError("FakeReader ran out of responses")
        return self.responses.pop(0)


@pytest.fixture
def fake_reader():
    def install(*responses):
        reader = FakeReader(responses)
        quarantine.set_client(reader)
        return reader

    return install


def sms_params(text: str, *, sender: str = OWNER_NUMBER, sid: str = "SM1"):
    """The form parameters Twilio posts for a plain text message."""
    return {"From": sender, "To": "+15555550999", "Body": text, "MessageSid": sid}


def mms_params(*, sender: str = OWNER_NUMBER, sid: str = "SM1", body: str = "",
               content_type: str = "audio/mpeg"):
    return {
        "From": sender,
        "To": "+15555550999",
        "Body": body,
        "MessageSid": sid,
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1",
        "MediaContentType0": content_type,
    }


def signed_event(params, *, signature=None, token=AUTH_TOKEN, url=WEBHOOK_URL):
    """An API Gateway event carrying a correctly signed Twilio webhook."""
    import urllib.parse

    from errand.channels import twilio

    sig = signature if signature is not None else twilio.expected_signature(token, url, params)
    return {
        "body": urllib.parse.urlencode(params),
        "headers": {
            twilio.SIGNATURE_HEADER: sig,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "isBase64Encoded": False,
    }


def say(text: str):
    """Shorthand: one texted message in, one Reply out."""
    from errand.channels.base import Inbound
    from errand.dispatcher import conversation

    return conversation.respond(Inbound(kind="text", text=text, sender_id=OWNER_NUMBER))


def tap(token: str, *, message_id: str = "500"):
    """Shorthand: one button token in, one Reply out.

    SMS has no buttons, but the token path is what a richer channel uses and
    it resolves to the same approval record, so it is worth keeping tested.
    """
    from errand.channels.base import Inbound
    from errand.dispatcher import conversation

    return conversation.respond(
        Inbound(kind="button", token=token, sender_id=OWNER_NUMBER, message_id=message_id)
    )


def hint_for(reply, label: str) -> str:
    """The text Andrew would send for a given button."""
    for button in reply.buttons:
        if button.label.lower().startswith(label.lower()):
            return button.hint
    raise AssertionError(f"no {label!r} button in {[b.label for b in reply.buttons]}")


def token_for(reply, label: str) -> str:
    for button in reply.buttons:
        if button.label.lower().startswith(label.lower()):
            return button.token
    raise AssertionError(f"no {label!r} button in {[b.label for b in reply.buttons]}")


# ---------------------------------------------------------------- Telegram


def telegram_text(text: str, *, sender: str = OWNER_TELEGRAM_ID, update_id: str = "1"):
    """A Telegram Update for a plain text message."""
    return {
        "update_id": int(update_id),
        "message": {
            "message_id": 100 + int(update_id),
            "from": {"id": int(sender)},
            "chat": {"id": int(sender)},
            "text": text,
        },
    }


def telegram_voice(*, sender: str = OWNER_TELEGRAM_ID, update_id: str = "1", caption: str = ""):
    message = {
        "message_id": 100 + int(update_id),
        "from": {"id": int(sender)},
        "chat": {"id": int(sender)},
        "voice": {"file_id": "AwACAgEAAx", "duration": 3, "mime_type": "audio/ogg"},
    }
    if caption:
        message["caption"] = caption
    return {"update_id": int(update_id), "message": message}


def telegram_button(token: str, *, sender: str = OWNER_TELEGRAM_ID, update_id: str = "1",
                    message_id: str = "500"):
    return {
        "update_id": int(update_id),
        "callback_query": {
            "id": "cbq1",
            "from": {"id": int(sender)},
            "data": token,
            "message": {"message_id": int(message_id), "chat": {"id": int(sender)}},
        },
    }


def telegram_event(update, *, secret=None, header=None):
    """An API Gateway event carrying a Telegram webhook."""
    import json as _json

    from errand.channels import telegram as _tg

    headers = {"Content-Type": "application/json"}
    name = header or _tg.SECRET_HEADER
    if secret is not None:
        headers[name] = secret
    elif header is None:
        headers[name] = TELEGRAM_SECRET
    else:
        headers[name] = TELEGRAM_SECRET
    return {"body": _json.dumps(update), "headers": headers, "isBase64Encoded": False}
