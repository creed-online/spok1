#!/usr/bin/env bash
# spok1 VM: the user part. Run as spok1 (no sudo). Safe to rerun.
#
#   ssh spok1@spok1-vm
#   git clone https://github.com/creed-online/spok1.git ~/spok1   # first time only
#   bash ~/spok1/vm/bootstrap.sh
#
# Installs Claude Code + Bun, syncs the workspace, signs gh in (you paste a fine-grained
# token), clones the brain, runs `spok1 install`, and makes ~/work (the manager's folder).
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
ROOT="$HOME/spok1"
BRAIN_REPO="${SPOK1_BRAIN_REPO:-https://github.com/creed-online/spok1-brain.git}"
# The small VM times out when uv opens many parallel downloads: fewer at a time, longer timeout.
export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-2}" UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

[ "$(id -un)" = spok1 ] || { echo "✗ run as spok1"; exit 1; }
[ -z "${ANTHROPIC_API_KEY:-}" ] || { echo "✗ ANTHROPIC_API_KEY is set: it would bill the API"; exit 1; }

echo "== Claude Code"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
claude --version

echo "== Bun (the Discord plugin's runtime)"
if ! command -v bun >/dev/null; then
  if command -v unzip >/dev/null; then
    curl -fsSL https://bun.sh/install | bash >/dev/null
  else  # no unzip and no sudo: unpack the official release with Python
    mkdir -p ~/.bun/bin && tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/bun.zip" "https://github.com/oven-sh/bun/releases/latest/download/bun-linux-$(uname -m | sed s/x86_64/x64/).zip"
    python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$tmp/bun.zip" "$tmp"
    mv "$tmp"/bun-linux-*/bun ~/.bun/bin/bun && chmod +x ~/.bun/bin/bun
  fi
fi
bun --version
grep -q '.bun/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"' >> ~/.bashrc

echo "== spok1 workspace"
git -C "$ROOT" pull -q --ff-only
(cd "$ROOT" && uv sync --locked --no-dev -q)

echo "== GitHub (fine-grained token: spok1-brain + spok1-sandbox only)"
if ! gh auth status >/dev/null 2>&1; then
  echo "Paste the token (hidden), then press Enter:"
  read -rs GH_TOKEN_INPUT; echo
  printf '%s' "$GH_TOKEN_INPUT" | gh auth login --with-token --git-protocol https
  unset GH_TOKEN_INPUT
fi
gh auth setup-git
gh auth status 2>&1 | grep -E "Logged in|Token" | head -2
git config --global user.name >/dev/null || git config --global user.name "spok1-vm"
git config --global user.email >/dev/null || git config --global user.email "spok1-vm@users.noreply.github.com"

echo "== brain + install"
"$ROOT/.venv/bin/spok1" install --brain-repo "$BRAIN_REPO" --machine spok1-vm

mkdir -p ~/work ~/projects
echo
echo "Next:"
echo "  1. Discord bot token: mkdir -p ~/.claude/channels/discord && nano ~/.claude/channels/discord/.env"
echo "     (one line: DISCORD_BOT_TOKEN=...), then chmod 600 it"
echo "  2. Start the manager: sudo is not needed, ask the owner to run: sudo systemctl start claude-manager"
echo "     (or test by hand first: bash ~/spok1/vm/manager-start.sh --foreground)"
