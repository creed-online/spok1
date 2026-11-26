---
name: new-agent
description: Create a new project agent from a plain-language description ("in project X, create a sales agent that ..."). Asks up to 5 clarifying questions, shows a spec card for approval, then writes, validates, registers and commits the agent definition. Use whenever the owner asks to create, add or design an agent.
---

# /new-agent: the agent factory

Creating an agent is a **T2 action**: nothing is written until the owner approves the spec card.

## 1. Understand
- Find the **project**. Check `~/brain/projects/registry.yaml`. If the project isn't there, ask whether to create it (also T2). Creating a project needs its id, name, type and mission.
- Ask **at most 5** short clarifying questions, in one AskUserQuestion call where possible. Cover only what you can't infer:
  1. what exactly it should produce, and for whom
  2. which tools or accounts it needs (read-only, or allowed to act?)
  3. when it should run: on demand, on a schedule, or on an event
  4. what "doing well" means (its KPIs)
  5. its limits: daily caps, and what always needs approval

## 2. Spec card
Dispatch the **agent-architect** in mode `spec`, with the description, the answers and the project context. Show its card to the owner **as is**, then ask: **approve / change / cancel**.

## 3. Write (only after "approve")
1. Dispatch the **agent-architect** in mode `write`. It writes `~/brain/projects/<project>/agents/<name>.md`.
2. Validate: `spok1 validate-brain`. If it reports a problem with the new file, fix it and validate again.
3. Register it: add `<name>` to the `agents:` list in `~/brain/projects/<project>/project.md`. If that file doesn't exist yet, create it with frontmatter `id`, `name`, `type`, `mission`, `agents`.
4. Schedules and watchers are recorded in the spec only, for now. agentd registers them in Phase 5.
5. Commit to the brain: `git -C ~/brain add -A && git -C ~/brain commit -m "agent: <project>/<name>"`. Then push, which is T2, so the guard asks the owner.
6. Report in one line: the agent's name, its file, how to use it ("ask for the <name> agent"), and the next step.

## Managing agents later
- "pause the sales agent" → set `paused: true` in its frontmatter. Its schedules stop in Phase 5.
- "edit / retire the X agent" → the same flow: show the change as a short diff card, wait for approval, then write, validate and commit.
