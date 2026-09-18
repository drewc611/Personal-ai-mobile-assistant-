"""AgentCore Runtime entrypoint.

This is what runs inside the AgentCore container. It holds the Strands agent
and the tool wrappers; the dispatcher Lambda calls it over the runtime API
rather than importing it, which keeps the model loop and the SMS plumbing in
separate blast radii.

The tool implementations run *here*, inside the runtime, so the gate and the
audit log run here too. The dispatcher only sees the finished reply.
"""

from __future__ import annotations

import os
from typing import Any

import errand.tools  # noqa: F401  (registers every tool exactly once)
from errand.agent import planner as planner_mod
from errand.store import tasks_store
from errand.tools import providers  # noqa: F401  (registers nothing; wiring below)

try:  # pragma: no cover - only present inside the runtime image
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()
except ImportError:  # pragma: no cover
    app = None


def _wire_providers() -> None:
    if _wire_providers.done:  # type: ignore[attr-defined]
        return
    from errand.common import config
    from errand.tools.providers import FakeSearch

    identity_provider = os.environ.get("ERRAND_IDENTITY_PROVIDER", "").strip()
    if not identity_provider:
        raise RuntimeError("ERRAND_IDENTITY_PROVIDER is not set")
    cfg = config.load()
    providers.set_providers(
        providers.build_live_providers(cfg.region, identity_provider, FakeSearch())
    )
    _wire_providers.done = True  # type: ignore[attr-defined]


_wire_providers.done = False  # type: ignore[attr-defined]


def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one task turn.

    The caller supplies the task id; the runtime does not invent one, because
    the task thread is owned by the dispatcher and a second id would split the
    audit trail for one job across two threads.
    """
    _wire_providers()

    task_id = str(payload.get("task_id") or "").strip()
    message = str(payload.get("prompt") or "").strip()
    if not task_id or not message:
        return {"error": "task_id and prompt are both required"}

    task = tasks_store.get(task_id)
    if task is None:
        return {"error": f"no task {task_id}"}

    text = planner_mod.get_planner().run(task, message)
    return {"task_id": task_id, "result": text}


if app is not None:  # pragma: no cover
    invoke = app.entrypoint(invoke)  # type: ignore[assignment]


if __name__ == "__main__":  # pragma: no cover
    if app is None:
        raise SystemExit("bedrock-agentcore is not installed in this environment")
    app.run()
