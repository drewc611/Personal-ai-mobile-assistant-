"""Model routing.

One config, one place. Instinct gives you no model choice at all; the point
here is that the choice is a table you can edit, and that every model named
runs inside Andrew's own Bedrock account.

Model ids are read from the environment and never defaulted in code. Bedrock
ids change, and a stale id compiled into a repo is a silent downgrade.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from errand.common import config


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    temperature: float
    max_tokens: int
    why: str


# Per-task-type overrides, by environment variable. Unset falls back to the
# planner model.
_OVERRIDE_VARS = {
    "email": "ERRAND_MODEL_EMAIL",
    "calendar": "ERRAND_MODEL_CALENDAR",
    "research": "ERRAND_MODEL_RESEARCH",
    "general": "ERRAND_MODEL_GENERAL",
}

_PROFILES = {
    "email": ModelChoice("", 0.2, 900, "drafting in Andrew's voice; low temperature"),
    "calendar": ModelChoice("", 0.0, 500, "reading times out of structured data"),
    "research": ModelChoice("", 0.4, 1200, "summarising several sources"),
    "general": ModelChoice("", 0.3, 800, "everything else"),
}


def choose(task_type: str) -> ModelChoice:
    profile = _PROFILES.get(task_type, _PROFILES["general"])
    override = os.environ.get(_OVERRIDE_VARS.get(task_type, ""), "").strip()
    model_id = override or config.planner_model_id()
    return ModelChoice(model_id, profile.temperature, profile.max_tokens, profile.why)


def describe() -> list[dict[str, str]]:
    """What "which model runs what" looks like, for a text reply."""
    rows = []
    for task_type, profile in _PROFILES.items():
        override = os.environ.get(_OVERRIDE_VARS[task_type], "").strip()
        rows.append(
            {
                "task_type": task_type,
                "model": override or "(planner default)",
                "temperature": str(profile.temperature),
                "why": profile.why,
            }
        )
    return rows
