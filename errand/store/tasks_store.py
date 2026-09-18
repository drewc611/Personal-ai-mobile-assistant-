"""Task threads.

Every job Andrew starts gets a task id. This is the direct answer to
Instinct's single crowded thread: "T7 status" addresses one job, not the
whole conversation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from errand.common import clock, config, ids
from errand.store import backend as backend_mod
from errand.store import counters

OPEN = "OPEN"
WAITING_APPROVAL = "WAITING_APPROVAL"
HOLDING = "HOLDING"          # approved, inside the undo window
DONE = "DONE"
FAILED = "FAILED"
STOPPED = "STOPPED"

TERMINAL = {DONE, FAILED, STOPPED}


@dataclass
class Task:
    task_id: str
    title: str
    status: str = OPEN
    task_type: str = "general"
    created_at: str = ""
    updated_at: str = ""
    last_message: str = ""
    source: str = "telegram"
    notes: list[str] = field(default_factory=list)

    def to_item(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = json.dumps(self.notes)
        data["pk"] = f"task#{self.task_id}"
        data["sk"] = "meta"
        return data

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Task:
        notes = item.get("notes") or "[]"
        return cls(
            task_id=item["task_id"],
            title=item.get("title", ""),
            status=item.get("status", OPEN),
            task_type=item.get("task_type", "general"),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
            last_message=item.get("last_message", ""),
            source=item.get("source", "telegram"),
            notes=json.loads(notes) if isinstance(notes, str) else list(notes),
        )


def _table() -> str:
    return config.load().table("tasks")


def _next_number() -> int:
    return counters.next_value(_table(), "tasks")


def create(title: str, *, task_type: str = "general", source: str = "telegram") -> Task:
    number = _next_number()
    task = Task(
        task_id=ids.format_task_id(number),
        title=title[:280],
        task_type=task_type,
        source=source,
        created_at=clock.now_iso(),
        updated_at=clock.now_iso(),
    )
    backend_mod.get_backend().put(_table(), task.to_item())
    return task


def get(task_id: str) -> Task | None:
    item = backend_mod.get_backend().get(_table(), f"task#{task_id}", "meta")
    return Task.from_item(item) if item else None


def save(task: Task) -> Task:
    task.updated_at = clock.now_iso()
    backend_mod.get_backend().put(_table(), task.to_item())
    return task


def set_status(task_id: str, status: str, *, note: str = "") -> Task | None:
    task = get(task_id)
    if task is None:
        return None
    task.status = status
    if note:
        task.notes.append(f"{clock.now_iso()} {note}")
        task.last_message = note
    return save(task)


def open_tasks() -> list[Task]:
    rows = [
        r
        for r in backend_mod.get_backend().scan(_table())
        if r.get("sk") == "meta" and r.get("status") not in TERMINAL
    ]
    tasks = [Task.from_item(r) for r in rows]
    return sorted(tasks, key=lambda t: int(t.task_id[1:]))


def all_tasks() -> list[Task]:
    rows = [r for r in backend_mod.get_backend().scan(_table()) if r.get("sk") == "meta"]
    return sorted((Task.from_item(r) for r in rows), key=lambda t: int(t.task_id[1:]))


def record_outcome(task: Task, outcome: str) -> None:
    """Per-task-type history. Earned autonomy reads this later; v0 only
    collects it, which is why it is worth writing correctly now."""
    backend_mod.get_backend().put(
        _table(),
        {
            "pk": f"type#{task.task_type}",
            "sk": f"run#{clock.now():015.3f}#{task.task_id}",
            "task_id": task.task_id,
            "task_type": task.task_type,
            "outcome": outcome,
            "at": clock.now_iso(),
        },
    )


def outcomes_for_type(task_type: str) -> list[dict[str, Any]]:
    return backend_mod.get_backend().query(_table(), f"type#{task_type}")
