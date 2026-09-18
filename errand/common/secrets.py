"""Secrets Manager access.

Hard rule 7: no secrets in code or env files. The environment holds the *name*
of a secret; the value is fetched here and cached for the life of the Lambda
container only.
"""

from __future__ import annotations

import json
from typing import Any

from errand.common import config

_cache: dict[str, dict[str, Any]] = {}


def get_secret(secret_id: str) -> dict[str, Any]:
    if secret_id in _cache:
        return _cache[secret_id]

    import boto3

    client = boto3.client("secretsmanager", region_name=config.load().region)
    response = client.get_secret_value(SecretId=secret_id)
    value = json.loads(response["SecretString"])
    _cache[secret_id] = value
    return value


def twilio_credentials() -> dict[str, Any]:
    """Expects {"account_sid": ..., "auth_token": ...}."""
    secret = get_secret(config.load().twilio_secret_id)
    missing = {"account_sid", "auth_token"} - set(secret)
    if missing:
        raise RuntimeError(f"twilio secret is missing {', '.join(sorted(missing))}")
    return secret


def set_cached(secret_id: str, value: dict[str, Any]) -> None:
    """Tests only."""
    _cache[secret_id] = value


def clear_cache() -> None:
    _cache.clear()
