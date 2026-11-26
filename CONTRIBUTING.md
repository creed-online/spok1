# Contributing to spok1

Thanks for your interest. spok1 is in the **design phase**: the spec is in [docs/prd.md](docs/prd.md) and the build plan is in [docs/todo.md](docs/todo.md). Code arrives phase by phase.

## Before you start

- **Open an issue first** for anything bigger than a typo, so we can agree on the approach before you write code.
- Read the spec section your change touches. [docs/architecture.md](docs/architecture.md) has the diagrams and [docs/tech-stack.md](docs/tech-stack.md) has the approved stack. A new dependency needs agreement in an issue first.

## Hard rules

These are part of the design, not style preferences. PRs that break them won't be merged.

1. **Max subscription only.** Nothing may use `ANTHROPIC_API_KEY`, the Agent SDK with a token, or any paid LLM API. LLM work goes through the unmodified `claude` binary.
2. **No secrets or personal data in the repo.** Commit only empty templates (`.env.example`, `*.example.yaml`). Real values belong in gitignored `*.local.yaml`, `.env` files or the vault. Secret scanning with push protection is on.
3. **Guardrails only get stricter.** A change may never lower a risk tier, weaken the guard, or add a way round approvals without an issue and an explicit maintainer decision. Never use `--dangerously-skip-permissions` or `permissionMode: bypassPermissions`.
4. **Guard changes need tests.** Every change to `spok1-guard` adds test cases, and the whole suite must pass, including the latency gate.
5. **Plain code before an LLM.** If something can be done without an LLM, do it that way.

## Development setup

- Python **3.12**, **uv** workspace (`packages/spok1-*`)
- Before opening a PR, run: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`
- Shell scripts: Bash for Linux/macOS (LF line endings) and PowerShell for Windows. `.gitattributes` enforces the line endings.

## Pull requests

- One topic per PR, with a clear description of what changed and why.
- Link the issue and the PRD section it relates to.
- If you changed behaviour that the spec describes, update `docs/` in the same PR.
- Keep commits focused; squash fixups before review.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE), as section 5 of the license describes. If you adapt code from another project, add its attribution to [NOTICE](NOTICE).
