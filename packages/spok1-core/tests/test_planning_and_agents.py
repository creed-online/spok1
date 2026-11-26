from __future__ import annotations

from pathlib import Path

import pytest

from spok1_core import planning, schemas
from spok1_core.planning import Plan, PlanTask

ROOT = Path(__file__).resolve().parents[3]


def task(**over: object) -> PlanTask:
    base: dict[str, object] = {
        "id": "t1",
        "title": "Fix bug",
        "agent": "coder",
        "model": "sonnet",
        "est_turns": 10,
        "max_turns": 17,
    }
    base.update(over)
    return PlanTask.model_validate(base)


def test_clean_plan_needs_nothing() -> None:
    plan = Plan(
        request="x",
        tasks=[
            task(),
            task(
                id="t2",
                title="Review t1",
                agent="reviewer",
                model="opus",
                est_turns=5,
                max_turns=9,
                depends_on=["t1"],
            ),
        ],
    )
    result = planning.audit(plan)
    assert result.estimate == 10 * 3 + 5 * 15
    assert result.saving == 0 and not result.approval_needed
    assert [f.rule for f in result.findings] == []


def test_model_downgrade_saves_usage() -> None:
    plan = Plan(
        request="x",
        tasks=[
            task(model="opus"),
            task(id="r", title="Review", agent="reviewer", model="opus", est_turns=5, max_turns=9),
        ],
    )
    result = planning.audit(plan)
    assert result.revised.tasks[0].model == "sonnet"
    assert result.saving == 10 * (15 - 3)
    assert any(f.rule == "model" for f in result.findings)


def test_turn_caps_duplicates_and_review() -> None:
    plan = Plan(
        request="x",
        tasks=[
            task(max_turns=None),
            task(id="t2", title="Fix  bug", max_turns=200),  # near-duplicate of t1
            task(id="t3", title="Add docs", est_turns=4, max_turns=200),
        ],
    )
    result = planning.audit(plan)
    rules = [f.rule for f in result.findings]
    assert "duplicate" in rules and "turn-cap" in rules and "review" in rules
    assert [t.id for t in result.revised.tasks] == ["t1", "t3"]
    assert result.revised.tasks[0].max_turns == planning.sensible_cap(10)
    assert result.revised.tasks[1].max_turns == planning.sensible_cap(4)


def test_approval_triggers() -> None:
    plan = Plan(
        request="x",
        tasks=[
            task(id=f"t{i}", title=title, group="a")
            for i, title in enumerate(
                [
                    "Add login page",
                    "Write billing API",
                    "Migrate database schema",
                    "Update README docs",
                ]
            )
        ]
        + [
            task(id="push", title="Open PRs", tier="T2"),
            task(
                id="r", title="Review all", agent="reviewer", model="opus", est_turns=5, max_turns=9
            ),
        ],
    )
    result = planning.audit(plan)
    assert result.approval_needed
    joined = " ".join(result.approval_reasons)
    assert "runs 4 agents at once" in joined and "push is T2" in joined


def test_budget_from_policy(tmp_path: Path) -> None:
    policy = tmp_path / "guardrails.toml"
    policy.write_text(
        "[usage]\nmodel_weights = { sonnet = 5 }\nper_task_units = 20\n", encoding="utf-8"
    )
    weights, budgets = planning.load_usage_policy(policy)
    assert weights["sonnet"] == 5 and weights["opus"] == 15 and budgets["per_task_units"] == 20
    result = planning.audit(
        Plan(
            request="x",
            tasks=[
                task(),
                task(
                    id="r", title="Review", agent="reviewer", model="opus", est_turns=1, max_turns=3
                ),
            ],
        ),
        weights,
        budgets,
    )
    assert result.approval_needed and "per-task budget 20" in result.approval_reasons[0]


def test_repo_policy_template_parses() -> None:
    weights, budgets = planning.load_usage_policy(ROOT / "claude" / "guardrails.toml")
    assert weights == {"haiku": 1, "sonnet": 3, "opus": 15}
    assert budgets["max_parallel_agents"] == 3


# ── agent and skill files ──────────────────────────────────────────────


def test_core_agents_and_skills_are_valid() -> None:
    problems = schemas.validate_brain(ROOT / "brain-template")
    assert problems == []
    names = {p.stem for p in (ROOT / "brain-template" / "agents").glob("*.md")}
    assert names == {
        "researcher",
        "coder",
        "reviewer",
        "tester",
        "librarian",
        "agent-architect",
        "auditor",
    }


@pytest.mark.parametrize(
    ("frontmatter", "problem"),
    [
        ("name: x\ndescription: short\nmodel: sonnet\nmaxTurns: 5\ntools: Read", "description"),
        (
            "name: bad\ndescription: a long enough description here\nmodel: gpt\nmaxTurns: 5\ntools: Read",
            "model",
        ),
        (
            "name: bad\ndescription: a long enough description here\nmodel: sonnet\ntools: Read",
            "maxTurns",
        ),
        (
            "name: bad\ndescription: a long enough description here\nmodel: sonnet\nmaxTurns: 5\ntools: Read\npermissionMode: bypassPermissions",
            "permissionMode",
        ),
        (
            "name: bad\ndescription: a long enough description here\nmodel: sonnet\nmaxTurns: 5\ntools: '*'",
            "wildcard",
        ),
        (
            "name: other\ndescription: a long enough description here\nmodel: sonnet\nmaxTurns: 5\ntools: Read",
            "match the file name",
        ),
    ],
)
def test_agent_validation_catches_problems(tmp_path: Path, frontmatter: str, problem: str) -> None:
    path = tmp_path / ("x.md" if "name: x" in frontmatter else "bad.md")
    path.write_text(
        f"---\n{frontmatter}\n---\n\nYou are an agent. You do one thing well and report back.\n",
        encoding="utf-8",
    )
    assert any(problem in p for p in schemas.validate_agent(path)), schemas.validate_agent(path)


def test_registry_and_project_frontmatter(tmp_path: Path) -> None:
    (tmp_path / "beliefs.yaml").write_text("version: 1\nbeliefs: []\n", encoding="utf-8")
    projects = tmp_path / "projects"
    (projects / "sandbox").mkdir(parents=True)
    (projects / "registry.yaml").write_text(
        "version: 1\nprojects:\n  - {id: sandbox, name: Sandbox, type: test, created: 2026-11-26}\n",
        encoding="utf-8",
    )
    (projects / "sandbox" / "project.md").write_text(
        "---\nid: sandbox\nname: Sandbox\ntype: test\nagents: [docs-writer]\n---\n\nA test project.\n",
        encoding="utf-8",
    )
    assert schemas.validate_brain(tmp_path) == []
    (projects / "registry.yaml").write_text(
        "version: 1\nprojects:\n  - {id: Bad Id, name: x, type: nope, created: 2026-11-26}\n",
        encoding="utf-8",
    )
    assert any("registry.yaml" in p for p in schemas.validate_brain(tmp_path))
