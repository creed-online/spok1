from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from spok1_agentd import learning, replies
from spok1_agentd.daemon import Daemon
from spok1_agentd.db import DB
from spok1_agentd.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 11, 28)
NOW = datetime(2026, 11, 28, 2, 0, tzinfo=IST).timestamp()

BELIEFS = """version: 1
beliefs:
  - id: comm-short-direct
    statement: "Wants short, direct replies."
    alpha: 9
    beta: 1
    impact: 5
    pinned: true
    evidence:
      - {date: 2026-11-26, source: interview, kind: confirm, note: "setup interview"}
    created: 2026-11-26
    updated: 2026-11-26
  - id: tooling-pnpm
    statement: "Uses pnpm for JavaScript."
    alpha: 9
    beta: 1
    pinned: true
    evidence:
      - {date: 2026-11-26, source: interview, kind: confirm}
    created: 2026-11-26
    updated: 2026-11-26
"""


def make_brain(tmp: Path) -> Path:
    brain = tmp / "brain"
    (brain / "inbox").mkdir(parents=True)
    (brain / "beliefs.yaml").write_text(BELIEFS, encoding="utf-8")
    (brain / "changelog.md").write_text("# Changelog\n", encoding="utf-8")
    (brain / "feedback.md").write_text(
        "# Feedback\n\n- 2026-11-27 [global] don't ask permission for small routine things\n",
        encoding="utf-8",
    )
    (brain / "inbox" / "laptop-2026-11-27-aa.md").write_text(
        "# Built the learning loop\n\n## Corrections\n\nUse uv run --no-sync on the laptop.\n\n## Lessons\n\n(none)\n",
        encoding="utf-8",
    )
    return brain


class Brain:
    """Stands in for the spok1-runas brain-write / brain-commit pair."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.commits: list[str] = []

    def write(self, name: str, content: str) -> None:
        assert name in ("beliefs.yaml", "profile.md", "changelog.md")
        (self.root / name).write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        assert message.startswith("learning: ")
        self.commits.append(message)
        return f"sha{len(self.commits)}"


def reply_with(**props: Any) -> str:
    return "Here you go:\n```json\n" + json.dumps(props) + "\n```"


NEW_AUTONOMY = {
    "action": "new",
    "id": "autonomy-no-small-prompts",
    "statement": "Don't ask permission for small routine things.",
    "note": "said so on 2026-11-27",
    "source": "feedback",
}


def test_nightly_learns_explicit_feedback_and_skips_when_nothing_new(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    b = Brain(brain)
    db = DB(":memory:")
    prompts: list[str] = []

    def think(prompt: str) -> str:
        prompts.append(prompt)
        return reply_with(
            beliefs=[NEW_AUTONOMY],
            facts=[{"text": "On the laptop, use uv run --no-sync", "app_id": "spok1"}],
            questions=[],
        )

    text = learning.run_nightly(db, brain, think, b.write, b.commit, now=NOW)
    assert "don't ask permission" in prompts[0] and "uv run --no-sync" in prompts[0]
    assert "comm-short-direct · 0.90" in prompts[0]
    assert "#1" in text and "in your profile" in text and "sha1" in text
    assert "worth remembering: On the laptop" in text  # no Mem0 key: shown, not saved
    bf = learning.load_beliefs(brain)
    new = next(x for x in bf.beliefs if x.id == "autonomy-no-small-prompts")
    assert new.pinned and new.confidence >= 0.8
    profile = (brain / "profile.md").read_text(encoding="utf-8")
    assert "Don't ask permission for small routine things." in profile
    assert "## How to talk to me" in profile and "Uses pnpm" in profile
    assert "autonomy-no-small-prompts" in (brain / "changelog.md").read_text(encoding="utf-8")
    # a second night with no new signals doesn't call Claude at all
    prompts.clear()
    assert (
        learning.run_nightly(db, brain, think, b.write, b.commit, now=NOW + 86400)
        == learning.NOTHING
    )
    assert prompts == [] and len(b.commits) == 1


def test_checks_reject_bad_proposals(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    bf = learning.load_beliefs(brain)
    props = learning.Proposals.model_validate(
        {
            "beliefs": [
                {"action": "new", "id": "Bad ID!", "statement": "x"},
                {"action": "new", "id": "code-secret", "statement": "token is ghp_abcdefghijkl"},
                {"action": "confirm", "id": "unknown-belief"},
                {
                    "action": "contradict",
                    "id": "tooling-pnpm",
                    "note": "used npm once",
                    "source": "episode",
                },
                *[
                    {"action": "new", "id": f"code-rule-{i}", "statement": f"rule {i}"}
                    for i in range(20)
                ],
            ],
            "questions": [{"belief_id": "nope", "text": "?"}],
        }
    )
    changes, questions, problems = learning.apply(bf, props, TODAY)
    ids = [c[0] for c in changes]
    assert "code-secret" not in ids and "unknown-belief" not in ids
    assert len(props.beliefs[: learning.MAX_CHANGES]) == 12 and len(changes) <= 12
    # an inferred contradiction of a pinned belief becomes a question, not a change
    assert "tooling-pnpm" not in ids
    assert len(questions) == 1 and questions[0].belief_id == "tooling-pnpm"
    assert len(problems) == 3
    # inferred beliefs start below the profile bar
    assert not learning.in_profile(next(b for b in bf.beliefs if b.id == "code-rule-0"))


def test_inferred_belief_is_promoted_after_three_confirmations(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    bf = learning.load_beliefs(brain)
    new = {
        "action": "new",
        "id": "code-type-hints",
        "statement": "Likes strict typing.",
        "source": "episode",
    }
    confirm = {"action": "confirm", "id": "code-type-hints", "source": "episode"}
    learning.apply(bf, learning.Proposals.model_validate({"beliefs": [new]}), TODAY)
    learning.apply(bf, learning.Proposals.model_validate({"beliefs": [confirm]}), TODAY)
    b = next(x for x in bf.beliefs if x.id == "code-type-hints")
    assert not learning.in_profile(b)
    learning.apply(bf, learning.Proposals.model_validate({"beliefs": [confirm]}), TODAY)
    b = next(x for x in bf.beliefs if x.id == "code-type-hints")
    assert b.confidence >= 0.8 and len(b.evidence) == 3 and learning.in_profile(b)
    assert "Likes strict typing." in learning.render_profile(bf, TODAY)


def test_unusable_reply_changes_nothing_and_retries(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    b = Brain(brain)
    db = DB(":memory:")
    text = learning.run_nightly(db, brain, lambda _p: "sorry, no JSON", b.write, b.commit, now=NOW)
    assert "skipped" in text and b.commits == []
    assert learning.prepare(db, brain) is not None  # the signals are still pending


def test_revert_and_answers(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    b = Brain(brain)
    db = DB(":memory:")
    confirm = {"action": "confirm", "id": "comm-short-direct", "note": "again", "source": "episode"}
    q = {"belief_id": "tooling-pnpm", "text": "Still pnpm everywhere?"}
    learning.run_nightly(
        db,
        brain,
        lambda _p: reply_with(beliefs=[NEW_AUTONOMY, confirm], questions=[q]),
        b.write,
        b.commit,
        now=NOW,
    )
    before = {x.id: x for x in learning.load_beliefs(brain).beliefs}
    assert before["comm-short-direct"].alpha == 10
    # revert the new belief: it's gone; revert the confirm: alpha is back to 9
    assert "Reverted #1" in learning.revert(db, brain, 1, b.write, b.commit, TODAY)
    assert "Reverted #2" in learning.revert(db, brain, 2, b.write, b.commit, TODAY)
    after = {x.id: x for x in learning.load_beliefs(brain).beliefs}
    assert "autonomy-no-small-prompts" not in after and after["comm-short-direct"].alpha == 9
    assert "No change #1" in learning.revert(db, brain, 1, b.write, b.commit, TODAY)
    assert "reverted #2" in (brain / "changelog.md").read_text(encoding="utf-8")
    # answer the question: no → less sure and unpinned
    text = learning.answer(db, brain, 1, False, b.write, b.commit, TODAY)
    assert "tooling-pnpm" in text
    pnpm = next(x for x in learning.load_beliefs(brain).beliefs if x.id == "tooling-pnpm")
    assert pnpm.beta == 4 and not pnpm.pinned
    assert "No open question" in learning.answer(db, brain, 1, True, b.write, b.commit, TODAY)


def test_reply_verbs_for_learning() -> None:
    assert replies.parse("revert 4") == ("revert", 4, "")
    assert replies.parse("yes 2") == ("yes", 2, "")
    assert replies.parse("No #3") == ("no", 3, "")


class FakeProc:
    def __init__(self, result: str) -> None:
        self.returncode = 0
        self.out = json.dumps({"result": result, "num_turns": 1, "subtype": "success"})

    def poll(self) -> int:
        return 0

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        return self.out, ""


def test_started_job_can_be_waited_on_with_a_timeout(monkeypatch: Any) -> None:
    import sys

    from spok1_agentd import runner

    code = "import sys; print(sys.stdin.read().upper())"
    monkeypatch.setattr(runner, "command", lambda *_a: [sys.executable, "-c", code])
    proc = runner.start(None, "sonnet", 3, ".", "hello", "tok")
    out, _err = proc.communicate(timeout=30)  # was: ValueError on the closed stdin
    assert out.strip() == "HELLO"


def test_learn_now_posts_even_in_quiet_hours(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    settings = Settings(
        bot_token="t",
        guild_id="g",
        owner_id="1",
        oauth_token="tok",
        brain_dir=brain,
        work_dir=tmp_path / "work",
        state_dir=tmp_path / "state",
    )

    def run_input(argv: list[str], data: str) -> tuple[int, str]:
        (brain / argv[-1]).write_text(data, encoding="utf-8")
        return 0, ""

    d = FakeDiscord()
    daemon = Daemon(
        settings,
        DB(":memory:"),
        d,  # type: ignore[arg-type]
        clock=lambda: NOW,
        run=lambda _a: (0, "abc9999"),
        run_input=run_input,
        start_job=lambda _j: FakeProc(reply_with(beliefs=[NEW_AUTONOMY])),
    )
    daemon.channels.update({"alerts": "77"})
    text = daemon.learn_now()
    assert "What I learned today" in text and "abc9999" in text
    assert d.sent == [("77", text)]  # no #learning channel: falls back to #alerts
    assert daemon.learn_now() == learning.NOTHING


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, channel_id: str, content: str, **_k: Any) -> dict[str, Any]:
        self.sent.append((channel_id, content))
        return {"id": str(len(self.sent))}

    def messages_after(self, channel_id: str, after: str | None, limit: int = 50) -> list[Any]:
        return []


def test_daemon_runs_learning_at_night_and_reports_in_the_morning(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    settings = Settings(
        bot_token="t",
        guild_id="g",
        owner_id="1",
        oauth_token="tok",
        brain_dir=brain,
        work_dir=tmp_path / "work",
        state_dir=tmp_path / "state",
    )
    clock = [datetime(2026, 11, 28, 1, 59, tzinfo=IST).timestamp()]
    started: list[dict[str, Any]] = []
    writes: list[tuple[list[str], str]] = []

    def start_job(job: dict[str, Any]) -> FakeProc:
        started.append(job)
        return FakeProc(reply_with(beliefs=[NEW_AUTONOMY]))

    def run(argv: list[str]) -> tuple[int, str]:
        if "brain-commit" in argv:
            return 0, "abc1234\n"
        return (0, "active") if "is-active" in argv else (0, "{}")

    def run_input(argv: list[str], data: str) -> tuple[int, str]:
        writes.append((argv, data))
        (brain / argv[-1]).write_text(data, encoding="utf-8")
        return 0, ""

    d = FakeDiscord()
    daemon = Daemon(
        settings,
        DB(":memory:"),
        d,  # type: ignore[arg-type]
        clock=lambda: clock[0],
        run=run,
        run_input=run_input,
        start_job=start_job,
    )
    daemon.channels.update({"alerts": "77", "learning": "80"})
    daemon.tick()
    assert started == []  # not 02:00 yet
    clock[0] += 60
    daemon.tick()  # 02:00: queued
    daemon.tick()  # started
    daemon.tick()  # finished: applied, but the report waits (quiet hours)
    assert len(started) == 1 and started[0]["type"] == "learning" and started[0]["max_turns"] == 3
    assert {w[0][-1] for w in writes} == {"beliefs.yaml", "profile.md", "changelog.md"}
    assert all(
        w[0][:5] == ["sudo", "-n", "-u", "spok1", "/opt/spok1/bin/spok1-runas"] for w in writes
    )
    assert not any("What I learned" in c for _ch, c in d.sent)
    clock[0] = datetime(2026, 11, 28, 7, 31, tzinfo=IST).timestamp()
    daemon.tick()
    reports = [(ch, c) for ch, c in d.sent if "What I learned" in c]
    assert len(reports) == 1 and reports[0][0] == "80" and "abc1234" in reports[0][1]
    daemon.tick()  # once only, and not again the same day
    assert len([c for _ch, c in d.sent if "What I learned" in c]) == 1 and len(started) == 1


def test_no_token_no_run(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    settings = Settings(brain_dir=brain, work_dir=tmp_path / "w", state_dir=tmp_path / "s")
    started: list[Any] = []
    daemon = Daemon(
        settings,
        DB(":memory:"),
        None,
        clock=lambda: NOW,
        run=lambda _a: (0, "active"),
        start_job=lambda j: started.append(j),
    )
    daemon.tick()
    assert started == []


def test_beliefs_dump_round_trips(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    bf = learning.load_beliefs(brain)
    again = yaml.safe_load(learning.dump_beliefs(bf))
    assert learning.load_beliefs(brain).model_dump() == type(bf).model_validate(again).model_dump()


def test_wrapper_brain_write_is_restricted() -> None:
    text = (Path(__file__).resolve().parents[3] / "vm/spok1-runas").read_text(encoding="utf-8")
    assert "beliefs.yaml|profile.md|changelog.md) ;;" in text
    assert "^learning:" in text and 'chmod 0644 "$tmp"' in text
