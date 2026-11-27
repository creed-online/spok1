"""agentd settings: one env file owned by the `spok1d` user (secrets), plus defaults.

/etc/spok1/agentd.env (0600, spok1d) holds:
  DISCORD_BOT_TOKEN      the bot token (REST only; the manager's plugin holds the gateway)
  DISCORD_GUILD_ID       your private server
  DISCORD_OWNER_ID       your Discord user ID (only this user's replies count)
  HEALTHCHECK_URL        https://hc-ping.com/<uuid>  (a secret)
  CLAUDE_CODE_OAUTH_TOKEN  from `claude setup-token` (headless jobs only)
  MEM0_API_KEY           for headless jobs (Phase 5: wired into job MCP config)
Everything else has a default and can be overridden in the same file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from spok1_core.config import parse_env

DEFAULT_ENV_FILE = Path("/etc/spok1/agentd.env")


@dataclass
class Settings:
    bot_token: str = ""
    guild_id: str = ""
    owner_id: str = ""
    healthcheck_url: str = ""
    oauth_token: str = ""
    mem0_api_key: str = ""
    state_dir: Path = Path("/var/lib/spok1d")
    brain_dir: Path = Path("/home/spok1/brain")
    work_dir: Path = Path("/home/spok1/work")
    run_as: str = "spok1"  # agent jobs run as this user (via the sudo wrapper)
    timezone: str = "Asia/Kolkata"
    quiet_start: str = "23:00"
    quiet_end: str = "07:30"
    max_messages_per_hour: int = 20
    max_parallel_jobs: int = 1  # 1 GB VM
    poll_seconds: int = 10
    brain_pull_seconds: int = 120
    extra: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def db_path(self) -> Path:
        return self.state_dir / "learning.db"


def load(path: Path | None = None) -> Settings:
    target = path or Path(os.environ.get("SPOK1D_ENV", str(DEFAULT_ENV_FILE)))
    values: dict[str, str] = {}
    if target.is_file():
        values = parse_env(target.read_text(encoding="utf-8"))
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    s = Settings()
    s.bot_token = values.get("DISCORD_BOT_TOKEN", "")
    s.guild_id = values.get("DISCORD_GUILD_ID", "")
    s.owner_id = values.get("DISCORD_OWNER_ID", "")
    s.healthcheck_url = values.get("HEALTHCHECK_URL", "")
    s.oauth_token = values.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    s.mem0_api_key = values.get("MEM0_API_KEY", "")
    for key, attr in (
        ("SPOK1D_STATE", "state_dir"),
        ("SPOK1_BRAIN_DIR", "brain_dir"),
        ("SPOK1_WORK_DIR", "work_dir"),
    ):
        if values.get(key):
            setattr(s, attr, Path(values[key]))
    s.run_as = values.get("SPOK1D_RUN_AS", s.run_as)
    s.timezone = values.get("TIMEZONE", s.timezone)
    if "QUIET_HOURS" in values and "-" in values["QUIET_HOURS"]:
        s.quiet_start, s.quiet_end = (x.strip() for x in values["QUIET_HOURS"].split("-", 1))
    for key, attr in (
        ("MAX_MESSAGES_PER_HOUR", "max_messages_per_hour"),
        ("MAX_PARALLEL_JOBS", "max_parallel_jobs"),
        ("POLL_SECONDS", "poll_seconds"),
    ):
        if values.get(key, "").isdigit():
            setattr(s, attr, int(values[key]))
    s.extra = values
    return s


def missing(s: Settings) -> list[str]:
    """Settings agentd can't work without (the rest degrade gracefully)."""
    out: list[str] = []
    for name, value in (
        ("DISCORD_BOT_TOKEN", s.bot_token),
        ("DISCORD_GUILD_ID", s.guild_id),
        ("DISCORD_OWNER_ID", s.owner_id),
    ):
        if not value:
            out.append(name)
    return out
