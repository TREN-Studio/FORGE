"""
FORGE CLI
==========
Terminal commands for FORGE.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table

console = Console()
cli = typer.Typer(
    name="forge",
    help="FORGE - Free Open Reasoning & Generation Engine",
    add_completion=False,
    rich_markup_mode="rich",
)

FORGE_BANNER = """[bold #FF6B1A]FORGE[/bold #FF6B1A]
[dim]Free Open Reasoning & Generation Engine  v1.1.0[/dim]
[dim]https://www.trenstudio.com/FORGE[/dim]
"""


def _print_banner() -> None:
    console.print(FORGE_BANNER)


def _get_session():
    from forge.core.session import ForgeSession

    return ForgeSession()


def _get_operator(no_memory: bool = False):
    from forge.brain.operator import ForgeOperator
    from forge.config.settings import OperatorSettings

    settings = OperatorSettings(enable_memory=not no_memory)
    return ForgeOperator(settings=settings)


def _save_key(provider: str, key: str) -> None:
    keydir = Path.home() / ".forge" / "keys"
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / provider).write_text(key.strip())


def _format_leaderboard(rows: list[dict]) -> Table:
    table = Table(
        title="[bold #FF6B1A]Model Leaderboard[/bold #FF6B1A]",
        border_style="dim",
        header_style="bold dim",
    )
    table.add_column("#", width=4, justify="right")
    table.add_column("Model", min_width=32)
    table.add_column("Score", width=8, justify="right")
    table.add_column("Latency", width=10, justify="right")
    table.add_column("Quota", width=12, justify="right")
    table.add_column("Tier", width=8)
    table.add_column("Status", width=16)

    for row in rows:
        score_color = "#4ade80" if row["score"] > 0.75 else "#FFAA3C" if row["score"] > 0.5 else "#ef4444"
        status_color = {
            "online": "#4ade80",
            "slow": "#FFAA3C",
            "quota_exhausted": "#ef4444",
            "offline": "dim",
        }.get(row["status"], "dim")

        table.add_row(
            str(row["rank"]),
            row["model"],
            f"[{score_color}]{row['score']:.3f}[/]",
            f"{row['latency_ms']:.0f}ms",
            row["quota_left"],
            row["tier"],
            f"[{status_color}]{row['status']}[/]",
        )
    return table


@cli.command()
def start(
    task: Optional[str] = typer.Option(
        None,
        "--task",
        "-t",
        help="Task type: general|code|math|research|creative|reasoning|fast",
    ),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable persistent memory"),
):
    """Start an interactive FORGE session."""
    _print_banner()

    with Live(Spinner("dots", text="[dim]Booting FORGE...[/dim]"), refresh_per_second=10, transient=True):
        session = _get_session()

    task_type = task or "general"
    status_rows = session.leaderboard(task_type)
    console.print(f"[dim]Ready: {len(status_rows)} models  |  /exit  |  /status  |  /quota  |  /memory[/dim]\n")
    console.print(Rule(style="dim"))

    while True:
        try:
            user_input = Prompt.ask("[bold #FF6B1A]forge[/bold #FF6B1A]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if not user_input.strip():
            continue

        command = user_input.strip().lower()
        if command in {"/exit", "/quit", "/q"}:
            console.print("[dim]Goodbye.[/dim]")
            break
        if command == "/status":
            console.print(_format_leaderboard(session.leaderboard(task_type)))
            continue
        if command == "/memory":
            stats = session.memory_stats()
            console.print(
                Panel(
                    f"Entities: {stats.get('entities', 0)}  "
                    f"Observations: {stats.get('observations', 0)}  "
                    f"Messages: {stats.get('messages', 0)}",
                    title="Memory Graph",
                    border_style="dim",
                )
            )
            continue
        if command == "/reset":
            session.reset()
            console.print("[dim]Conversation reset.[/dim]")
            continue
        if command.startswith("/task "):
            task_type = user_input.strip().split(None, 1)[1]
            console.print(f"[dim]Task type set to: {task_type}[/dim]")
            continue
        if command == "/quota":
            health = session.quota_health()
            for provider, info in health.items():
                console.print(
                    f"  [dim]{provider:12}[/dim] "
                    f"{info['utilisation_label']:>8} used  "
                    f"{str(info.get('tokens_remaining', 'unlimited')):>12} left"
                )
            continue
        if command == "/discover":
            report = session.discover_models()
            console.print(
                Panel(
                    f"Discovered: {report['discovered']}\nAttached: {report['attached']}",
                    title="Discovery Report",
                    border_style="#FF6B1A",
                )
            )
            continue

        started = time.monotonic()
        try:
            with Live(Spinner("dots2", text="[dim]Thinking...[/dim]"), refresh_per_second=12, transient=True):
                reply = session.ask(
                    user_input,
                    task_type=task_type,
                    remember=not no_memory,
                )
            elapsed = time.monotonic() - started
        except RuntimeError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            console.print("[dim]Run `forge add-key` or `forge discover` to expand coverage.[/dim]")
            continue

        console.print()
        console.print(Panel(reply, border_style="#FF6B1A", padding=(0, 1)))
        console.print(f"[dim]{elapsed:.1f}s[/dim]\n")


@cli.command()
def ask(
    prompt: str = typer.Argument(..., help="Your question or task"),
    task: str = typer.Option("general", "--task", "-t"),
    raw: bool = typer.Option(False, "--raw", help="Print raw text only"),
):
    """Run a one-shot prompt."""
    if not raw:
        console.print(f"[dim]FORGE ->[/dim] [dim]{prompt[:72]}[/dim]")

    with Live(Spinner("dots", text=""), refresh_per_second=10, transient=not raw):
        session = _get_session()
        reply = session.ask(prompt, task_type=task)

    if raw:
        print(reply)
    else:
        console.print(Panel(reply, border_style="#FF6B1A", padding=(0, 1)))


@cli.command()
def operate(
    prompt: str = typer.Argument(..., help="Request for the operator brain"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm high-risk execution"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Force dry-run mode"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable memory injection for this run"),
    raw: bool = typer.Option(False, "--raw", help="Print raw text only"),
):
    """Run the modular skill-based operator brain."""
    if not raw:
        console.print(f"[dim]FORGE operator ->[/dim] [dim]{prompt[:72]}[/dim]")

    with Live(Spinner("dots", text="[dim]Planning and executing...[/dim]"), refresh_per_second=10, transient=not raw):
        operator = _get_operator(no_memory=no_memory)
        reply = operator.handle_as_text(prompt, confirmed=confirm, dry_run=dry_run)

    if raw:
        print(reply)
    else:
        console.print(Panel(reply, border_style="#FF6B1A", padding=(0, 1)))


@cli.command("add-key")
def add_key(
    provider: str = typer.Argument(..., help="Provider name: groq, gemini, deepseek, openrouter"),
    key: str = typer.Argument(..., help="Your API key"),
):
    """Save a provider API key."""
    provider = provider.lower()
    _save_key(provider, key)
    console.print(f"[#4ade80]OK[/] Key saved for [bold]{provider}[/bold] -> [dim]~/.forge/keys/{provider}[/dim]")
    console.print("[dim]Run `forge status` to verify the provider is online.[/dim]")


@cli.command()
def status():
    """Show system health, model leaderboard, and quota usage."""
    _print_banner()

    with Live(Spinner("dots", text="[dim]Checking providers...[/dim]"), refresh_per_second=10, transient=True):
        session = _get_session()

    system_status = session._router.status()
    console.print(
        Panel(
            f"Providers: [bold]{system_status['providers']}[/bold]  "
            f"Models online: [bold #4ade80]{system_status['models_online']}[/bold #4ade80]  "
            f"Quota exhausted: [bold #ef4444]{system_status['models_quota']}[/bold #ef4444]  "
            f"Offline: [dim]{system_status['models_offline']}[/dim]",
            title="System Status",
            border_style="#FF6B1A",
        )
    )
    console.print()
    console.print(_format_leaderboard(session.leaderboard()))
    console.print()

    health = session.quota_health()
    table = Table(title="[bold]Quota Health[/bold]", border_style="dim", header_style="bold dim")
    table.add_column("Provider", min_width=14)
    table.add_column("Used", width=10, justify="right")
    table.add_column("Limit", width=14, justify="right")
    table.add_column("Remaining", width=14, justify="right")
    table.add_column("Resets", width=20)

    for provider, info in health.items():
        color = "#4ade80" if info["utilisation"] < 0.60 else "#FFAA3C"
        table.add_row(
            provider,
            info["utilisation_label"],
            str(info["tokens_limit"]),
            f"[{color}]{info['tokens_remaining']}[/]",
            info["resets"],
        )
    console.print(table)


@cli.command()
def discover():
    """Discover new free models and attach them to live providers."""
    _print_banner()

    with Live(Spinner("dots", text="[dim]Scanning free model sources...[/dim]"), refresh_per_second=10, transient=True):
        session = _get_session()
        report = session.discover_models()

    console.print(
        Panel(
            f"Discovered: [bold]{report['discovered']}[/bold]\n"
            f"Attached: [bold #4ade80]{report['attached']}[/bold #4ade80]",
            title="Discovery Report",
            border_style="#FF6B1A",
        )
    )

    providers = report.get("providers", {})
    if not providers:
        console.print("[dim]No new compatible models were attached to current providers.[/dim]")
        return

    table = Table(title="[bold]Attached By Provider[/bold]", border_style="dim", header_style="bold dim")
    table.add_column("Provider", min_width=14)
    table.add_column("New Models", justify="right")
    for provider, count in sorted(providers.items()):
        table.add_row(provider, str(count))
    console.print(table)


@cli.command()
def memory(
    show: bool = typer.Option(True, "--show/--stats"),
    clear: bool = typer.Option(False, "--clear"),
):
    """Browse or manage the FORGE memory graph."""
    from forge.memory.graph import MemoryGraph

    mem = MemoryGraph()

    if clear:
        confirm = Prompt.ask("[red]Clear ALL memories?[/red] [dim](yes/no)[/dim]")
        if confirm.lower() == "yes":
            db_path = Path.home() / ".forge" / "memory.db"
            db_path.unlink(missing_ok=True)
            console.print("[dim]Memory cleared.[/dim]")
        return

    stats = mem.stats()
    console.print(
        Panel(
            f"\n"
            f"  [bold]Entities[/bold]      {stats['entities']:>8,}\n"
            f"  [bold]Observations[/bold]  {stats['observations']:>8,}\n"
            f"  [bold]Conversations[/bold] {stats['conversations']:>8,}\n"
            f"  [bold]Messages[/bold]      {stats['messages']:>8,}\n",
            title="[bold #FF6B1A]FORGE Memory Graph[/bold #FF6B1A]",
            border_style="#FF6B1A",
        )
    )

    if not show:
        return

    context = mem.recall_all(limit=40)
    if context:
        console.print(Panel(context, title="Recent Memories", border_style="dim"))
    else:
        console.print("[dim]No memories yet. Start a conversation to build your memory graph.[/dim]")


@cli.command()
def version():
    """Show the installed FORGE version."""
    from forge import __version__

    console.print(f"FORGE [bold #FF6B1A]{__version__}[/bold #FF6B1A]")


if __name__ == "__main__":
    cli()
