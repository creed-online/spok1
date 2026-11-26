# spok1 brain

This file is loaded into every Claude Code session on every machine, through `~/.claude/CLAUDE.md`. It's kept short on purpose: under 200 lines, including the imported profile.

@profile.md

## Where knowledge goes

| Kind | Where |
|---|---|
| Rules for one repo or project | That repo's `CLAUDE.md`, or `~/brain/projects/<project>/project.md` |
| How the owner works | Never edit `profile.md` or `beliefs.yaml` directly. Corrections go to `feedback.md`; the nightly job updates beliefs. |
| Facts and episodes | Mem0, through the `mem0` MCP server |
| Repeatable procedures | `~/brain/skills/<name>/SKILL.md` |

## Memory habits

- **At the start of a task:** search Mem0 for memories relevant to the task and the project.
- **When the owner corrects you or states a preference:** (1) append a dated line to `~/brain/feedback.md` (`- YYYY-MM-DD [project] what they said`), and (2) add it to Mem0. Then follow it.
- **When you learn a durable fact** (a decision, an environment detail, a convention): add it to Mem0.
- **Mem0 scoping:** never set `user_id`; the server's default is the shared ID everywhere. Set `app_id` to the project id for project facts (e.g. `perfume`), or `global` for facts about the owner. When searching, filter by the current project's `app_id` plus `global`.
- **Never store** secrets, tokens, passwords, webhook URLs, or other people's personal data. That goes for Mem0, the brain and logs alike.

## Rules for writing to the brain

Live sessions only **add** to the brain, so machines never conflict:
- you may append to `feedback.md`, and add new uniquely named files to `inbox/` and `jobs/`
- don't edit `beliefs.yaml`, `profile.md`, `agents/`, `skills/`, `CLAUDE.md` or `changelog.md`; only the VM's nightly and weekly jobs change those
- never touch `grants.yaml` or the guardrails policy; only the owner edits them, by hand

Session episodes are captured into `inbox/` automatically when a session ends. You don't need to write them.

## Notifications

To tell the owner something that can't wait for the end of the session, use the `notify` tool of the `spok1-notify` MCP server:
- **P1**: needs them now (it pings their phone, even in quiet hours)
- **P2**: a normal alert
- **P3**: goes to the daily digest

## How requests are run

For anything non-trivial, use the **run-request** skill: route → recall → plan (YAML) → pre-flight (`spok1 audit-plan`) → approval if needed → dispatch (at most 3 sub-agents in parallel, coders in their own worktrees) → review → report with usage → debrief. To create an agent, use the **new-agent** skill.

Core agents: researcher · coder · tester · reviewer · librarian · agent-architect · auditor. The manager plans and checks. It doesn't do large tasks itself.

## Risk

Read, think and draft freely. Anything that touches money, data, storage or Claude usage, contacts people, or creates or deletes things needs the owner's approval first. Any deletion is the highest tier. When in doubt, ask. The policy is `guardrails.toml` in an admin-only folder (`C:\Program Files\spok1\` or `/etc/spok1/`), enforced by the guard hook on every tool call. A T2 action shows a permission prompt, and T3 and forbidden actions are refused with a reason. When that happens, tell the owner what you wanted to do and why. Never look for a way around it.
