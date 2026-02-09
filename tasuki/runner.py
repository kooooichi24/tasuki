"""Harness execution loop: planner (root + sub) -> tasks -> worker -> handoff -> planner."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid

from tasuki.log import SessionLogger
from tasuki.task_store import TaskStore, Task
from tasuki.planner import run_planner
from tasuki.planner_registry import PlannerRegistry
from tasuki.worker import run_worker
from tasuki.repo import prepare_worker_repo
from tasuki.handoff import read_handoff
from tasuki.llm import load_config


def _gather_handoffs_md(task_store: TaskStore, planner_id: str) -> tuple[str | None, int]:
    """Combine handoffs for the specified planner into a single Markdown. Returns (Markdown, count)."""
    handoffs = []
    for t in task_store.get_handoffs_for_planner(planner_id):
        if t.handoff_path and t.handoff_path.exists():
            handoffs.append(f"--- Task {t.id} ---\n{read_handoff(t.handoff_path)}")
    md = "\n\n".join(handoffs) if handoffs else None
    return md, len(handoffs)


class HarnessRunner:
    def __init__(self, config_path: Path | None = None):
        self.config = load_config(config_path)
        session_root = self.config.get("session", {}).get("root", ".tasuki/sessions")
        self.session_id = uuid.uuid4().hex[:12]
        self.session_root = Path(session_root) / self.session_id
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.logger = SessionLogger(self.session_root)
        self.task_store = TaskStore(self.session_root)
        self.planner_registry = PlannerRegistry(self.session_root)
        self.repo_path = Path.cwd().resolve()
        self.max_workers = self.config.get("concurrency", {}).get("max_workers", 4)
        self.workers_root = self.session_root / "workers"

    def _run_planner_and_apply(
        self, planner_id: str, scope_instruction: str, handoffs_md: str | None, handoffs_count: int = 0
    ) -> list[Task]:
        """Run the planner once and add tasks to the store. Register sub-planner delegations in the registry. Returns newly added tasks."""
        self.logger.system_action("planner_run", planner_id=planner_id, handoffs_count=handoffs_count)
        _, tasks, sub_scopes = run_planner(
            planner_id, scope_instruction, handoffs_md, self.logger, self.config
        )
        for scope in sub_scopes:
            sub = self.planner_registry.add_sub(planner_id, scope)
            self.logger.system_action("sub_planner_created", sub_id=sub.id, parent_id=planner_id)
        if tasks:
            self.task_store.add_many(tasks)
            self.logger.system_action(
                "tasks_created",
                count=len(tasks),
                task_ids=[t.id for t in tasks],
                planner_id=planner_id,
            )
        if planner_id != "root":
            self.planner_registry.mark_run(planner_id)
        return tasks

    def run_one_round(self, user_instruction: str) -> list[Task]:
        """One round: generate tasks with root + sub-planners -> execute with workers -> handoffs are linked to each planner."""
        # 1) Run root planner
        handoffs_root_md, handoffs_root_n = _gather_handoffs_md(self.task_store, "root")
        self._run_planner_and_apply("root", user_instruction, handoffs_root_md, handoffs_root_n)

        # 2) Run sub-planners that are either "not yet run" or "have handoffs"
        for sub in self.planner_registry.get_subs_to_run(self.task_store):
            handoffs_md, handoffs_n = _gather_handoffs_md(self.task_store, sub.id)
            self._run_planner_and_apply(sub.id, sub.scope, handoffs_md, handoffs_n)

        all_new_tasks = self.task_store.get_pending()
        if not all_new_tasks:
            return []

        if not self.repo_path.exists():
            self.logger.system_action("skip_workers", reason="repo path not set or missing")
            return all_new_tasks

        self.workers_root.mkdir(parents=True, exist_ok=True)
        pending = self.task_store.get_pending()
        completed: list[Task] = []

        def run_one(task: Task) -> Task | None:
            worker_id = f"w-{task.id}"
            work_dir = self.workers_root / worker_id
            work_dir.mkdir(parents=True, exist_ok=True)
            repo_copy = prepare_worker_repo(self.repo_path, self.workers_root, worker_id)
            handoff_path = work_dir / "handoff.md"
            claimed = self.task_store.claim(task.id, worker_id)
            if not claimed:
                return None
            try:
                run_worker(claimed, repo_copy, handoff_path, self.logger, self.config)
                self.task_store.complete(task.id, handoff_path)
                return claimed
            except Exception as e:
                self.logger.log("worker_error", role="system", task_id=task.id, error=str(e))
                return None

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(run_one, t): t for t in pending[: self.max_workers * 2]}
            for fut in as_completed(futs):
                result = fut.result()
                if result:
                    completed.append(result)

        return completed

    def close(self) -> None:
        self.logger.close()
