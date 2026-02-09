"""Planner agent. Generates tasks, receives handoffs, and plans next steps. Both root and sub-planners use the same run_planner."""

import re
import uuid
from pathlib import Path

from tasuki.llm import chat, get_client, get_model, load_config
from tasuki.task_store import Task
from tasuki.log import SessionLogger


def load_planner_prompt() -> str:
    from tasuki.config import load_prompt
    text = load_prompt("planner_system.txt")
    return text or "You are a planner. Emit tasks."


def parse_sub_planner_scopes(response: str) -> list[str]:
    """Find the "sub-planner delegation" section in the response and return the body of blocks starting with Scope:."""
    scopes: list[str] = []
    lower = response.lower()
    # Look for blocks containing "## Sub-planner delegation" or "## New sub-planner"
    if "sub-planner" not in lower and "sub planner" not in lower:
        return scopes
    # Extract blocks starting with Scope: or Scope 1: / Scope 2: etc.
    scope_pattern = re.compile(
        r"^\s*Scope\s*(?:\d+)?\s*:\s*\n(.*?)(?=^\s*Scope\s*(?:\d+)?\s*:|^\s*#|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for m in scope_pattern.finditer(response):
        text = m.group(1).strip()
        if len(text) >= 20:
            scopes.append(text)
    # Also pick up simple single-line forms like "**Scope:** ..." or "Scope: ..."
    for line in response.splitlines():
        if re.match(r"^\s*\*?\*?Scope\*?\*?\s*:\s*", line, re.IGNORECASE):
            desc = re.sub(r"^\s*\*?\*?Scope\*?\*?\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            if len(desc) >= 20 and desc not in scopes:
                scopes.append(desc)
    return scopes


def parse_tasks_from_response(response: str, planner_id: str) -> list[Task]:
    """Extract a task list from the response text. Expects numbered lists or bullet lists."""
    tasks = []
    for line in response.splitlines():
        line = line.strip()
        # "1. ..." or "- ..." or "* ..."
        if not line:
            continue
        for prefix in (r"^\d+[.)]\s*", r"^[-*]\s*"):
            m = re.match(prefix, line)
            if m:
                desc = line[m.end() :].strip()
                if len(desc) < 10:
                    continue
                tasks.append(
                    Task(
                        id=f"task-{uuid.uuid4().hex[:8]}",
                        planner_id=planner_id,
                        description=desc,
                    )
                )
                break
    return tasks


def run_planner(
    planner_id: str,
    scope_instruction: str,
    handoffs_markdown: str | None,
    logger: SessionLogger | None,
    config: dict | None = None,
) -> tuple[str, list[Task], list[str]]:
    """Run the planner once. Returns (response body, task list, list of sub-planner delegation scopes)."""
    config = config or load_config()
    client = get_client(config)
    model = get_model(config)
    system = load_planner_prompt()

    section = "User instruction" if planner_id == "root" else "Your scope (delegated to you)"
    user_block = f"## {section}\n\n{scope_instruction}\n\n"
    if handoffs_markdown:
        user_block += f"## Handoffs from workers (use these to plan next steps)\n\n{handoffs_markdown}\n\n"
    user_block += "Output a list of concrete, focused tasks (one per line, numbered or bulleted). Each task should be self-contained for a worker. If you delegate part of your scope to a sub-planner, add a section '## Sub-planner delegation' with one or more 'Scope:' blocks."

    if logger:
        logger.agent_message(
            f"planner:{planner_id}",
            user_block[:500] + "..." if len(user_block) > 500 else user_block,
        )
    response = chat(client, model, system, user_block, config=config)
    if logger:
        logger.agent_message(
            f"planner:{planner_id}",
            response[:1000] + "..." if len(response) > 1000 else response,
        )

    tasks = parse_tasks_from_response(response, planner_id)
    sub_scopes = parse_sub_planner_scopes(response)
    return response, tasks, sub_scopes
