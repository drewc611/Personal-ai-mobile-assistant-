"""The Channel interface, the Twilio implementation, and hard rule 9."""

from __future__ import annotations

import pytest

from errand.channels import twilio
from errand.channels.base import Button, mask_sensitive, render_buttons, split_message
from errand.dispatcher import commands
from errand.tests.conftest import OWNER_NUMBER, mms_params, sms_params


def _channel():
    return twilio.TwilioChannel(
        account_sid="AC", auth_token="t", from_number="+15555550999",
        to_number=OWNER_NUMBER,
    )


def test_a_text_webhook_becomes_an_inbound():
    inbound = _channel().receive(sms_params("hello"))
    assert inbound.kind == "text"
    assert inbound.text == "hello"
    assert inbound.sender_id == OWNER_NUMBER


def test_an_audio_mms_becomes_a_voice_inbound():
    inbound = _channel().receive(mms_params())
    assert inbound.kind == "voice"
    assert inbound.voice_ref == "https://api.twilio.com/media/ME1"


def test_a_caption_beats_the_audio():
    """If he typed something too, that is what he meant to say."""
    inbound = _channel().receive(mms_params(body="actually just the calendar"))
    assert inbound.kind == "text"
    assert inbound.text == "actually just the calendar"


def test_an_image_mms_is_not_treated_as_a_memo():
    params = mms_params(content_type="image/jpeg")
    assert _channel().receive(params) is None


def test_an_empty_body_with_no_media_is_nothing():
    assert _channel().receive(sms_params("")) is None


def test_a_number_is_normalised_to_e164():
    assert twilio.normalise_number("(555) 555-0123") == "+5555550123"
    assert twilio.normalise_number("+1 555 555 0123") == "+15555550123"


# ------------------------------------------------- buttons degrade to text


def test_buttons_render_as_reply_hints_on_sms():
    buttons = [
        Button("Approve", "a:ap_1", reply="T7 yes"),
        Button("Reject", "r:ap_1", reply="T7 no"),
    ]
    rendered = render_buttons("T7: send the draft", buttons)
    assert rendered == 'T7: send the draft\nReply "T7 yes", "T7 no".'


def test_a_repeated_hint_is_shown_once():
    buttons = [
        Button("Approve T7", "a:ap_1", reply="T7 yes"),
        Button("Approve T7 again", "a:ap_2", reply="T7 yes"),
    ]
    assert render_buttons("x", buttons).count("T7 yes") == 1


def test_a_button_with_no_reply_falls_back_to_its_label():
    assert Button("Approve", "a:ap_1").hint == "Approve"


def test_no_buttons_leaves_the_text_alone():
    assert render_buttons("Nothing open.", []) == "Nothing open."


def test_a_button_token_fits_the_64_byte_limit():
    token = commands.button_token(commands.APPROVE, "ap_abcdefghijkl")
    assert len(token.encode()) <= 64
    Button("Approve", token)


def test_an_oversized_token_is_refused_at_construction():
    with pytest.raises(ValueError):
        Button("Approve", "x" * 65)


def test_an_oversized_token_is_refused_when_built():
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
    assert "998877" not in channel.all_output


# ------------------------------------------------- splitting


def test_a_short_reply_is_one_message():
    assert split_message("Nothing open.", 300, 4) == ["Nothing open."]


def test_a_long_reply_is_numbered():
    body = "\n".join(f"T{i} something reasonably long to read on a phone" for i in range(1, 30))
    parts = split_message(body, 300, 4)
    assert len(parts) > 1
    assert parts[0].startswith("(1/")
    assert all(len(p) <= 320 for p in parts)


def test_a_very_long_reply_is_truncated_rather_than_spammed():
    """Carriers reorder concatenated segments, so there is a ceiling."""
    body = "\n".join(f"line {i} " + "x" * 200 for i in range(200))
    parts = split_message(body, 300, 4)
    assert len(parts) <= 4
    assert "tasks" in parts[-1]


def test_an_empty_reply_sends_nothing():
    assert split_message("   ", 300, 4) == []
