"""Runtime configuration for Errand.

Everything here comes from the environment. Nothing is a secret: secret
*values* live in Secrets Manager and are fetched at call time (hard rule 7).
What lives here is only the name of the secret, table names, and model ids.

Model ids deliberately have no defaults. Andrew looks the current Bedrock
model id up in the console and sets it; a stale id hardcoded in source is a
silent downgrade nobody notices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """A required configuration value is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. See errand/README.md; model ids must be read "
            f"from the Bedrock console, not copied from documentation."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    region: str
    backend: str
    tasks_table: str
    approvals_table: str
    audit_table: str
    content_table: str
    receipts_bucket: str
    queue_url: str
    twilio_secret_id: str
    twilio_from_number: str
    owner_number: str
    approval_ttl_seconds: int
    undo_seconds: int
    tier3_cap_cents: int
    sms_segment_limit: int
    transcribe_bucket: str

    @property
    def tier3_cap_configured(self) -> bool:
        return self.tier3_cap_cents > 0


def load() -> Config:
    """Read config from the environment. Raises ConfigError on missing values."""
    return Config(
        region=_optional("ERRAND_REGION", "us-east-1"),
        backend=_optional("ERRAND_BACKEND", "dynamodb"),
        tasks_table=_optional("ERRAND_TASKS_TABLE", "errand-tasks"),
        approvals_table=_optional("ERRAND_APPROVALS_TABLE", "errand-approvals"),
        audit_table=_optional("ERRAND_AUDIT_TABLE", "errand-audit"),
        content_table=_optional("ERRAND_CONTENT_TABLE", "errand-content"),
        receipts_bucket=_optional("ERRAND_RECEIPTS_BUCKET", ""),
        queue_url=_optional("ERRAND_QUEUE_URL", ""),
        twilio_secret_id=_optional("ERRAND_TWILIO_SECRET_ID", "errand/twilio"),
        twilio_from_number=_optional("ERRAND_TWILIO_FROM", ""),
        owner_number=_optional("ERRAND_OWNER_NUMBER", ""),
        approval_ttl_seconds=_int("ERRAND_APPROVAL_TTL_SECONDS", 3600),
        # The undo window. Every tier 2+ action waits this long after
        # approval before it actually goes out.
        undo_seconds=_int("ERRAND_UNDO_SECONDS", 60),
        # 0 means "no cap decided yet" -> every tier 3 call is refused.
        tier3_cap_cents=_int("ERRAND_TIER3_CAP_CENTS", 0),
        sms_segment_limit=_int("ERRAND_SMS_SEGMENT_LIMIT", 4),
        transcribe_bucket=_optional("ERRAND_TRANSCRIBE_BUCKET", ""),
    )


def planner_model_id() -> str:
    """Model that plans and writes replies. Never sees raw untrusted content."""
    return _require("ERRAND_PLANNER_MODEL_ID")


def reader_model_id() -> str:
    """Quarantined reader. Zero tools, fixed schema (hard rules 2 and 3)."""
    return _require("ERRAND_READER_MODEL_ID")
