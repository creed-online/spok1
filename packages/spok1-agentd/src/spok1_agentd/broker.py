"""The approval broker, agentd's side (PRD §6.2.3).

1. The guard (as spok1) drops a request into the spool when it refuses a T3 action.
2. agentd posts a card to #approvals with the exact action, a dry-run view, an automatic
   backup where it can make one, and a one-time confirm code.
3. The owner replies `approve N CODE` (or `deny N`). A reaction never approves T3.
4. agentd writes the decision where only it can write; the guard lets that one action run once
   within 10 minutes. No answer in 30 minutes = denied.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Protocol

from spok1_agentd.db import DB

SPOOL = Path("/var/spool/spok1/requests")
APPROVALS = Path("/var/lib/spok1-approvals")
DECISION_TTL = 10 * 60  # an approval can be used within 10 minutes
REQUEST_TTL = 30 * 60  # no answer within 30 minutes = denied
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE NOT NULL, tier TEXT NOT NULL,
  tool TEXT, summary TEXT, cwd TEXT, rule TEXT, reason TEXT, agent TEXT, project TEXT,
  code TEXT NOT NULL, backup TEXT, backup_ok INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', decided REAL, message_id TEXT
);
"""

SQLITE_FILE = re.compile(r"\bsqlite3\s+(?:-\S+\s+)*([^\s\"';|&]+\.(?:db|sqlite3?|sqlite))\b")


class Poster(Protocol):
    def send(
        self, channel_id: str, content: str, *, mention_user: str | None = None
    ) -> dict[str, Any]: ...


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def project_of(cwd: str) -> str:
    m = re.search(r"/projects/([^/]+)", cwd.replace("\\", "/"))
    return m.group(1) if m else "global"


def make_backup(summary: str, cwd: str, backups: Path, approval_id: int) -> tuple[str, bool]:
    """Best-effort automatic backup before a T3 action. (description, verified)."""
    m = SQLITE_FILE.search(summary)
    if m:
        src = Path(m.group(1))
        if not src.is_absolute():
            src = Path(cwd) / src
        if src.is_file():
            backups.mkdir(parents=True, exist_ok=True)
            dest = backups / f"{approval_id}-{src.name}"
            shutil.copy2(src, dest)
            ok = dest.read_bytes()[:16] == b"SQLite format 3\x00"
            return (
                f"copied {src} → {dest} ({dest.stat().st_size} bytes, verified)"
                if ok
                else f"copied {src} → {dest} (NOT a valid SQLite file)"
            ), ok
        return f"database {src} not found, nothing to back up", True
    if re.search(r"\b(trash-put|git\s+stash)\b", summary):
        return "restorable without a backup", True
    return "no automatic backup for this kind of action", False


class Broker:
    def __init__(
        self,
        db: DB,
        poster: Poster | None,
        owner_id: str,
        channel: str | None,
        *,
        spool: Path = SPOOL,
        approvals: Path = APPROVALS,
        backups: Path | None = None,
        clock: Any = time.time,
    ) -> None:
        self.db = db
        self.poster = poster
        self.owner_id = owner_id
        self.channel = channel
        self.spool = spool
        self.approvals = approvals
        self.backups = backups or Path("/var/lib/spok1d/backups")
        self.clock = clock
        db.conn.executescript(SCHEMA)

    def now(self) -> float:
        return float(self.clock())

    # ── incoming requests ──────────────────────────────────────────────
    def scan(self) -> list[int]:
        """Turn new spool requests into cards. Returns the new approval ids."""
        new: list[int] = []
        if not self.spool.is_dir():
            return new
        for path in sorted(self.spool.glob("*.json")):
            try:
                req: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            h = str(req.get("hash") or path.stem)
            if self.db.one("SELECT id FROM approvals WHERE hash=?", (h,)) is not None:
                continue
            cwd = str(req.get("cwd") or "")
            aid = self.db.exec(
                "INSERT INTO approvals(hash,tier,tool,summary,cwd,rule,reason,agent,project,code,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    h,
                    "T3",
                    req.get("tool"),
                    req.get("summary"),
                    cwd,
                    req.get("rule"),
                    req.get("reason"),
                    req.get("agent"),
                    project_of(cwd),
                    new_code(),
                    self.now(),
                ),
            )
            backup, ok = make_backup(str(req.get("summary") or ""), cwd, self.backups, aid)
            self.db.exec(
                "UPDATE approvals SET backup=?, backup_ok=? WHERE id=?", (backup, int(ok), aid)
            )
            self.post_card(aid)
            self.db.event(
                "approval.requested", {"id": aid, "rule": req.get("rule")}, project_of(cwd)
            )
            new.append(aid)
        return new

    def card(self, aid: int) -> str:
        r = self.db.one("SELECT * FROM approvals WHERE id=?", (aid,))
        if r is None:
            return f"(approval #{aid} not found)"
        backup_line = f"Backup:  {'✅' if r['backup_ok'] else '⚠️'} {r['backup']}"
        how = f"approve {aid} {r['code']}" + ("" if r["backup_ok"] else " nobackup")
        return "\n".join(
            [
                f"🔐 **APPROVAL #{aid}: T3 (two-key)** · project {r['project']} · agent {r['agent']}",
                f"Action:  `{str(r['summary'])[:600]}`",
                f"Why it's T3: {r['reason']}",
                f"Where:   `{r['cwd']}`",
                "Dry run: the guard parsed exactly the action above; nothing has run yet.",
                backup_line,
                f"Reply:   `{how}` · `deny {aid}`   (no answer in 30 min = denied; a reaction never approves T3)",
            ]
        )

    def post_card(self, aid: int) -> None:
        if self.poster is None or not self.channel:
            return
        msg = self.poster.send(self.channel, self.card(aid), mention_user=self.owner_id)
        self.db.exec("UPDATE approvals SET message_id=? WHERE id=?", (str(msg.get("id", "")), aid))

    # ── decisions ──────────────────────────────────────────────────────
    def _write(self, h: str, aid: int, decision: str) -> None:
        self.approvals.mkdir(parents=True, exist_ok=True)
        tmp = self.approvals / f"{h}.json.tmp"
        tmp.write_text(
            json.dumps(
                {
                    "id": aid,
                    "decision": decision,
                    "expires": self.now() + DECISION_TTL,
                    "decided": self.now(),
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(self.approvals / f"{h}.json")

    def decide(self, aid: int, verb: str, args: list[str]) -> str:
        r = self.db.one("SELECT * FROM approvals WHERE id=?", (aid,))
        if r is None:
            return f"No approval #{aid}."
        if r["state"] != "pending":
            return f"Approval #{aid} is already {r['state']}."
        if verb == "deny":
            self.db.exec(
                "UPDATE approvals SET state='denied', decided=? WHERE id=?", (self.now(), aid)
            )
            self._write(str(r["hash"]), aid, "denied")
            self.db.event("approval.denied", {"id": aid}, str(r["project"]))
            return f"❌ #{aid} denied. The agent won't run it."
        code = next((a for a in args if a.lower() != "nobackup"), "")
        if code.upper() != str(r["code"]):
            return f"#{aid} needs its confirm code: `approve {aid} {'<code>'}` (from the card)."
        if not r["backup_ok"] and "nobackup" not in [a.lower() for a in args]:
            return f"#{aid} has no verified backup. If you have one, reply `approve {aid} {r['code']} nobackup`."
        self.db.exec(
            "UPDATE approvals SET state='approved', decided=? WHERE id=?", (self.now(), aid)
        )
        self._write(str(r["hash"]), aid, "approved")
        self.db.event(
            "approval.approved", {"id": aid, "nobackup": not r["backup_ok"]}, str(r["project"])
        )
        return f"✅ #{aid} approved. The agent may run that exact action once in the next 10 minutes. Tell it to go ahead."

    def expire(self) -> list[int]:
        """Deny requests nobody answered within 30 minutes; clean up old files."""
        out: list[int] = []
        for r in self.db.rows(
            "SELECT id, hash, project FROM approvals WHERE state='pending' AND created < ?",
            (self.now() - REQUEST_TTL,),
        ):
            self.db.exec(
                "UPDATE approvals SET state='expired', decided=? WHERE id=?", (self.now(), r["id"])
            )
            self._write(str(r["hash"]), int(r["id"]), "denied")
            self.db.event("approval.expired", {"id": r["id"]}, str(r["project"]))
            out.append(int(r["id"]))
        cutoff = self.now() - 86400
        for folder in (self.spool, self.approvals):
            if folder.is_dir():
                for f in folder.iterdir():
                    try:
                        if f.stat().st_mtime < cutoff:
                            f.unlink()
                    except OSError:
                        pass
        return out
