"""Plans and the rules-only auditor (PRD §2 step 4, §7; Phase 2 auditor = rules only).

The manager writes a plan as YAML; `spok1 audit-plan <file>` estimates its Claude usage,
applies cheap-but-safe rules, and says whether the owner must approve it before dispatch.
Usage units = turns * model weight (haiku 1, sonnet 3, opus 15 by default; the weights and
budgets come from the admin-owned guardrails.toml).
"""

from __future__ import annotations

import difflib
import math
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

Model = Literal["haiku", "sonnet", "opus"]
TierName = Literal["T0", "T1", "T2", "T3"]

DEFAULT_WEIGHTS: dict[str, int] = {"haiku": 1, "sonnet": 3, "opus": 15}
DEFAULT_BUDGETS = {"per_task_units": 150, "per_day_units": 4000, "max_parallel_agents": 3}
# The cheapest model each core agent is expected to need (from the agent definitions).
AGENT_MODEL: dict[str, str] = {
    "researcher": "sonnet",
    "coder": "sonnet",
    "tester": "sonnet",
    "reviewer": "opus",
    "librarian": "haiku",
    "auditor": "haiku",
    "agent-architect": "opus",
}
PLAIN_CODE_HINT = re.compile(
    r"\b(count|list (?:all )?files|check (?:if|whether) .* exists|ping|format (?:the )?json|"
    r"sort|rename files|word count|line count|disk usage)\b",
    re.IGNORECASE,
)
# Mechanical git/gh steps: a handful of fixed commands, no judgement needed. Running them on a
# strong model is pure cost (Checkpoint 2: a push + 2 draft PRs on opus was ~43% of the run).
MECHANICAL = re.compile(
    r"\b(push(es|ing)?|open(s|ing)? (draft )?(prs?|pull requests?)|create (draft )?(prs?|pull requests?)|"
    r"tag (a )?release|bump (the )?version|merge (the )?(branch|pr))\b",
    re.IGNORECASE,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanTask(_Strict):
    id: str
    title: str
    agent: str
    model: Model
    est_turns: int = Field(ge=1)
    max_turns: int | None = Field(default=None, ge=1)
    tier: TierName = "T0"
    group: str = ""  # tasks with the same group run in parallel
    depends_on: list[str] = Field(default_factory=list[str])
    brief: str = ""


class Plan(_Strict):
    request: str
    project: str = "global"
    tasks: list[PlanTask]


class Finding(BaseModel):
    task: str
    rule: str
    message: str
    saving: int = 0


class AuditResult(BaseModel):
    estimate: int
    revised_estimate: int
    findings: list[Finding]
    approval_needed: bool
    approval_reasons: list[str]
    revised: Plan

    @property
    def saving(self) -> int:
        return self.estimate - self.revised_estimate


# ── policy ─────────────────────────────────────────────────────────────


def default_policy_path() -> Path:
    if sys.platform == "win32":
        return (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "spok1" / "guardrails.toml"
        )
    if sys.platform == "darwin":
        return Path("/Library/Application Support/spok1/guardrails.toml")
    return Path("/etc/spok1/guardrails.toml")


def load_usage_policy(path: Path | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """(model weights, budgets) from guardrails.toml, falling back to the defaults."""
    weights = dict(DEFAULT_WEIGHTS)
    budgets = dict(DEFAULT_BUDGETS)
    target = path or default_policy_path()
    if target.is_file():
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        usage = cast(dict[str, Any], data.get("usage", {}))
        raw_weights = usage.get("model_weights")
        if isinstance(raw_weights, dict):
            for k, val in cast(dict[str, Any], raw_weights).items():
                if isinstance(val, int):
                    weights[k] = val
        for key in budgets:
            if isinstance(usage.get(key), int):
                budgets[key] = int(usage[key])
    return weights, budgets


# ── audit ──────────────────────────────────────────────────────────────


def units(task: PlanTask, weights: dict[str, int]) -> int:
    return task.est_turns * weights.get(task.model, 15)


def sensible_cap(est_turns: int) -> int:
    return math.ceil(est_turns * 1.5) + 2


def audit(
    plan: Plan, weights: dict[str, int] | None = None, budgets: dict[str, int] | None = None
) -> AuditResult:
    weights = weights or dict(DEFAULT_WEIGHTS)
    budgets = budgets or dict(DEFAULT_BUDGETS)
    revised = plan.model_copy(deep=True)
    findings: list[Finding] = []
    reasons: list[str] = []
    estimate = sum(units(t, weights) for t in plan.tasks)

    # duplicates: same agent, near-identical title → drop the later one
    kept: list[PlanTask] = []
    for task in revised.tasks:
        dupe = next(
            (
                k
                for k in kept
                if k.agent == task.agent
                and difflib.SequenceMatcher(None, k.title.lower(), task.title.lower()).ratio()
                > 0.85
            ),
            None,
        )
        if dupe is not None:
            findings.append(
                Finding(
                    task=task.id,
                    rule="duplicate",
                    message=f"duplicates {dupe.id} ('{dupe.title}'): merge them",
                    saving=units(task, weights),
                )
            )
            for other in revised.tasks:
                other.depends_on = [dupe.id if d == task.id else d for d in other.depends_on]
            continue
        kept.append(task)
    revised.tasks = kept

    for task in revised.tasks:
        # model: no stronger than the agent needs
        expected = AGENT_MODEL.get(task.agent)
        if expected and weights.get(task.model, 15) > weights.get(expected, 15):
            saving = task.est_turns * (weights[task.model] - weights[expected])
            findings.append(
                Finding(
                    task=task.id,
                    rule="model",
                    message=f"{task.agent} on {task.model}: {expected} is enough",
                    saving=saving,
                )
            )
            task.model = cast(Model, expected)
        # turn caps
        if task.max_turns is None:
            task.max_turns = sensible_cap(task.est_turns)
            findings.append(
                Finding(
                    task=task.id, rule="turn-cap", message=f"no max_turns: set to {task.max_turns}"
                )
            )
        elif task.max_turns > 2.5 * task.est_turns + 2:
            new_cap = sensible_cap(task.est_turns)
            findings.append(
                Finding(
                    task=task.id,
                    rule="turn-cap",
                    message=f"max_turns {task.max_turns} is far above the estimate: cap at {new_cap}",
                )
            )
            task.max_turns = new_cap
        elif task.max_turns < task.est_turns:
            findings.append(
                Finding(
                    task=task.id,
                    rule="turn-cap",
                    message=f"max_turns {task.max_turns} is below the estimate {task.est_turns}: it will stop early",
                )
            )
        # mechanical git/gh steps → the cheapest model
        if MECHANICAL.search(task.title) and task.agent not in ("coder", "reviewer", "tester"):
            cheapest = min(weights, key=lambda m: weights[m])
            if weights.get(task.model, 15) > weights[cheapest]:
                saving = task.est_turns * (weights[task.model] - weights[cheapest])
                findings.append(
                    Finding(
                        task=task.id,
                        rule="mechanical",
                        message=f"mechanical git/gh step on {task.model}: run it on {cheapest} "
                        "(or as plain commands)",
                        saving=saving,
                    )
                )
                task.model = cast(Model, cheapest)
        # plain code instead of an LLM
        if PLAIN_CODE_HINT.search(task.title) and task.agent in ("researcher", "librarian"):
            findings.append(
                Finding(
                    task=task.id,
                    rule="plain-code",
                    message="this looks like plain code (a script), not an LLM task",
                )
            )
        # budget per task
        task_units = units(task, weights)
        if task_units > budgets["per_task_units"]:
            reasons.append(
                f"{task.id} needs ~{task_units} units (per-task budget {budgets['per_task_units']})"
            )
        if task.tier in ("T2", "T3"):
            reasons.append(f"{task.id} is {task.tier}: {task.title}")

    # parallelism
    groups: dict[str, list[str]] = {}
    for task in revised.tasks:
        if task.group:
            groups.setdefault(task.group, []).append(task.id)
    for name, ids in groups.items():
        if len(ids) > budgets["max_parallel_agents"]:
            reasons.append(
                f"group '{name}' runs {len(ids)} agents at once (max {budgets['max_parallel_agents']})"
            )

    # quality: code changes must be reviewed
    if any(t.agent == "coder" for t in revised.tasks) and not any(
        t.agent == "reviewer" for t in revised.tasks
    ):
        findings.append(
            Finding(
                task="plan",
                rule="review",
                message="coder tasks without a reviewer task: add one (quality floor)",
            )
        )

    revised_estimate = sum(units(t, weights) for t in revised.tasks)
    if revised_estimate > budgets["per_day_units"]:
        reasons.append(
            f"plan needs ~{revised_estimate} units (daily budget {budgets['per_day_units']})"
        )
    return AuditResult(
        estimate=estimate,
        revised_estimate=revised_estimate,
        findings=findings,
        approval_needed=bool(reasons),
        approval_reasons=reasons,
        revised=revised,
    )


def load_plan(path: Path) -> Plan:
    data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Plan.model_validate(data)


def format_result(result: AuditResult) -> str:
    pct = f"{100 * result.saving / result.estimate:.0f}%" if result.estimate else "0%"
    lines = [
        f"ESTIMATE: {result.estimate} units → {result.revised_estimate} after changes   SAVING: {result.saving} ({pct})"
    ]
    if result.findings:
        lines.append("CHANGES:")
        lines += [
            f"- [{f.rule}] {f.task}: {f.message}" + (f" (saves {f.saving})" if f.saving else "")
            for f in result.findings
        ]
    else:
        lines.append("CHANGES: none")
    lines.append("APPROVAL NEEDED: " + ("yes" if result.approval_needed else "no"))
    lines += [f"- {r}" for r in result.approval_reasons]
    return "\n".join(lines)
