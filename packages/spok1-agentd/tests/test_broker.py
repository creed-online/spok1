"""The approval broker end to end: the guard (agent side) and agentd (owner side) share two
folders, exactly as on the VM, but in a temp dir."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from spok1_agentd.broker import Broker
from spok1_agentd.db import DB
from spok1_guard import broker as gbroker
from spok1_guard.hook import decide


class Poster:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(
        self, channel_id: str, content: str, *, mention_user: str | None = None
    ) -> dict[str, Any]:
        self.sent.append({"channel": channel_id, "content": content, "mention": mention_user})
        return {"id": str(len(self.sent))}


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    spool, approvals = tmp_path / "spool", tmp_path / "approvals"
    spool.mkdir()
    approvals.mkdir()
    monkeypatch.setattr(gbroker, "SPOOL", str(spool))
    monkeypatch.setattr(gbroker, "APPROVALS", str(approvals))
    home = tmp_path / "home"
    proj = home / "projects" / "sandbox"
    proj.mkdir(parents=True)
    db_file = proj / "test.db"
    con = sqlite3.connect(db_file)
    con.execute("create table users(id int)")
    con.commit()
    con.close()
    poster, clock = Poster(), Clock()
    b = Broker(
        DB(":memory:"),
        poster,
        "OWNER",
        "APPROVALS",
        spool=spool,
        approvals=approvals,
        backups=tmp_path / "backups",
        clock=clock,
    )
    return {
        "home": home,
        "proj": proj,
        "poster": poster,
        "clock": clock,
        "broker": b,
        "spool": spool,
    }


def bash(cmd: str, cwd: Path) -> dict[str, Any]:
    return {
        "cwd": str(cwd),
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "agent_type": "coder",
    }


def decision(out: dict[str, Any] | None) -> str:
    return "pass" if out is None else str(out["hookSpecificOutput"]["permissionDecision"])


def test_t3_card_code_and_single_use(env: dict[str, Any]) -> None:
    call = bash('sqlite3 test.db "DROP TABLE users"', env["proj"])
    v, out = decide(call, None, env["home"])
    assert (
        decision(out) == "deny"
        and out is not None
        and "approval card" in out["hookSpecificOutput"]["permissionDecisionReason"]
    )  # type: ignore[index]
    decide(call, None, env["home"])  # asking twice files one request
    assert env["broker"].scan() == [1] and env["broker"].scan() == []
    card = env["poster"].sent[0]
    assert (
        card["mention"] == "OWNER"
        and "APPROVAL #1" in card["content"]
        and "DROP TABLE users" in card["content"]
    )
    assert (
        "✅ copied" in card["content"] and "verified" in card["content"]
    )  # automatic sqlite backup
    code = env["broker"].db.one("SELECT code FROM approvals WHERE id=1")["code"]
    assert "needs its confirm code" in env["broker"].decide(1, "approve", [])  # no code: refused
    assert "needs its confirm code" in env["broker"].decide(1, "approve", ["WRONG1"])
    assert decision(decide(call, None, env["home"])[1]) == "deny"  # still waiting
    assert "approved" in env["broker"].decide(1, "approve", [code.lower()])
    v, out = decide(call, None, env["home"])
    assert out is None and v.rule.startswith("broker:")  # allowed once
    assert decision(decide(call, None, env["home"])[1]) == "deny"  # but only once
    other = bash('sqlite3 test.db "DROP TABLE orders"', env["proj"])
    assert (
        decision(decide(other, None, env["home"])[1]) == "deny"
    )  # a different action isn't covered


def test_deny_and_expiry(env: dict[str, Any]) -> None:
    call = bash("rm -rf /srv/old-data", env["proj"])
    decide(call, None, env["home"])
    env["broker"].scan()
    assert "denied" in env["broker"].decide(1, "deny", [])
    out = decide(call, None, env["home"])[1]
    assert "DENIED" in out["hookSpecificOutput"]["permissionDecisionReason"]  # type: ignore[index]
    late = bash("rm -rf /srv/other", env["proj"])
    decide(late, None, env["home"])
    env["broker"].scan()
    env["clock"].t += 31 * 60
    assert env["broker"].expire() == [2]
    assert "already expired" in env["broker"].decide(2, "approve", ["X"])


def test_no_backup_needs_explicit_nobackup(env: dict[str, Any]) -> None:
    call = bash("rm -rf /srv/old-data", env["proj"])
    decide(call, None, env["home"])
    env["broker"].scan()
    card = env["poster"].sent[0]["content"]
    assert "⚠️ no automatic backup" in card and "nobackup" in card
    code = env["broker"].db.one("SELECT code FROM approvals WHERE id=1")["code"]
    assert "no verified backup" in env["broker"].decide(1, "approve", [code])
    assert "approved" in env["broker"].decide(1, "approve", [code, "nobackup"])


def test_agent_cannot_approve_itself(env: dict[str, Any]) -> None:
    """The agent's user can write the spool, but a forged 'approval' there means nothing."""
    call = bash("rm -rf /srv/old-data", env["proj"])
    h = gbroker.action_hash("Bash", call["tool_input"], str(env["proj"]).replace("\\", "/").lower())
    (env["spool"] / f"{h}.json").write_text(
        '{"decision": "approved", "expires": 9999999999}', encoding="utf-8"
    )
    assert decision(decide(call, None, env["home"])[1]) == "deny"


def test_forbidden_never_reaches_the_broker(env: dict[str, Any]) -> None:
    decide(bash("cat .env", env["proj"]), None, env["home"])
    assert list(env["spool"].glob("*.json")) == []


def test_trash_is_t1(env: dict[str, Any]) -> None:
    v, out = decide(bash("trash-put /srv/old-data/report.txt", env["proj"]), None, env["home"])
    assert out is None and v.tier.name == "T1"
