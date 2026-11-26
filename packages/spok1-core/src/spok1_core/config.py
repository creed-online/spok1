"""Configuration: the machine's `.env`, its registered name, and `my-details.local.yaml`."""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from spok1_core import paths

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    """Parse a minimal `.env` file: KEY=value lines, `#` comments, optional quotes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def env_file() -> Path:
    return paths.agent_config_dir() / ".env"


def load_env() -> dict[str, str]:
    """Values from `~/.config/agent/.env`, overridden by the process environment."""
    values: dict[str, str] = {}
    path = env_file()
    if path.is_file():
        values.update(parse_env(path.read_text(encoding="utf-8")))
    for key in values:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def get_setting(name: str) -> str | None:
    """A single setting: the process environment first, then `~/.config/agent/.env`."""
    value = os.environ.get(name)
    if value:
        return value
    return load_env().get(name) or None


def machine_file() -> Path:
    return paths.agent_config_dir() / "machine.json"


def default_machine_name() -> str:
    name = socket.gethostname().lower()
    return re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "machine"


def machine_name() -> str:
    """The name this machine was registered with (falls back to the hostname)."""
    path = machine_file()
    if path.is_file():
        try:
            data: object = json.loads(path.read_text(encoding="utf-8"))
            name = cast(dict[str, object], data).get("name") if isinstance(data, dict) else None
            if isinstance(name, str) and name:
                return name
        except (OSError, ValueError):
            pass
    return default_machine_name()


def register_machine(name: str) -> Path:
    path = machine_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name}, indent=2) + "\n", encoding="utf-8")
    return path


# ── my-details.local.yaml ──────────────────────────────────────────────


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow")


class AzureDetails(_Section):
    subscription_id: str = ""
    region: str = ""
    resource_group: str = ""
    vm_size: str = "Standard_B2s"


class MachineDetails(_Section):
    primary_laptop: str = ""
    primary_laptop_os: str = ""


class Mem0Details(_Section):
    user_id: str = ""


class BrainDetails(_Section):
    repo: str = ""


class DiscordDetails(_Section):
    user_id: str = ""
    guild_id: str = ""


class ScheduleDetails(_Section):
    timezone: str = ""
    quiet_hours: str = ""
    eod_report: str = "19:00"
    weekly_review: str = "Mon 09:00"


class MyDetails(_Section):
    azure: AzureDetails = Field(default_factory=AzureDetails)
    machines: MachineDetails = Field(default_factory=MachineDetails)
    mem0: Mem0Details = Field(default_factory=Mem0Details)
    brain: BrainDetails = Field(default_factory=BrainDetails)
    discord: DiscordDetails = Field(default_factory=DiscordDetails)
    schedule: ScheduleDetails = Field(default_factory=ScheduleDetails)
    repos: list[str] = Field(default_factory=list[str])


def load_my_details(path: Path) -> MyDetails:
    """Load `config/my-details.local.yaml`. A missing file gives all-empty defaults."""
    if not path.is_file():
        return MyDetails()
    data: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    details = MyDetails.model_validate(data)
    details.repos = [r for r in details.repos if r]
    return details
