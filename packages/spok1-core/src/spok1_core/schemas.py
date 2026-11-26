"""Schemas for the brain's YAML files, plus `validate_brain()`.

Covers beliefs, jobs, skill `meta.yaml` and SKILL.md, agent definitions, the project
registry and `project.md` frontmatter. Org charts and grants come in later phases.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

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
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            problems.append(f"skills/{skill_dir.name}: missing SKILL.md")
        else:
            problems += [f"skills/{skill_dir.name}/SKILL.md: {p}" for p in validate_skill(skill_md)]
    agent_files = sorted((brain / "agents").glob("*.md")) + sorted(
        (brain / "projects").glob("*/agents/*.md")
    )
    for agent in agent_files:
        problems += [f"{agent.relative_to(brain).as_posix()}: {p}" for p in validate_agent(agent)]
    registry = brain / "projects" / "registry.yaml"
    if registry.is_file():
        check(registry, RegistryFile)
    for project_md in sorted((brain / "projects").glob("*/project.md")):
        try:
            meta, _ = split_frontmatter(project_md.read_text(encoding="utf-8"))
            ProjectFrontmatter.model_validate(meta)
        except (ValidationError, ValueError) as exc:
            first = str(exc).strip().splitlines()
            problems.append(
                f"{project_md.relative_to(brain).as_posix()}: {first[0] if first else exc}"
            )
    return problems


# ── agent and skill files (Markdown with YAML frontmatter) ─────────────


AGENT_MODELS = {"haiku", "sonnet", "opus", "fable", "inherit"}
FORBIDDEN_PERMISSION_MODES = {"bypasspermissions", "dontask", "auto", "acceptedits"}


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter (--- … ---)")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated YAML frontmatter")
    data: object = yaml.safe_load(parts[1]) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return cast(dict[str, Any], data), parts[2]


def validate_agent(path: Path) -> list[str]:
    """Problems with a sub-agent definition (PRD §2 / Phase 2 agent factory rules)."""
    try:
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    problems: list[str] = []
    name = meta.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        problems.append("`name` must be lowercase letters, digits and dashes")
    elif name != path.stem:
        problems.append(f"`name` ({name}) must match the file name ({path.stem})")
    if not isinstance(meta.get("description"), str) or len(str(meta.get("description"))) < 20:
        problems.append("`description` is missing or too short (it's how agents get picked)")
    if str(meta.get("model", "")) not in AGENT_MODELS:
        problems.append("`model` must be haiku, sonnet or opus")
    turns = meta.get("maxTurns")
    if not isinstance(turns, int) or not 1 <= turns <= 200:
        problems.append("`maxTurns` must be set (1-200)")
    mode = str(meta.get("permissionMode", "")).lower()
    if mode in FORBIDDEN_PERMISSION_MODES:
        problems.append(
            f"`permissionMode: {meta['permissionMode']}` is not allowed (it skips approvals)"
        )
    tools = meta.get("tools")
    if tools is None:
        problems.append("`tools` must list what the agent may use (least privilege)")
    elif "*" in str(tools):
        problems.append("`tools` must not be a wildcard")
    if len(body.strip()) < 40:
        problems.append("the body (the agent's instructions) is missing")
    return problems


def validate_skill(path: Path) -> list[str]:
    try:
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    problems: list[str] = []
    if not isinstance(meta.get("description"), str) or len(str(meta.get("description"))) < 20:
        problems.append("`description` is missing or too short")
    if len(body.splitlines()) > 500:
        problems.append("SKILL.md is over 500 lines (the docs' limit)")
    return problems


# ── projects ───────────────────────────────────────────────────────────


class ProjectType(StrEnum):
    BUSINESS = "business"
    SOFTWARE = "software"
    NGO = "ngo"
    RESEARCH = "research"
    EVENT = "event"
    PERSONAL = "personal"
    TEST = "test"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ProjectEntry(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    type: ProjectType
    status: ProjectStatus = ProjectStatus.ACTIVE
    priority: int = Field(default=3, ge=1, le=5)
    usage_share: float = Field(default=0.2, ge=0, le=1)
    created: date
    last_active: date | None = None
    repos: list[str] = Field(default_factory=list[str])
    discord_category_id: str = ""


class RegistryFile(_Strict):
    version: Literal[1] = 1
    projects: list[ProjectEntry] = Field(default_factory=list[ProjectEntry])

    @field_validator("projects")
    @classmethod
    def _unique(cls, projects: list[ProjectEntry]) -> list[ProjectEntry]:
        ids = [p.id for p in projects]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate project ids: {', '.join(dupes)}")
        return projects


class ProjectFrontmatter(BaseModel):
    """The YAML frontmatter of `projects/<id>/project.md` (the body is free text)."""

    model_config = ConfigDict(extra="allow")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    type: ProjectType
    mission: str = ""
    goals: list[str] = Field(default_factory=list[str])
    kpis: list[str] = Field(default_factory=list[str])
    repos: list[str] = Field(default_factory=list[str])
    agents: list[str] = Field(default_factory=list[str])
    approval_rules: list[str] = Field(default_factory=list[str])
