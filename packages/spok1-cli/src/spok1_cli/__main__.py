"""The `spok1` command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from spok1_cli import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="spok1: your agent system.")


@app.command()
def version() -> None:
    """Print the spok1 version."""
    typer.echo(f"spok1 {__version__}")


@app.command()
def hook(event: Annotated[str, typer.Argument(help="session-start | session-end")]) -> None:
    """Claude Code hook handler (called by Claude Code, not by you)."""
    from spok1_cli import hooks

    raise typer.Exit(hooks.run(event, sys.stdin.read()))


@app.command()
def capture(limit: Annotated[int, typer.Option(help="Max sessions to process")] = 10) -> None:
    """Turn finished sessions into brain/inbox episodes (normally started by the hooks)."""
    from spok1_cli import capture as capture_mod

    for line in capture_mod.run(limit):
        typer.echo(line)


@app.command()
def notify(
    message: Annotated[str, typer.Argument(help="Text to send")],
    priority: Annotated[str, typer.Option("--priority", "-p", help="P1 | P2 | P3")] = "P2",
    title: Annotated[str | None, typer.Option(help="Optional bold title")] = None,
) -> None:
    """Send yourself a Discord message through the configured webhook."""
    from spok1_core import discord

    if priority not in ("P1", "P2", "P3"):
        raise typer.BadParameter("priority must be P1, P2 or P3")
    level: Literal["P1", "P2", "P3"] = priority  # type: ignore[assignment]
    try:
        discord.send(message, level, title)
    except discord.NotifyError as exc:
        typer.echo(f"Not sent: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Sent ({priority}).")


@app.command("validate-brain")
def validate_brain(
    brain: Annotated[Path | None, typer.Option(help="Brain directory (default ~/brain)")] = None,
) -> None:
    """Check the brain's YAML files against the schemas."""
    from spok1_core import paths, schemas

    target = brain or paths.brain_dir()
    problems = schemas.validate_brain(target)
    for problem in problems:
        typer.echo(f"✗ {problem}")
    if problems:
        raise typer.Exit(1)
    typer.echo(f"✓ {target} is valid")


@app.command()
def install(
    brain_repo: Annotated[
        str | None, typer.Option(help="Brain repo URL (default: my-details brain.repo)")
    ] = None,
    machine: Annotated[str | None, typer.Option(help="Name to register this machine as")] = None,
    dry_run: Annotated[bool, typer.Option(help="Show what would change, change nothing")] = False,
) -> None:
    """Set up this computer: brain, links, hooks, MCP servers (safe to rerun)."""
    from spok1_cli import install as install_mod

    raise typer.Exit(install_mod.run(brain_repo=brain_repo, machine=machine, dry_run=dry_run))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    try:
        app()
    except SystemExit as exc:
        return int(exc.code or 0) if not isinstance(exc.code, str) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
