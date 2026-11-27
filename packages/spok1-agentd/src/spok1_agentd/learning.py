"""The nightly learning loop (PRD §5, Phase 6a).

signals (feedback.md, new episodes, alert replies, job outcomes)
  → ONE Claude run that only PROPOSES changes, as JSON
  → plain-code checks (schema, no secrets, pinned beliefs protected, at most N changes)
  → beliefs.yaml (Beta confidence) → profile.md → changelog.md → commit + push
  → "📚 What I learned today", plus up to 3 questions where a belief is uncertain.
Every change is recorded and `revert N` undoes it. Learning never touches the guardrails,
never creates anything that costs usage, and Claude never writes the brain directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, ValidationError

from spok1_agentd.db import DB
from spok1_core.schemas import Belief, BeliefsFile, Evidence

MAX_CHANGES = 12
MAX_SIGNAL_CHARS = 30_000
PROFILE_MAX_LINES = 120
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")
SECRET_RE = re.compile(
    r"(sk-ant-|ghp_|gho_|github_pat_|m0-[A-Za-z0-9]{8}|discord(app)?\.com/api/webhooks|hc-ping\.com/|BEGIN [A-Z ]*PRIVATE KEY|password\s*[:=])",
    re.IGNORECASE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, belief_id TEXT NOT NULL,
  action TEXT NOT NULL, before TEXT, after TEXT, note TEXT, sha TEXT, reverted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, belief_id TEXT NOT NULL,
  text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open'   -- open|yes|no|expired
);
"""

PROMPT = """You are spok1's learning job. From the signals below, propose changes to what spok1
believes about how its owner works. Reply with ONE JSON object and nothing else:

{"beliefs": [{"action": "new|confirm|contradict", "id": "kebab-case-id", "statement": "for new only",
              "note": "the evidence, in a few words", "source": "feedback|episode|reply|job"}],
 "facts":   [{"text": "a durable fact worth remembering", "app_id": "global or a project id"}],
 "questions": [{"belief_id": "id", "text": "one short yes/no question to the owner"}]}

Rules:
- Only durable preferences/conventions about HOW THE OWNER WORKS. Not one-off task details.
- Reuse an existing belief id when the signal is about the same thing (confirm or contradict it).
- A correction or preference the owner stated explicitly (source feedback) → action "new" (or
  "confirm" if it already exists). Weaker, inferred patterns → "new" too; the confidence model
  keeps them out of the profile until they're confirmed.
- At most 12 belief changes, 5 facts, 3 questions. Fewer is fine; an empty list is fine.
- Never include secrets, tokens, passwords, webhook URLs or other people's personal data.
- Ask a question only when a belief matters and the evidence is mixed.

CURRENT BELIEFS (id · confidence · statement):
{beliefs}

SIGNALS:
{signals}
"""


class Change(BaseModel):
    action: Literal["new", "confirm", "contradict"]
    id: str
    statement: str | None = None
    note: str = ""
    source: str = "episode"


class Fact(BaseModel):
    text: str
    app_id: str = "global"


class Question(BaseModel):
    belief_id: str
    text: str


class Proposals(BaseModel):
    beliefs: list[Change] = Field(default_factory=list[Change])
    facts: list[Fact] = Field(default_factory=list[Fact])
    questions: list[Question] = Field(default_factory=list[Question])


# ── signals ────────────────────────────────────────────────────────────


def _episode_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    keep: list[str] = [f"## episode {path.name}"]
    title = re.search(r"^# (.+)$", text, re.M)
    if title:
        keep.append(title.group(1))
    for section in ("Corrections", "Feedback", "Lessons", "Self-critique"):
        m = re.search(rf"^## {section}\n\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if m and "(none)" not in m.group(1):
            keep.append(f"{section}: {m.group(1).strip()[:800]}")
    return "\n".join(keep)


def collect(db: DB, brain: Path) -> tuple[str, dict[str, Any]]:
    """New signals since the last run, as text for the prompt, plus cursors to save after."""
    parts: list[str] = []
    cursors: dict[str, Any] = {}
    fb = brain / "feedback.md"
    if fb.is_file():
        lines = [ln for ln in fb.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
        done = int(db.get("learn:feedback_lines", "0") or 0)
        new = lines[done:]
        if new:
            parts.append("### Owner feedback (explicit)\n" + "\n".join(new))
        cursors["learn:feedback_lines"] = str(len(lines))
    seen = set(json.loads(db.get("learn:episodes", "[]") or "[]"))
    eps = sorted(p for p in (brain / "inbox").glob("*.md") if p.name not in seen)
    for p in eps[:20]:
        parts.append(_episode_digest(p))
    cursors["learn:episodes"] = json.dumps(sorted(seen | {p.name for p in eps[:20]}))
    last = float(db.get("learn:last_ts", "0") or 0)
    replies = db.rows("SELECT data FROM events WHERE kind='alert.reply' AND ts > ?", (last,))
    if replies:
        parts.append("### Alert replies\n" + "\n".join(str(r["data"]) for r in replies[:50]))
    runs = db.rows("SELECT job_id, status, summary FROM runs WHERE finished > ?", (last,))
    if runs:
        parts.append(
            "### Job outcomes\n"
            + "\n".join(
                f"{r['job_id']}: {r['status']} {str(r['summary'] or '')[:150]}" for r in runs[:30]
            )
        )
    text = "\n\n".join(parts)
    return text[:MAX_SIGNAL_CHARS], cursors


# ── proposals ──────────────────────────────────────────────────────────


def extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the model's reply")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return cast(dict[str, Any], data)


def load_beliefs(brain: Path) -> BeliefsFile:
    data: Any = yaml.safe_load((brain / "beliefs.yaml").read_text(encoding="utf-8")) or {}
    return BeliefsFile.model_validate(data)


def apply(
    bf: BeliefsFile, props: Proposals, today: date
) -> tuple[list[tuple[str, str, Belief | None, Belief, str]], list[Question], list[str]]:
    """Apply proposals with plain-code checks. Returns (changes, questions, problems).
    A change is (belief_id, action, before, after, note)."""
    by_id = {b.id: b for b in bf.beliefs}
    changes: list[tuple[str, str, Belief | None, Belief, str]] = []
    questions: list[Question] = []
    problems: list[str] = []
    for ch in props.beliefs[:MAX_CHANGES]:
        if not ID_RE.match(ch.id):
            problems.append(f"bad id {ch.id!r}")
            continue
        blob = f"{ch.statement or ''} {ch.note}"
        if SECRET_RE.search(blob):
            problems.append(f"{ch.id}: looks like it contains a secret, skipped")
            continue
        explicit = ch.source == "feedback"
        evidence = Evidence(
            date=today,
            source=ch.source,
            kind="contradict" if ch.action == "contradict" else "confirm",
            note=ch.note[:200],
        )
        before = by_id.get(ch.id)
        if before is None:
            if ch.action != "new" or not ch.statement or len(ch.statement) > 240:
                problems.append(f"{ch.id}: unknown belief or missing statement")
                continue
            after = Belief(
                id=ch.id,
                statement=ch.statement.strip(),
                alpha=9 if explicit else 2,
                beta=1,
                impact=2,
                pinned=explicit,
                evidence=[evidence],
                created=today,
                updated=today,
            )
            bf.beliefs.append(after)
            by_id[ch.id] = after
            changes.append((ch.id, "new", None, after, ch.note))
            continue
        after = before.model_copy(deep=True)
        if ch.action == "contradict":
            if before.pinned and not explicit:
                questions.append(
                    Question(
                        belief_id=ch.id,
                        text=f'You said: "{before.statement}". Something suggested otherwise ({ch.note[:120]}). Still true?',
                    )
                )
                continue
            after.beta += 3 if explicit else 1
            if explicit:
                after.pinned = False
        else:
            after.alpha += 3 if explicit else 1
        after.evidence = [*after.evidence[-19:], evidence]
        after.updated = today
        bf.beliefs[bf.beliefs.index(before)] = after
        by_id[ch.id] = after
        changes.append((ch.id, ch.action, before, after, ch.note))
    known = set(by_id)
    for q in props.questions[:3]:
        if q.belief_id in known and not SECRET_RE.search(q.text):
            questions.append(q)
    return changes, questions[:3], problems


# ── profile ────────────────────────────────────────────────────────────

SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Who", ("role-", "stack-", "schedule-timezone")),
    ("How to talk to me", ("comm-", "autonomy-")),
    ("Code", ("code-", "tooling-", "git-", "deps-")),
    ("Writing and creative work", ("copy-", "creative-")),
    ("Notifications", ("alerts-", "schedule-")),
]


def in_profile(b: Belief) -> bool:
    return (b.pinned and b.confidence >= 0.6) or b.promotable


def render_profile(bf: BeliefsFile, today: date) -> str:
    chosen = [b for b in bf.beliefs if in_profile(b)]
    out = [
        "# Owner profile",
        "",
        "<!-- Generated nightly from beliefs.yaml by spok1's learning job (pinned beliefs, and",
        f"     beliefs with confidence >= 0.8 and >= 3 pieces of evidence). Last update {today}.",
        "     Don't edit by hand: corrections go to feedback.md. -->",
    ]
    used: set[str] = set()
    for title, prefixes in SECTIONS:
        items = [b for b in chosen if b.id not in used and b.id.startswith(prefixes)]
        if not items:
            continue
        out += ["", f"## {title}"]
        for b in items:
            out.append(f"- {b.statement}")
            used.add(b.id)
    rest = [b for b in chosen if b.id not in used]
    if rest:
        out += ["", "## Other"] + [f"- {b.statement}" for b in rest]
    lines = out[:PROFILE_MAX_LINES]
    return "\n".join(lines) + "\n"


def dump_beliefs(bf: BeliefsFile) -> str:
    head = (
        "# What spok1 believes about how its owner works. Generated by the nightly learning job.\n"
        "# confidence = alpha / (alpha + beta). In profile.md: pinned, or >= 0.8 with >= 3 evidence.\n"
        "# Corrections go to feedback.md, never here.\n\n"
    )
    data = bf.model_dump(mode="json")
    dump = cast(Callable[..., str], getattr(yaml, "safe_dump"))  # noqa: B009
    return head + dump(data, sort_keys=False, allow_unicode=True, width=100)


# ── the nightly run ────────────────────────────────────────────────────

Writer = Callable[[str, str], None]  # (file name, content)
Committer = Callable[[str], str]  # (message) -> short sha or ""
Thinker = Callable[[str], str]  # (prompt) -> model reply text


def ensure_schema(db: DB) -> None:
    db.conn.executescript(SCHEMA)


def save_cursors(db: DB, cursors: dict[str, Any], now: float) -> None:
    for k, v in cursors.items():
        db.put(k, str(v))
    db.put("learn:last_ts", str(now))


def prepare(db: DB, brain: Path) -> tuple[str, dict[str, Any]] | None:
    """(prompt, cursors) for tonight's run, or None when there's nothing new."""
    ensure_schema(db)
    signals, cursors = collect(db, brain)
    if not signals.strip():
        return None
    bf = load_beliefs(brain)
    summary = (
        "\n".join(f"{b.id} · {b.confidence:.2f} · {b.statement}" for b in bf.beliefs)
        or "(none yet)"
    )
    return PROMPT.replace("{beliefs}", summary).replace("{signals}", signals), cursors


def run_nightly(
    db: DB,
    brain: Path,
    think: Thinker,
    write: Writer,
    commit: Committer,
    *,
    now: float,
    tz: str = "Asia/Kolkata",
    add_fact: Callable[[str, str], None] | None = None,
) -> str:
    """One learning pass, synchronously (prepare → think → finish). Returns the report."""
    prep = prepare(db, brain)
    if prep is None:
        save_cursors(db, collect(db, brain)[1], now)
        return NOTHING
    return finish(
        db, brain, think(prep[0]), prep[1], write, commit, now=now, tz=tz, add_fact=add_fact
    )


NOTHING = "📚 **What I learned today**: nothing new to learn from."


def finish(
    db: DB,
    brain: Path,
    reply: str,
    cursors: dict[str, Any],
    write: Writer,
    commit: Committer,
    *,
    now: float,
    tz: str = "Asia/Kolkata",
    add_fact: Callable[[str, str], None] | None = None,
) -> str:
    """Check and apply the model's proposals. Signals are marked done only when this succeeds."""
    ensure_schema(db)
    today = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
    bf = load_beliefs(brain)
    try:
        props = Proposals.model_validate(extract_json(reply))
    except (ValueError, ValidationError) as exc:
        return f"📚 Learning run skipped: the model's reply wasn't usable ({str(exc)[:120]}). Nothing changed."
    changes, questions, problems = apply(bf, props, today)
    sha = ""
    if changes:
        write("beliefs.yaml", dump_beliefs(bf))
        write("profile.md", render_profile(bf, today))
        log_lines = [f"\n## {today} (nightly learning)"] + [
            f"- {a} `{bid}`: {after.statement} (confidence {after.confidence:.2f}; {note[:120]})"
            for bid, a, _b, after, note in changes
        ]
        old_log = (
            (brain / "changelog.md").read_text(encoding="utf-8")
            if (brain / "changelog.md").is_file()
            else "# Changelog\n"
        )
        write("changelog.md", old_log.rstrip("\n") + "\n" + "\n".join(log_lines) + "\n")
        sha = commit(f"learning: {today} nightly ({len(changes)} changes)")
    ids: list[int] = []
    for bid, action, before, after, note in changes:
        ids.append(
            db.exec(
                "INSERT INTO learning_changes(ts,belief_id,action,before,after,note,sha) VALUES(?,?,?,?,?,?,?)",
                (
                    now,
                    bid,
                    action,
                    before.model_dump_json() if before else None,
                    after.model_dump_json(),
                    note,
                    sha,
                ),
            )
        )
    facts_saved = 0
    unsaved: list[str] = []
    for f in props.facts[:5]:
        if SECRET_RE.search(f.text):
            continue
        if add_fact is None:
            unsaved.append(f.text[:200])
            continue
        try:
            add_fact(f.text, f.app_id or "global")
            facts_saved += 1
        except Exception:
            unsaved.append(f.text[:200])
    # expire questions nobody answered within 3 days, then keep at most 3 open
    db.exec(
        "UPDATE questions SET state='expired' WHERE state='open' AND ts < ?", (now - 3 * 86400,)
    )
    row = db.one("SELECT COUNT(*) AS n FROM questions WHERE state='open'")
    open_q = int(row["n"]) if row else 0
    new_qids: list[int] = []
    for q in questions[: max(0, 3 - open_q)]:
        new_qids.append(
            db.exec(
                "INSERT INTO questions(ts,belief_id,text) VALUES(?,?,?)", (now, q.belief_id, q.text)
            )
        )
    save_cursors(db, cursors, now)
    db.event(
        "learning.night",
        {
            "changes": len(changes),
            "facts": facts_saved,
            "questions": len(new_qids),
            "problems": problems[:5],
            "sha": sha,
        },
    )
    return report(db, changes, ids, new_qids, facts_saved, problems, sha, unsaved)


def report(
    db: DB,
    changes: list[tuple[str, str, Belief | None, Belief, str]],
    ids: list[int],
    qids: list[int],
    facts: int,
    problems: list[str],
    sha: str,
    unsaved: list[str] | None = None,
) -> str:
    lines = ["📚 **What I learned today**"]
    if not changes:
        lines.append("No changes to my beliefs.")
    for cid, (_bid, action, _before, after, _note) in zip(ids, changes, strict=True):
        verb = {"new": "learned", "confirm": "more sure", "contradict": "less sure"}[action]
        flag = " → in your profile" if in_profile(after) else ""
        lines.append(f"`#{cid}` {verb}: {after.statement} ({after.confidence:.0%}){flag}")
    if facts:
        lines.append(f"Saved {facts} fact(s) to memory.")
    for text in unsaved or []:
        lines.append(f"📝 worth remembering: {text}")
    for qid in qids:
        q = db.one("SELECT text FROM questions WHERE id=?", (qid,))
        if q is not None:
            lines.append(f"❓ `Q{qid}` {q['text']}  → reply `yes {qid}` or `no {qid}`")
    if problems:
        lines.append(f"(skipped {len(problems)} proposal(s) that failed the checks)")
    if sha:
        lines.append(f"Undo any line with `revert <#>`. Commit `{sha}`.")
    return "\n".join(lines)[:1900]


# ── owner replies: revert N · yes N · no N ─────────────────────────────


def revert(
    db: DB, brain: Path, change_id: int, write: Writer, commit: Committer, today: date
) -> str:
    ensure_schema(db)
    row = db.one("SELECT * FROM learning_changes WHERE id=?", (change_id,))
    if row is None or row["reverted"]:
        return f"No change #{change_id} to revert."
    bf = load_beliefs(brain)
    bf.beliefs = [b for b in bf.beliefs if b.id != row["belief_id"]]
    if row["before"]:
        bf.beliefs.append(Belief.model_validate_json(str(row["before"])))
    write("beliefs.yaml", dump_beliefs(bf))
    write("profile.md", render_profile(bf, today))
    old_log = (
        (brain / "changelog.md").read_text(encoding="utf-8")
        if (brain / "changelog.md").is_file()
        else "# Changelog\n"
    )
    write(
        "changelog.md",
        old_log.rstrip("\n")
        + f"\n- {today} reverted #{change_id} (`{row['belief_id']}`) at the owner's request\n",
    )
    sha = commit(f"learning: revert #{change_id} {row['belief_id']}")
    db.exec("UPDATE learning_changes SET reverted=1 WHERE id=?", (change_id,))
    db.event("learning.revert", {"change": change_id, "belief": row["belief_id"], "sha": sha})
    return f"↩️ Reverted #{change_id} (`{row['belief_id']}`). Commit `{sha}`."


def answer(
    db: DB, brain: Path, qid: int, yes: bool, write: Writer, commit: Committer, today: date
) -> str:
    ensure_schema(db)
    q = db.one("SELECT * FROM questions WHERE id=?", (qid,))
    if q is None or q["state"] != "open":
        return f"No open question Q{qid}."
    bf = load_beliefs(brain)
    b = next((x for x in bf.beliefs if x.id == q["belief_id"]), None)
    if b is None:
        return f"Q{qid}'s belief no longer exists."
    after = b.model_copy(deep=True)
    if yes:
        after.alpha += 3
        after.pinned = True
    else:
        after.beta += 3
        after.pinned = False
    after.evidence = [
        *after.evidence[-19:],
        Evidence(
            date=today, source="answer", kind="confirm" if yes else "contradict", note=f"Q{qid}"
        ),
    ]
    after.updated = today
    bf.beliefs[bf.beliefs.index(b)] = after
    write("beliefs.yaml", dump_beliefs(bf))
    write("profile.md", render_profile(bf, today))
    sha = commit(f"learning: answer Q{qid} {b.id}")
    db.exec("UPDATE questions SET state=? WHERE id=?", ("yes" if yes else "no", qid))
    cid = db.exec(
        "INSERT INTO learning_changes(ts,belief_id,action,before,after,note,sha) VALUES(?,?,?,?,?,?,?)",
        (
            datetime.now().timestamp(),
            b.id,
            "confirm" if yes else "contradict",
            b.model_dump_json(),
            after.model_dump_json(),
            f"answer to Q{qid}",
            sha,
        ),
    )
    return f"✅ Thanks. `{b.id}` is now {after.confidence:.0%}{' (pinned)' if yes else ''}. Change `#{cid}`."
