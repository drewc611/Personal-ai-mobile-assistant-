"""The Channel interface, the Telegram implementation, and hard rule 9."""

from __future__ import annotations

import pytest

from errand.channels import telegram
from errand.channels.base import Button, mask_sensitive, split_message
from errand.dispatcher import commands
from errand.tests.conftest import OWNER_ID, button_press, text_message, voice_message


def _channel():
    return telegram.TelegramChannel(bot_token="t", chat_id=OWNER_ID)


def test_a_text_update_becomes_an_inbound():
    inbound = _channel().receive(text_message("hello"))
    assert inbound.kind == "text"
    assert inbound.text == "hello"
    assert inbound.sender_id == OWNER_ID


def test_a_voice_update_becomes_an_inbound_with_a_file_ref():
    inbound = _channel().receive(voice_message())
    assert inbound.kind == "voice"
    assert inbound.voice_ref == "AwACAgEAAx"
    assert inbound.raw["mime_type"] == "audio/ogg"


def test_a_callback_query_becomes_a_button_inbound():
    inbound = _channel().receive(button_press("a:ap_1", message_id="42"))
    assert inbound.kind == "button"
    assert inbound.token == "a:ap_1"
    assert inbound.message_id == "42"
    # The callback id is what answerCallbackQuery needs to stop the spinner.
    assert inbound.raw["callback_query_id"] == "cbq1"


def test_an_update_with_nothing_we_handle_is_none():
    assert _channel().receive({"update_id": 1}) is None
    assert _channel().receive({"update_id": 1, "message": {"from": {"id": 1}}}) is None


def test_a_button_token_fits_telegrams_64_byte_limit():
    token = commands.button_token(commands.APPROVE, "ap_abcdefghijkl")
    assert len(token.encode()) <= 64
    Button("Approve", token)  # constructor enforces it too


def test_an_oversized_button_token_is_refused_at_construction():
    with pytest.raises(ValueError):
        Button("Approve", "x" * 65)


def test_an_oversized_callback_token_is_refused_when_built():
    with pytest.raises(ValueError):
        commands.button_token(commands.APPROVE, "a" * 70)


# ------------------------------------------------- hard rule 9


def test_a_card_number_is_masked_to_the_last_four():
    masked = mask_sensitive("Charge went to 4111 1111 1111 1111 today")
    assert "4111 1111 1111 1111" not in masked
    assert "card ending 1111" in masked


def test_a_one_time_code_is_masked():
    masked = mask_sensitive("Your verification code is 483920")
    assert "483920" not in masked
    assert "******" in masked


def test_a_password_is_not_repeated_back():
    masked = mask_sensitive("password: hunter2seasonal")
    assert "hunter2seasonal" not in masked
    assert "not shown" in masked


def test_ordinary_numbers_are_left_alone():
    text = "Rent is 2200 and the meeting is at 9:15 on the 4th"
    assert mask_sensitive(text) == text


def test_masking_is_applied_on_the_way_out(channel):
    channel.send_text("code is 998877")
    assert "998877" not in channel.last_text


def test_masking_applies_to_button_messages_too(channel):
    channel.send_buttons("card 4111111111111111", [Button("Approve", "a:x")])
    assert "4111111111111111" not in channel.button_messages[-1][0]


# ------------------------------------------------- splitting


def test_a_short_message_is_not_split():
    assert split_message("Nothing open.", 3500) == ["Nothing open."]


def test_a_long_message_splits_on_line_boundaries():
    body = "\n".join(f"T{i} a reasonably long task summary line" for i in range(1, 300))
    parts = split_message(body, 3500)
    assert len(parts) > 1
    assert all(len(p) <= 3500 for p in parts)
    assert "\n".join(parts).replace("\n", "") == body.replace("\n", "")


def test_a_single_enormous_line_is_chopped():
    parts = split_message("x" * 9000, 3500)
    assert len(parts) == 3
    assert all(len(p) <= 3500 for p in parts)


def test_an_empty_message_sends_nothing():
    assert split_message("   ", 3500) == []
