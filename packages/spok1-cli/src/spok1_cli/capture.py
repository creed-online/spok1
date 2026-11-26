"""Episode capture: transcript → haiku → `brain/inbox/<machine>-<date>-<id>.md` → git push.

Runs in a detached background process (`spok1 capture`), never inside a hook.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from spok1_cli import claude_cli, sessions, transcript
from spok1_core import config, paths

MIN_USER_TURNS = 2  # skip trivial sessions (saves Max usage)
LOCK_STALE_SECONDS = 30 * 60

PROMPT = """You are the spok1 librarian. Read the Claude Code session transcript below and write a
Reflexion-style debrief as ONE JSON object, with no other text. Fields:

- "title": short title of what the session was about (max 80 chars)
- "goal": what the user wanted
- "plan": how the work was approached
- "what_happened": what was actually done, and the result
- "outcome": one of "success", "partial", "failed", "abandoned", "unclear"
- "corrections": list of things the user corrected or redirected (their words, short)
- "feedback": list of explicit praise/complaints from the user (short)
- "self_critique": what should have been done differently
- "lessons": list of durable lessons about how this user works (preferences, conventions)
- "importance": integer 1-10, how useful this episode is for future work
- "project": the project or repo name if obvious, else ""

Rules: never include secrets, tokens, passwords, API keys, webhook URLs or other people's
personal data; write "[redacted]" instead. Be concise. Use [] for empty lists.

TRANSCRIPT:
"""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class CaptureError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply (tolerates code fences / chatter)."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise CaptureError("no JSON object in model output")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise CaptureError("model output is not valid JSON") from exc
    if not isinstance(data, dict):
        raise CaptureError("model output is not a JSON object")
    return cast(dict[str, Any], data)


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in cast(list[object], value) if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _importance(value: object) -> int:
    try:
        return max(1, min(10, int(str(value))))
    except ValueError:
        return 3


def short_title(title: str, limit: int = 80) -> str:
    """Trim a title to `limit` characters at a word boundary."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def _dump_yaml(data: dict[str, Any]) -> str:
    dump = cast(Callable[..., str], getattr(yaml, "safe_dump"))  # noqa: B009 - stub has an Unknown
    return dump(data, sort_keys=False, allow_unicode=True)


def render_episode(
    data: dict[str, Any],
    *,
    episode_id: str,
    machine: str,
    marker: sessions.Marker,
    digest: transcript.Digest,
    when: datetime,
) -> str:
    front: dict[str, Any] = {
        "id": episode_id,
        "kind": "episode",
        "machine": machine,
        "session_id": marker.session_id,
        "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cwd": marker.cwd,
        "project": str(data.get("project") or ""),
        "end_reason": marker.reason,
        "outcome": str(data.get("outcome") or "unclear"),
        "importance": _importance(data.get("importance")),
        "signals": {
            "user_turns": digest.user_turns,
            "assistant_turns": digest.assistant_turns,
            "corrections": len(_as_list(data.get("corrections"))),
            "tools_used": digest.tools_used,
        },
    }

    def section(title: str, body: str | list[str]) -> str:
        if isinstance(body, list):
            text = "\n".join(f"- {item}" for item in body) if body else "- (none)"
        else:
            text = body.strip() or "(none)"
        return f"## {title}\n\n{text}\n"

    parts = [
        "---\n" + _dump_yaml(front) + "---\n",
        f"# {short_title(str(data.get('title') or 'Session'))}\n",
        section("Goal", str(data.get("goal") or "")),
        section("Plan", str(data.get("plan") or "")),
        section("What happened", str(data.get("what_happened") or "")),
        section("Corrections", _as_list(data.get("corrections"))),
        section("Feedback", _as_list(data.get("feedback"))),
        section("Self-critique", str(data.get("self_critique") or "")),
        section("Lessons", _as_list(data.get("lessons"))),
    ]
    return "\n".join(parts)


def episode_name(machine: str, when: datetime, session_id: str) -> str:
    short = re.sub(r"[^A-Za-z0-9]", "", session_id)[:8] or "session"
    return f"{machine}-{when:%Y-%m-%d}-{short}"


# ── git ────────────────────────────────────────────────────────────────


def _git(brain: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(brain), *args],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def commit_and_push(brain: Path, files: list[Path], message: str) -> bool:
    """Commit new inbox files and push. Returns True if pushed (False = committed only)."""
    rel = [str(f.relative_to(brain)) for f in files]
    if _git(brain, "add", "--", *rel).returncode != 0:
        raise CaptureError("git add failed")
    if _git(brain, "commit", "-m", message, "--", *rel).returncode != 0:
        raise CaptureError("git commit failed")
    # Inbox files are unique, so a rebase can't conflict with other machines' pushes.
    _git(brain, "pull", "--rebase", "--quiet")
    return _git(brain, "push", "--quiet").returncode == 0


def push_pending_commits(brain: Path) -> None:
    """Retry pushing commits that a previous capture couldn't push (e.g. offline)."""
    status = _git(brain, "status", "-sb")
    if status.returncode == 0 and "ahead" in status.stdout.splitlines()[0]:
        _git(brain, "pull", "--rebase", "--quiet")
        _git(brain, "push", "--quiet")


# ── lock + main loop ───────────────────────────────────────────────────


def _lock_path() -> Path:
    return paths.state_dir() / "capture.lock"


def acquire_lock() -> bool:
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as handle:
        handle.write(str(os.getpid()))
    return True


def release_lock() -> None:
    _lock_path().unlink(missing_ok=True)


def log(message: str) -> None:
    path = paths.state_dir() / "capture.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def capture_one(marker_path: Path, brain: Path, machine: str) -> str:
    """Capture one session. Returns a short status string."""
    marker = sessions.read(marker_path)
    if marker is None:
        sessions.done(marker_path)
        return "skipped: unreadable marker"
    digest = transcript.digest(Path(marker.transcript_path))
    if digest.user_turns < MIN_USER_TURNS:
        sessions.done(marker_path)
        return f"skipped: {marker.session_id} has {digest.user_turns} user turn(s)"
    try:
        reply = claude_cli.run_print(PROMPT + digest.text)
        data = extract_json(reply)
        when = datetime.fromtimestamp(marker.ended_at or time.time(), UTC)
        name = episode_name(machine, when, marker.session_id)
        inbox = brain / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"{name}.md"
        target.write_text(
            render_episode(
                data,
                episode_id=name,
                machine=machine,
                marker=marker,
                digest=digest,
                when=when,
            ),
            encoding="utf-8",
        )
        pushed = commit_and_push(brain, [target], f"inbox: episode {name}")
    except (claude_cli.ClaudeError, CaptureError, OSError, subprocess.SubprocessError) as exc:
        sessions.record_failure(marker_path, marker, str(exc))
        return f"failed: {marker.session_id}: {exc}"
    sessions.done(marker_path)
    return f"captured: {name}{'' if pushed else ' (committed, push pending)'}"


def run(limit: int = 10) -> list[str]:
    """Process pending sessions (at most `limit`). Safe to call concurrently."""
    if claude_cli.is_internal():
        return []
    if not acquire_lock():
        return ["skipped: another capture is running"]
    results: list[str] = []
    try:
        brain = paths.brain_dir()
        if not (brain / ".git").exists():
            results.append(f"skipped: no brain repo at {brain}")
        else:
            machine = config.machine_name()
            push_pending_commits(brain)
            for marker_path in sessions.pending()[:limit]:
                results.append(capture_one(marker_path, brain, machine))
    finally:
        release_lock()
        for line in results:
            log(line)
    return results
