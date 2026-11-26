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
- **When you learn a durable fact** (a decision, an environment detail, a convention): add it to Mem0, with the project as context.
- **Never store** secrets, tokens, passwords, webhook URLs, or other people's personal data. That goes for Mem0, the brain and logs alike.

## Rules for writing to the brain

Live sessions only **add** to the brain, so machines never conflict:
- you may append to `feedback.md`, and add new uniquely named files to `inbox/` and `jobs/`
- don't edit `beliefs.yaml`, `profile.md`, `agents/`, `skills/`, `CLAUDE.md` or `changelog.md`; only the VM's nightly and weekly jobs change those
- never touch `guardrails.yaml` or `grants.yaml`; only the owner edits them, by hand

Session episodes are captured into `inbox/` automatically when a session ends. You don't need to write them.

## Notifications

To tell the owner something that can't wait for the end of the session, use the `notify` tool of the `spok1-notify` MCP server:
- **P1**: needs them now (it pings their phone, even in quiet hours)
- **P2**: a normal alert
- **P3**: goes to the daily digest

## How requests are run

For any non-trivial request:
1. **Route.** Work out which project this is, and load its `project.md` and agents.
2. **Recall.** Get the relevant memories, beliefs and skills.
3. **Plan.** Split the work into independent tasks, each with a usage estimate and a risk tier.
4. **Pre-flight check.** Look for a cheaper way to the same quality: a smaller model, an existing skill, a cached result, less context.
5. **Dispatch.** Hand tasks to sub-agents, at most 3 at a time, with coders in their own git worktrees.
6. **Review.** The reviewer checks all code changes. Risky or outward-facing actions need the owner's approval.
7. **Report.** Give one short summary, with links and the usage spent.
8. **Debrief.** Note anything worth remembering. The episode itself is captured automatically.

## Risk

Read, think and draft freely. Anything that touches money, data, storage or Claude usage, contacts people, or creates or deletes things needs the owner's approval first. Any deletion is the highest tier. When in doubt, ask. The full policy is in `guardrails.yaml`, which is enforced by the guard hook.
