# spok1

An open-source personal agent system with a self-improving brain, built by **Veldray**.

spok1 runs an always-on Claude Code manager on a small cloud VM. You reach it from your phone (Discord), from any computer with Claude Code, and from the Claude apps. It creates project agents and whole agent teams on demand, and acts on its own through schedules and watchers. It learns how you work from real outcome signals and gets measurably better over time. Anything risky needs your approval: guardrails enforced in code, not prompts.

> **Status: design phase.** No code yet. This repo holds the spec and the build plan.

## Key ideas

- **Runs on a Claude Max subscription only.** It uses the unmodified `claude` binary and never an API key. Plain code does everything it can without an LLM.
- **Guardrails in code.** Risk tiers T0–T3, a deterministic PreToolUse guard hook, an approval broker, least-privilege credentials, and a hash-chained audit log.
- **A brain that improves itself.** Beliefs with confidence scores, a skill lifecycle, learning from your edits, and prompt evolution. Every change has to pass an eval gate and can be reverted.
- **An auditor agent** that cuts Claude usage without lowering quality, and proves the quality held.
- **Project OS.** Many isolated projects (business, software, NGO, research…), each with teams, leads, specialists, a task board and reports.

## Docs

| Doc | What's in it |
|---|---|
| [docs/prd.md](docs/prd.md) | The full spec (source of truth) |
| [docs/architecture.md](docs/architecture.md) | Architecture diagrams |
| [docs/tech-stack.md](docs/tech-stack.md) | Every technology used, and what was ruled out |
| [docs/todo.md](docs/todo.md) | Phased build tracker and decisions log |

## Stack

Python 3.12 (uv workspace) · Claude Code · SQLite · Mem0 · Discord · Azure VM + Tailscale · systemd. The full list is in [docs/tech-stack.md](docs/tech-stack.md).

## Your own data stays private

The code and docs here are public. Everything about **you** stays private and never comes into this repo:
- your **brain** (profile, beliefs, memories, projects): a separate private repo
- your **details** (Azure subscription, Discord IDs, timezone): a gitignored local file
- your **secrets**: `.env` files and the encrypted vault, all gitignored

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attributions.
