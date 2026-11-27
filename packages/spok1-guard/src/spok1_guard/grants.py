"""Standing grants (PRD §6.3): narrow, time-limited pre-approvals for T2 actions.

The owner writes them BY HAND in an admin-only file next to the policy:
  Windows  C:\\Program Files\\spok1\\grants.toml      Linux  /etc/spok1/grants.toml
Agents can't write there, so an agent can never grant itself anything.

    [[grant]]
    id = "g1"
    why = "routine pushes to my own repos"
    rules = ["git.push"]                 # guard rule ids (shown in every ask/deny reason)
    cwd = ["~/Desktop/spok1", "~/brain"] # optional: only when the session is under one of these
    contains = ["spok1-vm"]              # optional: only when the command contains one of these
    expires = 2026-12-11                 # required; at most 30 days ahead

A grant can only turn a T2 "ask" into a pass. It never applies to T3 or FORBIDDEN (in code).
"""

from __future__ import annotations

import os
import time

from spok1_guard._compat import TYPE_CHECKING, cast
from spok1_guard.tiers import Tier, Verdict

if TYPE_CHECKING:
    from typing import Any

MAX_DAYS = 30
# Rules a grant may never cover, even at T2: settings/permissions changes and secrets use.
NEVER_GRANTABLE = {
    "git.config",
    "setx",
    "pwsh.policy",
    "env.print",
    "claude.headless",
    "claude.other",
    "spok1",
    "path.unknown",
    "parse",
    "unknown",
    "tool.unknown",
}


class Grant:
    __slots__ = ("contains", "cwd", "expires", "id", "rules", "why")

    def __init__(
        self,
        gid: str,
        rules: list[str],
        expires: float,
        cwd: list[str],
        contains: list[str],
        why: str,
    ) -> None:
        self.id = gid
        self.rules = rules
        self.expires = expires
        self.cwd = cwd
        self.contains = contains
        self.why = why


def _list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(x) for x in cast("list[object]", value)]
    return []


def _ts(value: object) -> float | None:
    import datetime as dt

    if isinstance(value, dt.datetime):
        return value.timestamp()
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, 23, 59, 59).timestamp()
    return None


def default_path(policy_path: str | None) -> str | None:
    if not policy_path:
        return None
    return os.path.join(os.path.dirname(policy_path), "grants.toml")


def load(path: str | None, now: float | None = None) -> tuple[list[Grant], list[str]]:
    """Valid, unexpired grants plus problems (bad entries are skipped, never widened)."""
    if not path or not os.path.isfile(path):
        return [], []
    import tomllib

    now = time.time() if now is None else now
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    out: list[Grant] = []
    problems: list[str] = []
    for i, raw in enumerate(cast("list[object]", data.get("grant") or [])):
        if not isinstance(raw, dict):
            continue
        entry = cast("dict[str, Any]", raw)
        gid = str(entry.get("id") or f"#{i + 1}")
        rules = [r for r in _list(entry.get("rules")) if r and r not in NEVER_GRANTABLE]
        expires = _ts(entry.get("expires"))
        if not rules:
            problems.append(f"grant {gid}: no grantable rules")
            continue
        if expires is None:
            problems.append(f"grant {gid}: missing expires")
            continue
        if expires < now:
            continue  # expired: silently inactive
        if expires > now + MAX_DAYS * 86400:
            problems.append(f"grant {gid}: expires more than {MAX_DAYS} days ahead")
            continue
        out.append(
            Grant(
                gid,
                rules,
                expires,
                _list(entry.get("cwd")),
                _list(entry.get("contains")),
                str(entry.get("why") or ""),
            )
        )
    return out, problems


def apply(verdict: Verdict, grants: list[Grant], cwd_norm: str, command: str, norm: Any) -> Verdict:
    """Turn a T2 verdict into a pass if a grant covers it. T3/FORBIDDEN are never touched."""
    if verdict.tier != Tier.T2 or not grants:
        return verdict
    for g in grants:
        if verdict.rule not in g.rules:
            continue
        if g.cwd and not any(
            cwd_norm == norm(c) or cwd_norm.startswith(norm(c).rstrip("/") + "/") for c in g.cwd
        ):
            continue
        if g.contains and not any(c in command for c in g.contains):
            continue
        return Verdict(Tier.T1, f"standing grant {g.id}: {verdict.reason}", f"grant:{g.id}")
    return verdict
