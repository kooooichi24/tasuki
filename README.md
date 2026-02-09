# Tasuki 襷

[日本語版はこちら (README.ja.md)](./README.ja.md)

A multi-agent coding harness inspired by Cursor's blog post ["Towards Self-Driving Codebases"](https://cursor.com/blog/self-driving-codebases). Named after the Japanese _tasuki_ (襷) — the sash passed between runners in an _ekiden_ relay, just like agents hand off results to one another.

- **Planners** generate tasks; **Workers** execute them on dedicated repo copies
- Large scopes are recursively delegated to **Sub-planners**
- Workers return a single **Handoff** document — no global sync needed
- Automatic **fallback chain** when a model hits its rate limit
- All messages and actions are logged with timestamps for analysis and replay

See [DESIGN.md](./DESIGN.md) for the full design document.

## Architecture

```
User Instruction
    ↓
┌──────────────┐
│ Root Planner │  Generates tasks from the goal (never writes code)
└──┬───────┬───┘
   ↓       ↓
 Tasks   Sub-planner   Delegates part of the scope (recursive)
   ↓       ↓
┌────────┐ ┌────────┐
│ Worker │ │ Worker │  Works on a dedicated repo copy
└──┬─────┘ └──┬─────┘
   ↓           ↓
 Handoff     Handoff    Returns results, notes, concerns in one document
   ↓           ↓
 Received by the requesting planner → next round
```

## Installation

```bash
# From PyPI (after publishing)
pip install tasuki

# Or directly from GitHub
pip install git+https://github.com/yourname/tasuki.git

# Run instantly with uvx (recommended)
uvx tasuki run
```

### From source (development)

```bash
git clone https://github.com/yourname/tasuki.git
cd tasuki
uv sync   # or pip install -e .
```

## Configuration

### Initialize a project

```bash
# In any project directory
tasuki init
```

This generates `.tasuki/config/tasuki.yaml` and `.tasuki/config/prompts/`.  
Config search order: `./.tasuki/config/` → `~/.config/tasuki/` → package defaults.

Edit `.tasuki/config/tasuki.yaml`:

### LLM Provider (Cursor CLI by default)

By default, LLM calls go through **Cursor CLI** (`agent -p`). No API key required — uses your Cursor subscription.

```bash
# Install Cursor CLI
curl https://cursor.com/install -fsS | bash

# Authenticate
agent login
```

```yaml
llm:
  provider: cursor # cursor | openai
  model: gpt-5.2-codex-xhigh # Use CLI model IDs (see: agent --list-models)
```

To use the OpenAI API instead, set `provider: openai` and configure `OPENAI_API_KEY`.

> **Note:** Model names use Cursor CLI IDs (e.g. `gpt-5.2-codex-xhigh`), not display names.  
> Run `agent --list-models` to see all available IDs.

### Fallback (automatic model switching on rate limits)

When the primary model hits its limit, the system retries then falls back automatically:

```
gpt-5.2-codex-xhigh → opus-4.6-thinking → auto
```

```yaml
llm:
  fallback_models:
    - opus-4.6-thinking # Claude 4.6 Opus (Thinking)
    - auto # Cursor's auto-select (last resort)
  retry:
    max_retries: 3 # Retries per model
    base_delay_sec: 2 # Exponential backoff (2, 4, 8... sec)
    max_delay_sec: 60
```

### Other settings

```yaml
repo:
  path: /path/to/your/project # target repository for workers

concurrency:
  max_workers: 2 # keep low to avoid hitting rate limits
```

System prompts for planners/workers can be customized in `.tasuki/config/prompts/`.

## Usage

```bash
# Start an interactive session
tasuki run

# Run instantly with uvx
uvx tasuki run

# Help
tasuki help
```

On launch:

1. Enter your instruction
2. Set the max number of rounds (default: 3)
3. Rounds execute automatically, showing progress

## Worker Tool Loop

Workers interact with the LLM in a loop (up to 20 iterations), using 3 tools to operate on the repository:

| Tool        | Function          | Arguments                                                            |
| ----------- | ----------------- | -------------------------------------------------------------------- |
| `run_cmd`   | Run shell command | `command` (required), `timeout` (optional, max 300s)                 |
| `read_file` | Read a file       | `path` (relative to repo root)                                       |
| `edit_file` | Edit a file       | `path` + `content` (full write), or `path` + `old` + `new` (replace) |

When all work is done, the worker outputs a Handoff document (Markdown starting with `# Summary`) without any tool call.

## Round Flow

1. **Root Planner** — Generates tasks from user instruction + previous handoffs. Registers sub-planners if delegating.
2. **Sub-planners** — Run for any new or handoff-received sub-scopes, generating additional tasks.
3. **Workers (parallel)** — Execute pending tasks (max 2 in parallel). Each works on a dedicated repo copy using the tool loop, then returns a handoff.
4. **Handoff collection** — Results are saved. Planners receive them in the next round.

Multiple rounds run automatically. Each round, planners receive handoffs from the previous round and plan next steps.

## Mapping to the Blog Post

| Lesson from the blog           | Implementation in this project                            |
| ------------------------------ | --------------------------------------------------------- |
| No integrator role             | Workers return handoffs directly                          |
| No shared locks                | Each worker operates on its own repo copy                 |
| Don't require 100% correctness | Error-tolerant, convergence-oriented design               |
| Observability from day one     | JSONL logs with timestamps for all actions and messages   |
| Keep things fresh              | Scratchpad rewrites, self-reflection reminders in prompts |
| Use concrete numbers for scope | Prompts include "Generate 20-100 tasks" etc.              |

## Future Work

- Headless execution (`--instruction "..." --rounds 5`)
- Additional worker tools (`git_commit`, `run_tests`, etc.)
- Parallel sub-planner execution

## License

MIT
