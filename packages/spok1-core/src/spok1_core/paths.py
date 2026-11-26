"""Well-known locations used by spok1 on every machine.

Every path derives from `home()`, which honours the `SPOK1_HOME` environment variable
so tests (and unusual setups) can point everything at a sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    override = os.environ.get("SPOK1_HOME")
    return Path(override) if override else Path.home()


def brain_dir() -> Path:
    """The brain git repo clone (`~/brain`, or `SPOK1_BRAIN_DIR`)."""
    override = os.environ.get("SPOK1_BRAIN_DIR")
    return Path(override) if override else home() / "brain"


def claude_dir() -> Path:
    """Claude Code's user directory (`~/.claude`)."""
    return home() / ".claude"


def agent_config_dir() -> Path:
    """Per-machine config and secrets (`~/.config/agent`): `.env`, `machine.json`."""
    return home() / ".config" / "agent"


def state_dir() -> Path:
    """spok1's local runtime state (`~/.spok1/state`): session markers, locks, logs."""
    return home() / ".spok1" / "state"
