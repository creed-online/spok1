"""`spok1 guard install|uninstall|status|bench`: put the guard where agents can't touch it.

Run `install` from an ADMIN terminal (Windows) or with sudo (Linux/macOS). It creates:
  <base>/python/…              an admin-owned Python (uv-managed CPython 3.12)
  <base>/guard/                the guard code (stdlib only) + run_guard.py, precompiled
  <base>/guardrails.toml       the policy (kept if it already exists)
  <managed>/managed-settings.json
      PreToolUse hook → the guard (onFailure: block), bypass mode disabled,
      deny rules for the forbidden list, max 3 concurrent sub-agents.
with <base> = C:\\Program Files\\spok1 | /opt/spok1 (policy in /etc/spok1 on Linux) and
<managed> = C:\\Program Files\\ClaudeCode | /etc/claude-code
            | /Library/Application Support/ClaudeCode.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from spok1_cli.install import repo_root

__all__ = [
    "install",
    "layout",
    "reference_paths",
    "remove_from",
    "render",
    "repo_root",
    "status",
    "uninstall",
]

PYTHON_VERSION = "3.12"
MAX_SUBAGENTS = "3"
DENY_RULES = [
    # secrets (the guard blocks these too; deny rules are a second, independent layer)
    "Read(**/.env)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.claude/.credentials.json)",
    "Read(~/.config/agent/.env)",
    "Read(~/.config/gh/hosts.yml)",
    "Read(**/*.pem)",
    # settings and hooks
    "Edit(~/.claude/settings.json)",
    "Edit(~/.claude/settings.local.json)",
    "Edit(**/.claude/settings.json)",
    "Edit(**/.claude/settings.local.json)",
    # the worst commands, in the form Claude usually writes them
    "Bash(claude --dangerously-skip-permissions*)",
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(rm -rf /)",
    "Bash(rm -rf ~)",
]


@dataclass
class Layout:
    base: Path  # admin-owned root for spok1's guard
    python_dir: Path
    guard_dir: Path
    policy: Path
    managed_dir: Path

    @property
    def managed_settings(self) -> Path:
        return self.managed_dir / "managed-settings.json"

    @property
    def runner(self) -> Path:
        return self.guard_dir / "run_guard.py"


def layout(platform: str = sys.platform) -> Layout:
    if platform == "win32":
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        base = pf / "spok1"
        return Layout(
            base, base / "python", base / "guard", base / "guardrails.toml", pf / "ClaudeCode"
        )
    if platform == "darwin":
        base = Path("/Library/Application Support/spok1")
        return Layout(
            base,
            base / "python",
            base / "guard",
            base / "guardrails.toml",
            Path("/Library/Application Support/ClaudeCode"),
        )
    base = Path("/opt/spok1")
    return Layout(
        base,
        base / "python",
        base / "guard",
        Path("/etc/spok1/guardrails.toml"),
        Path("/etc/claude-code"),
    )


def reference_paths(platform: str) -> tuple[str, str, str]:
    """(python, runner, policy) for the committed reference settings, with the right path
    separators on any OS (CI renders the Windows file on Linux)."""
    from pathlib import PurePosixPath, PureWindowsPath

    if platform == "win32":
        base = PureWindowsPath(r"C:\Program Files\spok1")
        return (
            str(base / "python" / "cpython-3.12" / "python.exe"),
            str(base / "guard" / "run_guard.py"),
            str(base / "guardrails.toml"),
        )
    base_p = PurePosixPath("/opt/spok1")
    return (
        str(base_p / "python" / "cpython-3.12" / "bin" / "python3"),
        str(base_p / "guard" / "run_guard.py"),
        "/etc/spok1/guardrails.toml",
    )


def hook_entry(python: str, runner: str, policy: str) -> dict[str, Any]:
    return {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": python,
                "args": ["-I", "-S", runner, "--policy", policy],
                "timeout": 10,
                "onFailure": "block",
                "statusMessage": "spok1 guard",
            }
        ],
    }


def _is_spok1_group(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = cast(dict[str, Any], group).get("hooks")
    if not isinstance(hooks, list):
        return False
    for h in cast(list[object], hooks):
        if isinstance(h, dict):
            args = cast(dict[str, Any], h).get("args")
            if isinstance(args, list) and any(
                "run_guard.py" in str(a) for a in cast(list[object], args)
            ):
                return True
    return False


# VM profile (owner decision, Phase 4): nobody watches the VM's terminal, so Claude Code's own
# prompts would flood the phone. Its rules allow these tools, and the guard is the approval layer:
# T0/T1 run, T2 "ask" goes to the owner's phone (Discord permission relay), T3/FORBIDDEN deny.
VM_ALLOW = [
    "Bash",
    "Edit",
    "Write",
    "NotebookEdit",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "mcp__plugin_discord_discord__*",
    "mcp__spok1-notify__*",
    "mcp__claude_ai_Mem0__*",
]
VM_MAX_SUBAGENTS = "1"  # 1 GB RAM (option A): one sub-agent at a time


def render(
    existing: dict[str, Any], python: str, runner: str, policy: str, profile: str = "laptop"
) -> dict[str, Any]:
    """Managed settings with spok1's guard merged in (other keys and hooks are kept)."""
    settings: dict[str, Any] = json.loads(json.dumps(existing))
    perms = cast(dict[str, Any], settings.setdefault("permissions", {}))
    perms["disableBypassPermissionsMode"] = "disable"
    deny = [str(r) for r in cast(list[object], perms.get("deny") or [])]
    perms["deny"] = deny + [r for r in DENY_RULES if r not in deny]
    env = cast(dict[str, Any], settings.setdefault("env", {}))
    env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = MAX_SUBAGENTS
    if profile == "vm":
        allow = [str(r) for r in cast(list[object], perms.get("allow") or [])]
        perms["allow"] = allow + [r for r in VM_ALLOW if r not in allow]
        perms["defaultMode"] = "default"
        perms["disableAutoMode"] = "disable"  # auto mode would approve by classifier, not guard
        env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = VM_MAX_SUBAGENTS
    hooks = cast(dict[str, Any], settings.setdefault("hooks", {}))
    groups = [
        g for g in cast(list[object], hooks.get("PreToolUse") or []) if not _is_spok1_group(g)
    ]
    hooks["PreToolUse"] = [hook_entry(python, runner, policy), *groups]
    return settings


def remove_from(existing: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(json.dumps(existing))
    hooks = cast(dict[str, Any], settings.get("hooks") or {})
    groups = [
        g for g in cast(list[object], hooks.get("PreToolUse") or []) if not _is_spok1_group(g)
    ]
    if groups:
        hooks["PreToolUse"] = groups
    else:
        hooks.pop("PreToolUse", None)
    if not hooks:
        settings.pop("hooks", None)
    perms = cast(dict[str, Any], settings.get("permissions") or {})
    if "deny" in perms:
        perms["deny"] = [r for r in cast(list[object], perms["deny"]) if r not in DENY_RULES]
        if not perms["deny"]:
            perms.pop("deny")
    perms.pop("disableBypassPermissionsMode", None)
    if "allow" in perms:
        perms["allow"] = [r for r in cast(list[object], perms["allow"]) if r not in VM_ALLOW]
        if not perms["allow"]:
            perms.pop("allow")
    for key in ("defaultMode", "disableAutoMode"):
        perms.pop(key, None)
    if not perms:
        settings.pop("permissions", None)
    env = cast(dict[str, Any], settings.get("env") or {})
    env.pop("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", None)
    if not env:
        settings.pop("env", None)
    return settings


# ── helpers ────────────────────────────────────────────────────────────


def is_admin() -> bool:
    if sys.platform == "win32":
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except OSError:
            return False
    return os.geteuid() == 0  # type: ignore[attr-defined,unused-ignore]


def find_python(python_dir: Path) -> Path | None:
    exe = "python.exe" if sys.platform == "win32" else "python3"
    candidates = sorted(
        python_dir.glob(
            f"cpython-{PYTHON_VERSION}*/{'' if sys.platform == 'win32' else 'bin/'}{exe}"
        )
    )
    return candidates[-1] if candidates else None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return cast(dict[str, Any], data)


def _sandbox_env() -> dict[str, str]:
    """Run test calls with a throwaway home, so they don't land in the real audit log."""
    import tempfile

    home = tempfile.mkdtemp(prefix="spok1-guard-test-")
    return {**os.environ, "USERPROFILE": home, "HOME": home}


def self_test(python: Path, runner: Path, policy: Path) -> list[str]:
    """Run the installed hook on known inputs. Returns problems (empty = OK)."""
    problems: list[str] = []
    env = _sandbox_env()
    cases = [
        ({"command": "git status"}, None),
        ({"command": "git push origin main"}, "ask"),
        ({"command": "git push --force"}, "deny"),
        ({"command": "cat .env"}, "deny"),
    ]
    for tool_input, expected in cases:
        payload = json.dumps(
            {"cwd": str(Path.home()), "tool_name": "Bash", "tool_input": tool_input}
        )
        result = subprocess.run(  # noqa: S603
            [str(python), "-I", "-S", str(runner), "--policy", str(policy)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        got = None
        if result.returncode != 0:
            problems.append(
                f"{tool_input['command']}: exit {result.returncode}: {result.stderr.strip()[:200]}"
            )
            continue
        if result.stdout.strip():
            got = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        if got != expected:
            problems.append(f"{tool_input['command']}: expected {expected}, got {got}")
    return problems


def bench(python: Path, runner: Path, policy: Path, runs: int = 40) -> tuple[float, float]:
    payload = json.dumps(
        {
            "cwd": str(Path.home()),
            "tool_name": "Bash",
            "tool_input": {"command": "git -C . push origin main && rm -rf build"},
        }
    )
    args = [str(python), "-I", "-S", str(runner), "--policy", str(policy)]
    env = _sandbox_env()
    subprocess.run(args, input=payload, capture_output=True, text=True, check=False, env=env)  # noqa: S603
    times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(args, input=payload, capture_output=True, text=True, check=False, env=env)  # noqa: S603
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times), statistics.quantiles(times, n=20)[18]


# ── commands ───────────────────────────────────────────────────────────


def install(dry_run: bool, update_policy: bool, profile: str = "laptop") -> int:
    lay = layout()
    src = repo_root() / "packages" / "spok1-guard"
    lines: list[str] = []

    def step(text: str) -> None:
        lines.append(("would: " if dry_run else "✓ ") + text)
        print(lines[-1], flush=True)

    if not dry_run and not is_admin():
        print(
            "✗ Run this from an ADMIN terminal "
            "(Windows: right-click PowerShell → Run as administrator).",
            file=sys.stderr,
        )
        return 1

    # 1. admin-owned Python
    python = find_python(lay.python_dir)
    if python is None:
        step(f"install Python {PYTHON_VERSION} into {lay.python_dir} (uv python install)")
        if not dry_run:
            uv = shutil.which("uv") or str(
                Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
            )
            result = subprocess.run(  # noqa: S603
                [uv, "python", "install", PYTHON_VERSION, "--install-dir", str(lay.python_dir)],
                check=False,
            )
            python = find_python(lay.python_dir)
            if result.returncode != 0 or python is None:
                print("✗ uv python install failed", file=sys.stderr)
                return 1
    else:
        print(f"· python: {python}")

    # 2. guard code (replaced every time, then precompiled: users can't write .pyc there)
    step(f"copy the guard to {lay.guard_dir} and precompile it")
    if not dry_run:
        if lay.guard_dir.exists():
            shutil.rmtree(lay.guard_dir)
        shutil.copytree(
            src / "src" / "spok1_guard",
            lay.guard_dir / "src" / "spok1_guard",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy2(src / "run_guard.py", lay.runner)
        if python is None:
            return 1
        subprocess.run(  # noqa: S603 - fixed argv
            [str(python), "-I", "-m", "compileall", "-q", str(lay.guard_dir)], check=True
        )

    # 3. policy
    template = repo_root() / "claude" / "guardrails.toml"
    if not lay.policy.exists() or update_policy:
        what = "update" if lay.policy.exists() else "create"
        step(f"{what} the policy {lay.policy}")
        if not dry_run:
            lay.policy.parent.mkdir(parents=True, exist_ok=True)
            if lay.policy.exists():
                shutil.copy2(
                    lay.policy, lay.policy.with_suffix(f".toml.bak-{datetime.now():%Y%m%d-%H%M%S}")
                )
            shutil.copy2(template, lay.policy)
    else:
        print(f"· policy kept: {lay.policy} (use --update-policy to replace it)")

    # 4. managed settings
    existing = _load_json(lay.managed_settings)
    py_str = str(python) if python else str(lay.python_dir / "<python>")
    merged = render(existing, py_str, str(lay.runner), str(lay.policy), profile)
    if merged != existing:
        step(f"write the guard hook + deny rules into {lay.managed_settings}")
        if not dry_run:
            lay.managed_dir.mkdir(parents=True, exist_ok=True)
            if lay.managed_settings.exists():
                shutil.copy2(
                    lay.managed_settings,
                    lay.managed_settings.with_suffix(f".json.bak-{datetime.now():%Y%m%d-%H%M%S}"),
                )
            lay.managed_settings.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    else:
        print("· managed settings already up to date")

    # 5. self-test + latency
    if not dry_run and python is not None:
        problems = self_test(python, lay.runner, lay.policy)
        if problems:
            print("✗ self-test failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        print("✓ self-test passed (pass / ask / deny / FORBIDDEN as expected)")
        median, p95 = bench(python, lay.runner, lay.policy)
        verdict = "PASS" if p95 <= 100 else "FAIL (over 100 ms)"
        print(f"· latency gate: median {median:.0f} ms, p95 {p95:.0f} ms → {verdict}")
        print("Restart Claude Code (close and reopen VS Code) so the new managed settings load.")
    return 0


def uninstall(dry_run: bool) -> int:
    lay = layout()
    if not dry_run and not is_admin():
        print("✗ Run this from an ADMIN terminal.", file=sys.stderr)
        return 1
    existing = _load_json(lay.managed_settings)
    cleaned = remove_from(existing)
    print(("would: " if dry_run else "✓ ") + f"remove the guard from {lay.managed_settings}")
    if not dry_run:
        if cleaned:
            lay.managed_settings.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
        elif lay.managed_settings.exists():
            lay.managed_settings.unlink()
    print(f"· left in place: {lay.base} (policy, python, guard code): delete by hand if you want")
    return 0


def status() -> int:
    lay = layout()
    settings = _load_json(lay.managed_settings)
    hooks = cast(dict[str, Any], settings.get("hooks") or {})
    installed = any(_is_spok1_group(g) for g in cast(list[object], hooks.get("PreToolUse") or []))
    python = find_python(lay.python_dir)
    hook_state = "guard hook present" if installed else "NO guard hook"
    policy_state = "present" if lay.policy.exists() else "missing → built-in defaults"
    print(f"managed settings : {lay.managed_settings} ({hook_state})")
    print(f"policy           : {lay.policy} ({policy_state})")
    print(f"guard code       : {lay.guard_dir} ({'present' if lay.runner.exists() else 'missing'})")
    print(f"python           : {python or 'missing'}")
    if installed and python and lay.runner.exists():
        median, p95 = bench(python, lay.runner, lay.policy, runs=20)
        print(f"latency          : median {median:.0f} ms, p95 {p95:.0f} ms")
    return 0 if installed else 1
