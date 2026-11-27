"""Read the owner's replies in agentd's channels by REST polling (no gateway, no Claude usage).

Only messages from the owner's Discord ID count. Commands: ack N · snooze N 2h · noise N · do it N.
(Approval replies, `approve N [code]` / `deny N`, are added by the broker in Phase 5b.)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from spok1_agentd.db import DB

COMMAND = re.compile(r"^\s*(ack|noise|do it|snooze)\s+#?(\d+)(?:\s+(\S+))?\s*$", re.IGNORECASE)


@dataclass
class Reply:
    verb: str
    alert_id: int
    arg: str
    message_id: str
    channel_id: str


class Reader(Protocol):
    def messages_after(
        self, channel_id: str, after: str | None, limit: int = 50
    ) -> list[dict[str, Any]]: ...


def parse(text: str) -> tuple[str, int, str] | None:
    m = COMMAND.match(text)
    if not m:
        return None
    return m.group(1).lower(), int(m.group(2)), (m.group(3) or "")


def poll(
    db: DB,
    reader: Reader,
    channel_ids: list[str],
    owner_id: str,
    handle: Callable[[Reply], str | None],
) -> int:
    """Fetch new messages in each channel, act on the owner's commands. Returns commands handled."""
    handled = 0
    for channel_id in channel_ids:
        key = f"last_msg:{channel_id}"
        last = db.get(key)
        msgs = reader.messages_after(channel_id, last)
        if last is None and msgs:  # first run: start after the newest message, don't replay history
            db.put(key, str(msgs[-1]["id"]))
            continue
        for msg in msgs:
            db.put(key, str(msg["id"]))
            author = cast(dict[str, Any], msg.get("author") or {})
            if str(author.get("id", "")) != owner_id or author.get("bot"):
                continue  # only the owner's messages count (others are ignored silently)
            parsed = parse(str(msg.get("content", "")))
            if parsed is None:
                continue
            handle(Reply(parsed[0], parsed[1], parsed[2], str(msg["id"]), channel_id))
            handled += 1
    return handled
