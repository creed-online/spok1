"""Schemas for the brain's YAML files, plus `validate_brain()`.

Phase 1 covers beliefs, jobs and skill `meta.yaml`. Guardrails, grants, projects
and org charts are added in later phases.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── beliefs.yaml ───────────────────────────────────────────────────────


class Evidence(_Strict):
    date: date
    source: str  # e.g. "feedback.md", "inbox/<file>", "pr#12", "interview"
    kind: Literal["confirm", "contradict"]
    note: str = ""


class Belief(_Strict):
    id: str
    statement: str
    scope: str = "global"  # "global" or a project id
    alpha: float = Field(default=1.0, ge=0)
    beta: float = Field(default=1.0, ge=0)
    impact: int = Field(default=1, ge=0)  # how often it affects tasks (for active learning)
    pinned: bool = False  # stated explicitly by the owner
    evidence: list[Evidence] = Field(default_factory=list[Evidence])
    created: date
    updated: date

    @property
    def confidence(self) -> float:
        total = self.alpha + self.beta
        return self.alpha / total if total else 0.0

    @property
    def promotable(self) -> bool:
        """Promoted into profile.md at confidence >= 0.8 with >= 3 pieces of evidence."""
        return self.confidence >= 0.8 and len(self.evidence) >= 3


class BeliefsFile(_Strict):
    version: Literal[1] = 1
    beliefs: list[Belief] = Field(default_factory=list[Belief])

    @field_validator("beliefs")
    @classmethod
    def _unique_ids(cls, beliefs: list[Belief]) -> list[Belief]:
        ids = [b.id for b in beliefs]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate belief ids: {', '.join(dupes)}")
        return beliefs


# ── jobs/*.yaml ────────────────────────────────────────────────────────


class JobType(StrEnum):
    REMINDER = "reminder"
    AGENT_RUN = "agent-run"
    DIGEST = "digest"


class Job(_Strict):
    id: str
    type: JobType
    project: str = "global"
    created_by: str  # machine or agent that created it
    created: datetime
    at: datetime | None = None  # one-off time
    cron: str | None = None  # recurring schedule
    timezone: str | None = None
    message: str | None = None  # reminder text
    agent: str | None = None  # agent-run / digest
    prompt: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    priority: Literal["P1", "P2", "P3"] = "P2"
    enabled: bool = True

    @model_validator(mode="after")
    def _check_shape(self) -> Job:
        if (self.at is None) == (self.cron is None):
            raise ValueError("a job needs exactly one of `at` or `cron`")
        if self.type is JobType.REMINDER and not self.message:
            raise ValueError("a reminder needs a `message`")
        if self.type is not JobType.REMINDER and not (self.agent and self.prompt):
            raise ValueError("agent-run and digest jobs need `agent` and `prompt`")
        if self.type is not JobType.REMINDER and self.max_turns is None:
            raise ValueError("LLM jobs need `max_turns`")
        return self


# ── skills/<name>/meta.yaml ────────────────────────────────────────────


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    TRIAL = "trial"
    ACTIVE = "active"
    REWRITTEN = "rewritten"
    RETIRED = "retired"


class SkillMeta(_Strict):
    status: SkillStatus = SkillStatus.CANDIDATE
    version: int = Field(default=1, ge=1)
    uses: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    last_used: date | None = None
    scope: str = "global"

    @property
    def success_rate(self) -> float:
        """Smoothed success rate (s+1)/(n+2)."""
        return (self.successes + 1) / (self.uses + 2)


# ── validation ─────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_brain(brain: Path) -> list[str]:
    """Validate the brain's structured files. Returns a list of problems (empty = OK)."""
    problems: list[str] = []

    def check(path: Path, model: type[BaseModel]) -> None:
        try:
            data = _load_yaml(path)
            model.model_validate(data if data is not None else {})
        except (ValidationError, yaml.YAMLError, ValueError) as exc:
            first = str(exc).strip().splitlines()
            problems.append(f"{path.relative_to(brain)}: {first[0] if first else exc}")

    beliefs = brain / "beliefs.yaml"
    if beliefs.is_file():
        check(beliefs, BeliefsFile)
    else:
        problems.append("beliefs.yaml: missing")

    for job in sorted((brain / "jobs").glob("*.yaml")):
        check(job, Job)
    for meta in sorted((brain / "skills").glob("*/meta.yaml")):
        check(meta, SkillMeta)
    for skill_dir in sorted(p for p in (brain / "skills").glob("*") if p.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            problems.append(f"skills/{skill_dir.name}: missing SKILL.md")
    return problems
