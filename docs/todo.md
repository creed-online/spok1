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
| 0 | Decisions and setup | laptop | `[x]` |
| 1 | The brain on any computer | laptop | `[x]` |
| 2 | Guardrails, orchestration, agent factory | laptop | `[x]` |
| 3 | Azure VM | Azure (costs start) | `[x]` |
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
| 2026-11-26 | Phase 2: policy in admin-only `guardrails.toml` (1a); guard installed via **managed settings** (2a); T0/T1 **pass through** so the guard can only make things stricter (3a); private `spok1-sandbox` for tests (4). Agent teams not adopted. |
| 2026-11-26 | Guard latency gate: **keep Python** (owner, option a). Linux 19 ms p95, Windows CI 51 ms, this laptop 141 ms. No Rust port for now. |
| 2026-11-26 | Agents stop at a local commit; only the manager pushes and opens PRs, after review (owner, during /new-agent). Coders make their own worktree inside the target repo (`isolation: worktree` worktrees the session repo instead). |
| 2026-11-26 | Phase 3 VM: **Standard_B2pts_v2** (2 ARM vCPU, 1 GB, free tier) in centralindia, ≈ $6.65/month. B2s isn't available on the student subscription. One agent at a time on the VM; parallel work on the laptop. No Docker on the VM. |
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
- [x] Set up the uv workspace: root `pyproject.toml` with empty `packages/spok1-*` skeletons, ruff, pyright (strict), pytest and Python 3.12. `spok1-guard` has no dependencies at all, not even `spok1-core`.
- [x] Add `.github/dependabot.yml` (`uv` + `github-actions` ecosystems, weekly, grouped)
- [x] Add the CI workflow (`.github/workflows/ci.yml`): ruff, pyright and pytest on Ubuntu and Windows, with actions pinned to SHAs

## Phase 1: The brain on any computer

**Check the docs first** (code.claude.com/docs): `@imports` on Windows, the user-level `CLAUDE.md`, the hook event names (`SessionStart`, `SessionEnd`), hook input and output formats, the Mem0 MCP setup.

- [x] Check the docs, and record what changed the design (PRD §4 and §8, open questions below)
- [x] Write `brain-template/`
  - [x] `CLAUDE.md` (manager process from §2, `@profile.md` import, under 200 lines)
  - [x] Empty `beliefs.yaml`, `profile.md`, `feedback.md`, `changelog.md`
  - [x] Folders: `agents/`, `skills/`, `projects/` (+ `registry.yaml`), `jobs/`, `evals/`, `inbox/`, `reports/`
  - [x] Placeholder `guardrails.yaml` and `grants.yaml` (filled in during Phase 2)
  - [x] Schemas for beliefs, jobs and skill `meta.yaml` (pydantic, in `spok1-core`) + `spok1 validate-brain`
- [x] Create the private brain repo `creed-online/spok1-brain` from the template (T2, approved 2026-11-26)
- [x] Interview me (13 questions) to seed `beliefs.yaml` (14 pinned beliefs) and `profile.md`; key preferences also saved to Mem0 (`app_id=global`)
- [x] `spok1-core`: paths, config loader (machine name, `.env`, `my-details.local.yaml`), schema validation, Discord webhook sender
- [x] Mem0: the hosted MCP server (`https://mcp.mem0.ai/mcp`), with OAuth on laptops, added by `spok1 install` (the local package is archived)
- [x] `spok1-mcp`: the `spok1-notify` server (Discord webhook, send-only), plus the `spok1 notify` CLI
- [x] Hooks in Python, run through exec form (`spok1 hook session-start|session-end`), one implementation for every OS
  - [x] `session-start`: `git pull --ff-only` (continues quietly if it fails), records the active session, catches up on missed or crashed captures, prints nothing
  - [x] `session-end`: writes a marker and starts a detached `spok1 capture` (tested: exits in 0.8 s, and the capture outlives it)
  - [x] `spok1 capture`: haiku (no tools or MCP, 1 turn) → `inbox/<machine>-<date>-<id>.md` → commit + push; skips trivial sessions; retries 3 times; holds a lock; never captures itself
- [x] `laptop/install.ps1` and `laptop/install.sh` (safe to rerun, `--dry-run`) → `spok1 install`
  - [x] Refuse to run if `ANTHROPIC_API_KEY` is set (process, user and system environment)
  - [x] Install the Claude Code CLI and uv with the official installers if missing; clone or pull the brain
  - [x] Back up `settings.json`, `CLAUDE.md` and `.claude.json`; add a managed import block to `~/.claude/CLAUDE.md`
  - [x] Link `~/.claude/agents`, and **each skill** separately (your own skills stay); junctions on Windows
  - [x] Merge the hooks into `~/.claude/settings.json` (keeping everything else); add the `mem0` and `spok1-notify` MCP servers
  - [x] Register the machine name; write `~/.config/agent/.env.example` and an empty `.env`
  - [ ] Permission lists (`settings.laptop.json`): moved to Phase 2, together with the guard
- [x] Tests: 43 passing (config, schemas, Discord, transcript, markers, hooks, capture end to end with a local git remote, install merge logic, junctions)
- [x] You: create the Discord server and an `#alerts` webhook, and paste it into `~/.config/agent/.env`
- [x] Run the installer on your laptop (T2, approved 2026-11-26): Claude Code CLI 2.1.332 installed, machine registered as `laptop-ve9lombo`
- [x] Mem0: the claude.ai Mem0 connector is already signed in and works (test fact written and read back); the duplicate `mem0` server was removed

**✅ Checkpoint 1: PASSED 2026-11-26**
- [x] A new session knows your profile and finds a Mem0 fact (a fresh headless session answered IST / pnpm / "no AI slop" unprompted, and quoted the Mem0 test fact)
- [x] Session episodes appear in `brain/inbox/` (`laptop-ve9lombo-2026-11-26-af9055c5.md`, pushed in 22 s, secret scan clean)
- [x] `notify` sends you a Discord message from the laptop
- Fixed while checking: concurrent `git pull`s clobbered each other (now under a lock); sessions without a transcript (`claude mcp list` etc.) no longer start a capture; episode titles cut at a word boundary

## Phase 2: Guardrails, orchestration and the agent factory (laptop)

**Check the docs first:** the PreToolUse hook's input and output format for permission decisions, settings scopes (pick the most protected one), sub-agent frontmatter (`model`, `isolation: worktree`, `tools`, `maxTurns`, `skills`), agent teams, worktrees.

**Guardrails first, before any agent can act**
- [x] Check the docs: Bash permission rules "aren't a security boundary"; a PowerShell tool exists on Windows; "ask" is auto-denied in `-p`; managed settings are admin-only; agent teams are experimental; worktrees go in `.claude/worktrees/`; hooks run inside sub-agents
- [x] Policy: `claude/guardrails.toml` (template) → admin-only `C:\Program Files\spok1\guardrails.toml` / `/etc/spok1/guardrails.toml` (decision 1a; the brain keeps no copy)
- [x] Settings: `claude/managed-settings.laptop.json` and `.vm.json` (generated; a test keeps them in sync): guard hook (`onFailure: block`), `disableBypassPermissionsMode`, deny rules, at most 3 sub-agents (decision 2a: managed settings)
- [x] `spok1-guard` (standard library only, nothing imported at runtime that isn't needed)
  - [x] Bash **and PowerShell** parsers: quotes, `&&` `;` `|`, subshells, `$(…)`, backticks, heredocs, redirections, `cd` tracking
  - [x] Unwrapping: `bash -c`, `sh -c`, `pwsh -Command`, `cmd /c`, `sudo`, `env`, `timeout`, `xargs`, full program paths, `git -C`/`-c`; PowerShell `curl`/`wget` checked both ways
  - [x] Programs: rm/mv/cp/chmod/dd/find/sed -i, git, gh, az, terraform, kubectl/helm, docker, npm/pnpm/yarn, pip/uv, curl/wget/httpie, SQL clients, claude, PowerShell cmdlets
  - [x] SQL: statement type, `WHERE` checks (including `WHERE 1=1`), `DROP`/`TRUNCATE`
  - [x] HTTP: GET T0, non-GET T1 to allow-listed hosts and T2 elsewhere, DELETE T3, uploading secret files FORBIDDEN
  - [x] MCP tools: policy patterns, then by name (get/list → T0, create/send → T2, delete → T3)
  - [x] Paths: worktree/scratch/project roots; protected (settings, policy, guard, managed settings); secret files (`.env`, keys, credentials)
  - [x] Unclear → T2, unresolvable deletion → T3, `ANTHROPIC_API_KEY` → FORBIDDEN
  - [x] Hook output: T0/T1 pass through (decision 3a: the guard only makes things stricter), T2 ask, T3/FORBIDDEN deny, crash → exit 2 (fails closed)
- [x] Guard test suite: **247 cases** (the PRD asks for at least 60), including the bypass forms the docs warn about; runs in CI
- [x] Hook launcher: `python -I -S run_guard.py --policy …` from an **admin-owned** Python and guard copy (`spok1 guard install`), precompiled
- [!] **Latency gate FAILED on the Windows laptop:** median 110 ms, p95 141 ms (target ≤ 100). Trimmed from 196/292 ms. Floor: Python's `import re` alone is ~75 ms p95 on this laptop. → open task below
- [x] Decision (2026-11-26): **keep the Python guard.** It passes on Linux (CI: 17 ms median, 19 ms p95, where the VM runs) and on Windows CI (46/51 ms); only this laptop is slow (most likely antivirus scanning each process start). Revisit the Rust port only if the laptop latency becomes a real problem.
- [x] Hash-chained, append-only audit log (`~/.spok1/state/audit/<day>.jsonl`), secrets redacted, `spok1 guard verify-log` (the `learning.db` copy comes in Phase 5)
- [x] Pre-flight usage estimate: `spok1 audit-plan` (turns × weight from the policy)

**Orchestration**
- [x] Core agents in `brain/agents/`: researcher, coder (`isolation: worktree`, `agent/*` branch), reviewer (read-only, opus), tester, librarian (haiku), agent-architect (opus); loading through the `~/.claude/agents` junction verified
- [x] Auditor, rules only: `auditor` agent + `spok1 audit-plan` (model downgrades, turn caps, duplicates, more than 3 in parallel, budgets, missing review, plain-code hints)
- [x] Manager process: the `run-request` skill (route → recall → plan YAML → audit → approval → dispatch → review → report → debrief); `CLAUDE.md` stays at 48 lines
- [x] One worktree per coder (`isolation: worktree`; Claude Code removes clean ones and keeps changed ones)
- [x] Project registry + `project.md` schemas, checked by `validate-brain`; agent files checked too (no `bypassPermissions`, `maxTurns` required, no wildcard tools)
- [x] `new-agent` skill: up to 5 questions → spec card → approval → write → validate → register → commit
- [x] Agent teams: **not adopted** (experimental, interactive-only, "significantly more tokens"); dispatching to sub-agents is the pattern
- [x] Sandbox for the checkpoint: private `creed-online/spok1-sandbox` (textkit + pytest), registered as project `sandbox`
- [x] Guard installed (admin, through the Windows elevation prompt); a normal user can't change the policy, settings, guard code or its Python (verified)

**✅ Checkpoint 2: PASSED 2026-11-26**
- [x] The guard test suite passes (311 tests, 258 guard cases), and the **installed** guard passes the latency gate: 85 ms median, 94 ms p95 (measured by the admin installer)
- [x] Live attempts at `DROP TABLE` on a test DB (via `sqlite3` and via `python -c`), `rm -rf` outside the worktree and `git push --force` are stopped with the correct tier and reason: all T3 deny in a fresh session whose own permissions allowed them; targets intact; audit chain verified *(2026-11-26)*
- [x] One request becomes 2+ parallel worktree tasks (2 coders in `spok1-sandbox/.claude/worktrees/{slugify,reverse-words}`), reviewed, draft PRs #1 and #2 opened only after the guard's T2 prompt, summary with usage (~141 units). The auditor first found **no saving**, so a `mechanical` rule was added: on the same plan it now saves 56 units (23%) by moving push/PR off opus *(2026-11-26)*
- [x] `/new-agent` created `sandbox/docs-writer` after the owner approved its spec card (with push/PR removed); passes `validate-brain`, registered in `project.md`, brain commit `0de11d7` pushed *(2026-11-26)*

## Phase 3: Azure VM

**Check first:** current Azure pricing for B2s, Standard SSD 32 GB and a public IP, plus Tailscale setup on Ubuntu 24.04.

- [x] Check pricing and the subscription (2026-11-26): "Azure for Students" (prepaid credit; allowed regions incl. centralindia); B2s is **not available** to it; free tier gives 750 h/month **per size** of B1s, B2ats_v2 (used by `tgma-vm`) and B2pts_v2 (unused)
- [x] Decision: **option A**, `Standard_B2pts_v2` (2 ARM vCPU, 1 GB) on free hours, ≈ $6.65/month (disk + IP). Agents run one at a time on the VM; parallel work runs on the laptop. Resize to B2pls_v2 (4 GB) with one command if memory is too tight
- [x] `infra/cloud-init.yaml`: Ubuntu 24.04 arm64, packages (git, gh, node, tmux, sqlite3, ffmpeg, unattended-upgrades), Tailscale (not joined), uv for `spok1`, 4 GB swap, `spok1` user with **no sudo**, `azureuser` break-glass admin, no secrets
- [x] No Docker on the VM (1 GB RAM); it stays available on the laptop
- [x] `infra/azure-create.sh`: dry run by default, `--yes` to create; resource group `rg-spok1` (tagged), VNet, NSG with **no inbound rules** (`--nsg-rule NONE`), Standard static public IP (outbound only), 32 GB Standard SSD, auto-shutdown off, waits for cloud-init, joins Tailscale with a one-time key you paste, best-effort $20 budget alert. Safe to rerun
- [x] `infra/azure-destroy.sh`: only deletes a group tagged `project=spok1`, after you type its name (T3: you run it)
- [x] Tests: cloud-init valid and secret-free, no inbound rules, destroy is scoped; shellcheck in CI
- [x] Tailscale account; laptop, iPhone and `spok1-vm` on the tailnet
- [x] VM created by the owner (`infra/azure-create.sh --yes`, 2026-11-26): `spok1-vm`, B2pts_v2, centralindia. The budget couldn't be created from the CLI on this subscription
- [ ] You: set a $20/month budget with an email alert in the portal (Cost Management → Budgets)

**✅ Checkpoint 3: PASSED 2026-11-26**
- [x] `ssh spok1@spok1-vm` over Tailscale works: user `spok1`, aarch64, **no sudo**; uv, gh, Tailscale installed; unattended upgrades active
- [x] The public IP has no open inbound ports: no NSG inbound rules, no NIC-level NSG, and a TCP probe of 1,036 ports from outside Azure found none open
- [x] Memory at idle: 944 MB total, ~606 MB available, 4 GB swap (12 MB used)

## Phase 4: The always-on manager and Discord

**Check the docs first:** the Channels and Discord plugin (name, pairing, allow-list, whether reactions and permission prompts are relayed), `/login` vs `setup-token`, Remote Control.

- [ ] `vm/bootstrap.sh`: Claude Code, plugins, brain clone, links, uv workspace install
- [ ] You: run `/login` on the VM over SSH
- [ ] You: create the Discord bot application and put its token in `vm/.env` on the VM
- [ ] You: create a **separate Mem0 API key named `spok1-vm`** (in the new Mem0 account) and paste it into the VM's `~/.config/agent/.env` over SSH. Never put it in cloud-init. The laptop key (kept in the laptop `.env`) stays as a spare.
- [ ] Add the hosted Mem0 MCP on the VM with that key as a bearer token, set `ENABLE_CLAUDEAI_MCP_SERVERS=false` for agentd jobs, and check that a VM job and the laptop see the same memories
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
- [x] `@import` syntax: `@path`, relative to the importing file; `@~/...` works; at most 4 hops; paths with spaces need `\ ` escapes; imports inside code blocks are ignored. The user file is `~/.claude/CLAUDE.md` on every OS. Imports from outside the working directory show a one-time approval dialog (user-scope files don't). Keep each CLAUDE.md under **200 lines**. *(checked 2026-11-26)*
- [x] PreToolUse output: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow|deny|ask|defer","permissionDecisionReason":"..."}}`. Exit 2 blocks (stderr is the reason); other non-zero exits don't block unless `"onFailure": "block"`. On Windows without Git Bash, only exits 0 and 2 count. Precedence: deny > defer > ask > allow. *(checked 2026-11-26)*
- [x] Most protected settings scope: managed settings (`C:\Program Files\ClaudeCode\managed-settings.json`, `/etc/claude-code/managed-settings.json`, `/Library/Application Support/ClaudeCode/managed-settings.json`). Precedence: managed > command line > local > project > user. Permission lists merge across files; rules are checked deny → ask → allow. *(checked 2026-11-26)*
- [x] `SessionStart` (sources: startup/resume/clear/compact/fork; can add `additionalContext`) and `SessionEnd` (reasons: clear/resume/logout/prompt_input_exit/other) both exist. **SessionEnd's default timeout is 1.5 s** (60 s max per hook). *(checked 2026-11-26)*
- [x] Mem0: `app_id` is supported and stored on every memory, so it can be used for project namespaces. The local `mem0-mcp-server` was archived in Mar 2026; use the hosted server `https://mcp.mem0.ai/mcp` (OAuth, or an API key as a bearer token). *(checked 2026-11-26)*
- [x] Mem0 hosted MCP: tools add `user_id: "mem0-mcp"` automatically unless a call overrides it, and `app_id` is a parameter of `add_memory`. Decision: `mem0-mcp` is the shared user_id everywhere, with `app_id=<project>` or `global`. *(tested 2026-11-26)*
- [x] Skills: `~/.claude/skills/` already holds your own skills, so each brain skill gets its own link (the docs confirm per-skill links work). Still to check live: that Claude Code loads agents through the `~/.claude/agents` junction (Phase 1 checkpoint).
- [ ] `claude mcp login` vs `/mcp` for remote MCP sign-in
- [ ] Is agent teams stable enough to adopt?
