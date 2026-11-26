#!/usr/bin/env bash
# The always-on manager: Claude Code with the Discord channel, in tmux session "manager".
# Started by claude-manager.service (as spok1). Watch it: ssh spok1@spok1-vm, tmux attach -t manager
# (detach: Ctrl+b then d). --foreground runs it in this terminal instead (for a first test).
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN  # manager uses /login only
# The Discord plugin runs `bun install` on every start; on the 1 GB VM that can exceed Claude
# Code's default MCP startup wait, and the channel then shows as failed. Give it 2 minutes.
export MCP_TIMEOUT=120000

# Sonnet for the manager's own chat/routing (fast); agents use the model their task needs
# (reviewer and agent-architect: opus), and "use opus for this" routes a task to opus.
MODEL="${SPOK1_MANAGER_MODEL:-sonnet}"
CMD="claude --model $MODEL --channels plugin:discord@claude-plugins-official"
mkdir -p "$HOME/work"
cd "$HOME/work"   # the manager's project root (the guard treats only this folder as "the project")

if [ "${1:-}" = "--foreground" ]; then
  exec $CMD
fi
if tmux has-session -t manager 2>/dev/null; then
  echo "manager already running"; exit 0
fi
exec tmux new-session -d -s manager -c "$HOME/work" "$CMD"
