#!/usr/bin/env python3
"""Tasuki CLI entry point.

usage:
    tasuki run          # Start an interactive session
    tasuki init         # Generate .tasuki/config/ in the current directory
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt

from tasuki.config import init_project, load_config
from tasuki.runner import HarnessRunner


def cmd_init(console: Console) -> None:
    """Copy .tasuki/config/ to the current directory to initialize."""
    try:
        dest = init_project()
        console.print(f"[green]Initialization complete.[/green] Configuration files generated at: {dest}")
        console.print("Edit .tasuki/config/tasuki.yaml to set repo.path and other settings.")
    except FileExistsError as e:
        console.print(f"[yellow]{e}[/yellow]")
    except Exception as e:
        console.print(f"[red]Initialization failed: {e}[/red]")
        sys.exit(1)


def cmd_run(console: Console) -> None:
    """Start an interactive session and run multiple rounds."""
    config = load_config()
    repo_path = config.get("repo", {}).get("path")
    if not repo_path:
        console.print(
            Panel(
                "Please set the repository path in .tasuki/config/tasuki.yaml under repo.path.\n"
                "If you haven't initialized yet, run [bold]tasuki init[/bold].",
                title="Configuration",
                border_style="yellow",
            )
        )

    console.print(
        Panel(
            "[bold]Tasuki[/bold] — Multi-agent coordination harness\n"
            "The planner generates tasks, workers execute them using tools (run_cmd / read_file / edit_file), and return handoffs.\n"
            "Session logs are saved to sessions/<id>/.",
            title="Self-driving codebases harness",
            border_style="blue",
        )
    )

    instruction = Prompt.ask(
        "\n[bold]User instruction[/bold] (goal or task summary to pass to the planner)",
        default="Organize the project README and document the main usage in a single guide",
    )
    if not instruction.strip():
        console.print("[red]Instruction is empty. Exiting.[/red]")
        sys.exit(1)

    max_rounds = IntPrompt.ask(
        "[bold]Max rounds[/bold] (number of planner -> worker -> handoff cycles)",
        default=3,
    )

    runner = HarnessRunner()
    try:
        console.print(f"\n[dim]Session ID: {runner.session_id}[/dim]")

        total_completed = 0
        for round_num in range(1, max_rounds + 1):
            console.rule(f"[bold blue]Round {round_num} / {max_rounds}[/bold blue]")
            console.print("[dim]Running planner and generating tasks...[/dim]\n")

            completed = runner.run_one_round(instruction.strip())
            total_completed += len(completed)

            console.print(
                f"  [green]Round {round_num} complete.[/green] "
                f"Tasks completed: {len(completed)} (total: {total_completed})"
            )

            pending = runner.task_store.get_pending()
            if not pending and round_num < max_rounds:
                console.print(
                    "  [yellow]No pending tasks.[/yellow] "
                    "The planner will plan new tasks from handoffs in the next round.\n"
                )

        console.rule("[bold green]All rounds complete[/bold green]")
        console.print(f"Total tasks completed: {total_completed}")
        console.print(
            f"Logs & handoffs: [link=file://{runner.session_root}]{runner.session_root}[/link]"
        )
    finally:
        runner.close()


def main() -> None:
    console = Console()
    args = sys.argv[1:]

    if not args or args[0] == "run":
        cmd_run(console)
    elif args[0] == "init":
        cmd_init(console)
    elif args[0] in ("-h", "--help", "help"):
        console.print(
            Panel(
                "[bold]tasuki[/bold] — Multi-agent coordination harness\n\n"
                "Commands:\n"
                "  [bold]run[/bold]   Start an interactive session (default)\n"
                "  [bold]init[/bold]  Generate .tasuki/config/ in the current directory\n"
                "  [bold]help[/bold]  Show this help message",
                title="Usage",
                border_style="blue",
            )
        )
    else:
        console.print(f"[red]Unknown command: {args[0]}[/red]")
        console.print("Run 'tasuki help' to see usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
