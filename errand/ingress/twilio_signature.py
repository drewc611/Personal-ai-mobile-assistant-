"""Twilio request signature validation.

Hard rule 5: validate on every webhook, reject on failure. Twilio signs the
full request URL concatenated with every POST parameter, sorted by name, using
the account auth token as an HMAC-SHA1 key.

Two details that are easy to get wrong behind API Gateway and both of which
turn into a webhook that accepts anything:

  - The URL must be the one Twilio called, scheme and host included, exactly
    as configured on the number. Rebuilding it from Lambda event fields gives
    you the internal hostname and every signature fails, so it is configured
    explicitly.
  - The comparison must be constant time. A fast reject leaks the signature
    one byte at a time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping


def expected_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    payload = url
    for key in sorted(params):
        payload += key + str(params[key])
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid(auth_token: str, url: str, params: Mapping[str, str], signature: str) -> bool:
    if not signature or not auth_token or not url:
        return False
    return hmac.compare_digest(expected_signature(auth_token, url, params), signature)
