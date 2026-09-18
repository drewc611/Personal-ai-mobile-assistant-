"""The planner.

Strands drives the loop; the tools it gets are wrappers around
`registry.call`, bound to one task id. That binding matters: the model cannot
name a different task to escape an approval, because the task id is closed
over here rather than passed as an argument.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from errand.agent import prompts, router
from errand.common import config
from errand.policy.tiers import FREE_TIERS, all_specs
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

    def run(self, task: tasks_store.Task, message: str) -> str:
        plan = self.script(task, message)
        lines = []
        for name, args in plan.tool_calls:
            result = registry.call(task.task_id, name, args)
            self.calls.append(result)
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

    def _tools(self, task_id: str) -> list[Any]:
        from strands import tool as strands_tool

        wrappers = []
        for spec in all_specs():
            wrappers.append(self._wrap(strands_tool, spec.name, spec.summary, task_id))
        return wrappers

    def _wrap(self, strands_tool, name: str, summary: str, task_id: str):
        def call_tool(**kwargs: Any) -> str:
            """Bound to one task; every call goes through the gate."""
            result = registry.call(task_id, name, kwargs)
            return json.dumps(result.to_model_payload(), default=str)

        call_tool.__name__ = name
        call_tool.__doc__ = (
            f"{summary}. Returns a JSON object with a status field. A status of "
            f"PENDING_APPROVAL means the call did not run and Andrew must approve it."
        )
        return strands_tool(call_tool)

    def run(self, task: tasks_store.Task, message: str) -> str:
        choice = router.choose(task.task_type)
        model = self._BedrockModel(
            model_id=choice.model_id,
            region_name=config.load().region,
            temperature=choice.temperature,
        )
        agent = self._Agent(
            model=model,
            tools=self._tools(task.task_id),
            system_prompt=prompts.planner_system(),
        )
        result = agent(prompts.TASK_PREAMBLE.format(task_id=task.task_id, message=message))
        return _text_of(result)


def _text_of(result: Any) -> str:
    """Strands returns an AgentResult; pull the assistant text out of it."""
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


_planner: Planner | None = None


def set_planner(planner: Planner | None) -> None:
    global _planner
    _planner = planner


def get_planner() -> Planner:
    global _planner
    if _planner is None:
        _planner = StrandsPlanner()
    return _planner


def free_tier_names() -> list[str]:
    return [s.name for s in all_specs() if s.tier in FREE_TIERS]


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
            # "T7 status" and does not blend two jobs together.
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
