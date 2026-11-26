# spok1 tech stack

The full list of technologies spok1 uses. The spec is [prd.md](prd.md), the diagrams are in [architecture.md](architecture.md), and the build tracker is [todo.md](todo.md).

**Status:** every item is **decided**. Items set by the PRD were decided there; the rest were approved on 2026-11-26 (see the decisions log in `todo.md`). Before any library goes into the code, check its current version against its docs and pin it.

**Rules that apply to the whole stack**
- All LLM work runs on the unmodified `claude` binary under Claude Max. No `ANTHROPIC_API_KEY` and no paid LLM APIs.
- Plain code does everything it can without an LLM, to save Max usage.
- Free tiers come first. Any paid service is a T2 decision (§6).
- Every dependency is pinned to an exact version in `uv.lock`.

## AI / LLM

| What | Tech | Status |
|---|---|---|
| All LLM work | **Claude Code CLI** (the unmodified `claude` binary) on a **Claude Max** subscription | Decided |
| Models | **Opus**: manager, reviewer, team leads, eval judge · **Sonnet**: coder, researcher, tester, auditor reviews · **Haiku**: librarian, watcher classifiers, auditor pre-flight checks | Decided |
| Headless runs | `claude -p --max-turns --output-format json`, authenticated with `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (agentd and cron jobs only) | Decided |
| Interactive manager | `claude` under `/login`, in tmux, with Channels | Decided |
| Claude Code features | Sub-agents, skills, hooks (PreToolUse, SessionStart, SessionEnd), git worktrees, `@imports`, permission settings, Channels (Discord plugin), Remote Control, MCP | Decided |

## Language and tooling

| What | Tech | Status |
|---|---|---|
| Language | **Python 3.12**, Python only. A Rust port of `spok1-guard` happens only if the Phase 2 latency gate fails (p95 over 100 ms). | Decided |
| Packaging | **uv** workspace with 6 packages: `spok1-core`, `spok1-guard`, `spok1-agentd`, `spok1-mcp`, `spok1-web`, `spok1-cli` | Decided |
| Lint and format | **ruff** | Decided |
| Tests | **pytest**: the 60+ case guard suite, agentd unit tests | Decided |
| Type checking | **pyright** | Decided |
| Scripts | **Bash** (VM, macOS, Linux) and **PowerShell** (Windows) | Decided |
| CI | **GitHub Actions**: ruff, pyright, pytest on Linux and Windows | Decided |

## Python libraries

| Purpose | Library | Status |
|---|---|---|
| Guard hook | **Standard library only** (`shlex`, `re`, `json`, `pathlib`), no third-party dependencies, so it starts fast | Decided |
| agentd runtime | **asyncio** (standard library) | Decided |
| HTTP client: Discord REST and webhooks, GitHub, Meta, healthchecks.io | **httpx** | Decided |
| Config and YAML schemas | **pydantic** + **PyYAML** | Decided |
| Cron expressions | **croniter** | Decided |
| Databases | **sqlite3** (standard library) with **FTS5** for keyword search | Decided |
| Local vector search for hybrid retrieval (FTS5 + vectors with RRF) | **Mem0** search first; **sqlite-vec** only if a local index turns out to be needed | Decided |
| Learning maths: MMR, clustering, Beta updates | **numpy** | Decided |
| Vault (AES-256-GCM) | **cryptography** | Decided |
| MCP servers: notify, custom channel, local connectors | The official **MCP Python SDK** | Decided |
| CLI (`spok1 use`, `spok1 status`, …) | **Typer** | Decided |
| Mission Control web app | **FastAPI** + **Jinja2** + **htmx** + **Chart.js**. The PRD only asks for "plain HTML and charts", so no paid BI tool and no SPA build step. | Decided |
| Email for the leads watcher | **imaplib** (standard library) or the **Gmail API** client | Decided |

## Data and memory

| What | Tech | Status |
|---|---|---|
| The brain (curated knowledge) | A private **Git** repo on **GitHub**, cloned to `~/brain` on every machine | Decided |
| Facts and episodes | **Mem0 Cloud** (free tier), through the Mem0 MCP server (Claude Code) and the claude.ai connector, with the same `user_id` everywhere and an `app_id` per project | Decided |
| Ops data | **SQLite**: `learning.db` on the VM and one `project.db` per project | Decided |
| Backups, audit log, media | **Azure Blob Storage** (Cool tier, 7-day retention for backups) | Decided |
| Config and content | **YAML** (guardrails, grants, jobs, watchers, org, registry) and **Markdown** (agents, skills, profile, reports) | Decided |

## Infrastructure

| What | Tech | Status |
|---|---|---|
| Cloud | **Azure**: Standard_B2s VM (2 vCPU / 4 GB + 4 GB swap), Standard SSD 32 GB, VNet + NSG with no inbound rules | Decided |
| Operating systems | **Ubuntu 24.04 LTS** (VM), **Windows 11** (primary laptop), macOS and Linux supported | Decided |
| Provisioning | **Azure CLI** scripts + **cloud-init** | Decided |
| Process management | **systemd** (`claude-manager.service`, `agentd.service`, backup timer) + **tmux** | Decided |
| Private network | **Tailscale** for SSH and Mission Control | Decided |
| Uptime monitoring | **healthchecks.io** dead-man switch (free tier) | Decided |
| Other tools on the VM | **git**, **gh** CLI, **Node.js** (runtime for Claude Code), **ffmpeg** | Decided |
| Containers | **Docker Engine in rootless mode**, installed on the VM. It's for project workloads only: test databases (for example Postgres for the guard's live `DROP TABLE` check), services a project's code needs, and reproducible builds. The core runtime (manager, agentd) stays on systemd + uv. Agents never get the Docker socket or the `docker` group (that group is root-equivalent). Every `docker` command goes through the guard: `run`, `build` and `pull` are T2; `rm`, `rmi`, `prune` and volume deletion are T3. | Decided |

## Messaging and access

| What | Tech | Status |
|---|---|---|
| Phone and desktop interface | **Discord** private server, with the official Claude Code **Discord channel plugin** holding the only gateway connection (on the VM) | Decided |
| agentd → Discord | **Discord bot REST API**: approval cards, channels, threads, reactions | Decided |
| Laptops → Discord | **Discord webhooks**, send-only | Decided |
| Other access | **Claude app / claude.ai**: Remote Control (to be verified) and the Mem0 connector | Decided |

## Connectors (Phase 8)

| Service | How it connects | Status |
|---|---|---|
| Canva, Google Drive/Sheets, Notion, Slack | The service's own **remote MCP server**, added directly on the VM (method 1 in §9.8) | Decided |
| Instagram + Facebook | **Meta Graph API** (needs a Professional account linked to a Page) | Decided |
| YouTube | **YouTube Data API** | Decided |
| Shopify | **Shopify Admin API** | Decided |
| Gmail (outreach mailbox) | **Gmail API** | Decided |
| Video production | **HyperFrames** + **ffmpeg** | Decided |
| Optional, paid (T2) | Ahrefs, Similarweb | Decided |

## Ruled out

| Tech | Why |
|---|---|
| Anthropic API key / Claude Agent SDK with a token | Bills the API or puts the Max account at risk; breaks the Max-only rule |
| Rust (for now) | No real speed gain for a workload that waits on I/O and the LLM; costs a second toolchain. Revisit only if the guard fails its latency gate. |
| Nous Hermes, Telegram | Replaced in v4 by Claude Code + Discord |
| Clerk, Praktor, ClaudeClaw as dependencies | We borrow patterns only (see the PRD's "Prior art" section) |
| Paid BI tools, X API | Cost; the X API is paid |
| Unofficial social media automation, scraping that breaks a platform's terms | Breaks the platforms' terms; official APIs only |
