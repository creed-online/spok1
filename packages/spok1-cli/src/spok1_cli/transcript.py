"""Turn a Claude Code transcript (JSONL) into compact text for the episode extractor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

MAX_CHARS = 60_000  # keep the newest part of long sessions; limits Max usage per capture
MAX_TOOL_INPUT = 200


@dataclass
class Digest:
    text: str
    user_turns: int
    assistant_turns: int
    tools_used: list[str]


def _blocks(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        items = cast(list[object], content)
        return [cast(dict[str, Any], b) for b in items if isinstance(b, dict)]
    return []


def _summarise_tool(block: dict[str, Any]) -> str:
    name = str(block.get("name", "tool"))
    raw = block.get("input")
    detail = ""
    if isinstance(raw, dict):
        data = cast(dict[str, Any], raw)
        for key in ("command", "file_path", "pattern", "url", "query", "description"):
            if key in data:
                detail = str(data[key])
                break
    if len(detail) > MAX_TOOL_INPUT:
        detail = detail[:MAX_TOOL_INPUT] + "…"
    return f"[tool: {name}{' ' + detail if detail else ''}]"


def digest(path: Path) -> Digest:
    lines: list[str] = []
    user_turns = assistant_turns = 0
    tools: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return Digest("", 0, 0, [])

    for raw in raw_lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        record = cast(dict[str, Any], entry)
        kind = record.get("type")
        if kind not in ("user", "assistant") or record.get("isMeta") or record.get("isSidechain"):
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = cast(dict[str, Any], message).get("content")
        texts: list[str] = []
        for block in _blocks(content):
            btype = block.get("type")
            if btype == "text" and str(block.get("text", "")).strip():
                texts.append(str(block["text"]).strip())
            elif btype == "tool_use" and kind == "assistant":
                tools.append(str(block.get("name", "tool")))
                texts.append(_summarise_tool(block))
        if not texts:
            continue  # tool results, attachments, etc.
        if kind == "user":
            user_turns += 1
            lines.append("USER: " + "\n".join(texts))
        else:
            assistant_turns += 1
            lines.append("ASSISTANT: " + "\n".join(texts))

    text = "\n\n".join(lines)
    if len(text) > MAX_CHARS:
        text = "[…earlier part of the session omitted…]\n\n" + text[-MAX_CHARS:]
    return Digest(text, user_turns, assistant_turns, sorted(set(tools)))
