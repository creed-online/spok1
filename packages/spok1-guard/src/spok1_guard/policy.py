"""The guardrails policy: built-in defaults, overridden by an admin-owned `guardrails.toml`.

The file lives where agents can't write it:
  Windows  C:\\Program Files\\spok1\\guardrails.toml
  Linux    /etc/spok1/guardrails.toml
  macOS    /Library/Application Support/spok1/guardrails.toml
Its path is passed to the hook by managed settings (also admin-owned), never by env vars.
"""

from __future__ import annotations

import os
import sys

from spok1_guard._compat import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Any

DEFAULT_ALLOWED_HOSTS = (
    "api.github.com",
    "github.com",
    "discord.com",
    "mcp.mem0.ai",
    "api.mem0.ai",
    "hc-ping.com",
)

# MCP tool name patterns (fnmatch, case-insensitive) → tier. First match wins.
DEFAULT_MCP_TIERS: tuple[tuple[str, str], ...] = (
    ("mcp__*mem0*__delete*", "T3"),
    ("mcp__*mem0*__*", "T0"),  # Mem0 reads and writes are T0 (PRD §6.1)
    ("mcp__spok1-notify__notify", "T0"),
    ("mcp__claude-vscode__*", "T0"),
)


def default_policy_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return os.path.join(base, "spok1", "guardrails.toml")
    if sys.platform == "darwin":
        return "/Library/Application Support/spok1/guardrails.toml"
    return "/etc/spok1/guardrails.toml"


class Policy:
    """Tunables for the guard. Budgets and quality floors in the same file are read by the
    auditor (spok1-core), not the guard."""

    def __init__(
        self,
        allowed_hosts: list[str] | None = None,
        extra_roots: list[str] | None = None,
        extra_protected: list[str] | None = None,
        extra_secrets: list[str] | None = None,
        mcp_tiers: list[tuple[str, str]] | None = None,
        mass_action_limit: int = 50,
    ) -> None:
        self.allowed_hosts = list(DEFAULT_ALLOWED_HOSTS) if allowed_hosts is None else allowed_hosts
        self.extra_roots = extra_roots or []  # extra writable roots (T0)
        self.extra_protected = extra_protected or []  # never writable (FORBIDDEN)
        self.extra_secrets = extra_secrets or []  # never readable (FORBIDDEN)
        self.mcp_tiers = list(DEFAULT_MCP_TIERS) if mcp_tiers is None else mcp_tiers
        self.mass_action_limit = mass_action_limit
        self.raw: dict[str, Any] = {}
        self.source = "built-in defaults"


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in cast("list[object]", value)]
    return []


def load_policy(path: str | os.PathLike[str] | None) -> Policy:
    """Load the policy. A missing file means built-in defaults; a broken file raises."""
    policy = Policy()
    if path is None or not os.path.isfile(path):
        return policy
    import tomllib  # only when a policy file exists

    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    policy.raw = data
    policy.source = os.fspath(path)
    paths = cast("dict[str, Any]", data.get("paths", {}))
    policy.extra_roots = _str_list(paths.get("extra_roots"))
    policy.extra_protected = _str_list(paths.get("extra_protected"))
    policy.extra_secrets = _str_list(paths.get("extra_secrets"))
    http = cast("dict[str, Any]", data.get("http", {}))
    if "allowed_hosts" in http:
        policy.allowed_hosts = _str_list(http["allowed_hosts"])
    mcp = cast("dict[str, Any]", data.get("mcp", {}))
    rules = mcp.get("tiers")
    if isinstance(rules, list):
        parsed: list[tuple[str, str]] = []
        for item in cast("list[object]", rules):
            if isinstance(item, dict):
                entry = cast("dict[str, Any]", item)
                parsed.append((str(entry.get("match", "")), str(entry.get("tier", "T2"))))
        policy.mcp_tiers = parsed + list(DEFAULT_MCP_TIERS)
    limits = cast("dict[str, Any]", data.get("limits", {}))
    if isinstance(limits.get("mass_action_records"), int):
        policy.mass_action_limit = int(limits["mass_action_records"])
    return policy
