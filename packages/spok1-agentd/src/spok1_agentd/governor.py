"""Usage governor (PRD §6.4): a priority queue, a concurrency cap, budgets, limit back-off.

Jobs wait in `runs` with status 'queued'. At most `max_parallel` run at once (1 on the 1 GB VM).
A usage-limit error pauses everything for 30 minutes (then retries); a job over the daily budget
waits for tomorrow and raises an alert.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from spok1_agentd.db import DB
from spok1_agentd.runner import JobResult, parse_result

BACKOFF_SECONDS = 30 * 60
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


@dataclass
class Running:
    run_id: int
    job: dict[str, Any]
    proc: Any
    started: float
    model: str


@dataclass
class Governor:
    db: DB
    start_job: Callable[[dict[str, Any]], Any]  # returns a Popen-like object
    max_parallel: int = 1
    per_day_units: int = 4000
    clock: Callable[[], float] = time.time
    running: list[Running] = field(default_factory=list[Running])

    def enqueue(self, job: dict[str, Any]) -> int:
        return self.db.exec(
            "INSERT INTO runs(job_id,project,started,status,model) VALUES(?,?,?,?,?)",
            (job["id"], job["project"], self.clock(), "queued", job.get("model", "")),
        )

    def used_today(self) -> int:
        row = self.db.one(
            "SELECT COALESCE(SUM(units),0) AS u FROM runs WHERE started > ?",
            (self.clock() - 86400,),
        )
        return int(row["u"]) if row else 0

    def paused_until(self) -> float:
        return float(self.db.get("governor:paused_until", "0") or 0)

    def tick(self, jobs: dict[str, dict[str, Any]]) -> list[tuple[dict[str, Any], JobResult]]:
        """Collect finished runs, start queued ones. Returns (job, result) for runs that finished."""
        now = self.clock()
        finished: list[tuple[dict[str, Any], JobResult]] = []
        for r in list(self.running):
            if r.proc.poll() is None:
                continue
            out, err = r.proc.communicate()
            result = parse_result(out, err, r.proc.returncode, r.model)
            self.db.exec(
                "UPDATE runs SET finished=?, status=?, turns=?, units=?, cost_usd=?, summary=? WHERE id=?",
                (
                    now,
                    result.status,
                    result.turns,
                    result.units,
                    result.cost_usd,
                    result.summary[:1000],
                    r.run_id,
                ),
            )
            if result.status == "limited":
                self.db.put("governor:paused_until", str(now + BACKOFF_SECONDS))
            self.running.remove(r)
            finished.append((r.job, result))
        if now < self.paused_until():
            return finished
        queued = self.db.rows("SELECT * FROM runs WHERE status='queued' ORDER BY id")
        queued_sorted = sorted(
            queued,
            key=lambda q: (
                PRIORITY_ORDER.get(str(jobs.get(str(q["job_id"]), {}).get("priority", "P2")), 1),
                q["id"],
            ),
        )
        for q in queued_sorted:
            if len(self.running) >= self.max_parallel:
                break
            job = jobs.get(str(q["job_id"]))
            if job is None:
                self.db.exec(
                    "UPDATE runs SET status='failed', summary='job no longer exists' WHERE id=?",
                    (q["id"],),
                )
                continue
            if self.used_today() >= self.per_day_units:
                break  # over the daily budget: wait (the caller raises the alert)
            proc = self.start_job(job)
            self.db.exec("UPDATE runs SET status='running', started=? WHERE id=?", (now, q["id"]))
            self.running.append(
                Running(int(q["id"]), job, proc, now, str(job.get("model", "sonnet")))
            )
        return finished

    def over_budget(self) -> bool:
        return self.used_today() >= self.per_day_units
