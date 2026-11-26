#!/usr/bin/env bash
# spok1 laptop installer (macOS / Linux). Safe to rerun.
#
#   laptop/install.sh [--brain-repo <url>] [--machine <name>] [--dry-run]
#
# Bootstraps the prerequisites, then hands over to `spok1 install` (all real logic is there).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
for arg in "$@"; do [ "$arg" = "--dry-run" ] && DRY_RUN=1; done

# Never run with an API key: it would bill the API instead of the Max plan.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "✗ ANTHROPIC_API_KEY is set. Unset it (and remove it from your shell profile), then rerun." >&2
  exit 1
fi

command -v git >/dev/null || { echo "✗ git is required" >&2; exit 1; }
export PATH="$HOME/.local/bin:$PATH"

# Claude Code CLI (official native installer)
if ! command -v claude >/dev/null; then
  if [ "$DRY_RUN" = 1 ]; then echo "would: install Claude Code CLI (curl -fsSL https://claude.ai/install.sh | bash)"
  else
    echo "Installing Claude Code CLI (official installer)..."
    curl -fsSL https://claude.ai/install.sh | bash
    command -v claude >/dev/null || { echo "✗ claude still not on PATH; open a new terminal and rerun" >&2; exit 1; }
  fi
fi

# uv (official installer)
if ! command -v uv >/dev/null; then
  if [ "$DRY_RUN" = 1 ]; then echo "would: install uv (curl -LsSf https://astral.sh/uv/install.sh | sh)"; echo "(dry run stops here)"; exit 0
  else
    echo "Installing uv (official installer)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
fi

(cd "$ROOT" && uv sync --locked --quiet)
exec "$ROOT/.venv/bin/spok1" install "$@"
