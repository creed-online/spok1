# spok1 dev tracker

This tracks building spok1, phase by phase. The spec is [prd.md](prd.md), the diagrams are in [architecture.md](architecture.md) and the stack is in [tech-stack.md](tech-stack.md). Section numbers such as §6 point to sections of the PRD.

**How to use this file**
- `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked (add the reason inline)
- Each phase ends at a **✅ checkpoint**. Stop there and report: what was done (3 lines or fewer), the commands you need to run yourself, pass or fail with evidence, and anything in the docs that differed from the PRD.
- Don't start a phase until the previous checkpoint has passed.
- Anything risky follows the guardrails in §6, during the build as well.

## Status

| Phase | Name | Where | Status |
|---|---|---|---|
| 0 | Decisions and setup | laptop | `[~]` |
| 1 | The brain on any computer | laptop | `[ ]` |
| 2 | Guardrails, orchestration, agent factory | laptop | `[ ]` |
| 3 | Azure VM | Azure (costs start) | `[ ]` |
| 4 | Always-on manager and Discord | VM | `[ ]` |
| 5 | agentd runtime and approval broker | VM | `[ ]` |
| 6 | Learning engine and full auditor | VM + laptop | `[ ]` |
| 7 | Project OS core | VM | `[ ]` |
| 8 | Pilot project (perfume brand) | VM | `[ ]` |
| 9 | Day-to-day operations | VM + laptop | `[ ]` |

## Decisions log

| Date | Decision |
|---|---|
| 2026-11-25 | Build our own system rather than adopt Clerk, Praktor or ClaudeClaw. Borrow patterns from Clerk and ideas from Praktor, and keep MIT attribution in `NOTICE`. |
| 2026-11-26 | Product name **spok1** (company Veldray). Packages: `spok1-core`, `-guard`, `-agentd`, `-mcp`, `-web`, `-cli`. |
| 2026-11-26 | The Hermes design was dropped; v4 of the PRD is the only spec. |
| 2026-11-26 | **Python only**: a uv workspace with no Rust. The guard is written in plain Python with only the standard library. Phase 2 measures its latency, and only if it fails the gate (p95 over 100 ms) is the guard ported to a standalone Rust binary. |
| 2026-11-26 | Tech stack approved ([tech-stack.md](tech-stack.md)): pyright, GitHub Actions, asyncio, httpx, pydantic + PyYAML, croniter, sqlite-vec (only if needed), numpy, cryptography, the MCP Python SDK, Typer, FastAPI + Jinja2 + htmx + Chart.js, imaplib / the Gmail API. |
| 2026-11-26 | **spok1 is open source**: public repo github.com/creed-online/spok1 under **Apache-2.0** (copyright Veldray). Personal data never goes into it: the brain is a separate private repo, personal details live in gitignored `*.local.yaml`, and secrets live in `.env` / the vault. |
| 2026-11-26 | Docker stays: rootless Docker Engine on the VM for project workloads only (test DBs, project services, builds). The core runtime stays on systemd + uv, agents never get the Docker socket, and every `docker` command goes through the guard. |

---

## Phase 0: Decisions and setup

- [x] Fix the PRD's inconsistencies: section numbering, `karta`, phase references, package layout
- [x] Write `docs/architecture.md`
- [x] Choose the language: Python only
- [ ] Fill in every `<<PLACEHOLDER>>` from the PRD's "My details" table in **`config/my-details.local.yaml`** (gitignored). The repo is public, so never put real values in the PRD.
  - [ ] Azure subscription, region, resource group
  - [ ] Mem0 `user_id`
  - [ ] Brain repo URL (private)
  - [ ] Discord user ID
  - [ ] Timezone and quiet hours
  - [ ] Repos the agents may work on
  - [ ] T3 cost threshold (default ₹500), report times (EOD 19:00, weekly Mon 09:00)
- [x] `git init` this repo and add a `.gitignore` (`.env`, `*.env`, `*.db`, key-bearing JSON, `.venv/`)
- [x] Push to the public open-source repo: github.com/creed-online/spok1
- [x] Add `LICENSE` (Apache-2.0), `NOTICE`, `README.md` and `.gitattributes` (LF for `.sh`)
- [x] Gitignore `*.local.yaml`
- [x] Add `config/my-details.example.yaml` (empty values)
- [x] Add `SECURITY.md` (how to report a vulnerability privately) and `CONTRIBUTING.md`
- [x] Turn on GitHub secret scanning + push protection, Dependabot alerts + security updates, and private vulnerability reporting
- [ ] Set up the uv workspace: root `pyproject.toml` with empty `packages/spok1-*` skeletons, ruff, pyright, pytest and Python 3.12
- [ ] Add `.github/dependabot.yml` (`uv` + `github-actions` ecosystems), once `pyproject.toml` and the CI workflow exist
- [ ] Add the CI workflow (`.github/workflows/ci.yml`): ruff, pyright and pytest on Ubuntu and Windows

## Phase 1: The brain on any computer

**Check the docs first** (code.claude.com/docs): `@imports` on Windows, the user-level `CLAUDE.md`, the hook event names (`SessionStart`, `SessionEnd`), hook input and output formats, the Mem0 MCP setup.

- [ ] Write `brain-template/`
  - [ ] `CLAUDE.md` (manager process from §2, `@profile.md` import)
  - [ ] Empty `beliefs.yaml`, `profile.md`, `feedback.md`, `changelog.md`
  - [ ] Folders: `agents/`, `skills/`, `projects/`, `jobs/`, `evals/`, `inbox/`, `reports/`
  - [ ] Placeholder `guardrails.yaml` and `grants.yaml` (filled in during Phase 2)
  - [ ] YAML schemas for beliefs, jobs and skill `meta.yaml`
- [ ] Create the private brain repo from the template (T2)
- [ ] Interview me (10–15 questions) to seed `beliefs.yaml` and `profile.md`
- [ ] `spok1-core`: config loader (machine name, paths, `.env`), YAML schema validation
- [ ] `claude/mcp-mem0.sh` and `.ps1`: Mem0 MCP at user scope with the shared `user_id`
- [ ] `spok1-mcp`: the `notify` server (Discord webhook, send-only), plus a CLI fallback script
- [ ] Hooks (`.sh` and `.ps1`)
  - [ ] `session-start-sync`: `git -C ~/brain pull --ff-only`, continuing quietly if it fails
  - [ ] `session-end-capture`: `claude -p --model haiku` extracts the episode into `inbox/<machine>-<date>-<id>.md`, then commits and pushes in the background
- [ ] `laptop/install.ps1` and `laptop/install.sh` (safe to rerun)
  - [ ] Install Claude Code if missing and clone the brain
  - [ ] Link `~/.claude/{CLAUDE.md,agents,skills}` (symlinks; `mklink /J` on Windows)
  - [ ] Add the Mem0 MCP, the hooks and `notify`, and copy `settings.laptop.json`
  - [ ] Register the machine name, and create `~/.config/agent/.env.example`
- [ ] You: create the Discord server and webhooks, and fill in `~/.config/agent/.env` and the Mem0 key
- [ ] Run the installer on your laptop

**✅ Checkpoint 1**
- [ ] A new session knows your profile and finds a Mem0 fact
- [ ] Session episodes appear in `brain/inbox/`
- [ ] `notify` sends you a Discord message from the laptop

## Phase 2: Guardrails, orchestration and the agent factory (laptop)

**Check the docs first:** the PreToolUse hook's input and output format for permission decisions, settings scopes (pick the most protected one), sub-agent frontmatter (`model`, `isolation: worktree`, `tools`, `maxTurns`, `skills`), agent teams, worktrees.

**Guardrails first, before any agent can act**
- [ ] `guardrails.yaml`: tiers T0–T3 and Forbidden, budgets, quality floor, project path rules
- [ ] `claude/settings.laptop.json` and `settings.vm.json`: deny for Forbidden and the worst T3 commands, ask for T2/T3, allow for T0 only
- [ ] `spok1-guard`: the classifier (standard library only, imports loaded only when needed)
  - [ ] Shell parser: `rm`, `mv`, `chmod` outside the worktree, `rm -rf`, pipes and `sudo`
  - [ ] SQL: statement type, `WHERE` checks, `DROP` / `TRUNCATE`
  - [ ] Cloud CLI verbs: `az`, `gh`, `git` (push, force, history rewrites), `npm publish`, `terraform`, `kubectl`, `docker`
  - [ ] HTTP: `DELETE`/`PUT`/`PATCH`/`POST` to hosts not on the allow-list
  - [ ] MCP tool tiers from `connectors/registry.yaml`
  - [ ] Project path isolation (anything outside `~/projects/<active>/` is denied)
  - [ ] Unclear → one tier higher; unclassifiable → T2; any deletion → T3
  - [ ] Return allow, or ask/deny with a reason, in the hook format from the docs
- [ ] Guard test suite: **at least 60 cases** across T0–T3 and Forbidden, run in CI
- [ ] `claude/hooks/guard.sh` and `guard.ps1`: these call the venv's `python` directly, never `uv run`
- [ ] **Latency gate:** measure the guard's p95 on the Windows laptop. Pass is ≤ 100 ms; on failure, open a "port spok1-guard to Rust" task.
- [ ] `spok1-core`: hash-chained, append-only audit log (`learning.db` table + daily JSONL)
- [ ] The manager's pre-flight usage estimate (turns × model weight: haiku 1, sonnet 3, opus 15)

**Orchestration**
- [ ] Core agents in `brain/agents/`: researcher, coder, reviewer, tester, librarian, agent-architect
- [ ] Auditor, rules only for now (§7.1 pre-flight checks without haiku)
- [ ] Manager process in `brain/CLAUDE.md`: route → recall → plan → pre-flight → dispatch → review → report → debrief
- [ ] One worktree per coder: branch naming `agent/*`, cleanup
- [ ] Project registry (`brain/projects/registry.yaml`) and the `project.md` schema
- [ ] `/new-agent` skill: up to 5 questions → spec card → approval → write the definition → commit
- [ ] Evaluate agent teams and write the decision into the decisions log

**✅ Checkpoint 2**
- [ ] The guard test suite passes, and the latency gate passes (or the Rust port task is opened)
- [ ] Live attempts at `DROP TABLE` on a test DB, `rm -rf` outside the worktree and `git push --force` are stopped with the correct tier and reason
- [ ] One request becomes 2+ parallel worktree tasks. The auditor suggests at least one saving, the PRs open only after approval, and you get a summary with the usage spent.
- [ ] `/new-agent` creates a test project agent after you approve its spec card

## Phase 3: Azure VM

**Check first:** current Azure pricing for B2s, Standard SSD 32 GB and a public IP, plus Tailscale setup on Ubuntu 24.04.

- [ ] `infra/cloud-init.yaml`: packages (git, gh, node, uv, tmux, tailscale, ffmpeg), 4 GB swap, non-root user
- [ ] Rootless Docker for the non-root user (not in the `docker` group); check that `docker run hello-world` works without root
- [ ] `infra/azure-create.sh`: resource group, VNet, NSG with **no inbound rules**, Ubuntu 24.04 B2s VM, auto-shutdown off (safe to rerun)
- [ ] `infra/azure-destroy.sh` (asks for confirmation; T3 when run)
- [ ] Show you the cost estimate, get approval (T2), then run it

**✅ Checkpoint 3**
- [ ] `ssh` over Tailscale works as the non-root user
- [ ] The public IP has no open inbound ports (verified by an external scan)

## Phase 4: The always-on manager and Discord

**Check the docs first:** the Channels and Discord plugin (name, pairing, allow-list, whether reactions and permission prompts are relayed), `/login` vs `setup-token`, Remote Control.

- [ ] `vm/bootstrap.sh`: Claude Code, plugins, brain clone, links, uv workspace install
- [ ] You: run `/login` on the VM over SSH
- [ ] You: create the Discord bot application and put its token in `vm/.env` on the VM
- [ ] Install the Discord channel plugin, pair your account, allow only your user ID
- [ ] agentd bootstrap step: create the HQ channels (`#command`, `#approvals`, `#alerts`, `#daily-digest`, `#learning`) and their webhooks
- [ ] `vm/manager-start.sh` + `claude-manager.service` (tmux, `Restart=always`)
- [ ] Clone the agent repos on the VM with the fine-grained GitHub token
- [ ] `vm/.env.example` with every variable, left empty

**✅ Checkpoint 4**
- [ ] From your phone, "In repo1, add a unit test for X and open a PR" is dispatched, reviewed and opened as a PR, and you get a summary
- [ ] Messages from another Discord account are ignored (tested with an alt account)
- [ ] The manager survives `sudo reboot`

## Phase 5: The agentd runtime and the approval broker

**Check first:** the healthchecks.io API, the Discord REST and gateway limits, the `claude -p --output-format json` usage fields.

- [ ] `spok1-agentd` skeleton: asyncio service, `agentd.service` (`Restart=always`), structured logs
- [ ] `learning.db` schema: events, episodes, jobs, alerts, stats, audit
- [ ] Scheduler: `brain/jobs/*.yaml` pulled every 2 min → SQLite; one-off, cron and natural-language jobs; job types reminder, agent-run and digest
- [ ] Notifier: P1/P2/P3, quiet hours, dedup with counts, at most 20 per hour then a digest, short alert IDs
- [ ] Reply and reaction parsing: `ack`, `snooze`, `noise`, `do it`, ✅ ❌ 🔕 🗑️
- [ ] Built-in watchers: job/agent failure, CI, PR waiting more than 24 h, disk and memory, Claude login expiry (10 days ahead), usage limits, manager health
- [ ] Governor: priority queue, at most 3 running at once, per-task, per-project and daily budgets, rate-limit back-off
- [ ] **Approval broker**
  - [ ] Discord approval cards (embeds) with action, reason, impact, usage, cost and undo
  - [ ] `approve` / `deny` / `ask` replies and ✅/❌ reactions (never enough for T3)
  - [ ] T3: one-time confirm code, dry-run output, verified backup ID
  - [ ] Timeout after 30 min counts as a denial; the task can be resumed later
  - [ ] Standing grants from `grants.yaml` (scope, limit, expiry, `revoke`); never for T3
  - [ ] Credential injection for a single approved command, removed afterwards
- [ ] `spok1-core` vault: AES-256-GCM, entries tagged by project
- [ ] `~/.trash` (7 days) in place of `rm`
- [ ] Dead-man switch: ping healthchecks.io every 5 min, alert on Discord when it stops
- [ ] `/remind` and `/schedule` skills; "remember X" sends facts to Mem0 and dated items to reminders
- [ ] Unit tests for agentd

**✅ Checkpoint 5**
- [ ] A T2 action produces a Discord card; `deny` stops it, and no answer times out as a denial
- [ ] A T3 action is refused without the confirm code and a verified backup
- [ ] A standing grant allows exactly its scope and expires on time
- [ ] "Remind me in 10 minutes to call the CA" arrives on time
- [ ] A job created on the laptop runs on the VM
- [ ] A deliberately failing CI run causes a P2 ping
- [ ] Stopping the VM triggers the dead-man alert
- [ ] `noise <id>` is recorded

## Phase 6: The learning engine and the full auditor

- [ ] Signal capture from explicit, outcome and process events; laptop signals come in through `inbox/`
- [ ] Reflexion debriefs (the librarian writes an episode after each task)
- [ ] Scored retrieval: Mem0 relevance × recency decay (30-day half-life) × importance, with MMR; hybrid FTS5 + vector with RRF; reinforcement; archive after 90 days
- [ ] Nightly consolidation: episodes → facts → belief candidates, dedup, contradiction flags
- [ ] Belief model: Beta(α, β); promote at ≥0.8 with ≥3 pieces of evidence, demote below 0.6; regenerate `profile.md` (300 lines or fewer)
- [ ] Active learning: up to 3 questions a day, ranked by uncertainty × impact
- [ ] Skill lifecycle: candidate → trial → active (at least 0.7 after 3 uses) → rewritten (below 0.5 after 5) → retired (unused 60 days); stats in `meta.yaml`
- [ ] Diff learning: agent's version vs. your final version → style beliefs
- [ ] Evals: golden tasks harvested from real work, rubrics, an opus judge plus deterministic checks, sampled runs
- [ ] Weekly prompt evolution (GEPA-style), gated by evals
- [ ] Self-tuning watchers (precision from ack/noise/do it) and model routing (cheapest at equal success)
- [ ] Auto-commit with `changelog.md`; `revert <item>`
- [ ] Nightly "What I learned today" and the weekly growth report (§5.4 metrics)
- [ ] Learning limits enforced: it can't create anything that uses usage or reaches outside the brain, and can't touch the guardrails
- [ ] **Full auditor**
  - [ ] Usage accounting per task (turns, tokens, model, outcome)
  - [ ] Pre-flight check with haiku for plans over 20 units
  - [ ] Waste detection during runs (retry loops, re-reads, more than half the turn cap)
  - [ ] Nightly top-5 usage sources → proposals → eval gate (no drop on must-pass items, at most 2% overall)
  - [ ] 7-day live monitor with automatic rollback
  - [ ] Weekly usage report, including the auditor's own cost

**✅ Checkpoint 6**
- [ ] A laptop correction ("always use pnpm") is followed on the VM after the nightly run, with no approval, and the nightly message lists it
- [ ] The auditor's cheaper-model proposal passes the gate and is adopted; a deliberately worse proposal is rejected
- [ ] Editing an agent's PR before merging creates a style belief
- [ ] A deliberately bad prompt change is rejected by the eval gate
- [ ] `revert` works
- [ ] The first growth report and the first usage report arrive

## Phase 7: Project OS core

- [ ] Org-designer interview and starter templates (§9.0): business, software, NGO, research, event, personal
- [ ] Schemas for `project.md` and `org.yaml`, plus the agent generator (YAML → `.claude/agents/*.md`)
- [ ] `project.db` schema: tasks, metrics, hypotheses, approvals, leads
- [ ] Task board: statuses `backlog → … → done` and `blocked`, dependencies, linked artifacts, usage spent
- [ ] Work cycles and event triggers in agentd
- [ ] Cross-team routing through the manager; structured task comments and handoff notes
- [ ] Improvement-loop templates (hypothesis → experiment → measure)
- [ ] Reports: end of day, weekly (with per-agent scorecards and hire/retire recommendations), monthly, plus the twice-daily approvals digest
- [ ] Multi-project isolation (§9.10)
  - [ ] `registry.yaml` lifecycle: `new project`, `use`, `pause`, `resume`, `archive`, `restore`, `export`, `delete`
  - [ ] `project_id` on every record
  - [ ] Guard path isolation, vault tag checks, Mem0 `app_id` filter (check the Mem0 field name)
  - [ ] Discord category per project, plus the archive category
  - [ ] Fair scheduling across projects by `priority` and `usage_share`
  - [ ] Portfolio view in reports
- [ ] `spok1-cli`: `spok1 use/new/pause/resume/archive/restore/export/status/audit`
- [ ] `spok1-web` **Mission Control** (Tailscale-only): org chart, task boards, approvals inbox, KPIs, reports, usage by team

**✅ Checkpoint 7**
- [ ] A dummy project with 2 teams and 5 agents runs a full day on schedule
- [ ] A second dummy project: project B can't read A's files, memory or tokens (the guard and the vault both refuse)
- [ ] `pause`/`resume` A gives a correct summary; `archive`/`restore` A brings back an identical state
- [ ] The end-of-day report shows both projects plus the portfolio summary, on Discord and in Mission Control
- [ ] A forced weekly review produces scorecards and a hire/retire recommendation that needs approval

## Phase 8: Pilot project (perfume brand)

**Check the docs first:** each connector's official MCP server or API, the Meta Graph API requirements (Professional account linked to a Page, token lifetime).

- [ ] `/connect` flow: prerequisites → official sign-in link → vault → read-only test → agent permission mapping (T2)
- [ ] `connectors/registry.yaml` (tool → tier → agents), enforced by the guard
- [ ] Token-expiry watcher; `disconnect <service>`
- [ ] Action limits per account; dry-run preview before any public action
- [ ] Phase 8a connectors: Instagram + Facebook Page, Canva (method 1), Google Sheets/Drive (method 1)
- [ ] Prove method 1 works under `setup-token` in `claude -p`, and that jobs still run with `/login` expired
- [ ] You: describe the brand; the org-designer builds Research, Marketing and Development first
- [ ] You: create the accounts using the agents' checklist, then `/connect` each one
- [ ] Development builds the store or landing page (every launch step needs approval)
- [ ] Phase 8b connectors: Gmail (outreach mailbox), Shopify or store, YouTube
- [ ] Sales: sending domain and mailbox, approved templates, daily cap, opt-out line, suppression list, `leads` watcher
- [ ] Add Sales, Legal and HR when the auditor shows there's room in the limits

**✅ Checkpoint 8**
- [ ] One full marketing loop: insight → experiment → content → brand and legal check → approval → API post → measurement
- [ ] The week-1 review shows KPIs and per-agent scorecards
- [ ] Sales dry run: an "interested" reply → P1 ping with a draft; `send <id>` only after confirmation; unsubscribe → suppression list
- [ ] A second template (for example, a software tool) is tested, to prove the system isn't business-specific

## Phase 9: Day-to-day operations

- [ ] `vm/backup.sh` + timer: `~/.claude` state, `learning.db` and `project.db` → Blob (Cool, 7 days)
- [ ] Audit-log JSONL shipped to Blob daily; `/audit today`
- [ ] `RUNBOOK.md`: updates, logs, restarting the manager and agentd, `/login` renewal, rotating each token, restore, a new computer, adding a project or agent, pausing all outreach, teardown
- [ ] `tests/smoke-vm.sh` and `tests/smoke-laptop.ps1`
- [ ] Test a restore from backup

**✅ Checkpoint 9**
- [ ] Both smoke tests pass
- [ ] A restore has been tested
- [ ] You've read the RUNBOOK

---

## Security checklist (all must be checked before "done")

- [ ] No inbound ports; SSH only over Tailscale
- [ ] Private Discord server; bot allows only your user ID with pairing locked; minimal bot permissions; laptops hold only webhooks; a reaction never approves T3
- [ ] Non-root user; `~/.trash` instead of `rm`
- [ ] Fine-grained GitHub token, listed repos plus the brain only, no admin or delete rights
- [ ] Allow and deny lists on every machine; `--max-turns` on every headless run; never `--dangerously-skip-permissions`
- [ ] `ANTHROPIC_API_KEY` unset everywhere; the Max token used only by the `claude` binary
- [ ] No secrets in git, the brain, Mem0 or logs; lead data stays in the project store
- [ ] Outreach: approved templates, cap, opt-out, suppression list, separate domain, approval for each new segment
- [ ] Learning: automatic, eval-gated, committed, reported, revertible; can't create anything that uses usage; can't touch the guardrails
- [ ] Guard on every tool call on every machine; suite passes; every deletion is T3; no answer means denied; only you edit `guardrails.yaml`
- [ ] Write, admin and delete credentials injected only for approved commands
- [ ] Project isolation: `project_id` everywhere, guard paths, vault scope, Mem0 filter, cross-project copies T2, project deletion T3 after an export
- [ ] Connectors: OAuth or official tokens only, in the vault, least privilege, publishing T2 and deleting T3, action limits, expiry watcher, `disconnect` tested, content treated as data
- [ ] Audit log append-only and hash-chained, shipped to Blob, `/audit today` works
- [ ] Auditor: nothing adopted without the eval gate; rollback tested; its own usage reported
- [ ] Dead-man switch tested; backups and a restore tested

## Open questions to check against the docs

These are flagged "verify" in the PRD. Record each answer here, and in the PRD if it changes the design.

- [ ] Discord channel plugin: exact name, pairing, whether it relays reactions and permission prompts
- [ ] Does Remote Control work with the VM manager's login?
- [ ] `@import` syntax for `~/.claude/CLAUDE.md` on Windows
- [ ] PreToolUse hook output format for allow/ask/deny with a reason
- [ ] The most protected settings scope that agents can't edit
- [ ] Hook names `SessionStart` / `SessionEnd`
- [ ] Mem0 field for the project namespace (`app_id`?)
- [ ] `claude mcp login` vs `/mcp` for remote MCP sign-in
- [ ] Is agent teams stable enough to adopt?
