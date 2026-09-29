"""Runtime configuration for Errand.

Everything here comes from the environment. Nothing is a secret: secret
*values* live in Secrets Manager and are fetched at call time (hard rule 7).
What lives here is the name of a secret, the table names, the model ids, and
the budget.

Model ids have no defaults. CLAUDE.md says to look the current ids up in the
Bedrock console and never hardcode one from memory, so the code raises rather
than falling back to something plausible and stale.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """A required configuration value is missing or malformed."""


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _require(name: str) -> str:
    value = _optional(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Model ids must be read from the Bedrock console "
            f"for this account and region; see errand/README.md."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = _optional(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = _optional(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


# One table per entity, as PLAN.md sets out. `content` is the extra one, and
# it is what makes a deletion receipt countable rather than a promise.
TABLE_VARS = {
    "tasks": ("ERRAND_TASKS_TABLE", "errand-tasks"),
    "approvals": ("ERRAND_APPROVALS_TABLE", "errand-approvals"),
    "audit": ("ERRAND_AUDIT_TABLE", "errand-audit"),
    "rules": ("ERRAND_RULES_TABLE", "errand-rules"),
    "receipts": ("ERRAND_RECEIPTS_TABLE", "errand-receipts"),
    "connections": ("ERRAND_CONNECTIONS_TABLE", "errand-connections"),
    "budget": ("ERRAND_BUDGET_TABLE", "errand-budget"),
    "content": ("ERRAND_CONTENT_TABLE", "errand-content"),
    "recipes": ("ERRAND_RECIPES_TABLE", "errand-recipes"),
}


@dataclass(frozen=True)
class Config:
    region: str
    backend: str
    tables: dict[str, str] = field(default_factory=dict)
    receipts_bucket: str = ""
    transcribe_bucket: str = ""
    queue_url: str = ""
    # Which Channel implementation is live. The rest of the system does not
    # read this -- only the ingress Lambda and channels.get_channel do.
    channel: str = "telegram"
    twilio_secret_id: str = "errand/twilio"
    twilio_from_number: str = ""
    owner_number: str = ""
    telegram_secret_id: str = "errand/telegram"
    owner_telegram_id: str = ""
    telegram_char_limit: int = 3500
    approval_ttl_seconds: int = 3600
    undo_seconds: int = 60
    tier3_cap_cents: int = 0
    monthly_budget_usd: float = 0.0
    segment_chars: int = 300
    max_segments: int = 4

    def table(self, name: str) -> str:
        try:
            return self.tables[name]
        except KeyError as exc:
            raise ConfigError(f"no table configured for {name!r}") from exc

    @property
    def is_telegram(self) -> bool:
        return self.channel == "telegram"

    @property
    def tier3_cap_configured(self) -> bool:
        return self.tier3_cap_cents > 0

    @property
    def budget_configured(self) -> bool:
        return self.monthly_budget_usd > 0


def load() -> Config:
    return Config(
        region=_optional("ERRAND_REGION", "us-east-2"),
        backend=_optional("ERRAND_BACKEND", "dynamodb"),
        tables={key: _optional(var, default) for key, (var, default) in TABLE_VARS.items()},
        receipts_bucket=_optional("ERRAND_RECEIPTS_BUCKET"),
        transcribe_bucket=_optional("ERRAND_TRANSCRIBE_BUCKET"),
        queue_url=_optional("ERRAND_QUEUE_URL"),
        channel=_optional("ERRAND_CHANNEL", "telegram").lower(),
        twilio_secret_id=_optional("ERRAND_TWILIO_SECRET_ID", "errand/twilio"),
        twilio_from_number=_optional("ERRAND_TWILIO_FROM"),
        owner_number=_optional("ERRAND_OWNER_NUMBER"),
        telegram_secret_id=_optional("ERRAND_TELEGRAM_SECRET_ID", "errand/telegram"),
        owner_telegram_id=_optional("ERRAND_OWNER_TELEGRAM_ID"),
        telegram_char_limit=_int("ERRAND_TELEGRAM_CHAR_LIMIT", 3500),
        approval_ttl_seconds=_int("ERRAND_APPROVAL_TTL_SECONDS", 3600),
        # Tier 2 and 3 actions wait this long, with an Undo button, before
        # they execute.
        undo_seconds=_int("ERRAND_UNDO_SECONDS", 60),
        # 0 means no cap has been chosen, and every tier 3 approval is refused.
        tier3_cap_cents=_int("ERRAND_TIER3_CAP_CENTS", 0),
        # 0 means no budget has been chosen, and every model call is refused.
        monthly_budget_usd=_float("ERRAND_MONTHLY_BUDGET_USD", 0.0),
        # A GSM-7 SMS segment is 153 characters once concatenated. 300 keeps a
        # part to two segments; four parts is the most worth sending at once.
        segment_chars=_int("ERRAND_SEGMENT_CHARS", 300),
        max_segments=_int("ERRAND_MAX_SEGMENTS", 4),
    )


def default_model_id() -> str:
    """Claude Haiku 4.5. Read from config, looked up in the Bedrock console."""
    return _require("ERRAND_DEFAULT_MODEL_ID")


def escalation_model_id() -> str:
    """Claude Sonnet 5, used when the router escalates."""
    return _require("ERRAND_ESCALATION_MODEL_ID")


def reader_model_id() -> str:
    """The quarantined reader. Haiku, zero tools, fixed schema."""
    return _require("ERRAND_READER_MODEL_ID")


def model_rates() -> dict[str, dict[str, float]]:
    """USD per million tokens, by model id, as {"id": {"in": x, "out": y}}.

    Read from ERRAND_MODEL_RATES as JSON. There is no built-in price table on
    purpose: Bedrock pricing changes, and a wrong number here does not fail,
    it just quietly misreports what Andrew is spending.
    """
    raw = _optional("ERRAND_MODEL_RATES")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"ERRAND_MODEL_RATES is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("ERRAND_MODEL_RATES must be a JSON object keyed by model id")

    rates: dict[str, dict[str, float]] = {}
    for model_id, entry in parsed.items():
        if not isinstance(entry, dict) or "in" not in entry or "out" not in entry:
            raise ConfigError(f"ERRAND_MODEL_RATES[{model_id}] needs 'in' and 'out'")
        rates[model_id] = {"in": float(entry["in"]), "out": float(entry["out"])}
    return rates
