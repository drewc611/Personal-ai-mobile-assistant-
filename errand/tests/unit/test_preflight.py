"""preflight refuses a tfvars file that would deploy an assistant that cannot act.

The part worth testing is the channel split: hard rule 3 is one owner, and
which identity that is depends on the channel. Requiring both pairs would mean
inventing a phone number to deploy a Telegram bot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "preflight", Path(__file__).resolve().parents[3] / "scripts" / "preflight.py"
)
preflight = importlib.util.module_from_spec(_SPEC)
sys.modules["preflight"] = preflight
_SPEC.loader.exec_module(preflight)


COMPLETE = """
channel           = "telegram"
owner_telegram_id = "123456789"

default_model_id    = "a-haiku-id"
escalation_model_id = "a-sonnet-id"
reader_model_id     = "a-haiku-id"

model_rates_json = <<JSON
{
  "a-haiku-id":  {"in": 1.00, "out": 5.00},
  "a-sonnet-id": {"in": 3.00, "out": 15.00}
}
JSON

monthly_budget_usd = 25
tier3_cap_cents    = 5000
"""


def run(tmp_path, text):
    path = tmp_path / "terraform.tfvars"
    path.write_text(text)
    return preflight.main(["preflight.py", str(path)])


def test_a_complete_telegram_file_passes(tmp_path):
    assert run(tmp_path, COMPLETE) == 0


def test_telegram_does_not_require_a_phone_number(tmp_path):
    """The whole point: no Twilio account, no invented number, still deployable."""
    assert "owner_number" not in COMPLETE
    assert run(tmp_path, COMPLETE) == 0


def test_telegram_without_an_owner_id_is_refused(tmp_path):
    missing = COMPLETE.replace('owner_telegram_id = "123456789"', "")
    assert run(tmp_path, missing) == 1


def test_a_placeholder_owner_id_is_refused(tmp_path):
    placeholder = COMPLETE.replace("123456789", "PUT-YOUR-NUMERIC-TELEGRAM-USER-ID-HERE")
    assert run(tmp_path, placeholder) == 1


def test_an_unset_channel_is_treated_as_telegram(tmp_path):
    """config.load() defaults to telegram; preflight must agree or it passes a
    file the Lambda then rejects at runtime."""
    unset = COMPLETE.replace('channel           = "telegram"', "")
    assert run(tmp_path, unset) == 0

    both_missing = unset.replace('owner_telegram_id = "123456789"', "")
    assert run(tmp_path, both_missing) == 1


def test_twilio_requires_both_numbers(tmp_path):
    twilio = COMPLETE.replace('channel           = "telegram"', 'channel = "twilio"')
    twilio = twilio.replace('owner_telegram_id = "123456789"', "")
    assert run(tmp_path, twilio) == 1

    with_owner = twilio + '\nowner_number = "+15555550123"\n'
    assert run(tmp_path, with_owner) == 1

    with_both = with_owner + 'twilio_from_number = "+15555550999"\n'
    assert run(tmp_path, with_both) == 0


def test_an_unknown_channel_is_refused(tmp_path):
    assert run(tmp_path, COMPLETE.replace('"telegram"', '"whatsapp"')) == 1


@pytest.mark.parametrize("line", ["monthly_budget_usd = 0", "tier3_cap_cents    = 0"])
def test_the_zero_refusals_still_hold(tmp_path, line):
    key = line.split("=")[0].strip()
    zeroed = "\n".join(
        line if row.strip().startswith(key) else row for row in COMPLETE.splitlines()
    )
    assert run(tmp_path, zeroed) == 1


def test_the_shipped_example_is_not_deployable_as_is(tmp_path):
    """It is full of placeholders on purpose. If this ever passes, the example
    has been filled in with something real and committed."""
    example = Path(__file__).resolve().parents[2] / "infra" / "terraform.tfvars.example"
    assert run(tmp_path, example.read_text()) == 1
