"""The planner.

Strands drives the loop; the tools it gets are wrappers around
`registry.call`, bound to one task id. That binding matters: the model cannot
name a different task to escape an approval, because the task id is closed
over here rather than passed as an argument.

Two things wrap every model call. The budget is checked before it (hard rule
8, and checking after means the cap is always exceeded by one call), and token
usage is recorded after it. The router sits between the two, deciding which
model the call goes to based on how this task has behaved so far.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from errand.agent import prompts, router
from errand.common import config
from errand.policy import budget
from errand.policy.tiers import all_specs
from errand.store import tasks_store
from errand.tools import registry


class Planner(Protocol):
    def run(self, task: tasks_store.Task, message: str) -> str: ...


@dataclass
class PlannerReply:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class ScriptedPlanner:
    """A planner for tests and the injection suite.

    It runs a fixed list of tool calls and then returns a fixed reply, which
    makes it possible to test what the *system* does with a model that has
    already been talked into something. That is the interesting case: the
    tests should pass even against a fully compromised planner.
    """

    script: Callable[[tasks_store.Task, str], PlannerReply]
    calls: list[registry.ToolResult] = field(default_factory=list)
    state: router.RouterState = field(default_factory=router.RouterState)
    tokens_in: int = 400
    tokens_out: int = 120

    def run(self, task: tasks_store.Task, message: str) -> str:
        choice = router.choose(self.state)
        budget.guard(choice.model_id)
        budget.record(choice.model_id, self.tokens_in, self.tokens_out)

        plan = self.script(task, message)
        lines = []
        for name, args in plan.tool_calls:
            result = registry.call(task.task_id, name, args)
            self.calls.append(result)
            self.state.record_tool_call(failed=result.status == "ERROR")
            if result.status != "OK" and result.message:
                lines.append(result.message)

        text = plan.text
        if lines:
            text = (text + "\n" + "\n".join(lines)).strip() if text else "\n".join(lines)
        return text


class StrandsPlanner:
    """Strands Agents on Bedrock, inside AgentCore Runtime."""

    def __init__(self) -> None:
        from strands import Agent
        from strands.models import BedrockModel

        self._Agent = Agent
        self._BedrockModel = BedrockModel

    def _tools(self, task_id: str, state: router.RouterState) -> list[Any]:
        from strands import tool as strands_tool

        return [
            self._wrap(strands_tool, spec.name, spec.summary, task_id, state)
            for spec in all_specs()
        ]

    def _wrap(self, strands_tool, name: str, summary: str, task_id: str,
              state: router.RouterState):
        def call_tool(**kwargs: Any) -> str:
            """Bound to one task; every call goes through the gate."""
            result = registry.call(task_id, name, kwargs)
            state.record_tool_call(failed=result.status == "ERROR")
            return json.dumps(result.to_model_payload(), default=str)

        call_tool.__name__ = name
        call_tool.__doc__ = (
            f"{summary}. Returns a JSON object with a status field. A status of "
            f"PENDING_APPROVAL means the call did not run and Andrew must approve it."
        )
        return strands_tool(call_tool)

    def run(self, task: tasks_store.Task, message: str) -> str:
        state = router.RouterState()
        choice = router.choose(state)
        budget.guard(choice.model_id)

        model = self._BedrockModel(
            model_id=choice.model_id,
            region_name=config.load().region,
            temperature=choice.temperature,
        )
        agent = self._Agent(
            model=model,
            tools=self._tools(task.task_id, state),
            system_prompt=prompts.planner_system(),
        )
        result = agent(prompts.TASK_PREAMBLE.format(task_id=task.task_id, message=message))

        tokens_in, tokens_out = _usage_of(result)
        budget.record(choice.model_id, tokens_in, tokens_out)

        # If the task turned out to need escalation, the next turn on this
        # task gets the bigger model. Escalating mid-turn would mean throwing
        # away the work Haiku already did.
        after = router.choose(state)
        if after.tier == router.ESCALATED and choice.tier == router.DEFAULT:
            tasks_store.set_status(
                task.task_id, task.status, note=f"escalated: {after.reason}"
            )

        return _text_of(result)


def _usage_of(result: Any) -> tuple[int, int]:
    """Pull the token counts out of a Strands AgentResult.

    Unknown usage counts as zero tokens but is recorded as an unpriced call, so
    `/budget` says the total is incomplete rather than implying it is exact.
    """
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return 0, 0
    summary = metrics.get_summary() if hasattr(metrics, "get_summary") else {}
    usage = (summary or {}).get("accumulated_usage", {}) if isinstance(summary, dict) else {}
    return int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0)


def _text_of(result: Any) -> str:
    message = getattr(result, "message", result)
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            ).strip()
        if isinstance(content, str):
            return content.strip()
    return str(message).strip()


class AgentCoreRuntimePlanner:
    """Calls the Strands agent running in AgentCore Runtime.

    This is what the dispatcher Lambda uses. The Lambda never loads a model or
    a tool implementation; it posts the task and reads back a sentence.
    """

    def __init__(self, runtime_arn: str, region: str) -> None:
        import boto3

        self._arn = runtime_arn
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    def run(self, task: tasks_store.Task, message: str) -> str:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self._arn,
            # One session per task, so AgentCore Memory keeps the thread for
            # /status T7 and does not blend two jobs together.
            runtimeSessionId=f"errand-{task.task_id}",
            payload=json.dumps({"task_id": task.task_id, "prompt": message}).encode(),
        )
        body = response.get("response")
        raw = body.read() if hasattr(body, "read") else body
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return str(raw).strip()
        if isinstance(parsed, dict):
            if parsed.get("error"):
                raise RuntimeError(parsed["error"])
            return str(parsed.get("result", "")).strip()
        return str(parsed).strip()


_planner: Planner | None = None


def set_planner(planner: Planner | None) -> None:
    global _planner
    _planner = planner


def get_planner() -> Planner:
    global _planner
    if _planner is None:
        _planner = StrandsPlanner()
    return _planner
