"""`spok1 install`: set up this computer (safe to rerun; `--dry-run` changes nothing).

Steps: preflight → brain clone/pull → backup → CLAUDE.md import → agent/skill links →
hooks in ~/.claude/settings.json → MCP servers (Mem0 hosted + spok1-notify) →
machine name → ~/.config/agent/.env template.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from spok1_cli import claude_cli
from spok1_core import config, paths

MEM0_NAME = "mem0"
MEM0_URL = "https://mcp.mem0.ai/mcp"
NOTIFY_NAME = "spok1-notify"
BLOCK_BEGIN = "<!-- spok1:begin (managed by `spok1 install`; do not edit inside) -->"
BLOCK_END = "<!-- spok1:end -->"
ENV_TEMPLATE = """# spok1 machine settings. SECRETS: never commit, never paste into a chat.
# Webhook URLs: Discord → channel settings → Integrations → Webhooks → Copy Webhook URL
DISCORD_WEBHOOK_ALERTS=
DISCORD_WEBHOOK_DIGEST=
# Your Discord user ID (lets P1 alerts @mention you)
DISCORD_USER_ID=
"""


class InstallError(RuntimeError):
    pass


class Plan:
    """Collects actions; applies them unless dry-run."""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.lines: list[str] = []

    def do(self, description: str, action: Callable[[], object] | None = None) -> None:
        self.lines.append(("would: " if self.dry_run else "✓ ") + description)
        if action is not None and not self.dry_run:
            action()

    def note(self, text: str) -> None:
        self.lines.append(text)


# ── helpers ────────────────────────────────────────────────────────────


def repo_root() -> Path:
    root = Path(__file__).resolve().parents[4]
    if not (root / "pyproject.toml").is_file():
        raise InstallError(f"can't find the spok1 repo root (looked in {root})")
    return root


def scripts_dir() -> Path:
    return Path(sys.executable).parent


def exe(name: str) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return scripts_dir() / f"{name}{suffix}"


def api_key_sources() -> list[str]:
    """Where ANTHROPIC_API_KEY is set (it would bill the API instead of Max)."""
    found = [f"process env ({k})" for k in ("ANTHROPIC_API_KEY",) if os.environ.get(k)]
    if sys.platform == "win32":
        import winreg

        for hive, sub, label in (
            (winreg.HKEY_CURRENT_USER, r"Environment", "user env"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                "system env",
            ),
        ):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    value, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
                    if value:
                        found.append(label)
            except OSError:
                pass
    return found


def same_target(link: Path, target: Path) -> bool:
    try:
        return os.path.realpath(link) == os.path.realpath(target)
    except OSError:
        return False


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def make_dir_link(link: Path, target: Path) -> None:
    """Directory link: a junction on Windows (no admin needed), a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        create = cast(Callable[[str, str], None], _winapi.CreateJunction)  # type: ignore[attr-defined]
        create(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_link(link: Path) -> None:
    if link.is_junction():
        os.rmdir(link)  # removes the junction only, never the target
    else:
        link.unlink()


# ── CLAUDE.md block ────────────────────────────────────────────────────


def import_line(brain: Path) -> str:
    if brain == paths.home() / "brain":
        return "@~/brain/CLAUDE.md"
    return "@" + str(brain / "CLAUDE.md").replace("\\", "/").replace(" ", "\\ ")


def with_block(existing: str, line: str) -> str:
    block = f"{BLOCK_BEGIN}\n{line}\n{BLOCK_END}"
    if BLOCK_BEGIN in existing and BLOCK_END in existing:
        head, rest = existing.split(BLOCK_BEGIN, 1)
        _, tail = rest.split(BLOCK_END, 1)
        return f"{head}{block}{tail}"
    sep = (
        ""
        if not existing or existing.endswith("\n\n")
        else ("\n" if existing.endswith("\n") else "\n\n")
    )
    return f"{existing}{sep}{block}\n"


# ── settings.json hooks ────────────────────────────────────────────────


def _is_spok1_hook(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hook = cast(dict[str, Any], entry)
    args = hook.get("args")
    command = str(hook.get("command", ""))
    first = cast(list[object], args)[0] if isinstance(args, list) and args else None
    return Path(command).stem == "spok1" and first == "hook"


def merge_hooks(settings: dict[str, Any], spok1_exe: Path) -> dict[str, Any]:
    """Add/refresh spok1's SessionStart/SessionEnd hooks, keeping everything else."""
    hooks = cast(dict[str, Any], settings.setdefault("hooks", {}))
    wanted = {
        "SessionStart": {"event": "session-start", "timeout": 30},
        "SessionEnd": {"event": "session-end", "timeout": 10},
    }
    for event, spec in wanted.items():
        groups = cast(list[Any], hooks.get(event) or [])
        kept: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                kept.append(group)
                continue
            g = cast(dict[str, Any], group)
            inner = [h for h in cast(list[Any], g.get("hooks") or []) if not _is_spok1_hook(h)]
            if inner:
                kept.append({**g, "hooks": inner})
        kept.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": str(spok1_exe),
                        "args": ["hook", spec["event"]],
                        "timeout": spec["timeout"],
                    }
                ]
            }
        )
        hooks[event] = kept
    return settings


# ── MCP ────────────────────────────────────────────────────────────────


def _claude(claude: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [claude, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=claude_cli.child_env(),
        check=False,
    )


def parse_subscription(status_json: str) -> str:
    """`subscriptionType` from `claude auth status` JSON ('' if missing / unparseable)."""
    try:
        data: object = json.loads(status_json)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(cast(dict[str, object], data).get("subscriptionType") or "").lower()


def subscription_type(claude: str) -> str:
    try:
        return parse_subscription(_claude(claude, "auth", "status").stdout)
    except (OSError, subprocess.SubprocessError):
        return ""


# ── main ───────────────────────────────────────────────────────────────


def run(brain_repo: str | None, machine: str | None, dry_run: bool) -> int:
    plan = Plan(dry_run)
    try:
        _run(plan, brain_repo, machine)
    except InstallError as exc:
        for line in plan.lines:
            print(line)
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    for line in plan.lines:
        print(line)
    return 0


def _run(plan: Plan, brain_repo: str | None, machine: str | None) -> None:
    root = repo_root()
    details = config.load_my_details(root / "config" / "my-details.local.yaml")
    claude_home = paths.claude_dir()
    brain = paths.brain_dir()

    # 1. preflight
    sources = api_key_sources()
    if sources:
        raise InstallError(
            "ANTHROPIC_API_KEY is set (" + ", ".join(sources) + "). It would bill the API "
            "instead of your Max plan. Remove it, open a new terminal, and rerun."
        )
    claude = claude_cli.find_claude()
    if claude is None:
        raise InstallError("claude CLI not found. Run laptop/install.ps1 (or install.sh) first.")
    if shutil.which("git") is None:
        raise InstallError("git not found on PATH")
    plan.note(f"· claude: {claude}")
    plan_type = subscription_type(claude)
    if plan_type != "max":
        plan.note(
            f"! signed in to a '{plan_type or 'unknown'}' plan, not Max. spok1 is built for Max: "
            "run `claude auth logout`, then `claude auth login` with your Max account."
        )
    else:
        plan.note("· plan: max")

    # 2. brain
    if (brain / ".git").exists():
        plan.do(
            f"pull brain ({brain})",
            lambda: subprocess.run(  # noqa: S603
                ["git", "-C", str(brain), "pull", "--ff-only", "--quiet"],  # noqa: S607
                check=False,
                timeout=60,
            ),
        )
    else:
        url = brain_repo or details.brain.repo
        if not url:
            raise InstallError(
                "no brain repo: pass --brain-repo or set brain.repo in config/my-details.local.yaml"
            )
        if brain.exists() and any(brain.iterdir()):
            raise InstallError(f"{brain} exists and isn't a git repo; move it aside first")

        def clone() -> None:
            result = subprocess.run(  # noqa: S603
                ["git", "clone", "--quiet", url, str(brain)],  # noqa: S607
                check=False,
                timeout=300,
            )
            if result.returncode != 0:
                raise InstallError(f"git clone of {url} failed")

        plan.do(f"clone brain {url} → {brain}", clone)

    # 3. backup
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = claude_home / "backups" / f"spok1-{stamp}"
    to_backup = [
        p
        for p in (
            claude_home / "settings.json",
            claude_home / "CLAUDE.md",
            paths.home() / ".claude.json",
        )
        if p.is_file()
    ]

    def do_backup() -> None:
        backup.mkdir(parents=True, exist_ok=True)
        for p in to_backup:
            shutil.copy2(p, backup / p.name)

    if to_backup:
        plan.do(f"back up {', '.join(p.name for p in to_backup)} → {backup}", do_backup)

    # 4. CLAUDE.md import
    claude_md = claude_home / "CLAUDE.md"
    current = claude_md.read_text(encoding="utf-8") if claude_md.is_file() else ""
    updated = with_block(current, import_line(brain))
    if updated != current:
        plan.do(
            f"add brain import to {claude_md}",
            lambda: (
                claude_home.mkdir(parents=True, exist_ok=True),
                claude_md.write_text(updated, encoding="utf-8"),
            ),
        )
    else:
        plan.note(f"· {claude_md}: brain import already present")

    # 5. links
    _link_agents(plan, claude_home / "agents", brain / "agents")
    _link_skills(plan, claude_home / "skills", brain / "skills")

    # 6. hooks
    settings_path = claude_home / "settings.json"
    try:
        settings = (
            cast(dict[str, Any], json.loads(settings_path.read_text(encoding="utf-8")))
            if settings_path.is_file()
            else {}
        )
    except ValueError as exc:
        raise InstallError(f"{settings_path} is not valid JSON; fix it first") from exc
    merged = merge_hooks(json.loads(json.dumps(settings)), exe("spok1"))
    if merged != settings:
        plan.do(
            f"set SessionStart/SessionEnd hooks in {settings_path}",
            lambda: settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8"),
        )
    else:
        plan.note("· hooks already up to date")

    # 7. MCP servers (user scope)
    if _claude(claude, "mcp", "get", MEM0_NAME).returncode != 0:
        plan.do(
            f"add MCP server {MEM0_NAME} ({MEM0_URL}); sign in with /mcp on first use",
            lambda: _check(
                _claude(
                    claude,
                    "mcp",
                    "add",
                    "--transport",
                    "http",
                    "--scope",
                    "user",
                    MEM0_NAME,
                    MEM0_URL,
                )
            ),
        )
    else:
        plan.note(f"· MCP {MEM0_NAME} already configured")
    notify_exe = exe("spok1-notify-mcp")
    existing = _claude(claude, "mcp", "get", NOTIFY_NAME)
    if existing.returncode != 0 or str(notify_exe) not in existing.stdout:

        def add_notify() -> None:
            if existing.returncode == 0:
                _claude(claude, "mcp", "remove", NOTIFY_NAME, "--scope", "user")
            _check(
                _claude(
                    claude,
                    "mcp",
                    "add",
                    "--scope",
                    "user",
                    "--transport",
                    "stdio",
                    NOTIFY_NAME,
                    "--",
                    str(notify_exe),
                )
            )

        plan.do(f"add MCP server {NOTIFY_NAME} ({notify_exe})", add_notify)
    else:
        plan.note(f"· MCP {NOTIFY_NAME} already configured")

    # 8. machine name
    name = machine or (
        config.machine_name() if config.machine_file().is_file() else config.default_machine_name()
    )
    if not config.machine_file().is_file() or (machine and machine != config.machine_name()):
        plan.do(f"register this machine as '{name}'", lambda: config.register_machine(name))
    else:
        plan.note(f"· machine: {config.machine_name()}")

    # 9. env template
    agent_dir = paths.agent_config_dir()
    example = agent_dir / ".env.example"
    env = config.env_file()

    def write_env() -> None:
        agent_dir.mkdir(parents=True, exist_ok=True)
        example.write_text(ENV_TEMPLATE, encoding="utf-8")
        if not env.exists():
            env.write_text(ENV_TEMPLATE, encoding="utf-8")
            if sys.platform != "win32":
                env.chmod(0o600)

    plan.do(f"write {example}" + ("" if env.exists() else f" and empty {env}"), write_env)

    # next steps
    plan.note("")
    plan.note("Next:")
    if not config.get_setting("DISCORD_WEBHOOK_ALERTS"):
        plan.note(f"  1. Paste your #alerts webhook URL into {env} (DISCORD_WEBHOOK_ALERTS=)")
    plan.note("  2. Start Claude Code, run /mcp, and sign in to mem0 in the browser")
    plan.note(f'  3. Test: & "{exe("spok1")}" notify "hello from spok1"')


def _check(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise InstallError((result.stderr or result.stdout).strip()[:300] or "claude mcp failed")


def _link_agents(plan: Plan, link: Path, target: Path) -> None:
    if not target.is_dir():
        plan.note(f"· {target} missing; skipping agents link")
        return
    if is_link(link):
        if same_target(link, target):
            plan.note(f"· {link} → brain (ok)")
            return
        plan.do(
            f"relink {link} → {target}", lambda: (remove_link(link), make_dir_link(link, target))
        )
    elif link.exists():
        plan.note(
            f"! {link} is a real folder; not replacing it. Move your agents into "
            f"{target} and delete {link}, then rerun."
        )
    else:
        plan.do(f"link {link} → {target}", lambda: make_dir_link(link, target))


def _link_skills(plan: Plan, skills_home: Path, brain_skills: Path) -> None:
    if not brain_skills.is_dir():
        return
    wanted = {p.name: p for p in brain_skills.iterdir() if p.is_dir()}
    for name, target in sorted(wanted.items()):
        link = skills_home / name
        if is_link(link) and same_target(link, target):
            continue
        if link.exists() and not is_link(link):
            plan.note(f"! {link} already exists (not spok1's); brain skill '{name}' not linked")
            continue

        def relink(link: Path = link, target: Path = target) -> None:
            if is_link(link):
                remove_link(link)
            make_dir_link(link, target)

        plan.do(f"link skill {name}", relink)
    # remove dangling links that used to point into the brain
    if skills_home.is_dir():
        brain_real = os.path.realpath(brain_skills)
        for entry in skills_home.iterdir():
            if is_link(entry) and entry.name not in wanted:
                real = os.path.realpath(entry)
                if real.startswith(brain_real):
                    plan.do(f"remove stale skill link {entry.name}", lambda e=entry: remove_link(e))
