"""Built-in watchers (PRD §3). Plain checks, no LLM. Each returns alerts to raise.

| watcher        | interval | alert                                                      |
| disk / memory  | 5 min    | P2 when / is over 90% full or available RAM stays < 80 MB  |
| manager        | 1 min    | P1 when claude-manager isn't active                        |
| login expiry   | 6 h      | P2 within 10 days of the /login end, P1 within 2 days      |
| job failures   | on run   | P2 on a failure, P1 after 3 in a row                       |
| CI on main     | 10 min   | P2 when the latest run on the default branch failed        |
| PR waiting     | 1 h      | P2 when an open PR has waited more than 24 h               |
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

RUNAS = ["sudo", "-n", "-u", "spok1", "/opt/spok1/bin/spok1-runas"]


@dataclass
class Alert:
    priority: str
    title: str
    body: str
    key: str
    project: str = "global"
    source: str = "watcher"


Run = Callable[[list[str]], tuple[int, str]]


def run_cmd(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return p.returncode, (p.stdout or p.stderr)


def disk_and_memory(root: str = "/", meminfo: Path = Path("/proc/meminfo")) -> list[Alert]:
    out: list[Alert] = []
    usage = shutil.disk_usage(root)
    pct = 100 * usage.used / usage.total if usage.total else 0
    if pct > 90:
        out.append(
            Alert(
                "P2",
                f"Disk {pct:.0f}% full",
                f"{usage.free // 2**20} MB free on {root}",
                "watch:disk",
                source="disk",
            )
        )
    if meminfo.is_file():
        fields = {
            k.strip(): v
            for k, _, v in (ln.partition(":") for ln in meminfo.read_text().splitlines())
        }
        avail_kb = int(fields.get("MemAvailable", "0 kB").split()[0])
        if avail_kb < 80 * 1024:
            out.append(
                Alert(
                    "P2",
                    "Low memory",
                    f"{avail_kb // 1024} MB available",
                    "watch:memory",
                    source="memory",
                )
            )
    return out


def discord_plugin_running(run: Run = run_cmd) -> bool:
    code, _out = run(["pgrep", "-u", "spok1", "-x", "bun"])
    return code == 0


class BotWatch:
    """The manager can be 'active' while its Discord plugin failed to start (seen twice on the
    1 GB VM). Two misses in a row → restart the manager (allowed by one sudo rule); if that
    didn't help within 10 minutes → P1 alert."""

    def __init__(self) -> None:
        self.misses = 0
        self.last_restart = 0.0

    def check(self, now: float, run: Run = run_cmd) -> list[Alert]:
        _code, out = run(["systemctl", "is-active", "claude-manager"])
        if out.strip() != "active" or discord_plugin_running(run):
            self.misses = 0
            return []
        self.misses += 1
        if self.misses < 2:
            return []
        if now - self.last_restart > 600:
            self.last_restart = now
            self.misses = 0
            run(["sudo", "-n", "/usr/bin/systemctl", "restart", "claude-manager"])
            return [
                Alert(
                    "P3",
                    "Discord bot was offline: manager restarted",
                    "The Discord plugin wasn't running; agentd restarted claude-manager.",
                    f"bot:restart:{int(now // 3600)}",
                    source="manager",
                )
            ]
        return [
            Alert(
                "P1",
                "Discord bot is offline",
                "The Discord plugin keeps failing to start even after a restart. Check: ssh spok1@spok1-vm, tmux attach -t manager, /mcp.",
                "watch:bot",
                source="manager",
            )
        ]


def manager(run: Run = run_cmd) -> list[Alert]:
    _code, out = run(["systemctl", "is-active", "claude-manager"])
    state = out.strip() or "unknown"
    if state != "active":
        return [
            Alert(
                "P1",
                "Manager is down",
                f"claude-manager is {state}. systemd restarts it; if this repeats, check `journalctl -u claude-manager`.",
                "watch:manager",
                source="manager",
            )
        ]
    return []


def login_expiry(now: float, run: Run = run_cmd) -> list[Alert]:
    _code, out = run([*RUNAS, "auth-status"])
    try:
        data = cast(dict[str, Any], json.loads(out))
    except ValueError:
        return [
            Alert(
                "P2",
                "Can't read the Claude login status",
                out.strip()[:200],
                "watch:login:unreadable",
                source="login",
            )
        ]
    if not data.get("loggedIn"):
        return [
            Alert(
                "P1",
                "Claude is signed out on the VM",
                "Run /login in the manager: ssh spok1@spok1-vm, tmux attach -t manager.",
                "watch:login:out",
                source="login",
            )
        ]
    expires = str(data.get("loginExpiresAt") or "")
    if not expires:
        return []
    left = (datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp() - now) / 86400
    if left <= 2:
        return [
            Alert(
                "P1",
                f"Claude login ends in {left:.1f} days",
                "Renew now: ssh spok1@spok1-vm, tmux attach -t manager, then /login.",
                "watch:login:2d",
                source="login",
            )
        ]
    if left <= 10:
        return [
            Alert(
                "P2",
                f"Claude login ends in {left:.0f} days",
                "Renew before then: ssh spok1@spok1-vm, tmux attach -t manager, then /login.",
                "watch:login:10d",
                source="login",
            )
        ]
    return []


def job_failure(job_id: str, project: str, failures: int, summary: str) -> list[Alert]:
    if failures <= 0:
        return []
    priority = "P1" if failures >= 3 else "P2"
    return [
        Alert(
            priority,
            f"Job {job_id} failed" + (f" ({failures} in a row)" if failures > 1 else ""),
            summary[:500],
            f"job:{job_id}",
            project,
            "job",
        )
    ]


def repos_from_registry(brain: Path) -> list[tuple[str, str]]:
    """(project, owner/name) for every GitHub repo of every active project."""
    import yaml

    from spok1_agentd.scheduler import project_status

    path = brain / "projects" / "registry.yaml"
    if not path.is_file():
        return []
    data = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    status = project_status(brain)
    out: list[tuple[str, str]] = []
    for p in cast(list[dict[str, Any]], data.get("projects") or []):
        if status.get(str(p.get("id")), "active") != "active":
            continue
        for url in cast(list[str], p.get("repos") or []):
            if "github.com/" in url:
                slug = url.split("github.com/", 1)[1].removesuffix(".git").strip("/")
                out.append((str(p["id"]), slug))
    return out


def ci_and_prs(brain: Path, now: float, run: Run = run_cmd) -> list[Alert]:
    out: list[Alert] = []
    for project, repo in repos_from_registry(brain):
        code, text = run([*RUNAS, "gh-ci", repo])
        if code == 0:
            try:
                runs = cast(list[dict[str, Any]], json.loads(text or "[]"))
            except ValueError:
                runs = []
            if runs and runs[0].get("conclusion") == "failure":
                r = runs[0]
                out.append(
                    Alert(
                        "P2",
                        f"CI failing on {repo}",
                        f"{r.get('workflowName', '')}: {r.get('displayTitle', '')} {r.get('url', '')}",
                        f"ci:{repo}:{r.get('databaseId')}",
                        project,
                        "ci",
                    )
                )
        code, text = run([*RUNAS, "gh-prs", repo])
        if code == 0:
            try:
                prs = cast(list[dict[str, Any]], json.loads(text or "[]"))
            except ValueError:
                prs = []
            for pr in prs:
                created = datetime.fromisoformat(
                    str(pr["createdAt"]).replace("Z", "+00:00")
                ).timestamp()
                if now - created > 86400:
                    day = int(now // 86400)
                    out.append(
                        Alert(
                            "P2",
                            f"PR #{pr['number']} waiting on you: {pr['title']}",
                            str(pr.get("url", "")),
                            f"pr:{repo}:{pr['number']}:{day}",
                            project,
                            "pr",
                        )
                    )
    return out
