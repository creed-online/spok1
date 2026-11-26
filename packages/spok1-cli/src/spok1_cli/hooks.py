"""Claude Code hook handlers: `spok1 hook session-start` and `spok1 hook session-end`.

Rules for every hook here:
- Fast. SessionEnd has a 1.5 s budget, so slow work goes to a detached `spok1 capture`.
- Silent. SessionStart stdout would be injected into the session as context.
- Never break the session. Errors are logged to ~/.spok1/state/hooks.log and exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from spok1_cli import claude_cli, sessions
from spok1_core import paths

PULL_TIMEOUT = 10


def _log(message: str) -> None:
    try:
        path = paths.state_dir() / "hooks.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def read_input(raw: str) -> dict[str, Any]:
    try:
        data: object = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def spawn_capture() -> None:
    """Start `spok1 capture` fully detached, so it outlives the hook and the session."""
    cmd = [sys.executable, "-m", "spok1_cli", "capture"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(paths.home()),
    }
    if sys.platform == "win32":
        base = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
        )
        try:  # escape Claude Code's job object so the capture isn't killed with the session
            subprocess.Popen(  # noqa: S603 - fixed argv
                cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs
            )
        except OSError:
            subprocess.Popen(cmd, creationflags=base, **kwargs)  # noqa: S603
    else:
        subprocess.Popen(cmd, start_new_session=True, **kwargs)  # noqa: S603


def _pull_brain() -> None:
    brain = paths.brain_dir()
    if not (brain / ".git").exists():
        return
    # Concurrent `git pull`s in one repo clobber each other's FETCH_HEAD ("Cannot
    # fast-forward to multiple branches"). If another session is pulling, skip: it
    # brings in the same changes.
    lock = paths.state_dir() / "pull.lock"
    if not _try_lock(lock, stale_after=PULL_TIMEOUT * 3):
        return
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(brain), "pull", "--ff-only", "--quiet"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=PULL_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            _log(f"session-start: brain pull failed (continuing): {result.stderr.strip()[:200]}")
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"session-start: brain pull error (continuing): {exc}")
    finally:
        lock.unlink(missing_ok=True)


def _try_lock(lock: Path, stale_after: float) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > stale_after:
            lock.unlink(missing_ok=True)
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    except OSError:
        return False
    return True


def session_start(data: dict[str, Any]) -> None:
    session_id = str(data.get("session_id") or "")
    if session_id:
        sessions.mark_active(
            session_id, str(data.get("transcript_path") or ""), str(data.get("cwd") or "")
        )
    _pull_brain()
    sessions.promote_stale_active()
    if sessions.pending():
        spawn_capture()


def session_end(data: dict[str, Any]) -> None:
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return
    transcript_path = str(data.get("transcript_path") or "")
    if not transcript_path or not Path(transcript_path).is_file():
        # e.g. `claude mcp list` / `claude auth status`: no conversation, nothing to capture
        sessions.forget(session_id)
        return
    sessions.mark_ended(
        session_id,
        transcript_path,
        str(data.get("cwd") or ""),
        str(data.get("reason") or ""),
    )
    spawn_capture()


HANDLERS = {"session-start": session_start, "session-end": session_end}


def run(event: str, raw_input: str) -> int:
    """Entry point for `spok1 hook <event>`. Always returns 0."""
    if claude_cli.is_internal():
        return 0  # a claude process started by spok1 itself: never capture it
    handler = HANDLERS.get(event)
    if handler is None:
        _log(f"unknown hook event: {event}")
        return 0
    try:
        handler(read_input(raw_input))
    except Exception:
        _log(f"{event} failed:\n{traceback.format_exc()}")
    return 0
