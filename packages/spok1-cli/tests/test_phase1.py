from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spok1_cli import capture, claude_cli, hooks, install, sessions, transcript

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPOK1_HOME", str(tmp_path))
    monkeypatch.delenv("SPOK1_BRAIN_DIR", raising=False)
    monkeypatch.delenv(claude_cli.INTERNAL_ENV, raising=False)
    return tmp_path


def write_transcript(path: Path, user_msgs: int) -> Path:
    rows: list[dict[str, object]] = []
    for i in range(user_msgs):
        rows.append({"type": "user", "message": {"role": "user", "content": f"question {i}"}})
        rows.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "secret thoughts"},
                        {"type": "text", "text": f"answer {i}"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                    ]
                },
            }
        )
        rows.append(
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}
        )
    rows.append({"type": "user", "isMeta": True, "message": {"content": "meta"}})
    rows.append({"type": "user", "isSidechain": True, "message": {"content": "subagent"}})
    rows.append({"type": "ai-title", "aiTitle": "t"})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return path


# ── transcript ─────────────────────────────────────────────────────────


def test_digest_keeps_dialogue_and_tool_names_only(tmp_path: Path) -> None:
    d = transcript.digest(write_transcript(tmp_path / "t.jsonl", 2))
    assert d.user_turns == 2 and d.assistant_turns == 2
    assert "USER: question 0" in d.text and "ASSISTANT: answer 1" in d.text
    assert "[tool: Bash ls -la]" in d.text
    assert "secret thoughts" not in d.text and "meta" not in d.text and "subagent" not in d.text
    assert d.tools_used == ["Bash"]


def test_digest_truncates_long_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript, "MAX_CHARS", 100)
    d = transcript.digest(write_transcript(tmp_path / "t.jsonl", 20))
    assert d.text.startswith("[…earlier part")
    assert len(d.text) < 200


def test_digest_missing_file(tmp_path: Path) -> None:
    assert transcript.digest(tmp_path / "nope.jsonl").user_turns == 0


# ── session markers ────────────────────────────────────────────────────


def test_marker_lifecycle(tmp_path: Path) -> None:
    sessions.mark_active("s1", str(tmp_path / "t.jsonl"), "/work")
    assert (sessions.sessions_dir() / "active" / "s1.json").exists()
    sessions.mark_ended("s1", str(tmp_path / "t.jsonl"), "/work", "prompt_input_exit")
    assert not (sessions.sessions_dir() / "active" / "s1.json").exists()
    [path] = sessions.pending()
    marker = sessions.read(path)
    assert marker is not None and marker.reason == "prompt_input_exit" and marker.ended_at


def test_unsafe_session_ids_are_sanitised() -> None:
    sessions.mark_active("../../evil", "t", "c")
    assert (sessions.sessions_dir() / "active" / "______evil.json").exists()


def test_crashed_sessions_are_promoted(tmp_path: Path) -> None:
    t = write_transcript(tmp_path / "t.jsonl", 2)
    sessions.mark_active("s2", str(t), "/w")
    assert sessions.promote_stale_active(now=time.time()) == 0
    assert sessions.promote_stale_active(now=time.time() + 3 * 3600) == 1
    marker = sessions.read(sessions.pending()[0])
    assert marker is not None and marker.reason == "crashed"


def test_failures_retry_then_give_up() -> None:
    sessions.mark_ended("s3", "t", "c", "other")
    for _ in range(sessions.MAX_ATTEMPTS):
        path = sessions.pending()[0]
        marker = sessions.read(path)
        assert marker is not None
        sessions.record_failure(path, marker, "boom")
    assert sessions.pending() == []
    assert (sessions.sessions_dir() / "failed" / "s3.json").exists()


# ── hooks ──────────────────────────────────────────────────────────────


def test_hooks_never_raise_and_spawn_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[bool] = []
    monkeypatch.setattr(hooks, "spawn_capture", lambda: spawned.append(True))
    payload = json.dumps(
        {"session_id": "s4", "transcript_path": "t", "cwd": "c", "reason": "other"}
    )
    assert hooks.run("session-start", payload) == 0
    assert spawned == []  # nothing pending yet
    assert hooks.run("session-end", payload) == 0
    assert spawned == [True]
    assert hooks.run("session-end", "not json") == 0
    assert hooks.run("unknown", "{}") == 0


def test_hooks_skip_spok1s_own_claude_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude_cli.INTERNAL_ENV, "1")
    monkeypatch.setattr(hooks, "spawn_capture", lambda: pytest.fail("must not spawn"))
    assert hooks.run("session-end", json.dumps({"session_id": "s5"})) == 0
    assert sessions.pending() == []


def test_hook_cli_is_fast_and_silent(sandbox: Path) -> None:
    """SessionStart stdout would be injected into the session, so it must print nothing."""
    env = {**os.environ, "SPOK1_HOME": str(sandbox)}
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "spok1_cli", "hook", "session-start"],
        input=json.dumps({"session_id": "s6", "transcript_path": "t", "cwd": "c"}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    elapsed = time.perf_counter() - start
    assert result.returncode == 0
    assert result.stdout == ""
    assert elapsed < 5


# ── claude env ─────────────────────────────────────────────────────────


def test_child_env_strips_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    env = claude_cli.child_env()
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env[claude_cli.INTERNAL_ENV] == "1"


# ── capture ────────────────────────────────────────────────────────────


def test_extract_json_tolerates_fences() -> None:
    assert capture.extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(capture.CaptureError):
        capture.extract_json("no json here")


def test_render_episode(tmp_path: Path) -> None:
    marker = sessions.Marker(
        session_id="abcdef123456", transcript_path="t", cwd="/w", reason="other"
    )
    digest = transcript.Digest("x", 3, 3, ["Bash", "Edit"])
    when = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    name = capture.episode_name("home-laptop", when, marker.session_id)
    assert name == "home-laptop-2026-11-26-abcdef12"
    text = capture.render_episode(
        {"title": "Fix CI", "outcome": "success", "corrections": ["use pnpm"], "importance": 99},
        episode_id=name,
        machine="home-laptop",
        marker=marker,
        digest=digest,
        when=when,
    )
    assert text.startswith("---\nid: home-laptop-2026-11-26-abcdef12\n")
    assert "importance: 10" in text  # clamped
    assert "# Fix CI" in text and "- use pnpm" in text and "corrections: 1" in text


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


def test_capture_end_to_end_with_fake_claude(
    tmp_path: Path, sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    brain = sandbox / "brain"
    _git(tmp_path, "clone", "-q", str(remote), str(brain))
    _git(brain, "config", "user.email", "t@example.com")
    _git(brain, "config", "user.name", "t")
    (brain / "README.md").write_text("x", encoding="utf-8")
    _git(brain, "add", ".")
    _git(brain, "commit", "-qm", "init")
    _git(brain, "push", "-q", "origin", "HEAD")

    t_long = write_transcript(tmp_path / "long.jsonl", 3)
    t_short = write_transcript(tmp_path / "short.jsonl", 1)
    sessions.mark_ended("long1234", str(t_long), "/w", "other")
    sessions.mark_ended("short123", str(t_short), "/w", "other")

    reply = json.dumps({"title": "Did a thing", "outcome": "success", "lessons": ["likes tests"]})

    def fake_run_print(prompt: str, **_: object) -> str:
        return f"```json\n{reply}\n```"

    monkeypatch.setattr(claude_cli, "run_print", fake_run_print)

    results = capture.run()
    assert any(r.startswith("captured:") for r in results), results
    assert any("skipped: short123" in r for r in results), results
    assert sessions.pending() == []
    [episode] = list((brain / "inbox").glob("*.md"))
    assert "# Did a thing" in episode.read_text(encoding="utf-8")
    log = subprocess.run(  # noqa: S603
        ["git", "-C", str(remote), "log", "--oneline"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    assert "inbox: episode" in log.stdout  # pushed to the remote


def test_capture_lock_prevents_parallel_runs() -> None:
    assert capture.acquire_lock()
    try:
        assert capture.run() == ["skipped: another capture is running"]
    finally:
        capture.release_lock()


# ── install helpers ────────────────────────────────────────────────────


def test_claude_md_block_is_idempotent_and_keeps_user_content() -> None:
    line = "@~/brain/CLAUDE.md"
    once = install.with_block("# my notes\n", line)
    assert once.startswith("# my notes\n") and line in once
    assert install.with_block(once, line) == once
    changed = install.with_block(once, "@/other/CLAUDE.md")
    assert "@/other/CLAUDE.md" in changed and line not in changed
    assert changed.count(install.BLOCK_BEGIN) == 1


def test_import_line_escapes_spaces(sandbox: Path) -> None:
    assert install.import_line(sandbox / "brain") == "@~/brain/CLAUDE.md"
    custom = install.import_line(Path("C:/Users/A B/brain"))
    assert custom == "@C:/Users/A\\ B/brain/CLAUDE.md"


def test_merge_hooks_keeps_other_hooks_and_is_idempotent() -> None:
    exe = Path("C:/x/.venv/Scripts/spok1.exe")
    other = {"type": "command", "command": "echo hi"}
    settings: dict[str, object] = {
        "model": "opus",
        "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [other]}]},
    }
    once = install.merge_hooks(json.loads(json.dumps(settings)), exe)
    twice = install.merge_hooks(json.loads(json.dumps(once)), exe)
    assert once == twice
    start = once["hooks"]["SessionStart"]
    assert start[0] == {"matcher": "startup", "hooks": [other]}
    assert start[1]["hooks"][0]["args"] == ["hook", "session-start"]
    assert once["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 10
    assert once["model"] == "opus"


def test_dir_links(tmp_path: Path) -> None:
    target = tmp_path / "brain" / "skills" / "s1"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("x", encoding="utf-8")
    link = tmp_path / "claude" / "skills" / "s1"
    install.make_dir_link(link, target)
    assert install.is_link(link) and install.same_target(link, target)
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "x"
    install.remove_link(link)
    assert not link.exists() and (target / "SKILL.md").exists()  # target untouched


def test_install_refuses_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert install.run(brain_repo=None, machine=None, dry_run=True) == 1


def test_repo_root() -> None:
    assert install.repo_root() == ROOT


def test_parse_subscription() -> None:
    assert install.parse_subscription('{"subscriptionType": "max", "email": "x"}') == "max"
    assert install.parse_subscription('{"subscriptionType": "Pro"}') == "pro"
    assert install.parse_subscription("not json") == ""
    assert install.parse_subscription("[]") == ""
