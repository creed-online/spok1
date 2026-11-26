"""The `spok1` command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from spok1_cli import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="spok1: your agent system.")
guard_app = typer.Typer(
    no_args_is_help=True, help="The guard hook: install (as admin), status, bench."
)
app.add_typer(guard_app, name="guard")


@guard_app.command("install")
def guard_install(
    dry_run: Annotated[bool, typer.Option(help="Show what would change, change nothing")] = False,
    update_policy: Annotated[
        bool, typer.Option(help="Replace the installed guardrails.toml (backs it up)")
    ] = False,
    profile: Annotated[
        str, typer.Option(help="laptop (default) | vm (unattended: guard is the approval layer)")
    ] = "laptop",
) -> None:
    """Install the guard where agents can't change it (run as ADMIN / root)."""
    from spok1_cli import guard_install as gi

    if profile not in ("laptop", "vm"):
        raise typer.BadParameter("profile must be laptop or vm")
    raise typer.Exit(gi.install(dry_run=dry_run, update_policy=update_policy, profile=profile))


@guard_app.command("uninstall")
def guard_uninstall(
    dry_run: Annotated[bool, typer.Option(help="Show what would change")] = False,
) -> None:
    """Remove the guard hook from managed settings (run as ADMIN / root)."""
    from spok1_cli import guard_install as gi

    raise typer.Exit(gi.uninstall(dry_run=dry_run))


@guard_app.command("status")
def guard_status() -> None:
    """Show whether the guard is installed, and its latency."""
    from spok1_cli import guard_install as gi

    raise typer.Exit(gi.status())


@guard_app.command("verify-log")
def guard_verify_log() -> None:
    """Check today's (and every) audit log file for tampering."""
    from spok1_core import paths
    from spok1_guard import audit

    folder = Path(audit.audit_dir(paths.home()))
    files = sorted(folder.glob("*.jsonl"))
    bad = 0
    for f in files:
        problems = audit.verify(f)
        bad += len(problems)
        typer.echo(
            f"{'✓' if not problems else '✗'} {f.name}: "
            f"{len(f.read_text(encoding='utf-8').splitlines())} records"
            + (f", {problems[0]}" if problems else "")
        )
    if not files:
        typer.echo(f"no audit logs in {folder}")
    raise typer.Exit(1 if bad else 0)


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


@app.command("audit-plan")
def audit_plan(
    plan: Annotated[Path, typer.Argument(help="Plan YAML written by the manager")],
    write: Annotated[bool, typer.Option(help="Overwrite the plan with the revised one")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Pre-flight check: estimate usage, suggest savings, say if approval is needed."""
    import yaml

    from spok1_core import planning

    try:
        parsed = planning.load_plan(plan)
    except Exception as exc:
        typer.echo(f"✗ invalid plan: {exc}", err=True)
        raise typer.Exit(2) from exc
    weights, budgets = planning.load_usage_policy()
    result = planning.audit(parsed, weights, budgets)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(planning.format_result(result))
    if write:
        data = result.revised.model_dump(exclude_defaults=True)
        dump = getattr(yaml, "safe_dump")  # noqa: B009 - the stub's type is partly Unknown
        plan.write_text(str(dump(data, sort_keys=False, allow_unicode=True)), encoding="utf-8")
    raise typer.Exit(10 if result.approval_needed else 0)


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
