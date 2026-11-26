"""Risk tiers (PRD §6.1) and the verdicts the classifier produces."""

from __future__ import annotations

from enum import IntEnum  # `re` imports enum anyway, so this costs nothing extra


class Tier(IntEnum):
    T0 = 0  # auto: read-only, or local and fully reversible
    T1 = 1  # batch: low risk, reversible, creates something small
    T2 = 2  # approve: money, data, storage, usage, outward-facing, creating things
    T3 = 3  # two-key: destructive, irreversible or high-impact
    FORBIDDEN = 4  # never, even with approval through an agent

    @property
    def label(self) -> str:
        return "FORBIDDEN" if self is Tier.FORBIDDEN else self.name


class Verdict:
    __slots__ = ("reason", "rule", "tier")

    def __init__(self, tier: Tier, reason: str, rule: str = "") -> None:
        self.tier = tier
        self.reason = reason
        self.rule = rule

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Verdict) and (self.tier, self.reason, self.rule) == (
            other.tier,
            other.reason,
            other.rule,
        )

    def __hash__(self) -> int:
        return hash((self.tier, self.reason, self.rule))

    def __repr__(self) -> str:
        return f"Verdict({self.tier.label}, {self.reason!r}, {self.rule!r})"

    def at_least(self, tier: Tier, reason: str = "", rule: str = "") -> Verdict:
        """This verdict, raised to `tier` if it is lower."""
        if self.tier >= tier:
            return self
        return Verdict(tier, reason or self.reason, rule or self.rule)


T0_OK = Verdict(Tier.T0, "read-only or local and reversible", "t0")


def worst(verdicts: list[Verdict]) -> Verdict:
    """The highest-tier verdict (the first one wins a tie)."""
    if not verdicts:
        return T0_OK
    best = verdicts[0]
    for v in verdicts[1:]:
        if v.tier > best.tier:
            best = v
    return best


def v(tier: Tier, reason: str, rule: str) -> Verdict:
    return Verdict(tier, reason, rule)
