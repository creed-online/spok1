---
name: remind
description: "Create a reminder or a scheduled job when the owner says 'remind me…', 'every weekday at 9…', 'in 2 hours…', or 'remember to…' with a date. Writes a job file to ~/brain/jobs/ that agentd runs (plain reminders use no Claude)."
---

# Reminders and scheduled jobs

The owner's timezone is **Asia/Kolkata (IST)** unless they say otherwise. Times without a date mean the next time that clock time comes round.

## 1. Work out the job
- **One-off** ("in 10 minutes", "tomorrow 9am", "on the 20th"): use `at:` with a local date and time, plus `timezone: Asia/Kolkata`. For "in N minutes", compute it from `TZ=Asia/Kolkata date -d '+N minutes' +%Y-%m-%dT%H:%M:00`.
- **Recurring** ("every weekday at 9"): use `cron:` (minute hour day month weekday), e.g. `0 9 * * 1-5`.
- **Plain reminder** (just a message): `type: reminder` plus `message`. This **uses no Claude**.
- **Something to do** ("every Monday summarize open PRs"): `type: agent-run` (or `digest`) plus `agent`, `prompt`, `max_turns` and `model` (default `sonnet`; `haiku` when it's simple). This uses Claude usage, so it's **T2**: confirm with the owner first, and show its weekly usage (turns × weight × runs).
- **Priority:** P2 by default. Use P1 only if they say it's urgent; it pings them even at night.

## 2. Write the file
`~/brain/jobs/<id>.yaml`, where the id is short and unique, like `call-ca-20261127-1810`:
```yaml
id: call-ca-20261127-1810
type: reminder
created_by: <machine, e.g. spok1-vm>
created: <now, UTC ISO, e.g. 2026-11-27T12:40:00Z>
at: "2026-11-27T18:10:00"
timezone: Asia/Kolkata
message: "Call the CA"
priority: P2
```
Project jobs go in `~/brain/projects/<project>/jobs/` instead.

Then check it: `spok1 validate-brain`. On a laptop, also commit and push (`git -C ~/brain add jobs/<file> && git -C ~/brain commit -m "job: <id>" && git -C ~/brain push`). On the VM, agentd reads the file within 2 minutes.

## 3. Confirm in one line
`⏰ Set: "Call the CA", today 18:10 IST.` For a recurring job, give its next run: `every weekday 09:00 IST, next Mon 1 Dec`.

## "Remember …"
- A fact ("remember I use pnpm"): add it to Mem0 (`app_id=global` or the project), no job.
- A dated item ("remember my GST filing on the 20th"): add it to Mem0 **and** make a reminder (default 10:00 IST that day, plus one the day before if it's a deadline). Confirm both.
