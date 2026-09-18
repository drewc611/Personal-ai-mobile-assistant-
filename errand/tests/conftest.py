"""Test wiring.

Every test runs against the memory backend and fake providers, with a frozen
clock. Nothing here touches AWS, which is deliberate: the properties these
tests check - a send never happens without an approval, an unknown number
never gets a reply - are properties of this code, and a test that needs
credentials to prove them is a test nobody runs.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ERRAND_BACKEND", "memory")
os.environ.setdefault("ERRAND_OWNER_NUMBER", "+15555550123")
os.environ.setdefault("ERRAND_TWILIO_FROM", "+15555550999")
os.environ.setdefault("ERRAND_PLANNER_MODEL_ID", "test-planner-model")
os.environ.setdefault("ERRAND_READER_MODEL_ID", "test-reader-model")
os.environ.setdefault("ERRAND_UNDO_SECONDS", "60")
os.environ.setdefault("ERRAND_APPROVAL_TTL_SECONDS", "3600")
os.environ.setdefault("ERRAND_TIER3_CAP_CENTS", "15000")
os.environ.setdefault("ERRAND_WEBHOOK_URL", "https://errand.example.com/sms")

import errand.tools  # noqa: E402,F401  registers every tool
from errand.agent import planner as planner_mod  # noqa: E402
from errand.common import clock, secrets  # noqa: E402
from errand.dispatcher import sms, voice  # noqa: E402
from errand.reader import quarantine  # noqa: E402
from errand.store import backend as backend_mod  # noqa: E402
from errand.tools import providers  # noqa: E402

FIXED_NOW = 1_764_500_000.0  # 2025-11-30T12:13:20Z, a Sunday lunchtime


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    backend = backend_mod.MemoryBackend()
    backend_mod.set_backend(backend)
    clock.freeze(FIXED_NOW)
    secrets.clear_cache()
    secrets.set_cached(
        "errand/twilio", {"account_sid": "AC_test", "auth_token": "test_token"}
    )

    fake = providers.build_fake_providers()
    providers.set_providers(fake)

    sender = sms.RecordingSender()
    sms.set_sender(sender)

    transcriber = voice.FakeTranscriber()
    voice.set_transcriber(transcriber)

    quarantine.set_client(None)
    planner_mod.set_planner(None)

    yield

    clock.unfreeze()
    backend_mod.set_backend(None)
    providers.set_providers(None)
    sms.set_sender(None)
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
def sender():
    return sms.get_sender()


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
