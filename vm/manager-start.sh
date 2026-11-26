#!/usr/bin/env bash
# The always-on manager: Claude Code with the Discord channel, in tmux session "manager".
# Started by claude-manager.service (as spok1). Watch it: ssh spok1@spok1-vm, tmux attach -t manager
# (detach: Ctrl+b then d). --foreground runs it in this terminal instead (for a first test).
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN  # manager uses /login only

CMD="claude --channels plugin:discord@claude-plugins-official"
mkdir -p "$HOME/work"
cd "$HOME/work"   # the manager's project root (the guard treats only this folder as "the project")

if [ "${1:-}" = "--foreground" ]; then
  exec $CMD
fi
if tmux has-session -t manager 2>/dev/null; then
  echo "manager already running"; exit 0
fi
exec tmux new-session -d -s manager -c "$HOME/work" "$CMD"
