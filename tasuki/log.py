"""Timestamped logging. For session analysis and replay."""

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LogEntry:
    ts: str
    kind: str  # "agent_message" | "system_action" | "command_output" | "handoff" | ...
    role: str | None  # "planner" | "worker" | "system"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_line(self) -> str:
        return json.dumps(
            {"ts": self.ts, "kind": self.kind, "role": self.role, **self.payload},
            ensure_ascii=False,
        ) + "\n"


class SessionLogger:
    """Per-session logger. Records all agent messages, system actions, and command outputs."""

    def __init__(self, session_root: Path):
        self.session_root = Path(session_root)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.session_root / "harness.log"
        self._file = open(self.log_path, "a", encoding="utf-8")

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(self, kind: str, role: str | None = None, **payload: Any) -> None:
        entry = LogEntry(ts=self._ts(), kind=kind, role=role, payload=payload)
        line = entry.to_line()
        self._file.write(line)
        self._file.flush()

    def agent_message(self, role: str, message: str, extra: dict | None = None) -> None:
        self.log("agent_message", role=role, message=message, **(extra or {}))

    def system_action(self, action: str, **kwargs: Any) -> None:
        self.log("system_action", role="system", action=action, **kwargs)

    def command_output(self, role: str, command: str, stdout: str, stderr: str, exit_code: int) -> None:
        self.log(
            "command_output",
            role=role,
            command=command,
            stdout=stdout[:2000],
            stderr=stderr[:2000],
            exit_code=exit_code,
        )

    def handoff(self, worker_id: str, task_id: str, handoff_preview: str) -> None:
        self.log("handoff", role="worker", worker_id=worker_id, task_id=task_id, handoff_preview=handoff_preview[:500])

    def close(self) -> None:
        self._file.close()
