"""Notifier (PRD §3): priorities, quiet hours, dedup, rate limit, alert IDs, the daily digest.

P1: now, even in quiet hours, with an @mention.  P2: now, but held during quiet hours.
P3: collected for the daily digest.  A repeat of an open alert (same dedup key) is counted,
not re-sent.  At most N messages an hour (P1 exempt); the rest wait for the next window.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from spok1_agentd.db import DB

ICON = {"P1": "🚨", "P2": "🔔", "P3": "📝"}
DEDUP_WINDOW = 24 * 3600


class Sender(Protocol):
    def send(
        self, channel_id: str, content: str, *, mention_user: str | None = None
    ) -> dict[str, object]: ...


def parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def in_quiet_hours(now: float, tz: str, start: str, end: str) -> bool:
    local = datetime.fromtimestamp(now, ZoneInfo(tz))
    minutes = local.hour * 60 + local.minute
    sh, sm = parse_hhmm(start)
    eh, em = parse_hhmm(end)
    s, e = sh * 60 + sm, eh * 60 + em
    return (s <= minutes or minutes < e) if s > e else (s <= minutes < e)


def quiet_ends_at(now: float, tz: str, end: str) -> float:
    zone = ZoneInfo(tz)
    local = datetime.fromtimestamp(now, zone)
    eh, em = parse_hhmm(end)
    target = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.timestamp()


class Notifier:
    def __init__(
        self,
        db: DB,
        sender: Sender | None,
        owner_id: str,
        channels: dict[str, str],
        *,
        tz: str = "Asia/Kolkata",
        quiet: tuple[str, str] = ("23:00", "07:30"),
        per_hour: int = 20,
        clock: object = time.time,
    ) -> None:
        self.db = db
        self.sender = sender
        self.owner_id = owner_id
        self.channels = channels  # alerts, daily-digest, approvals, learning, command
        self.tz = tz
        self.quiet = quiet
        self.per_hour = per_hour
        self.clock = clock

    def now(self) -> float:
        return float(self.clock())  # type: ignore[operator]

    # ── raising alerts ─────────────────────────────────────────────────
    def notify(
        self,
        priority: str,
        title: str,
        body: str = "",
        *,
        project: str = "global",
        source: str = "agentd",
        dedup_key: str | None = None,
    ) -> int:
        """Record an alert and send it if its rules allow. Returns the alert id."""
        now = self.now()
        key = dedup_key or f"{source}:{title}"
        existing = self.db.one(
            "SELECT id, count FROM alerts WHERE dedup_key=? AND state IN ('open','snoozed','digest') AND last_seen > ?",
            (key, now - DEDUP_WINDOW),
        )
        if existing is not None:
            self.db.exec(
                "UPDATE alerts SET count=count+1, last_seen=? WHERE id=?", (now, existing["id"])
            )
            return int(existing["id"])
        noise = self.db.one(
            "SELECT id FROM alerts WHERE dedup_key=? AND state='noise' AND last_seen > ?",
            (key, now - 7 * 86400),
        )
        if noise is not None:  # the owner marked this kind of alert as noise: digest only
            priority = "P3"
        state = "digest" if priority == "P3" else "open"
        alert_id = self.db.exec(
            "INSERT INTO alerts(project,priority,source,dedup_key,title,body,created,last_seen,state) VALUES(?,?,?,?,?,?,?,?,?)",
            (project, priority, source, key, title, body, now, now, state),
        )
        self.flush()
        return alert_id

    # ── delivery ───────────────────────────────────────────────────────
    def _sent_last_hour(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM sent_log WHERE ts > ?", (self.now() - 3600,))
        return int(row["n"]) if row else 0

    def format(self, row: dict[str, object]) -> str:
        count = int(str(row["count"]))
        times = f" (x{count})" if count > 1 else ""
        project = f" · {row['project']}" if row["project"] != "global" else ""
        head = f"{ICON[str(row['priority'])]} **{row['priority']} #{row['id']}**{project} · {row['title']}{times}"
        body = str(row["body"]).strip()
        tail = f"Reply: `ack {row['id']}` · `snooze {row['id']} 2h` · `noise {row['id']}` · `do it {row['id']}`"
        return "\n".join(x for x in (head, body, tail) if x)

    def flush(self) -> int:
        """Send every open alert whose rules now allow it. Returns how many were sent."""
        if self.sender is None or "alerts" not in self.channels:
            return 0
        now = self.now()
        quiet = in_quiet_hours(now, self.tz, *self.quiet)
        sent = 0
        pending = self.db.rows(
            "SELECT * FROM alerts WHERE sent=0 AND state IN ('open','snoozed') AND (snooze_until IS NULL OR snooze_until <= ?) ORDER BY priority, id",
            (now,),
        )
        for row in pending:
            priority = str(row["priority"])
            if priority != "P1":
                if quiet:
                    continue
                if self._sent_last_hour() >= self.per_hour:
                    break
            msg = self.sender.send(
                self.channels["alerts"],
                self.format(dict(row)),
                mention_user=self.owner_id if priority == "P1" else None,
            )
            self.db.exec(
                "UPDATE alerts SET sent=1, state='open', message_id=? WHERE id=?",
                (str(msg.get("id", "")), row["id"]),
            )
            self.db.exec("INSERT INTO sent_log(ts,channel) VALUES(?,?)", (now, "alerts"))
            sent += 1
        return sent

    def digest(self) -> str | None:
        """The daily digest: P3 items plus anything still unanswered. Sends it and returns the text."""
        now = self.now()
        rows = self.db.rows(
            "SELECT * FROM alerts WHERE (state='digest' AND sent=0) OR (state='open' AND sent=1 AND created > ?) ORDER BY project, id",
            (now - 86400,),
        )
        if not rows:
            return None
        lines = ["📝 **Daily digest**"]
        for r in rows:
            mark = "unanswered" if r["state"] == "open" else ""
            lines.append(
                f"- #{r['id']} {r['project']} · {r['title']}" + (f" ({mark})" if mark else "")
            )
        text = "\n".join(lines)
        channel = self.channels.get("daily-digest") or self.channels.get("alerts")
        if self.sender is not None and channel:
            self.sender.send(channel, text[:2000])
            self.db.exec("UPDATE alerts SET sent=1, state='done' WHERE state='digest' AND sent=0")
        return text

    # ── owner replies ──────────────────────────────────────────────────
    def apply_reply(self, verb: str, alert_id: int, arg: str = "") -> str:
        row = self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        if row is None:
            return f"No alert #{alert_id}."
        now = self.now()
        if verb == "ack":
            self.db.exec("UPDATE alerts SET state='acked' WHERE id=?", (alert_id,))
        elif verb == "noise":
            self.db.exec("UPDATE alerts SET state='noise', last_seen=? WHERE id=?", (now, alert_id))
        elif verb == "snooze":
            secs = parse_duration(arg or "2h")
            self.db.exec(
                "UPDATE alerts SET state='snoozed', sent=0, snooze_until=? WHERE id=?",
                (now + secs, alert_id),
            )
        elif verb == "do it":
            self.db.exec("UPDATE alerts SET state='done' WHERE id=?", (alert_id,))
        self.db.event(
            "alert.reply",
            {"alert": alert_id, "verb": verb, "arg": arg, "source": row["source"]},
            str(row["project"]),
        )
        return {
            "ack": "✅ acked",
            "noise": "🗑️ marked as noise (future ones go to the digest)",
            "snooze": f"🔕 snoozed {arg or '2h'}",
            "do it": "▶️ queued",
        }[verb] + f" #{alert_id}"


def parse_duration(text: str) -> float:
    text = text.strip().lower()
    units = {"m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units and text[:-1].isdigit():
        return int(text[:-1]) * units[text[-1]]
    if text.isdigit():
        return int(text) * 60
    return 7200.0
