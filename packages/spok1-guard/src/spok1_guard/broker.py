"""The guard's side of the approval broker (PRD §6.2.3, T3 two-key).

On the VM two folders connect the guard (runs as the agent's user, spok1) and agentd (spok1d):
  /var/spool/spok1/requests   the guard WRITES requests here (spok1 can write; spok1d reads)
  /var/lib/spok1-approvals    agentd WRITES decisions here (only spok1d can write; spok1 reads)
An agent can file a request but can never approve one. On a machine without these folders
(the laptop), T3 stays a plain deny: the owner runs it by hand.

An approval is for ONE exact action (tool + input + folder), valid 10 minutes, usable once.
"""

from __future__ import annotations

import json
import os
import time

from spok1_guard._compat import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

SPOOL = "/var/spool/spok1/requests"
APPROVALS = "/var/lib/spok1-approvals"


def action_hash(tool_name: str, tool_input: dict[str, Any], cwd: str) -> str:
    try:
        from _sha2 import sha256  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
    except ImportError:  # pragma: no cover
        from hashlib import sha256
    keep = {
        k: v
        for k, v in tool_input.items()
        if k not in ("description", "timeout", "run_in_background")
    }
    blob = json.dumps({"tool": tool_name, "input": keep, "cwd": cwd}, sort_keys=True, default=str)
    return str(sha256(blob.encode()).hexdigest())[:24]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def enabled(spool: str | None = None, approvals: str | None = None) -> bool:
    return os.path.isdir(spool or SPOOL) and os.path.isdir(approvals or APPROVALS)


def check(
    h: str, approvals: str | None = None, spool: str | None = None, now: float | None = None
) -> str:
    """'approved' (and consumes it), 'denied', or 'none'."""
    approvals, spool = approvals or APPROVALS, spool or SPOOL
    now = time.time() if now is None else now
    path = os.path.join(approvals, h + ".json")
    if not os.path.isfile(path):
        return "none"
    try:
        with open(path, encoding="utf-8") as handle:
            rec = json.load(handle)
    except (OSError, ValueError):
        return "none"
    if rec.get("decision") == "denied":
        return "denied"
    if rec.get("decision") != "approved" or float(rec.get("expires", 0)) < now:
        return "none"
    used = os.path.join(spool, h + ".used")
    try:  # one use: O_EXCL makes a second attempt fail even if two run at once
        os.close(os.open(used, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640))
    except FileExistsError:
        return "none"
    except OSError:
        return "none"
    return "approved"


def request(h: str, record: dict[str, Any], spool: str | None = None) -> bool:
    """File a request (once per action). Returns True if this call created it."""
    spool = spool or SPOOL
    path = os.path.join(spool, h + ".json")
    if os.path.exists(path):
        return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"hash": h, "ts": time.time(), **record}, handle)
    os.replace(tmp, path)
    return True
