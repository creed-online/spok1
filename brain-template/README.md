# brain

The private brain of a [spok1](https://github.com/creed-online/spok1) install. It's cloned to `~/brain` on every machine, and linked into Claude Code by `spok1 install`.

**Keep this repo private.** It describes its owner.

| Path | What it is | Who edits it |
|---|---|---|
| `CLAUDE.md` | Loaded into every session (imports `profile.md`) | VM jobs |
| `profile.md` | Generated from high-confidence beliefs | VM nightly job |
| `beliefs.yaml` | Beliefs about the owner, each with a confidence score | VM nightly job |
| `feedback.md` | Every correction, dated (append-only) | any session (append) |
| `agents/` | Core agent definitions → `~/.claude/agents` | VM jobs |
| `skills/<name>/` | `SKILL.md` + `meta.yaml` → `~/.claude/skills/<name>` | VM jobs |
| `projects/` | `registry.yaml`, plus `<project>/` folders | manager (T2) |
| `jobs/` | Scheduled jobs and reminders (`*.yaml`) | any session (new files) |
| `evals/` | Golden tasks + rubrics for the eval gate | VM weekly job |
| `inbox/` | Session episodes: `<machine>-<date>-<id>.md` | any session (new files) |
| `reports/` | Weekly growth reports | VM jobs |
| `changelog.md` | Every self-change and its reason | VM jobs |
| `guardrails.yaml`, `grants.yaml` | Risk policy and standing grants | **the owner, by hand** |

Check the YAML files: `spok1 validate-brain`.
