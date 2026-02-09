"""Worker agent. Executes a single task and writes a handoff.
Tool loop: LLM calls run_cmd / read_file / edit_file, receives results, and decides the next action.
"""

import os
import subprocess
from pathlib import Path

from tasuki.handoff import validate_handoff
from tasuki.llm import chat, get_client, get_model, load_config
from tasuki.log import SessionLogger
from tasuki.task_store import Task

# --- Tool execution ---

_MAX_OUTPUT = 8000  # Maximum characters for tool output


def _tool_run_cmd(args: dict, repo_path: Path) -> str:
    """Execute a shell command in repo_path."""
    command = args.get("command", "")
    if not command.strip():
        return "ERROR: command is empty"
    timeout = min(int(args.get("timeout", 120)), 300)
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = ""
        if r.stdout:
            out += r.stdout[:_MAX_OUTPUT]
        if r.stderr:
            out += f"\n[stderr]\n{r.stderr[:_MAX_OUTPUT]}"
        out += f"\n[exit_code: {r.returncode}]"
        return out.strip()
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _tool_read_file(args: dict, repo_path: Path) -> str:
    """Read a file. Relative paths are resolved against repo_path."""
    file_path = args.get("path", "")
    if not file_path:
        return "ERROR: path is empty"
    p = (repo_path / file_path).resolve()
    if not str(p).startswith(str(repo_path.resolve())):
        return "ERROR: path is outside the repository"
    if not p.exists():
        return f"ERROR: file not found: {file_path}"
    if not p.is_file():
        return f"ERROR: not a file: {file_path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_OUTPUT:
            return content[:_MAX_OUTPUT] + f"\n... (truncated, total {len(content)} chars)"
        return content
    except Exception as e:
        return f"ERROR: {e}"


def _tool_edit_file(args: dict, repo_path: Path) -> str:
    """Edit a file (full overwrite or partial replacement)."""
    file_path = args.get("path", "")
    if not file_path:
        return "ERROR: path is empty"
    p = (repo_path / file_path).resolve()
    if not str(p).startswith(str(repo_path.resolve())):
        return "ERROR: path is outside the repository"

    # If content is provided, overwrite the entire file
    content = args.get("content")
    if content is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: wrote {len(content)} chars to {file_path}"

    # If old / new are provided, perform partial replacement
    old = args.get("old", "")
    new = args.get("new", "")
    if old:
        if not p.exists():
            return f"ERROR: file not found for replacement: {file_path}"
        text = p.read_text(encoding="utf-8")
        if old not in text:
            return f"ERROR: old string not found in {file_path}"
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        return f"OK: replaced in {file_path}"

    return "ERROR: provide 'content' (full write) or 'old'+'new' (replace)"


_TOOLS = {
    "run_cmd": _tool_run_cmd,
    "read_file": _tool_read_file,
    "edit_file": _tool_edit_file,
}

# --- Parsing tool calls ---

import re


def _parse_tool_call(text: str) -> tuple[str | None, dict | None, str]:
    """Find a tool call block in the LLM response.
    Format:
        <tool_call>
        tool: run_cmd
        command: ls -la
        </tool_call>

    Returns: (tool_name, args_dict, remaining_text_after_call)
    """
    pattern = re.compile(
        r"<tool_call>\s*\n(.*?)</tool_call>",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return None, None, text

    block = m.group(1).strip()
    tool_name = None
    args: dict = {}

    # Parse key: value pairs, handling multi-line values (e.g. content)
    current_key = None
    current_lines: list[str] = []

    for line in block.splitlines():
        kv = re.match(r"^(\w+):\s*(.*)", line)
        if kv:
            # Save the previous key
            if current_key:
                val = "\n".join(current_lines).strip()
                if current_key == "tool":
                    tool_name = val
                else:
                    args[current_key] = val
            current_key = kv.group(1)
            current_lines = [kv.group(2)]
        else:
            current_lines.append(line)

    # Last key
    if current_key:
        val = "\n".join(current_lines).strip()
        if current_key == "tool":
            tool_name = val
        else:
            args[current_key] = val

    remaining = text[m.end():].strip()
    return tool_name, args, remaining


# --- Main worker logic ---

_MAX_TOOL_ITERATIONS = 20


def load_worker_prompt() -> str:
    from tasuki.config import load_prompt
    text = load_prompt("worker_system.txt")
    return text or "You are a worker. Complete the task and write a handoff."


def run_worker(
    task: Task,
    repo_path: Path,
    handoff_path: Path,
    logger: SessionLogger | None,
    config: dict | None = None,
) -> str:
    """Run the worker in a tool loop. Execute the task on repo_path and write the handoff to handoff_path."""
    config = config or load_config()
    client = get_client(config)
    model = get_model(config)
    system = load_worker_prompt()

    # Brief repository context
    try:
        listing = list_repo(repo_path)
    except Exception:
        listing = "(listing failed)"

    # Conversation history
    messages: list[dict] = []

    # Initial user message
    initial_user = (
        f"## Task (from planner)\n\n{task.description}\n\n"
        f"## Repository context (top-level)\n\n{listing}\n\n"
        "Complete this task using tools (run_cmd, read_file, edit_file). "
        "When you are finished, output your HANDOFF document (starting with '# Summary')."
    )
    messages.append({"role": "user", "content": initial_user})

    if logger:
        logger.agent_message("worker", f"task_id={task.id} start (tool loop)")

    handoff_content = ""

    for iteration in range(_MAX_TOOL_ITERATIONS):
        # LLM call
        response = chat(client, model, system, messages[-1]["content"], messages=messages[:-1], config=config)
        messages.append({"role": "assistant", "content": response})

        if logger:
            logger.agent_message(
                "worker",
                f"task_id={task.id} iter={iteration} response_len={len(response)}",
            )

        # Check for tool calls
        tool_name, tool_args, remaining = _parse_tool_call(response)
        if tool_name is None:
            # No tool call -> consider done
            handoff_content = extract_handoff_from_response(response)
            break

        # Execute tool
        if tool_name not in _TOOLS:
            tool_result = f"ERROR: unknown tool '{tool_name}'. Available: {', '.join(_TOOLS)}"
        else:
            tool_result = _TOOLS[tool_name](tool_args or {}, repo_path)
            if logger:
                logger.log(
                    "tool_call",
                    role="worker",
                    task_id=task.id,
                    tool=tool_name,
                    args={k: v[:200] if isinstance(v, str) else v for k, v in (tool_args or {}).items()},
                    result_preview=tool_result[:300],
                )

        # Add tool result as a user message
        feedback = f"<tool_result>\n{tool_result}\n</tool_result>\n\nContinue with the task. Use another tool or write the HANDOFF if done."
        messages.append({"role": "user", "content": feedback})
    else:
        # Loop iteration limit reached
        if logger:
            logger.log("worker_max_iterations", role="system", task_id=task.id)
        # Try to extract handoff from the last response
        if messages and messages[-1]["role"] == "assistant":
            handoff_content = extract_handoff_from_response(messages[-1]["content"])

    if not handoff_content:
        handoff_content = "# Summary\n\nTask processing reached iteration limit. Partial work may have been done.\n\n# Notes / Concerns\n\nMax tool iterations reached."

    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(handoff_content, encoding="utf-8")

    ok, _ = validate_handoff(handoff_content)
    if logger:
        logger.handoff("worker", task.id, handoff_content[:300])
    return handoff_content


def list_repo(repo_path: Path, max_entries: int = 100) -> str:
    lines = []
    for i, p in enumerate(sorted(Path(repo_path).iterdir())):
        if i >= max_entries:
            lines.append("...")
            break
        name = p.name
        if name.startswith("."):
            continue
        lines.append(f"- {name}/" if p.is_dir() else f"- {name}")
    return "\n".join(lines) or "(empty)"


def extract_handoff_from_response(response: str) -> str:
    """Extract the trailing Markdown heading block from the response as a handoff."""
    for start in ("# Summary", "# Handoff", "# What was done"):
        idx = response.find(start)
        if idx != -1:
            return response[idx:].strip()
    # From the last # heading to the end
    last_h = response.rfind("\n# ")
    if last_h != -1:
        return response[last_h + 1 :].strip()
    return response.strip()
