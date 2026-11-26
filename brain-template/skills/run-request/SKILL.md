---
name: run-request
description: The manager's procedure for any non-trivial request (code changes across files, research plus implementation, anything with several steps or agents). Route, recall, plan as YAML, pre-flight audit, approval, dispatch to sub-agents (at most 3 in parallel, coders in their own worktrees), review, report with usage, debrief.
---

# Run a request (the manager's procedure)

Use this for any request that needs more than a couple of tool calls. For a quick question or a one-line fix, just do it.

## 1. Route
- Which project is it? Use the folder you're in, `~/brain/projects/registry.yaml`, or ask if it's unclear.
- Read `~/brain/projects/<project>/project.md` and that project's agents, if they exist.

## 2. Recall
- Search Mem0, filtered by the project's `app_id` plus `global`.
- Check `~/brain/skills/` for an existing procedure before you plan a new one.

## 3. Plan
Write the plan to `~/.spok1/plans/<yyyymmdd-hhmm>-<slug>.yaml`:

```yaml
request: "Add a unit test for parse_date and fix the timezone bug"
project: sandbox
tasks:
  - id: t1
    title: Fix the timezone bug in parse_date
    agent: coder          # researcher | coder | tester | reviewer | librarian | <project agent>
    model: sonnet         # haiku | sonnet | opus
    est_turns: 15
    max_turns: 25
    tier: T1              # highest risk the task needs: local commit = T1, push/PR = T2
    group: a              # same group = run in parallel (max 3)
    brief: "src/dates.py: parse_date drops the offset. Acceptance: tests in tests/test_dates.py pass."
  - id: t2
    title: Review t1
    agent: reviewer
    model: opus
    est_turns: 6
    depends_on: [t1]
```

Split the work into **independent** tasks wherever you can. Every task gets a short, complete brief: sub-agents don't see this conversation.

## 4. Pre-flight check (the auditor)
Run `spok1 audit-plan <plan.yaml>`, or `--write` to save the revised plan.
- Adopt the suggested changes, or write down why not.
- If it says **APPROVAL NEEDED: yes**, show the owner the plan summary and the reasons, then wait for a yes.

## 5. Dispatch
- Use the Agent tool with `subagent_type` set to the task's agent. Launch the tasks of one group **in the same message**, so they run in parallel. Claude Code caps this at 3.
- Give each coder its own task. They each get a separate worktree automatically.
- The guard checks every tool call. If it **asks**, the owner sees the prompt. If it **refuses**, don't look for a way around it. Note it for the report.

## 6. Review
- Every code change goes to the **reviewer** before it leaves the machine.
- If the verdict is CHANGES NEEDED, send it back to a coder **once**. After a second failure, stop and report.
- Pushing a branch or opening a PR is T2, so the guard asks the owner. Push `agent/*` branches and open **draft** PRs, with a clear title and a short summary.

## 7. Report
One message, short and direct:
```
✅ <request, in one line>
- <task>: <result> (<PR link or branch>)
⚠️ <anything blocked, refused or needing the owner>
Usage: ~<units> units (<turns> turns: <n> haiku · <n> sonnet · <n> opus)
```

## 8. Debrief
- If there were owner corrections or durable lessons, dispatch the **librarian** with them.
- The session episode is captured automatically when the session ends.
