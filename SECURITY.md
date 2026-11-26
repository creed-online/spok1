# Security policy

spok1 lets AI agents act on real accounts, so security bugs matter here more than in most projects. Thank you for reporting them responsibly.

## Reporting a vulnerability

**Don't open a public issue.** Report it privately:

1. Go to the repo's **Security** tab → **Report a vulnerability** (GitHub private vulnerability reporting).
2. Include what you found, how to reproduce it, and what an attacker could do with it.

You'll get an acknowledgement within **7 days**. Once it's fixed, you'll be credited in the advisory unless you'd rather not be. There is no bug bounty.

## Supported versions

spok1 is in the **design phase** and has no released versions yet. Until 1.0, only the latest commit on `main` is supported.

## What we especially want to hear about

- **Guard bypasses**: any tool call that gets a lower risk tier than it should, or slips past the PreToolUse guard (shell tricks, SQL, cloud CLI, HTTP, MCP)
- **Approval bypasses**: running a T2/T3 action without a valid approval, approving T3 without the confirm code, or a standing grant that reaches beyond its scope or expiry
- **Prompt injection that leads to action**: text in emails, comments, DMs or web pages that gets an agent to do something
- **Secret exposure**: tokens or keys ending up in git, the brain, Mem0, logs or Discord
- **Project isolation breaks**: one project reading another's files, memory or credentials
- **Audit log tampering** that goes undetected
- **Anything that makes `claude` bill an API key** instead of the Max subscription

## Out of scope

- Bugs in Claude Code, Discord, Mem0, Tailscale or Azure themselves (report those to their vendors)
- Problems that need an attacker to already have shell access as the spok1 user
- Running spok1 with the guardrails deliberately weakened (editing `guardrails.yaml`, `--dangerously-skip-permissions`)
