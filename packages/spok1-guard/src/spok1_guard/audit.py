"""Append-only, hash-chained audit log (PRD §6.2.6).

One JSONL file per day in ~/.spok1/state/audit/. Each record stores the SHA-256 of the
previous record, so editing or deleting a line breaks the chain (`verify()` finds it).
Commands are summarised with secrets redacted.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from spok1_guard._compat import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import IO, Any

try:  # the C module directly: `import hashlib` costs ~7 ms on every tool call
    from _sha2 import sha256  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
except ImportError:  # pragma: no cover
    from hashlib import sha256

GENESIS = "0" * 64
_REDACT = (
    re.compile(r"(?i)(authorization:\s*(?:bearer|token|basic)\s+)\S+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|pwd)\s*[=:]\s*)\S+"),
    re.compile(r"()https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"),
    re.compile(r"()\b(?:ghp_|gho_|ghs_|github_pat_|sk-ant-|sk-|m0-|xox[abp]-)[A-Za-z0-9_\-]{8,}"),
)


def redact(text: str) -> str:
    for pattern in _REDACT:
        text = pattern.sub(lambda m: m.group(1) + "[redacted]", text)
    return text


def summarise(tool_name: str, tool_input: dict[str, Any]) -> str:
    for key in (
        "command",
        "file_path",
        "notebook_path",
        "url",
        "query",
        "pattern",
        "path",
        "prompt",
    ):
        if key in tool_input:
            return redact(str(tool_input[key]))[:300]
    return redact(json.dumps(tool_input, sort_keys=True, default=str))[:300]


def audit_dir(home: str | os.PathLike[str]) -> str:
    return os.path.join(os.fspath(home), ".spok1", "state", "audit")


def _lock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        for _ in range(200):  # ~2 s max
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)
        raise TimeoutError("audit log lock")
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            return
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _last_hash(handle: IO[bytes]) -> str:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return GENESIS
    block = min(size, 8192)
    handle.seek(size - block)
    tail = handle.read(block).rstrip(b"\n").rsplit(b"\n", 1)[-1]
    try:
        return str(json.loads(tail)["hash"])
    except (ValueError, KeyError):
        return "corrupt"


def record_hash(record: dict[str, Any]) -> str:
    body = {k: record[k] for k in sorted(record) if k != "hash"}
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return str(sha256(data).hexdigest())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def append(home: str | os.PathLike[str], entry: dict[str, Any]) -> dict[str, Any]:
    folder = audit_dir(home)
    os.makedirs(folder, exist_ok=True)
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1e6):06d}Z"
    path = os.path.join(folder, f"{day}.jsonl")
    with open(path, "a+b") as handle:
        _lock(handle)
        try:
            record: dict[str, Any] = {"ts": stamp, **entry}
            record["prev"] = _last_hash(handle)
            record["hash"] = record_hash(record)
            handle.seek(0, os.SEEK_END)
            handle.write((json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode())
            handle.flush()
        finally:
            _unlock(handle)
    return record


def verify(path: str | os.PathLike[str]) -> list[str]:
    """Problems in one day's log (empty = intact)."""
    problems: list[str] = []
    prev = GENESIS
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except ValueError:
            problems.append(f"line {number}: not JSON")
            prev = "corrupt"
            continue
        if record.get("prev") != prev:
            problems.append(f"line {number}: chain broken (prev hash mismatch)")
        if record_hash(record) != record.get("hash"):
            problems.append(f"line {number}: record was modified")
        prev = str(record.get("hash"))
    return problems
