"""The agentd main loop: one process, one thread, a tick every few seconds.

Uses no Claude at all except for agent-run/digest jobs (and, later, haiku watcher classifiers).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from spok1_agentd import hq, replies, runner, scheduler, watchers
from spok1_agentd.broker import Broker
from spok1_agentd.db import DB
from spok1_agentd.discord_api import Discord, DiscordError
from spok1_agentd.governor import Governor
from spok1_agentd.notifier import Notifier, parse_hhmm
from spok1_agentd.settings import Settings
from spok1_core.schemas import Job

log = logging.getLogger("agentd")

INTERVALS = {
    "poll": 10,
    "brain": 120,
    "manager": 60,
    "disk": 300,
    "deadman": 300,
    "ci": 600,
    "login": 6 * 3600,
}


class Daemon:
    def __init__(
        self,
        settings: Settings,
        db: DB,
        discord: Discord | None,
        *,
        clock: Callable[[], float] = time.time,
        run: watchers.Run = watchers.run_cmd,
        start_job: Callable[[dict[str, Any]], Any] | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.s = settings
        self.db = db
        self.discord = discord
        self.clock = clock
        self.run = run
        self.http = http or httpx.Client(timeout=15)
        self.last: dict[str, float] = {}
        self.channels = hq.known(db)
        self.notifier = Notifier(
            db,
            discord,
            settings.owner_id,
            self.channels,
            tz=settings.timezone,
            quiet=(settings.quiet_start, settings.quiet_end),
            per_hour=settings.max_messages_per_hour,
            clock=clock,
        )
        self.jobs: dict[str, dict[str, Any]] = {}
        self.broker = Broker(
            db,
            discord,
            settings.owner_id,
            self.channels.get("approvals") or self.channels.get("alerts"),
            clock=clock,
        )
        self.governor = Governor(
            db, start_job or self._start_job, max_parallel=settings.max_parallel_jobs, clock=clock
        )

    # ── setup ──────────────────────────────────────────────────────────
    def setup_discord(self) -> list[str]:
        if self.discord is None:
            return ["no Discord token: skipped"]
        ids, actions = hq.ensure(self.discord, self.db, self.s.guild_id)
        self.channels.update(ids)
        self.notifier.channels = self.channels
        self.broker.channel = self.channels.get("approvals") or self.channels.get("alerts")
        return actions

    # ── jobs ───────────────────────────────────────────────────────────
    def _start_job(self, job: dict[str, Any]) -> Any:
        cwd = str(self.s.work_dir)
        project_dir = self.s.work_dir.parent / "projects" / str(job["project"])
        if job["project"] != "global" and project_dir.is_dir():
            cwd = str(project_dir)
        prompt = str(job.get("prompt") or "")
        return runner.start(
            job.get("agent"),
            str(job.get("model") or "sonnet"),
            int(job.get("max_turns") or 20),
            cwd,
            prompt,
            self.s.oauth_token,
        )

    def _due_jobs(self, now: float) -> None:
        for row in scheduler.due(self.db, now):
            spec = Job.model_validate(json.loads(str(row["spec"])))
            if spec.type.value == "reminder":
                self.notifier.notify(
                    spec.priority,
                    f"⏰ {spec.message}",
                    "",
                    project=str(row["project"]),
                    source="reminder",
                    dedup_key=f"reminder:{spec.id}:{int(row['next_run'])}",
                )
                scheduler.advance(self.db, row, now, self.s.timezone, "ok")
                self.db.event("reminder.sent", {"job": spec.id}, str(row["project"]))
                continue
            job = {
                "id": spec.id,
                "project": row["project"],
                "type": spec.type.value,
                "agent": spec.agent,
                "prompt": spec.prompt,
                "max_turns": spec.max_turns,
                "model": spec.model,
                "priority": spec.priority,
            }
            self.jobs[spec.id] = job
            self.governor.enqueue(job)
            # mark it taken now, so a slow run isn't queued twice
            scheduler.advance(self.db, row, now, self.s.timezone, "queued")

    def _finished(self, job: dict[str, Any], result: runner.JobResult) -> None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
        failures = 0
        if row is not None:
            failures = 0 if result.status == "ok" else int(row["failures"] or 0) + 1
            self.db.exec(
                "UPDATE jobs SET last_status=?, failures=? WHERE id=?",
                (result.status, failures, job["id"]),
            )
        self.db.event(
            "job.finished",
            {
                "job": job["id"],
                "status": result.status,
                "turns": result.turns,
                "units": result.units,
            },
            str(job["project"]),
        )
        if result.status == "ok":
            body = f"{result.summary[:1500]}\n_{result.turns} turns · ~{result.units} units_"
            self.notifier.notify(
                str(job.get("priority") or "P2"),
                f"✅ {job['id']} done",
                body,
                project=str(job["project"]),
                source="job",
                dedup_key=f"jobok:{job['id']}:{int(self.clock())}",
            )
        elif result.status == "limited":
            self.notifier.notify(
                "P2",
                "Claude usage limit reached: jobs paused 30 min",
                result.summary,
                source="governor",
                dedup_key="governor:limited",
            )
        else:
            for a in watchers.job_failure(
                str(job["id"]), str(job["project"]), failures, result.summary
            ):
                self.notifier.notify(
                    a.priority, a.title, a.body, project=a.project, source=a.source, dedup_key=a.key
                )

    # ── replies ────────────────────────────────────────────────────────
    def _handle_reply(self, reply: replies.Reply) -> str | None:
        if reply.verb in ("approve", "deny"):
            text = self.broker.decide(reply.alert_id, reply.verb, reply.arg.split())
            if self.discord is not None:
                try:
                    self.discord.send(reply.channel_id, text, reply_to=reply.message_id)
                except DiscordError as exc:
                    log.warning("reply ack failed: %s", exc)
            return text
        text = self.notifier.apply_reply(reply.verb, reply.alert_id, reply.arg)
        if reply.verb == "do it":
            row = self.db.one("SELECT * FROM alerts WHERE id=?", (reply.alert_id,))
            if row is not None:
                job_id = f"doit-{reply.alert_id}"
                job = {
                    "id": job_id,
                    "project": row["project"],
                    "type": "agent-run",
                    "agent": None,
                    "model": "sonnet",
                    "max_turns": 30,
                    "priority": "P2",
                    "prompt": f"The owner replied 'do it' to alert #{row['id']} ({row['source']}): {row['title']}\n{row['body']}\nHandle it. Use the run-request skill. Report what you did in one short message.",
                }
                self.jobs[job_id] = job
                self.governor.enqueue(job)
        if self.discord is not None:
            try:
                self.discord.send(reply.channel_id, text, reply_to=reply.message_id)
            except DiscordError as exc:
                log.warning("reply ack failed: %s", exc)
        return text

    # ── the tick ───────────────────────────────────────────────────────
    def due(self, name: str, now: float) -> bool:
        if now - self.last.get(name, 0) >= INTERVALS[name]:
            self.last[name] = now
            return True
        return False

    def raise_all(self, alerts: list[watchers.Alert]) -> None:
        for a in alerts:
            self.notifier.notify(
                a.priority, a.title, a.body, project=a.project, source=a.source, dedup_key=a.key
            )

    def tick(self) -> None:
        now = self.clock()
        if (
            "alerts" not in self.channels
            and self.discord is not None
            and now - self.last.get("setup", 0) > 600
        ):
            self.last["setup"] = now  # retry channel setup every 10 min (e.g. after a re-authorize)
            try:
                self.setup_discord()
            except DiscordError as exc:
                log.warning("Discord setup failed: %s", exc)
        if self.due("brain", now):
            self.run(
                ["sudo", "-n", "-u", self.s.run_as, "/opt/spok1/bin/spok1-runas", "git-pull-brain"]
            )
            _loaded, problems = scheduler.sync(self.db, self.s.brain_dir, self.s.timezone, now)
            for p in problems:
                self.notifier.notify(
                    "P3", "Bad job file", p, source="scheduler", dedup_key=f"badjob:{p}"
                )
        self._due_jobs(now)
        try:
            self.broker.scan()
            for aid in self.broker.expire():
                self.notifier.notify(
                    "P3",
                    f"Approval #{aid} expired (denied)",
                    "No answer within 30 minutes.",
                    source="broker",
                    dedup_key=f"expired:{aid}",
                )
        except (DiscordError, OSError) as exc:
            log.warning("broker failed: %s", exc)
        for job, result in self.governor.tick(self.jobs):
            self._finished(job, result)
        if self.due("poll", now) and self.discord is not None:
            channels = sorted(
                {c for c in (self.channels.get("alerts"), self.channels.get("approvals")) if c}
            )
            try:
                replies.poll(self.db, self.discord, channels, self.s.owner_id, self._handle_reply)
            except DiscordError as exc:
                log.warning("poll failed: %s", exc)
        if self.due("manager", now):
            self.raise_all(watchers.manager(self.run))
        if self.due("disk", now):
            self.raise_all(watchers.disk_and_memory())
        if self.due("login", now):
            self.raise_all(watchers.login_expiry(now, self.run))
        if self.due("ci", now):
            self.raise_all(watchers.ci_and_prs(self.s.brain_dir, now, self.run))
        if self.due("deadman", now):
            self.ping_deadman()
        self._daily_digest(now)
        try:
            self.notifier.flush()
        except DiscordError as exc:
            log.warning("flush failed: %s", exc)

    def _daily_digest(self, now: float) -> None:
        local = datetime.fromtimestamp(now, ZoneInfo(self.s.timezone))
        h, m = parse_hhmm(self.s.extra.get("EOD_REPORT", "19:00"))
        today = local.strftime("%Y-%m-%d")
        if (local.hour, local.minute) >= (h, m) and self.db.get("digest:last") != today:
            self.db.put("digest:last", today)
            try:
                self.notifier.digest()
            except DiscordError as exc:
                log.warning("digest failed: %s", exc)

    def ping_deadman(self, fail: bool = False) -> None:
        if not self.s.healthcheck_url:
            return
        try:
            self.http.get(self.s.healthcheck_url + ("/fail" if fail else ""), timeout=10)
        except httpx.HTTPError as exc:
            log.warning("healthcheck ping failed: %s", type(exc).__name__)

    def run_forever(self) -> None:  # pragma: no cover - the service loop
        log.info("agentd started; channels: %s", ", ".join(sorted(self.channels)) or "none")
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
                self.ping_deadman(fail=True)
            time.sleep(2)


def state_paths(settings: Settings) -> Path:
    return settings.state_dir
