"""Scheduler (PRD §3): jobs are YAML files in the brain, synced into learning.db.

  ~/brain/jobs/*.yaml                    global jobs (project "global")
  ~/brain/projects/<id>/jobs/*.yaml      project jobs (skipped while the project is paused/archived)

A job has exactly one of `at` (one-off) or `cron` (recurring, in its timezone). Natural language
("every weekday 9am", "in 2 hours") is turned into this structure by the manager, not here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter
from pydantic import ValidationError

from spok1_agentd.db import DB
from spok1_core.schemas import Job, RegistryFile


def project_status(brain: Path) -> dict[str, str]:
    path = brain / "projects" / "registry.yaml"
    if not path.is_file():
        return {}
    try:
        reg = RegistryFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (ValidationError, yaml.YAMLError):
        return {}
    return {p.id: p.status.value for p in reg.projects}


def job_files(brain: Path) -> list[tuple[str, Path]]:
    files = [("global", p) for p in sorted((brain / "jobs").glob("*.yaml"))]
    status = project_status(brain)
    for project_dir in sorted((brain / "projects").glob("*/jobs")):
        project = project_dir.parent.name
        if status.get(project, "active") != "active":
            continue
        files += [(project, p) for p in sorted(project_dir.glob("*.yaml"))]
    return files


def next_run(job: Job, after: float, default_tz: str) -> float | None:
    tz = ZoneInfo(job.timezone or default_tz)
    if job.at is not None:
        at = job.at if job.at.tzinfo else job.at.replace(tzinfo=tz)
        return at.timestamp()
    if job.cron:
        base = datetime.fromtimestamp(after, tz)
        nxt: datetime = croniter(job.cron, base).get_next(datetime)
        return float(nxt.timestamp())
    return None


def sync(db: DB, brain: Path, default_tz: str, now: float) -> tuple[int, list[str]]:
    """Load job files into the db. Returns (jobs loaded, problems)."""
    problems: list[str] = []
    seen: set[str] = set()
    for project, path in job_files(brain):
        try:
            data: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            job = Job.model_validate(data)
        except (ValidationError, yaml.YAMLError, OSError) as exc:
            problems.append(f"{path.name}: {str(exc).splitlines()[0]}")
            continue
        if job.project != "global" and project == "global":
            project = job.project
        seen.add(job.id)
        spec = job.model_dump_json()
        row = db.one("SELECT spec, done, next_run FROM jobs WHERE id=?", (job.id,))
        if row is None:
            nr = next_run(job, now, default_tz)
            db.exec(
                "INSERT INTO jobs(id,project,type,spec,source,next_run,enabled) VALUES(?,?,?,?,?,?,?)",
                (job.id, project, job.type.value, spec, str(path), nr, int(job.enabled)),
            )
        elif row["spec"] != spec:  # the file changed: reschedule
            nr = next_run(job, now, default_tz)
            db.exec(
                "UPDATE jobs SET spec=?, project=?, type=?, source=?, next_run=?, enabled=?, done=0 WHERE id=?",
                (spec, project, job.type.value, str(path), nr, int(job.enabled), job.id),
            )
    for row in db.rows("SELECT id FROM jobs"):
        if row["id"] not in seen:  # file deleted (or project paused): stop scheduling it
            db.exec("UPDATE jobs SET enabled=0 WHERE id=?", (row["id"],))
    return len(seen), problems


def due(db: DB, now: float) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT * FROM jobs WHERE enabled=1 AND done=0 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run",
        (now,),
    )
    return [dict(r) for r in rows]


def advance(db: DB, job_row: dict[str, Any], now: float, default_tz: str, status: str) -> None:
    """After a run: one-offs are done; recurring jobs get their next time."""
    job = Job.model_validate(json.loads(str(job_row["spec"])))
    failures = 0 if status == "ok" else int(job_row.get("failures") or 0) + 1
    if job.cron:
        nr = next_run(job, now, default_tz)
        db.exec(
            "UPDATE jobs SET last_run=?, last_status=?, next_run=?, failures=? WHERE id=?",
            (now, status, nr, failures, job.id),
        )
    else:
        db.exec(
            "UPDATE jobs SET last_run=?, last_status=?, done=1, failures=? WHERE id=?",
            (now, status, failures, job.id),
        )


def iso(ts: float, tz: str) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%a %d %b %H:%M")


__all__ = ["UTC", "advance", "due", "iso", "next_run", "sync"]
