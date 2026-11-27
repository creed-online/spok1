from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import yaml

from spok1_agentd import hq, replies, runner, scheduler, watchers
from spok1_agentd.daemon import Daemon
from spok1_agentd.db import DB
from spok1_agentd.governor import Governor
from spok1_agentd.notifier import Notifier, in_quiet_hours, parse_duration
from spok1_agentd.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
dump_yaml = cast(Callable[..., str], getattr(yaml, "safe_dump"))  # noqa: B009
OWNER = "1009339113519972403"


def at(hhmm: str, day: int = 27) -> float:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=IST).timestamp()


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.inbox: dict[str, list[dict[str, Any]]] = {}
        self.created: list[str] = []
        self._id = 1000
        self.existing: list[dict[str, Any]] = [{"id": "77", "name": "alerts", "type": 0}]

    def send(
        self,
        channel_id: str,
        content: str,
        *,
        mention_user: str | None = None,
        embeds: Any = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        self._id += 1
        self.sent.append(
            {
                "channel": channel_id,
                "content": content,
                "mention": mention_user,
                "reply_to": reply_to,
            }
        )
        return {"id": str(self._id)}

    def messages_after(
        self, channel_id: str, after: str | None, limit: int = 50
    ) -> list[dict[str, Any]]:
        msgs = self.inbox.get(channel_id, [])
        return [m for m in msgs if after is None or int(m["id"]) > int(after)]

    def channels(self, guild_id: str) -> list[dict[str, Any]]:
        return self.existing

    def create_channel(
        self,
        guild_id: str,
        name: str,
        kind: int = 0,
        parent_id: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        self._id += 1
        ch = {"id": str(self._id), "name": name, "type": kind}
        self.existing.append(ch)
        self.created.append(name)
        return ch


CHANNELS = {"alerts": "77", "daily-digest": "78", "approvals": "79"}


def notifier(
    clock: Clock, discord: FakeDiscord | None = None, per_hour: int = 20
) -> tuple[Notifier, FakeDiscord, DB]:
    db = DB(":memory:")
    d = discord or FakeDiscord()
    return Notifier(db, d, OWNER, dict(CHANNELS), per_hour=per_hour, clock=clock), d, db


# ── notifier ───────────────────────────────────────────────────────────


def test_quiet_hours_cross_midnight() -> None:
    q = ("23:00", "07:30")
    assert in_quiet_hours(at("23:30"), "Asia/Kolkata", *q)
    assert in_quiet_hours(at("03:00"), "Asia/Kolkata", *q)
    assert not in_quiet_hours(at("07:30"), "Asia/Kolkata", *q)
    assert not in_quiet_hours(at("14:00"), "Asia/Kolkata", *q)


def test_priorities_quiet_hours_and_mentions() -> None:
    clock = Clock(at("23:30"))
    n, d, _db = notifier(clock)
    n.notify("P2", "CI failing", "x", dedup_key="ci")
    assert d.sent == []  # P2 waits during quiet hours
    n.notify("P1", "Manager is down", "y", dedup_key="mgr")
    assert len(d.sent) == 1 and d.sent[0]["mention"] == OWNER and "P1 #2" in d.sent[0]["content"]
    clock.t = at("07:31", day=28)
    n.flush()
    assert (
        len(d.sent) == 2 and d.sent[1]["mention"] is None and "CI failing" in d.sent[1]["content"]
    )
    n.notify("P3", "minor", "z")
    assert len(d.sent) == 2  # P3 goes to the digest only


def test_dedup_counts_instead_of_resending() -> None:
    n, d, db = notifier(Clock(at("12:00")))
    first = n.notify("P2", "Disk 95% full", dedup_key="disk")
    again = n.notify("P2", "Disk 95% full", dedup_key="disk")
    assert first == again and len(d.sent) == 1
    assert db.one("SELECT count FROM alerts WHERE id=?", (first,))["count"] == 2  # type: ignore[index]


def test_rate_limit_holds_extra_messages() -> None:
    n, d, _ = notifier(Clock(at("12:00")), per_hour=3)
    for i in range(5):
        n.notify("P2", f"alert {i}", dedup_key=f"k{i}")
    assert len(d.sent) == 3
    n.notify("P1", "urgent", dedup_key="u")
    assert len(d.sent) == 4  # P1 is exempt


def test_replies_ack_snooze_noise() -> None:
    clock = Clock(at("12:00"))
    n, d, db = notifier(clock)
    a = n.notify("P2", "Job x failed", dedup_key="job:x")
    assert "acked" in n.apply_reply("ack", a)
    b = n.notify("P2", "flaky", dedup_key="flaky")
    n.apply_reply("snooze", b, "1h")
    clock.t += 1800
    n.flush()
    assert sum("flaky" in s["content"] for s in d.sent) == 1  # not re-sent yet
    clock.t += 3601
    n.flush()
    assert sum("flaky" in s["content"] for s in d.sent) == 2  # back after the snooze
    c = n.notify("P2", "noisy", dedup_key="noisy")
    n.apply_reply("noise", c)
    db.exec("UPDATE alerts SET last_seen=last_seen-90000 WHERE id=?", (c,))  # past the dedup window
    clock.t += 90000
    n.notify("P2", "noisy", dedup_key="noisy")
    assert db.one("SELECT priority FROM alerts ORDER BY id DESC LIMIT 1")["priority"] == "P3"  # type: ignore[index]
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='alert.reply'")["n"] == 3  # type: ignore[index]


def test_digest_collects_p3_and_unanswered() -> None:
    n, d, _ = notifier(Clock(at("18:00")))
    n.notify("P3", "Bad job file", dedup_key="b")
    n.notify("P2", "PR waiting", dedup_key="pr")
    text = n.digest()
    assert text and "Bad job file" in text and "PR waiting" in text and "(unanswered)" in text
    assert d.sent[-1]["channel"] == "78"


def test_parse_duration() -> None:
    assert (
        parse_duration("2h") == 7200
        and parse_duration("30m") == 1800
        and parse_duration("1d") == 86400
    )


# ── replies ────────────────────────────────────────────────────────────


def test_reply_parsing_and_owner_only() -> None:
    assert replies.parse("ack 42") == ("ack", 42, "")
    assert replies.parse("snooze #7 3h") == ("snooze", 7, "3h")
    assert replies.parse("do it 5") == ("do it", 5, "")
    assert replies.parse("please ack 5") is None
    db = DB(":memory:")
    d = FakeDiscord()
    d.inbox["77"] = [{"id": "100", "author": {"id": OWNER}, "content": "ack 1"}]
    got: list[replies.Reply] = []
    assert (
        replies.poll(db, d, ["77"], OWNER, lambda r: got.append(r) or None) == 0
    )  # history skipped
    d.inbox["77"] += [
        {"id": "101", "author": {"id": "999"}, "content": "ack 1"},  # someone else
        {"id": "102", "author": {"id": OWNER, "bot": True}, "content": "ack 1"},
        {"id": "103", "author": {"id": OWNER}, "content": "noise 3"},
    ]
    assert replies.poll(db, d, ["77"], OWNER, lambda r: got.append(r) or None) == 1
    assert got[0].verb == "noise" and got[0].alert_id == 3


# ── hq ─────────────────────────────────────────────────────────────────


def test_hq_reuses_existing_channels() -> None:
    db = DB(":memory:")
    d = FakeDiscord()
    ids, _actions = hq.ensure(d, db, "g")  # type: ignore[arg-type]
    assert ids["alerts"] == "77"  # the owner's existing #alerts is reused
    assert "alerts" not in d.created and {
        "command",
        "approvals",
        "daily-digest",
        "learning",
    } <= set(d.created)
    _, again = hq.ensure(d, db, "g")  # type: ignore[arg-type]
    assert again == [] and hq.known(db)["approvals"]


# ── scheduler ──────────────────────────────────────────────────────────


def write_job(brain: Path, rel: str, data: dict[str, Any]) -> None:
    path = brain / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def test_scheduler_one_off_cron_and_paused_projects(tmp_path: Path) -> None:
    db = DB(":memory:")
    now = at("09:00")
    write_job(
        tmp_path,
        "jobs/r1.yaml",
        {
            "id": "r1",
            "type": "reminder",
            "created_by": "laptop",
            "created": "2026-11-27T03:00:00Z",
            "at": "2026-11-27T09:10:00",
            "message": "call the CA",
        },
    )
    write_job(
        tmp_path,
        "jobs/d1.yaml",
        {
            "id": "d1",
            "type": "digest",
            "created_by": "vm",
            "created": "2026-11-27T03:00:00Z",
            "cron": "0 9 * * 1-5",
            "agent": "researcher",
            "prompt": "PR digest",
            "max_turns": 5,
        },
    )
    write_job(
        tmp_path,
        "projects/tool/jobs/t1.yaml",
        {
            "id": "t1",
            "type": "reminder",
            "created_by": "vm",
            "created": "2026-11-27T03:00:00Z",
            "at": "2026-11-27T09:05:00",
            "message": "tool thing",
        },
    )
    (tmp_path / "projects" / "registry.yaml").write_text(
        dump_yaml(
            {
                "version": 1,
                "projects": [
                    {
                        "id": "tool",
                        "name": "Tool",
                        "type": "software",
                        "status": "paused",
                        "created": "2026-11-26",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_job(tmp_path, "jobs/bad.yaml", {"id": "bad", "type": "reminder"})
    loaded, problems = scheduler.sync(db, tmp_path, "Asia/Kolkata", now)
    assert loaded == 2 and len(problems) == 1  # t1 skipped (paused project), bad reported
    assert scheduler.due(db, now) == []
    due = scheduler.due(db, at("09:10"))
    assert [j["id"] for j in due] == ["r1"]  # the 09:00 weekday digest was already past at sync
    scheduler.advance(db, due[0], at("09:10"), "Asia/Kolkata", "ok")
    assert scheduler.due(db, at("23:00")) == []
    nxt = float(db.one("SELECT next_run FROM jobs WHERE id='d1'")["next_run"])  # type: ignore[index]
    assert (
        datetime.fromtimestamp(nxt, IST).strftime("%a %H:%M") == "Mon 09:00"
    )  # 27 Nov 2026 is a Friday


# ── governor and runner ────────────────────────────────────────────────


class FakeProc:
    def __init__(self, out: str, code: int = 0) -> None:
        self.out, self.returncode, self.done = out, code, False

    def poll(self) -> int | None:
        return self.returncode if self.done else None

    def communicate(self) -> tuple[str, str]:
        return self.out, ""


def ok_json(turns: int = 3) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "num_turns": turns,
            "total_cost_usd": 0.01,
            "modelUsage": {"claude-sonnet-5-5": {}},
        }
    )


def test_parse_result_variants() -> None:
    r = runner.parse_result(ok_json(4), "", 0, "sonnet")
    assert r.status == "ok" and r.turns == 4 and r.units == 12
    m = runner.parse_result(
        json.dumps({"subtype": "error_max_turns", "is_error": True, "num_turns": 20}),
        "",
        1,
        "sonnet",
    )
    assert m.status == "failed" and "error_max_turns" in m.summary
    lim = runner.parse_result(
        json.dumps({"is_error": True, "result": "You've hit your session limit · resets 3:45pm"}),
        "",
        1,
        "sonnet",
    )
    assert lim.status == "limited"


def test_runner_command_is_the_fixed_wrapper() -> None:
    cmd = runner.command("researcher", "sonnet", 10, "/home/spok1/work")
    assert cmd[:6] == [
        "sudo",
        "-n",
        "--preserve-env=CLAUDE_CODE_OAUTH_TOKEN",
        "-u",
        "spok1",
        runner.WRAPPER,
    ]
    assert cmd[6:] == ["claude-job", "/home/spok1/work", "sonnet", "10", "researcher"]


def test_governor_concurrency_and_backoff() -> None:
    db = DB(":memory:")
    clock = Clock(at("10:00"))
    procs: list[FakeProc] = []

    def start(job: dict[str, Any]) -> FakeProc:
        p = FakeProc(
            ok_json()
            if job["id"] != "lim"
            else json.dumps({"is_error": True, "result": "You've hit your session limit"}),
            0 if job["id"] != "lim" else 1,
        )
        procs.append(p)
        return p

    g = Governor(db, start, max_parallel=1, clock=clock)
    jobs = {
        j: {"id": j, "project": "global", "model": "sonnet", "priority": "P2"}
        for j in ("a", "b", "lim")
    }
    for j in ("a", "b"):
        g.enqueue(jobs[j])
    g.tick(jobs)
    assert len(procs) == 1  # one at a time
    procs[0].done = True
    finished = g.tick(jobs)
    assert finished[0][1].status == "ok" and len(procs) == 2
    procs[1].done = True
    g.enqueue(jobs["lim"])
    g.tick(jobs)
    procs[2].done = True
    finished = g.tick(jobs)
    assert finished[0][1].status == "limited"
    g.enqueue(jobs["a"])
    g.tick(jobs)
    assert len(procs) == 3  # paused after the limit
    clock.t += 31 * 60
    g.tick(jobs)
    assert len(procs) == 4


# ── watchers ───────────────────────────────────────────────────────────


def fake_run(outputs: dict[str, tuple[int, str]]) -> watchers.Run:
    def run(argv: list[str]) -> tuple[int, str]:
        for key, val in outputs.items():
            if key in " ".join(argv):
                return val
        return 0, ""

    return run


def test_login_expiry_thresholds() -> None:
    now = at("12:00")

    def status(days: float) -> watchers.Run:
        exp = datetime.fromtimestamp(now + days * 86400, ZoneInfo("UTC")).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        return fake_run({"auth-status": (0, json.dumps({"loggedIn": True, "loginExpiresAt": exp}))})

    assert watchers.login_expiry(now, status(30)) == []
    assert watchers.login_expiry(now, status(8))[0].priority == "P2"
    assert watchers.login_expiry(now, status(1))[0].priority == "P1"
    out = watchers.login_expiry(
        now, fake_run({"auth-status": (1, json.dumps({"loggedIn": False}))})
    )
    assert out[0].priority == "P1" and "signed out" in out[0].title


def test_manager_watcher() -> None:
    assert watchers.manager(fake_run({"is-active": (0, "active\n")})) == []
    assert watchers.manager(fake_run({"is-active": (3, "inactive\n")}))[0].priority == "P1"


def test_ci_and_pr_watchers(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "registry.yaml").write_text(
        dump_yaml(
            {
                "version": 1,
                "projects": [
                    {
                        "id": "sandbox",
                        "name": "S",
                        "type": "test",
                        "created": "2026-11-26",
                        "repos": ["https://github.com/o/r"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    now = at("12:00")
    old = datetime.fromtimestamp(now - 2 * 86400, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    run = fake_run(
        {
            "gh-ci": (
                0,
                json.dumps(
                    [
                        {
                            "conclusion": "failure",
                            "workflowName": "CI",
                            "displayTitle": "x",
                            "url": "u",
                            "databaseId": 9,
                        }
                    ]
                ),
            ),
            "gh-prs": (
                0,
                json.dumps(
                    [{"number": 3, "title": "t", "url": "u", "createdAt": old, "isDraft": True}]
                ),
            ),
        }
    )
    out = watchers.ci_and_prs(tmp_path, now, run)
    assert {a.source for a in out} == {"ci", "pr"} and all(a.project == "sandbox" for a in out)


def test_job_failure_escalates() -> None:
    assert watchers.job_failure("j", "p", 1, "boom")[0].priority == "P2"
    assert watchers.job_failure("j", "p", 3, "boom")[0].priority == "P1"


# ── daemon end to end ──────────────────────────────────────────────────


def test_daemon_reminder_reaches_discord(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    write_job(
        brain,
        "jobs/r1.yaml",
        {
            "id": "r1",
            "type": "reminder",
            "created_by": "laptop",
            "created": "2026-11-27T03:00:00Z",
            "at": "2026-11-27T10:10:00",
            "message": "call the CA",
        },
    )
    settings = Settings(
        bot_token="t",
        guild_id="g",
        owner_id=OWNER,
        brain_dir=brain,
        work_dir=tmp_path / "work",
        state_dir=tmp_path / "state",
    )
    clock = Clock(at("10:00"))
    d = FakeDiscord()
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return (0, "active") if "is-active" in argv else (0, "{}")

    daemon = Daemon(settings, DB(":memory:"), d, clock=clock, run=run)  # type: ignore[arg-type]
    daemon.setup_discord()
    daemon.tick()
    assert not any("call the CA" in s["content"] for s in d.sent)
    clock.t = at("10:10")
    daemon.tick()
    assert any("⏰ call the CA" in s["content"] for s in d.sent)
    assert any(
        "git-pull-brain" in " ".join(c) for c in calls
    )  # the brain is pulled through the wrapper


@pytest.mark.parametrize("path", ["vm/spok1-runas"])
def test_wrapper_only_allows_fixed_subcommands(path: str) -> None:
    text = (Path(__file__).resolve().parents[3] / path).read_text(encoding="utf-8")
    assert (
        "unset ANTHROPIC_API_KEY" in text
        and 'case "$cwd" in /home/spok1/work|/home/spok1/projects/*)' in text
    )
    assert "eval" not in text and "bash -c" not in text
