"""
FORGE CLI
==========
Terminal commands for FORGE.
"""

from __future__ import annotations

import time
import sys
import os
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
from forge import __version__

def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()
console = Console()
cli = typer.Typer(
    name="forge",
    help="FORGE - Free Open Reasoning & Generation Engine",
    add_completion=False,
    rich_markup_mode="rich",
)

FORGE_BANNER = """[bold #FF6B1A]FORGE[/bold #FF6B1A]
[dim]Free Open Reasoning & Generation Engine  v{version}[/dim]
[dim]https://www.trenstudio.com/FORGE[/dim]
"""


def _print_banner() -> None:
    console.print(FORGE_BANNER.format(version=__version__))


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"FORGE [bold #FF6B1A]{__version__}[/bold #FF6B1A]")
        raise typer.Exit()


@cli.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed FORGE version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """FORGE command line entrypoint."""
    return None


def _get_session():
    from forge.core.session import ForgeSession

    return ForgeSession()


def _instant_cli_response(prompt: str) -> str | None:
    from forge.brain.identity_guard import get_instant_response

    instant = get_instant_response(prompt)
    return str(instant.get("user_response") or "") if instant else None


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".forge-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _fallback_workspace_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "FORGE" / "workspace"
    return Path.home() / ".forge" / "workspace"


def _is_system_workspace_candidate(path: Path) -> bool:
    resolved = path.resolve()
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()
    system_roots = {
        windir,
        windir / "System32",
        windir / "SysWOW64",
    }
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for raw in program_files:
        if raw:
            system_roots.add(Path(raw).resolve())

    return any(resolved == root or root in resolved.parents for root in system_roots)


def _resolve_operator_workspace(workspace_root: str | Path | None = None) -> Path:
    if workspace_root:
        normalized = Path(workspace_root).expanduser().resolve()
        if not _is_writable_directory(normalized):
            raise typer.BadParameter(f"Workspace is not writable: {normalized}")
        return normalized

    env_root = os.environ.get("FORGE_WORKSPACE_ROOT")
    candidates = []
    if env_root:
        candidates.append(Path(env_root).expanduser().resolve())
    cwd = Path.cwd().resolve()
    if not _is_system_workspace_candidate(cwd):
        candidates.append(cwd)
    candidates.append(_fallback_workspace_root().resolve())

    for candidate in candidates:
        if _is_writable_directory(candidate):
            return candidate

    raise typer.BadParameter("No writable FORGE workspace found. Use --workspace to choose one.")


def _get_operator(no_memory: bool = False, workspace_root: str | Path | None = None):
    from forge.brain.operator import ForgeOperator
    from forge.config.settings import OperatorSettings

    normalized_workspace = _resolve_operator_workspace(workspace_root)
    settings = OperatorSettings(enable_memory=not no_memory, workspace_root=normalized_workspace)
    return ForgeOperator(settings=settings)


def _save_key(provider: str, key: str) -> None:
    keydir = Path.home() / ".forge" / "keys"
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / provider).write_text(key.strip())


def _save_provider_value(provider: str, name: str, value: str) -> None:
    keydir = Path.home() / ".forge" / "keys"
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / f"{provider}.{name}").write_text(value.strip())


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

    with Live(Spinner("line", text="[dim]Booting FORGE...[/dim]"), refresh_per_second=10, transient=True):
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

        instant = _instant_cli_response(user_input)
        if instant is not None:
            console.print()
            console.print(Panel(instant, border_style="#FF6B1A", padding=(0, 1)))
            console.print("[dim]0.0s[/dim]\n")
            continue

        started = time.monotonic()
        try:
            with Live(Spinner("line", text="[dim]Thinking...[/dim]"), refresh_per_second=12, transient=True):
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
        console.print("[bold #FF6B1A]FORGE[/bold #FF6B1A]")

    instant = _instant_cli_response(prompt)
    if instant is not None:
        if raw:
            print(instant)
        else:
            console.print(Panel(instant, border_style="#FF6B1A", padding=(0, 1)))
        return

    if raw:
        session = _get_session()
        reply = session.ask(prompt, task_type=task)
    else:
        with Live(Spinner("line", text=""), refresh_per_second=10, transient=True):
            session = _get_session()
            reply = session.ask(prompt, task_type=task)

    if raw:
        print(reply)
    else:
        console.print(Panel(reply, border_style="#FF6B1A", padding=(0, 1)))


@cli.command()
def operate(
    prompt: Optional[str] = typer.Argument(None, help="Request for the operator brain"),
    task: Optional[str] = typer.Option(None, "--task", "-t", help="Request for the operator brain"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Workspace root for this operator run"),
    allow_real_changes: bool = typer.Option(False, "--allow-real-changes", help="Alias for --confirm on local file/shell tasks"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm high-risk execution"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Force dry-run mode"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable memory injection for this run"),
    resume_mission: str | None = typer.Option(None, "--resume-mission", help="Resume a previous mission by ID"),
    raw: bool = typer.Option(False, "--raw", help="Print raw text only"),
):
    """Run the modular skill-based operator brain."""
    request = (task or prompt or "").strip()
    if not request:
        raise typer.BadParameter("Provide a prompt argument or --task.")
    confirmed = confirm or allow_real_changes

    if not raw:
        console.print("[bold #FF6B1A]FORGE[/bold #FF6B1A]")

    instant = _instant_cli_response(request)
    if instant is not None:
        if raw:
            print(instant)
        else:
            console.print(Panel(instant, border_style="#FF6B1A", padding=(0, 1)))
        return

    if raw:
        operator = _get_operator(no_memory=no_memory, workspace_root=workspace)
        reply = operator.handle_as_text(
            request,
            confirmed=confirmed,
            dry_run=dry_run,
            resume_mission_id=resume_mission,
        )
    else:
        with Live(Spinner("line", text="[dim]Planning and executing...[/dim]"), refresh_per_second=10, transient=True):
            operator = _get_operator(no_memory=no_memory, workspace_root=workspace)
            reply = operator.handle_as_text(
                request,
                confirmed=confirmed,
                dry_run=dry_run,
                resume_mission_id=resume_mission,
            )

    if raw:
        print(reply)
    else:
        console.print(Panel(reply, border_style="#FF6B1A", padding=(0, 1)))


@cli.command("add-key")
def add_key(
    provider: str = typer.Argument(
        ...,
        help="Provider name: ollama, groq, gemini, deepseek, openrouter, mistral, together, nvidia, cloudflare, anthropic, openai",
    ),
    key: str = typer.Argument(..., help="Your API key"),
    account_id: str | None = typer.Option(None, "--account-id", help="Required for cloudflare"),
    organization: str | None = typer.Option(None, "--organization", help="Optional OpenAI organization"),
    project: str | None = typer.Option(None, "--project", help="Optional OpenAI project"),
):
    """Save a provider API key."""
    from forge.providers import supported_provider_names

    provider = provider.lower()
    if provider not in supported_provider_names():
        raise typer.BadParameter(
            f"Unsupported provider '{provider}'. Supported: {', '.join(supported_provider_names())}"
        )

    _save_key(provider, key)
    saved_notes = [f"key -> [dim]~/.forge/keys/{provider}[/dim]"]

    if provider == "cloudflare":
        if not account_id:
            raise typer.BadParameter("--account-id is required for cloudflare")
        _save_provider_value(provider, "account_id", account_id)
        saved_notes.append(f"account_id -> [dim]~/.forge/keys/{provider}.account_id[/dim]")

    if provider == "openai":
        if organization:
            _save_provider_value(provider, "organization", organization)
            saved_notes.append(f"organization -> [dim]~/.forge/keys/{provider}.organization[/dim]")
        if project:
            _save_provider_value(provider, "project", project)
            saved_notes.append(f"project -> [dim]~/.forge/keys/{provider}.project[/dim]")

    console.print(f"[#4ade80]OK[/] Key saved for [bold]{provider}[/bold] -> [dim]~/.forge/keys/{provider}[/dim]")
    for note in saved_notes[1:]:
        console.print(f"[dim]{note}[/dim]")
    console.print("[dim]Run `forge status` to verify the provider is online.[/dim]")


@cli.command()
def status():
    """Show system health, model leaderboard, and quota usage."""
    _print_banner()

    with Live(Spinner("line", text="[dim]Checking providers...[/dim]"), refresh_per_second=10, transient=True):
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

    with Live(Spinner("line", text="[dim]Scanning free model sources...[/dim]"), refresh_per_second=10, transient=True):
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
    console.print(f"FORGE [bold #FF6B1A]{__version__}[/bold #FF6B1A]")


@cli.command()
def gateway(
    host: str = typer.Option("127.0.0.1", "--host", help="Gateway host"),
    port: int = typer.Option(18789, "--port", help="Gateway port"),
    token: str = typer.Option("", "--token", help="Optional Bearer token required by the gateway"),
    rate_limit: int = typer.Option(60, "--rate-limit", help="Requests per minute per client"),
    no_heartbeat: bool = typer.Option(False, "--no-heartbeat", help="Disable heartbeat tasks"),
):
    """Run the FORGE agent gateway with WebSocket and HTTP entrypoints."""
    from forge.runtime.agent import AgentRuntimeSettings
    from forge.runtime.gateway import GatewaySettings, run_gateway

    _print_banner()
    console.print(
        f"[dim]Gateway starting on ws://{host}:{port}/ws and http://{host}:{port}/api/message[/dim]"
    )

    gateway_settings = GatewaySettings(
        host=host,
        port=port,
        auth_token=token,
        requests_per_minute=rate_limit,
    )
    runtime_settings = AgentRuntimeSettings(
        workspace_root=Path.cwd().resolve(),
        enable_heartbeat=not no_heartbeat,
    )
    run_gateway(gateway_settings=gateway_settings, runtime_settings=runtime_settings)


@cli.command()
def heartbeat(
    once: bool = typer.Option(True, "--once/--watch", help="Run heartbeat once or keep watching"),
):
    """Run the autonomous heartbeat tasks without starting the gateway."""
    import asyncio

    from forge.runtime.agent import AgentRuntimeSettings, ForgeAgentRuntime

    runtime = ForgeAgentRuntime(
        AgentRuntimeSettings(
            workspace_root=Path.cwd().resolve(),
            enable_heartbeat=True,
        )
    )

    async def _run_once() -> None:
        reports = await runtime.run_heartbeat_once()
        for report in reports:
            console.print(
                Panel(
                    f"status: {report['status']}\n"
                    f"duration_ms: {report['duration_ms']}\n"
                    f"details: {report['details']}",
                    title=f"Heartbeat: {report['task_name']}",
                    border_style="#FF6B1A",
                )
            )
        await runtime.stop()

    async def _watch() -> None:
        await runtime.start()
        console.print("[dim]Heartbeat running. Press Ctrl+C to stop.[/dim]")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runtime.stop()

    asyncio.run(_run_once() if once else _watch())


@cli.command("worker-host")
def worker_host(
    host: str = typer.Option("127.0.0.1", "--host", help="Worker bind host"),
    port: int = typer.Option(18895, "--port", help="Worker bind port"),
    gateway_url: str = typer.Option("http://127.0.0.1:18789", "--gateway-url", help="Gateway base URL"),
    gateway_token: str = typer.Option("", "--gateway-token", help="Optional gateway Bearer token"),
    worker_id: str = typer.Option("", "--worker-id", help="Optional fixed worker id"),
):
    """Run a separate council worker host that registers to the gateway."""
    from forge.runtime.worker_host import WorkerHostSettings, run_worker_host

    _print_banner()
    console.print(f"[dim]Worker host starting on http://{host}:{port} -> gateway {gateway_url}[/dim]")
    run_worker_host(
        WorkerHostSettings(
            host=host,
            port=port,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
            worker_id=worker_id,
            workspace_root=Path.cwd().resolve(),
        )
    )


if __name__ == "__main__":
    cli()
