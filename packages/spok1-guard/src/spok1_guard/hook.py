"""The PreToolUse hook: classify the call, log it, answer Claude Code.

Decisions (PRD §6, owner decision 3a: the guard only ever makes things stricter):
  T0, T1     → no decision: Claude Code's normal permission rules apply (T1 is logged for
               the daily batch from Phase 5)
  T2         → "ask": a permission prompt with the reason (auto-denied in headless -p runs)
  T3         → "deny": needs two-key approval through the broker (Phase 5)
  FORBIDDEN  → "deny"
  guard error → exit 2 (blocks the call): it fails closed.
"""

from __future__ import annotations

import json
import os
import sys

from spok1_guard import audit
from spok1_guard._compat import TYPE_CHECKING, cast
from spok1_guard.paths import PathContext
from spok1_guard.policy import default_policy_path, load_policy
from spok1_guard.tiers import Tier, Verdict
from spok1_guard.tools import classify_tool

if TYPE_CHECKING:
    from typing import Any


def _temp_dirs(data: dict[str, Any]) -> list[str]:
    dirs = [
        d for d in (os.environ.get("TEMP"), os.environ.get("TMP"), os.environ.get("TMPDIR")) if d
    ]
    if sys.platform != "win32":
        dirs.append("/tmp")  # noqa: S108 - a scratch root for path classification only
    scratch = data.get("scratchpad_dir")
    if isinstance(scratch, str) and scratch:
        dirs.append(scratch)
    return dirs


def _managed_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "ClaudeCode")
    if sys.platform == "darwin":
        return "/Library/Application Support/ClaudeCode"
    return "/etc/claude-code"


def decide(
    data: dict[str, Any], policy_path: str | os.PathLike[str] | None, home: str | os.PathLike[str]
) -> tuple[Verdict, dict[str, Any] | None]:
    policy = load_policy(policy_path)
    guard_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    installed = "site-packages" not in guard_dir and ".venv" not in guard_dir
    ctx = PathContext(
        cwd=str(data.get("cwd") or os.getcwd()),
        home=os.fspath(home),
        temp_dirs=_temp_dirs(data),
        policy=policy,
        policy_path=os.fspath(policy_path) if policy_path else "",
        guard_dir=os.path.dirname(guard_dir) if installed else "",
        managed_settings=_managed_dir(),
    )
    tool_name = str(data.get("tool_name") or "")
    raw_input = data.get("tool_input")
    tool_input = cast("dict[str, Any]", raw_input) if isinstance(raw_input, dict) else {}
    verdict = classify_tool(tool_name, tool_input, ctx)
    return verdict, _output(verdict)


def _output(verdict: Verdict) -> dict[str, Any] | None:
    if verdict.tier <= Tier.T1:
        return None
    if verdict.tier == Tier.T2:
        decision, reason = "ask", f"spok1 guard · T2 (needs approval): {verdict.reason}"
    elif verdict.tier == Tier.T3:
        decision = "deny"
        reason = (
            f"spok1 guard · T3 (two-key): {verdict.reason}. Not allowed through an agent: it "
            "needs approval with a confirm code, a verified backup and a dry run. Ask the "
            "owner to run it by hand, or wait for the approval broker."
        )
    else:
        decision = "deny"
        reason = (
            f"spok1 guard · FORBIDDEN: {verdict.reason}. This is never allowed through an agent."
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    policy_path: str | None = default_policy_path()
    if "--policy" in args:
        idx = args.index("--policy")
        policy_path = args[idx + 1] if idx + 1 < len(args) else None
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or os.path.expanduser("~")
    try:
        raw = sys.stdin.read()
        data: object = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("hook input is not a JSON object")
        payload = cast("dict[str, Any]", data)
        verdict, output = decide(payload, policy_path, home)
    except Exception as exc:  # fail closed: block the call
        sys.stderr.write(
            f"spok1 guard error, call blocked (fails closed): {type(exc).__name__}: {exc}\n"
        )
        return 2
    try:
        tool_input = payload.get("tool_input")
        audit.append(
            home,
            {
                "event": "pretooluse",
                "session": str(payload.get("session_id", ""))[:64],
                "agent": str(payload.get("agent_type") or payload.get("agent_id") or "main")[:64],
                "cwd": str(payload.get("cwd", ""))[:300],
                "tool": str(payload.get("tool_name", ""))[:100],
                "summary": audit.summarise(
                    str(payload.get("tool_name", "")),
                    cast("dict[str, Any]", tool_input) if isinstance(tool_input, dict) else {},
                ),
                "tier": verdict.tier.label,
                "rule": verdict.rule,
                "reason": audit.redact(verdict.reason)[:300],
                "decision": "pass"
                if output is None
                else output["hookSpecificOutput"]["permissionDecision"],
            },
        )
    except Exception as exc:  # logging must not block work, but must be visible
        sys.stderr.write(f"spok1 guard: audit log write failed: {exc}\n")
    if output is not None:
        sys.stdout.write(json.dumps(output))
    return 0
