"""Run agent jobs: `claude -p` as the `spok1` user, through a root-owned sudo wrapper.

agentd (user spok1d) can't run arbitrary commands as spok1: sudoers allows exactly one program,
/opt/spok1/bin/spok1-runas, which only accepts `claude-job`, `git-pull-brain` and `auth-status`.
The headless token (CLAUDE_CODE_OAUTH_TOKEN) is passed for the job only; ANTHROPIC_API_KEY never.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, cast

WRAPPER = "/opt/spok1/bin/spok1-runas"
WEIGHTS = {"haiku": 1, "sonnet": 3, "opus": 15, "fable": 15}
LIMIT_RE = re.compile(
    r"hit your (session|weekly|opus|sonnet)? ?limit|rate.?limit|429|usage limit", re.IGNORECASE
)


@dataclass
class JobResult:
    status: str  # ok | failed | limited
    summary: str
    turns: int = 0
    units: int = 0
    cost_usd: float = 0.0
    model: str = ""


def command(agent: str | None, model: str, max_turns: int, cwd: str) -> list[str]:
    args = [
        "sudo",
        "-n",
        "--preserve-env=CLAUDE_CODE_OAUTH_TOKEN",
        "-u",
        "spok1",
        WRAPPER,
        "claude-job",
        cwd,
        model,
        str(max_turns),
    ]
    if agent:
        args.append(agent)
    return args


def start(
    agent: str | None, model: str, max_turns: int, cwd: str, prompt: str, token: str
) -> subprocess.Popen[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    proc = subprocess.Popen(  # noqa: S603 - fixed argv through the root-owned wrapper
        command(agent, model, max_turns, cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if proc.stdin is None:  # pragma: no cover
        raise RuntimeError("no stdin pipe")
    proc.stdin.write(prompt)
    proc.stdin.close()
    return proc


def parse_result(stdout: str, stderr: str, returncode: int, model: str) -> JobResult:
    text = (stdout or "").strip()
    try:
        data = cast(dict[str, Any], json.loads(text.splitlines()[-1] if text else "{}"))
    except ValueError:
        data = {}
    result = str(data.get("result") or "")
    turns = int(data.get("num_turns") or 0)
    cost = float(data.get("total_cost_usd") or 0.0)
    units = 0
    usage = data.get("modelUsage")
    if isinstance(usage, dict) and usage:
        for name in cast(dict[str, Any], usage):
            weight = next((w for k, w in WEIGHTS.items() if k in name.lower()), 15)
            units = max(units, turns * weight)
    else:
        units = turns * WEIGHTS.get(model, 15)
    blob = f"{result}\n{stderr}"
    if LIMIT_RE.search(blob) and (returncode != 0 or data.get("is_error")):
        return JobResult("limited", (result or stderr).strip()[:300], turns, units, cost, model)
    if returncode != 0 or data.get("is_error") or str(data.get("subtype", "success")) != "success":
        why = str(data.get("subtype") or "") or f"exit {returncode}"
        return JobResult(
            "failed", f"{why}: {(result or stderr).strip()[:300]}", turns, units, cost, model
        )
    return JobResult("ok", result.strip(), turns, units, cost, model)
