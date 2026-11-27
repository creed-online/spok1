"""Create and remember the HQ channels in the owner's private Discord server (PRD §1).

📌 HQ (category): #command #approvals #alerts #daily-digest #learning
An existing channel with the same name (anywhere in the server) is reused, not duplicated.
"""

from __future__ import annotations

from typing import Any

from spok1_agentd.db import DB
from spok1_agentd.discord_api import CATEGORY, TEXT, Discord

CATEGORY_NAME = "📌 HQ"
CHANNELS = {
    "command": "talk to the manager (no project context needed)",
    "approvals": "approval cards (T2/T3), all projects: approve N · deny N",
    "alerts": "P1/P2 alerts: ack N · snooze N 2h · noise N · do it N",
    "daily-digest": "end-of-day report and P3 items (keep muted)",
    "learning": "what spok1 learned, growth and usage reports",
}


def ensure(discord: Discord, db: DB, guild_id: str) -> tuple[dict[str, str], list[str]]:
    """Create what's missing. Returns ({name: channel_id}, actions taken)."""
    actions: list[str] = []
    existing: list[dict[str, Any]] = discord.channels(guild_id)
    by_name = {(c["name"], c["type"]): c for c in existing}
    category = by_name.get((CATEGORY_NAME, CATEGORY))
    if category is None:
        category = discord.create_channel(guild_id, CATEGORY_NAME, CATEGORY)
        actions.append(f"created category {CATEGORY_NAME}")
    ids: dict[str, str] = {}
    for name, topic in CHANNELS.items():
        found = by_name.get((name, TEXT))
        if found is None:
            found = discord.create_channel(
                guild_id, name, TEXT, parent_id=str(category["id"]), topic=topic
            )
            actions.append(f"created #{name}")
        ids[name] = str(found["id"])
        db.put(f"channel:{name}", ids[name])
    return ids, actions


def known(db: DB) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in CHANNELS:
        value = db.get(f"channel:{name}")
        if value:
            out[name] = value
    return out
