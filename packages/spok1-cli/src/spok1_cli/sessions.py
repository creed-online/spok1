"""Session markers: how a session's transcript reaches the capture step reliably.

SessionEnd has a 1.5 s default budget and never fires if a session crashes, so the hooks
only move small JSON markers between folders, and the slow work runs elsewhere:

    active/<id>.json   written at SessionStart
    pending/<id>.json  written at SessionEnd (or by catch-up for a crashed session)
    failed/<id>.json   capture gave up after MAX_ATTEMPTS
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from spok1_core import paths

MAX_ATTEMPTS = 3
STALE_ACTIVE_SECONDS = 2 * 60 * 60  # a session idle this long without SessionEnd = crashed
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class Marker:
    session_id: str
    transcript_path: str
    cwd: str = ""
    reason: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    attempts: int = 0
    last_error: str = ""


def sessions_dir() -> Path:
    return paths.state_dir() / "sessions"


def _folder(name: str) -> Path:
    folder = sessions_dir() / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _path(folder: str, session_id: str) -> Path:
    return _folder(folder) / f"{_SAFE_ID.sub('_', session_id)}.json"


def _write(path: Path, marker: Marker) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(marker), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read(path: Path) -> Marker | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Marker(**data)
    except (OSError, ValueError, TypeError):
        return None


def mark_active(session_id: str, transcript_path: str, cwd: str) -> None:
    path = _path("active", session_id)
    if path.exists() or _path("pending", session_id).exists():
        return  # resumed / compacted session: keep the original marker
    _write(path, Marker(session_id=session_id, transcript_path=transcript_path, cwd=cwd))


def mark_ended(session_id: str, transcript_path: str, cwd: str, reason: str) -> None:
    active = _path("active", session_id)
    marker = read(active) if active.exists() else None
    marker = marker or Marker(session_id=session_id, transcript_path=transcript_path, cwd=cwd)
    marker.transcript_path = transcript_path or marker.transcript_path
    marker.reason = reason
    marker.ended_at = time.time()
    _write(_path("pending", session_id), marker)
    active.unlink(missing_ok=True)


def promote_stale_active(now: float | None = None) -> int:
    """Move crashed sessions (no SessionEnd, transcript idle > 2 h) to pending."""
    now = now or time.time()
    moved = 0
    for path in _folder("active").glob("*.json"):
        marker = read(path)
        if marker is None:
            path.unlink(missing_ok=True)
            continue
        transcript = Path(marker.transcript_path)
        last = transcript.stat().st_mtime if transcript.exists() else marker.started_at
        if now - last > STALE_ACTIVE_SECONDS:
            marker.reason = "crashed"
            marker.ended_at = last
            _write(_path("pending", marker.session_id), marker)
            path.unlink(missing_ok=True)
            moved += 1
    return moved


def pending() -> list[Path]:
    return sorted(_folder("pending").glob("*.json"), key=lambda p: p.stat().st_mtime)


def record_failure(path: Path, marker: Marker, error: str) -> None:
    marker.attempts += 1
    marker.last_error = error[:500]
    if marker.attempts >= MAX_ATTEMPTS:
        _write(_path("failed", marker.session_id), marker)
        path.unlink(missing_ok=True)
    else:
        _write(path, marker)


def done(path: Path) -> None:
    path.unlink(missing_ok=True)
