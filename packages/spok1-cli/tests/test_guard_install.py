from __future__ import annotations

import json
from typing import Any

from spok1_cli import guard_install as gi


def test_render_merges_and_is_idempotent() -> None:
    existing: dict[str, Any] = {
        "model": "opus",
        "permissions": {"deny": ["Read(./secret)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]
        },
    }
    once = gi.render(existing, "C:/py/python.exe", "C:/g/run_guard.py", "C:/g/guardrails.toml")
    twice = gi.render(once, "C:/py/python.exe", "C:/g/run_guard.py", "C:/g/guardrails.toml")
    assert once == twice
    assert once["model"] == "opus"
    assert once["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert once["permissions"]["deny"][0] == "Read(./secret)"
    assert "Read(**/.env)" in once["permissions"]["deny"]
    assert once["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "3"
    pre = once["hooks"]["PreToolUse"]
    assert len(pre) == 2 and pre[1]["matcher"] == "Bash"  # other hooks kept
    guard = pre[0]["hooks"][0]
    assert guard["onFailure"] == "block" and guard["args"][:2] == ["-I", "-S"]
    assert guard["args"][-2:] == ["--policy", "C:/g/guardrails.toml"]


def test_uninstall_restores_the_rest() -> None:
    existing: dict[str, Any] = {"model": "opus", "permissions": {"deny": ["Read(./secret)"]}}
    merged = gi.render(existing, "p", "r/run_guard.py", "x")
    assert gi.remove_from(merged) == existing


def test_committed_reference_settings_match_render() -> None:
    """claude/managed-settings.*.json are generated: keep them in sync with render()."""
    root = gi.repo_root() / "claude"
    for name, lay in (("laptop", gi.layout("win32")), ("vm", gi.layout("linux"))):
        py = str(
            lay.python_dir
            / ("cpython-3.12/python.exe" if name == "laptop" else "cpython-3.12/bin/python3")
        )
        expected = gi.render({}, py, str(lay.runner), str(lay.policy))
        committed = json.loads((root / f"managed-settings.{name}.json").read_text(encoding="utf-8"))
        assert committed == expected, f"regenerate claude/managed-settings.{name}.json"
