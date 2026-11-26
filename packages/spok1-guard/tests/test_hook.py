from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from spok1_guard import audit
from spok1_guard.hook import decide
from spok1_guard.policy import load_policy

RUNNER = Path(__file__).resolve().parents[1] / "run_guard.py"


def run_hook(
    payload: dict[str, Any] | str, home: Path, policy: Path | None = None
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, "-I", "-S", str(RUNNER)]
    if policy is not None:
        args += ["--policy", str(policy)]
    env = {"USERPROFILE": str(home), "HOME": str(home), "SYSTEMROOT": "C:\\Windows", "PATH": ""}
    return subprocess.run(  # noqa: S603
        args,
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def bash_call(command: str, cwd: str) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def test_decisions_map_tiers_to_hook_output(tmp_path: Path) -> None:
    proj = str(tmp_path / "proj")
    t0, out0 = decide(bash_call("git status", proj), None, tmp_path)
    assert t0.tier.name == "T0" and out0 is None  # pass through to normal permissions
    _, out1 = decide(bash_call("git commit -m x", proj), None, tmp_path)
    assert out1 is None  # T1: logged, no prompt
    _, out2 = decide(bash_call("git push", proj), None, tmp_path)
    assert out2 is not None and out2["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "T2" in out2["hookSpecificOutput"]["permissionDecisionReason"]
    _, out3 = decide(bash_call("git push --force", proj), None, tmp_path)
    assert out3 is not None and out3["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "two-key" in out3["hookSpecificOutput"]["permissionDecisionReason"]
    _, outf = decide(bash_call("cat .env", proj), None, tmp_path)
    assert (
        outf is not None and "FORBIDDEN" in outf["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_hook_process_end_to_end(tmp_path: Path) -> None:
    result = run_hook(bash_call("git push --force", str(tmp_path)), tmp_path)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    quiet = run_hook(bash_call("ls", str(tmp_path)), tmp_path)
    assert quiet.returncode == 0 and quiet.stdout == ""


@pytest.mark.parametrize("bad", ["not json", "[1, 2]"])
def test_hook_fails_closed(tmp_path: Path, bad: str) -> None:
    result = run_hook(bad, tmp_path)
    assert result.returncode == 2  # exit 2 = Claude Code blocks the call
    assert "fails closed" in result.stderr


def test_broken_policy_fails_closed(tmp_path: Path) -> None:
    policy = tmp_path / "guardrails.toml"
    policy.write_text("this is = = not toml", encoding="utf-8")
    result = run_hook(bash_call("ls", str(tmp_path)), tmp_path, policy)
    assert result.returncode == 2


def test_policy_file_overrides(tmp_path: Path) -> None:
    policy = tmp_path / "guardrails.toml"
    policy.write_text(
        'version = 1\n[http]\nallowed_hosts = ["hooks.example.com"]\n'
        '[paths]\nextra_protected = ["~/important"]\n'
        '[[mcp.tiers]]\nmatch = "mcp__canva__*"\ntier = "T3"\n',
        encoding="utf-8",
    )
    loaded = load_policy(policy)
    assert loaded.allowed_hosts == ["hooks.example.com"]
    proj = str(tmp_path / "proj")
    post, _ = decide(
        bash_call("curl -X POST https://hooks.example.com/x -d 1", proj), policy, tmp_path
    )
    assert post.tier.name == "T1"
    prot, _ = decide(bash_call("echo x > ~/important/a.txt", proj), policy, tmp_path)
    assert prot.tier.name == "FORBIDDEN"
    mcp: dict[str, Any] = {"cwd": proj, "tool_name": "mcp__canva__get_design", "tool_input": {}}
    assert decide(mcp, policy, tmp_path)[0].tier.name == "T3"
    edit = {"cwd": proj, "tool_name": "Edit", "tool_input": {"file_path": str(policy)}}
    assert decide(edit, policy, tmp_path)[0].tier.name == "FORBIDDEN"  # the policy itself


def test_missing_policy_uses_defaults(tmp_path: Path) -> None:
    assert load_policy(tmp_path / "nope.toml").source == "built-in defaults"


# ── audit log ──────────────────────────────────────────────────────────


def test_audit_chain_and_tamper_detection(tmp_path: Path) -> None:
    for i in range(3):
        run_hook(bash_call(f"echo {i}", str(tmp_path)), tmp_path)
    run_hook(
        bash_call(
            "curl -H 'Authorization: Bearer ghp_abcdefghijklmnop' https://x.example", str(tmp_path)
        ),
        tmp_path,
    )
    [log] = list(Path(audit.audit_dir(tmp_path)).glob("*.jsonl"))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert audit.verify(log) == []
    assert "ghp_abcdefghijklmnop" not in log.read_text(encoding="utf-8")  # redacted
    first = json.loads(lines[0])
    assert first["prev"] == audit.GENESIS and first["tier"] == "T0" and first["decision"] == "pass"

    tampered = json.loads(lines[1])
    tampered["tier"] = "T0"
    tampered["summary"] = "nothing to see"
    lines[1] = json.dumps(tampered, sort_keys=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("modified" in p for p in audit.verify(log))

    del lines[1]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("chain broken" in p for p in audit.verify(log))


def test_redaction() -> None:
    text = (
        "curl -H 'Authorization: Bearer abc123' https://discord.com/api/webhooks/1/xyz "
        "m0-abcdefghij12 token=supersecret"
    )
    out = audit.redact(text)
    for secret in ("abc123", "webhooks/1/xyz", "m0-abcdefghij12", "supersecret"):
        assert secret not in out


# ── latency gate (PRD Phase 2: p95 ≤ 100 ms per tool call) ──────────────


def test_latency_gate(tmp_path: Path) -> None:
    payload = bash_call("git -C . push origin main && rm -rf build", str(tmp_path))
    run_hook(payload, tmp_path)  # warm the file cache
    times: list[float] = []
    for _ in range(25):
        start = time.perf_counter()
        result = run_hook(payload, tmp_path)
        times.append((time.perf_counter() - start) * 1000)
        assert result.returncode == 0
    p95 = statistics.quantiles(times, n=20)[18]
    print(f"\nguard latency: median {statistics.median(times):.0f} ms, p95 {p95:.0f} ms")
    # CI runners are noisy; the real gate is measured on the laptop with `spok1 guard bench`.
    assert p95 < 400
