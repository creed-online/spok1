"""Classify SQL text by statement type (PRD §6.1: DB writes T2; DROP/TRUNCATE/unbounded
DELETE or UPDATE T3)."""

from __future__ import annotations

import re

from spok1_guard.tiers import T0_OK, Tier, Verdict, v, worst

_TRIVIAL_WHERE = re.compile(
    r"\bwhere\s+(1\s*=\s*1|true|1|'1'\s*=\s*'1'|0\s*=\s*0)\s*(;|$|\)|limit|order|returning)",
    re.IGNORECASE,
)


def split_statements(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for c in sql:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in "'\"`":
            quote = c
            buf.append(c)
            continue
        if c == ";":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    parts.append("".join(buf))
    cleaned: list[str] = []
    for p in parts:
        lines = [ln for ln in p.splitlines() if not ln.strip().startswith("--")]
        text = " ".join(" ".join(lines).split())
        if text:
            cleaned.append(text)
    return cleaned


def classify_statement(stmt: str) -> Verdict:
    s = stmt.strip()
    low = s.lower()
    if low.startswith("."):  # sqlite3 dot-commands
        word = low.split()[0]
        if word in (".tables", ".schema", ".indexes", ".headers", ".mode", ".help", ".quit"):
            return T0_OK
        return v(Tier.T2, f"sqlite3 command {word}", "sql.dot")
    first = low.split()[0] if low.split() else ""
    if first == "with":  # CTE: classify by the main verb
        for verb in ("delete", "update", "insert", "merge"):
            if re.search(rf"\)\s*{verb}\b", low):
                first = verb
                break
        else:
            first = "select"
    if first in ("select", "explain", "show", "describe", "desc", "values", "table"):
        return T0_OK
    if first == "pragma":
        return T0_OK if "=" not in low else v(Tier.T2, "PRAGMA that changes settings", "sql.pragma")
    if first in ("drop", "truncate"):
        return v(Tier.T3, f"SQL {first.upper()}", "sql.drop")
    if first in ("delete", "update"):
        if " where " not in f" {low} " or _TRIVIAL_WHERE.search(low):
            return v(Tier.T3, f"SQL {first.upper()} without a narrow WHERE", "sql.unbounded")
        return v(Tier.T2, f"SQL {first.upper()} (database write)", "sql.write")
    if first in ("insert", "replace", "merge", "upsert", "copy", "load"):
        return v(Tier.T2, f"SQL {first.upper()} (database write)", "sql.write")
    if first in ("create", "alter", "grant", "revoke", "rename", "comment", "attach", "detach"):
        return v(Tier.T2, f"SQL {first.upper()} (schema / permissions change)", "sql.ddl")
    if first in ("vacuum", "analyze", "reindex", "begin", "commit", "rollback", "end", "savepoint"):
        return v(Tier.T1, f"SQL {first.upper()}", "sql.maintenance")
    return v(Tier.T2, f"unrecognised SQL ({first or 'empty'})", "sql.unknown")


def classify_sql(sql: str) -> Verdict:
    statements = split_statements(sql)
    if not statements:
        return T0_OK
    return worst([classify_statement(s) for s in statements])
