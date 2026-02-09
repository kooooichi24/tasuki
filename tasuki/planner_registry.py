"""Sub-planner registration and persistence. Root is implicit with id='root'."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import uuid


@dataclass
class SubPlanner:
    id: str
    parent_id: str  # "root" or another sub id
    scope: str
    is_new: bool = True  # True if not yet run. Set to False after first run

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "scope": self.scope,
            "is_new": self.is_new,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SubPlanner":
        return cls(
            id=d["id"],
            parent_id=d["parent_id"],
            scope=d["scope"],
            is_new=d.get("is_new", True),
        )


class PlannerRegistry:
    """List of sub-planners within a session. Saved to sessions/<id>/planners.json."""

    def __init__(self, session_root: Path):
        self.session_root = Path(session_root)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.path = self.session_root / "planners.json"
        self._subs: dict[str, SubPlanner] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._subs = {
                    s["id"]: SubPlanner.from_dict(s)
                    for s in data.get("sub_planners", [])
                }
            except Exception:
                pass

    def _save(self) -> None:
        data = {
            "sub_planners": [s.to_dict() for s in self._subs.values()],
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_sub(self, parent_id: str, scope: str) -> SubPlanner:
        """Add a new sub-planner. id is sub-<uuid8>."""
        sid = f"sub-{uuid.uuid4().hex[:8]}"
        sub = SubPlanner(id=sid, parent_id=parent_id, scope=scope.strip(), is_new=True)
        self._subs[sid] = sub
        self._save()
        return sub

    def get(self, planner_id: str) -> SubPlanner | None:
        return self._subs.get(planner_id)

    def get_all_subs(self) -> list[SubPlanner]:
        return list(self._subs.values())

    def get_subs_to_run(self, task_store: Any) -> list[SubPlanner]:
        """Return sub-planners that have handoffs or have not yet been run (is_new)."""
        to_run: list[SubPlanner] = []
        for sub in self._subs.values():
            if sub.is_new:
                to_run.append(sub)
            elif task_store.get_handoffs_for_planner(sub.id):
                to_run.append(sub)
        return to_run

    def mark_run(self, planner_id: str) -> None:
        """Mark a sub-planner as "already run" (is_new=False)."""
        if planner_id in self._subs:
            self._subs[planner_id].is_new = False
            self._save()
