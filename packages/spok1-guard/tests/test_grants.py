from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from spok1_guard import grants
from spok1_guard.hook import decide


def write(tmp: Path, body: str) -> Path:
    (tmp / "guardrails.toml").write_text("version = 1\n", encoding="utf-8")
    (tmp / "grants.toml").write_text(body, encoding="utf-8")
    return tmp / "guardrails.toml"


def soon(days: int = 7) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def call(cmd: str, cwd: str) -> dict[str, Any]:
    return {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": cmd}}


def test_grant_turns_matching_t2_into_pass(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = home / "Desktop" / "spok1"
    repo.mkdir(parents=True)
    policy = write(
        tmp_path,
        f'[[grant]]\nid = "push"\nrules = ["git.push"]\ncwd = ["{repo.as_posix()}"]\nexpires = {soon()}\n',
    )
    v, out = decide(call("git push -q", str(repo)), policy, home)
    assert out is None and v.rule == "grant:push"
    other = home / "elsewhere"
    other.mkdir()
    _, out2 = decide(call("git push -q", str(other)), policy, home)
    assert (
        out2 is not None and out2["hookSpecificOutput"]["permissionDecision"] == "ask"
    )  # outside cwd


def test_grants_never_cover_t3_or_forbidden(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    policy = write(
        tmp_path,
        f'[[grant]]\nid = "all"\nrules = ["git.push.force", "git.push", "secret.read", "delete.outside"]\nexpires = {soon()}\n',
    )
    for cmd, decision in (
        ("git push --force", "deny"),
        ("cat .env", "deny"),
        ("rm -rf /data", "deny"),
    ):
        _, out = decide(call(cmd, str(home)), policy, home)
        assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == decision, cmd


def test_contains_filter_and_expiry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    policy = write(
        tmp_path,
        f'[[grant]]\nid = "vm"\nrules = ["remote"]\ncontains = ["spok1-vm"]\nexpires = {soon()}\n',
    )
    assert decide(call("ssh spok1@spok1-vm true", str(home)), policy, home)[1] is None
    out = decide(call("ssh root@other-host", str(home)), policy, home)[1]
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "ask"
    write(
        tmp_path,
        f'[[grant]]\nid = "old"\nrules = ["remote"]\nexpires = {(date.today() - timedelta(days=1)).isoformat()}\n',
    )
    assert (
        decide(call("ssh spok1@spok1-vm true", str(home)), policy, home)[1] is not None
    )  # expired


def test_bad_grants_are_rejected() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "grants.toml"
        far = (date.today() + timedelta(days=90)).isoformat()
        p.write_text(
            f'[[grant]]\nid = "a"\nrules = ["git.push"]\n\n[[grant]]\nid = "b"\nrules = ["git.push"]\nexpires = {far}\n\n[[grant]]\nid = "c"\nrules = ["git.config", "claude.headless"]\nexpires = {soon()}\n',
            encoding="utf-8",
        )
        active, problems = grants.load(str(p), time.time())
        assert active == [] and len(problems) == 3  # no expiry, too long, not grantable


def test_audit_records_the_grant(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    policy = write(
        tmp_path,
        f'[[grant]]\nid = "vm"\nrules = ["remote"]\ncontains = ["spok1-vm"]\nexpires = {soon()}\n',
    )
    v, _ = decide(call("ssh spok1@spok1-vm true", str(home)), policy, home)
    assert json.dumps({"rule": v.rule}) == '{"rule": "grant:vm"}'
