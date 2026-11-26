"""Running the `claude` binary safely: Max subscription only, never an API key."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from spok1_core import paths

# Set on every claude process spok1 starts, so spok1's own hooks skip it (no capture loops).
INTERNAL_ENV = "SPOK1_INTERNAL"
# Never passed to a claude child: any of these would bill the API instead of Max.
_BLOCKED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class ClaudeError(RuntimeError):
    pass


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        paths.home() / ".local" / "bin" / "claude.exe",
        paths.home() / ".local" / "bin" / "claude",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _BLOCKED_ENV}
    env[INTERNAL_ENV] = "1"
    return env


def is_internal() -> bool:
    return os.environ.get(INTERNAL_ENV) == "1"


def run_print(
    prompt: str,
    *,
    model: str = "haiku",
    max_turns: int = 1,
    timeout: int = 300,
    cwd: Path | None = None,
) -> str:
    """Run `claude -p` with no tools and no MCP servers, and return the result text."""
    exe = find_claude()
    if exe is None:
        raise ClaudeError("claude CLI not found; run the spok1 laptop installer")
    cmd = [
        exe,
        "-p",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools",
        "",
        "--strict-mcp-config",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=child_env(),
            cwd=cwd or paths.state_dir(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"claude -p timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-300:]
        raise ClaudeError(f"claude -p exited {proc.returncode}: {tail}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise ClaudeError("claude -p did not return JSON") from exc
    if not isinstance(data, dict):
        raise ClaudeError("unexpected claude -p output")
    result = cast(dict[str, Any], data)
    if result.get("is_error"):
        raise ClaudeError(f"claude -p reported an error: {str(result.get('result'))[:300]}")
    return str(result.get("result", ""))
