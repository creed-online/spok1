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
    for name, platform in (("laptop", "win32"), ("vm", "linux")):
        expected = gi.render({}, *gi.reference_paths(platform), profile=name)
        committed = json.loads((root / f"managed-settings.{name}.json").read_text(encoding="utf-8"))
        assert committed == expected, f"regenerate claude/managed-settings.{name}.json"


def test_vm_profile() -> None:
    vm = gi.render({}, "p", "r/run_guard.py", "x", profile="vm")
    perms = vm["permissions"]
    assert "Bash" in perms["allow"] and perms["disableAutoMode"] == "disable"
    assert perms["defaultMode"] == "default" and perms["disableBypassPermissionsMode"] == "disable"
    assert vm["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "1"
    assert vm["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"] == "block"  # the guard stays
    laptop = gi.render({}, "p", "r/run_guard.py", "x")
    assert "allow" not in laptop["permissions"]  # the laptop keeps Claude Code's own prompts
    assert gi.remove_from(vm) == {}
