from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from spok1_core import config, discord, schemas


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPOK1_HOME", str(tmp_path))
    for key in ("DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_DIGEST", "DISCORD_USER_ID"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def write_env(home: Path, text: str) -> None:
    path = home / ".config" / "agent" / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── .env ───────────────────────────────────────────────────────────────


def test_parse_env_handles_comments_quotes_and_export() -> None:
    text = "# c\nA=1\nexport B=\"two words\"\nC='x'\nD=val # trailing\n\nbad line\n"
    assert config.parse_env(text) == {"A": "1", "B": "two words", "C": "x", "D": "val"}


def test_get_setting_prefers_process_env(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(sandbox, "X=file\nY=file\n")
    monkeypatch.setenv("X", "env")
    assert config.get_setting("X") == "env"
    assert config.get_setting("Y") == "file"
    assert config.get_setting("MISSING") is None


def test_empty_values_count_as_unset(sandbox: Path) -> None:
    write_env(sandbox, "DISCORD_WEBHOOK_ALERTS=\n")
    assert config.get_setting("DISCORD_WEBHOOK_ALERTS") is None


# ── machine ────────────────────────────────────────────────────────────


def test_machine_name_registration(sandbox: Path) -> None:
    assert config.machine_name() == config.default_machine_name()
    config.register_machine("home-laptop")
    assert config.machine_name() == "home-laptop"
    data = json.loads((sandbox / ".config" / "agent" / "machine.json").read_text())
    assert data == {"name": "home-laptop"}


# ── my-details ─────────────────────────────────────────────────────────


def test_my_details_missing_file_gives_defaults(tmp_path: Path) -> None:
    details = config.load_my_details(tmp_path / "nope.yaml")
    assert details.mem0.user_id == ""
    assert details.azure.vm_size == "Standard_B2s"


def test_my_details_example_template_loads() -> None:
    root = Path(__file__).resolve().parents[3]
    details = config.load_my_details(root / "config" / "my-details.example.yaml")
    assert details.repos == []  # the template's [""] placeholder is dropped
    assert details.schedule.eod_report == "19:00"


# ── schemas ────────────────────────────────────────────────────────────


def make_belief(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "b1",
        "statement": "Uses pnpm",
        "alpha": 9,
        "beta": 1,
        "created": "2026-11-26",
        "updated": "2026-11-26",
        "evidence": [
            {"date": "2026-11-26", "source": "interview", "kind": "confirm"},
            {"date": "2026-11-26", "source": "feedback.md", "kind": "confirm"},
            {"date": "2026-11-26", "source": "pr#1", "kind": "confirm"},
        ],
    }
    base.update(over)
    return base


def test_belief_confidence_and_promotion() -> None:
    belief = schemas.Belief.model_validate(make_belief())
    assert belief.confidence == pytest.approx(0.9)
    assert belief.promotable
    weak = schemas.Belief.model_validate(make_belief(alpha=3, beta=2))
    assert not weak.promotable


def test_duplicate_belief_ids_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        schemas.BeliefsFile.model_validate({"beliefs": [make_belief(), make_belief()]})


def test_job_rules() -> None:
    reminder = {
        "id": "r1",
        "type": "reminder",
        "created_by": "home-laptop",
        "created": "2026-11-26T10:00:00Z",
        "at": "2026-11-26T11:00:00Z",
        "message": "call the CA",
    }
    assert schemas.Job.model_validate(reminder).type is schemas.JobType.REMINDER
    with pytest.raises(ValueError):
        schemas.Job.model_validate({**reminder, "cron": "0 9 * * 1"})  # both at and cron
    llm = {**reminder, "type": "agent-run", "agent": "researcher", "prompt": "x"}
    with pytest.raises(ValueError, match="max_turns"):
        schemas.Job.model_validate(llm)
    assert schemas.Job.model_validate({**llm, "max_turns": 5}).max_turns == 5


def test_skill_success_rate_is_smoothed() -> None:
    assert schemas.SkillMeta().success_rate == pytest.approx(0.5)
    assert schemas.SkillMeta(uses=3, successes=3).success_rate == pytest.approx(0.8)


def test_brain_template_is_valid() -> None:
    root = Path(__file__).resolve().parents[3]
    assert schemas.validate_brain(root / "brain-template") == []


def test_validate_brain_reports_problems(tmp_path: Path) -> None:
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "bad.yaml").write_text("id: x\ntype: nope\n", encoding="utf-8")
    (tmp_path / "skills" / "s1").mkdir(parents=True)
    problems = schemas.validate_brain(tmp_path)
    assert any("beliefs.yaml: missing" in p for p in problems)
    assert any(p.startswith("jobs") and "bad.yaml" in p for p in problems)
    assert any("missing SKILL.md" in p for p in problems)


# ── discord ────────────────────────────────────────────────────────────


def test_payload_never_pings_unless_p1() -> None:
    p2 = discord.build_payload("hi", "P2", mention_user_id="123")
    assert "<@123>" not in p2["content"]
    assert p2["allowed_mentions"] == {"parse": []}
    p1 = discord.build_payload("hi", "P1", title="CI", machine="m1", mention_user_id="123")
    assert p1["content"].startswith("<@123> 🚨 **P1** · CI · `m1`")
    assert p1["allowed_mentions"] == {"parse": [], "users": ["123"]}


def test_payload_is_truncated() -> None:
    payload = discord.build_payload("x" * 5000)
    assert len(payload["content"]) == discord.MAX_CONTENT


def test_routing_and_missing_webhook(sandbox: Path) -> None:
    with pytest.raises(discord.NotifyError, match="DISCORD_WEBHOOK_ALERTS"):
        discord.webhook_for("P2")
    write_env(sandbox, "DISCORD_WEBHOOK_ALERTS=https://a\n")
    assert discord.webhook_for("P1") == "https://a"
    assert discord.webhook_for("P3") == "https://a"  # digest falls back to alerts
    write_env(sandbox, "DISCORD_WEBHOOK_ALERTS=https://a\nDISCORD_WEBHOOK_DIGEST=https://d\n")
    assert discord.webhook_for("P3") == "https://d"


def test_send_posts_payload_and_hides_url_on_error(sandbox: Path) -> None:
    secret = "https://discord.example/api/webhooks/1/SECRET"
    write_env(sandbox, f"DISCORD_WEBHOOK_ALERTS={secret}\n")
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    discord.send("hello", "P2", client=httpx.Client(transport=httpx.MockTransport(ok)))
    assert str(seen[0].url) == secret
    assert json.loads(seen[0].content)["content"].endswith("hello")

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(discord.NotifyError) as err:
        discord.send("x", client=httpx.Client(transport=httpx.MockTransport(fail)))
    assert "SECRET" not in str(err.value)
