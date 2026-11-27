"""learning.db: agentd's SQLite store (PRD architecture: events, jobs, alerts, stats).

Every row carries `project` (PRD §9.10: every task, alert and log line has a project id).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, type TEXT NOT NULL,
  spec TEXT NOT NULL,            -- the job YAML as JSON
  source TEXT NOT NULL,          -- file it came from
  next_run REAL,                 -- epoch seconds, NULL = nothing scheduled
  last_run REAL, last_status TEXT, failures INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1, done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, project TEXT NOT NULL,
  started REAL NOT NULL, finished REAL, status TEXT NOT NULL,   -- queued|running|ok|failed|limited
  model TEXT, turns INTEGER, units INTEGER, cost_usd REAL, summary TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, priority TEXT NOT NULL,
  source TEXT NOT NULL, dedup_key TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  created REAL NOT NULL, last_seen REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'open',      -- open|acked|snoozed|noise|done|digest
  snooze_until REAL, sent INTEGER NOT NULL DEFAULT 0, message_id TEXT
);
CREATE INDEX IF NOT EXISTS alerts_dedup ON alerts(dedup_key, state);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project TEXT NOT NULL,
  kind TEXT NOT NULL, data TEXT NOT NULL      -- signals for the learning engine (Phase 6)
);
CREATE TABLE IF NOT EXISTS sent_log (
  ts REAL NOT NULL, channel TEXT NOT NULL    -- for the messages-per-hour limit
);
"""


class DB:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ── kv ─────────────────────────────────────────────────────────────
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def put(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ── events ─────────────────────────────────────────────────────────
    def event(self, kind: str, data: dict[str, Any], project: str = "global") -> None:
        self.conn.execute(
            "INSERT INTO events(ts,project,kind,data) VALUES(?,?,?,?)",
            (time.time(), project, kind, json.dumps(data, default=str)),
        )

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, args).fetchall())

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(sql, args).fetchone()
        return row

    def exec(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        cur = self.conn.execute(sql, args)
        return int(cur.lastrowid or 0)
