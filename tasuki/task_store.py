"""Task storage. Planners add tasks, workers retrieve them."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass
class Task:
    id: str
    planner_id: str
    description: str
    status: str = "pending"  # pending | running | done
    worker_id: str | None = None
    handoff_path: Path | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "planner_id": self.planner_id,
            "description": self.description,
            "status": self.status,
            "worker_id": self.worker_id,
            "handoff_path": str(self.handoff_path) if self.handoff_path else None,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d["id"],
            planner_id=d["planner_id"],
            description=d["description"],
            status=d.get("status", "pending"),
            worker_id=d.get("worker_id"),
            handoff_path=Path(d["handoff_path"]) if d.get("handoff_path") else None,
            meta=d.get("meta", {}),
        )


class TaskStore:
    """In-memory + file backup. Can be restored from .tasuki/sessions/<id>/tasks.json on restart."""

    def __init__(self, session_root: Path):
        self.session_root = Path(session_root)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.path = self.session_root / "tasks.json"
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._tasks = {k: Task.from_dict(v) for k, v in data.get("tasks", {}).items()}
            except Exception:
                pass

    def _save(self) -> None:
        data = {"tasks": {tid: t.to_dict() for tid, t in self._tasks.items()}}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._save()

    def add_many(self, tasks: list[Task]) -> None:
        for t in tasks:
            self._tasks[t.id] = t
        self._save()

    def get_pending(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == "pending"]

    def claim(self, task_id: str, worker_id: str) -> Task | None:
        t = self._tasks.get(task_id)
        if t and t.status == "pending":
            t.status = "running"
            t.worker_id = worker_id
            self._save()
            return t
        return None

    def complete(self, task_id: str, handoff_path: Path) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = "done"
            self._tasks[task_id].handoff_path = handoff_path
            self._save()

    def get_by_planner(self, planner_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.planner_id == planner_id]

    def get_handoffs_for_planner(self, planner_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.planner_id == planner_id and t.status == "done" and t.handoff_path]
