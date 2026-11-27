#!/usr/bin/env bash
# spok1 VM: the admin part. Run ONCE (and after updates) as azureuser, with sudo:
#
#   ssh azureuser@spok1-vm      # the break-glass admin (key ~/.ssh/spok1_vm_ed25519)
#   curl -fsSL https://raw.githubusercontent.com/creed-online/spok1/main/vm/root-setup.sh | sudo bash
#   (or: sudo bash /home/spok1/spok1/vm/root-setup.sh  after reading it)
#
# It runs as root, so it NEVER executes code from spok1's home (agents can write there).
# It clones its own copy of the repo into /opt/spok1/src (root-owned) and runs from that.
#
# What it does (safe to rerun):
#   1. installs unzip (for Bun) and uv into /opt/spok1/bin
#   2. installs the guard as root: /opt/spok1/{python,guard}, /etc/spok1/guardrails.toml,
#      /etc/claude-code/managed-settings.json with the VM profile
#   3. installs claude-manager.service (the always-on manager, as user spok1)
set -euo pipefail

REPO="${SPOK1_REPO:-https://github.com/creed-online/spok1.git}"
REF="${SPOK1_REF:-main}"
BASE=/opt/spok1
SRC=$BASE/src
# The small VM times out when uv opens many parallel downloads: fewer at a time, longer timeout.
export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-2}" UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

[ "$(id -u)" = 0 ] || { echo "✗ run with sudo"; exit 1; }
id spok1 >/dev/null 2>&1 || { echo "✗ user spok1 missing (cloud-init didn't run?)"; exit 1; }

echo "== 0. prefer IPv4 (the VNet has no IPv6 route; uv waits on IPv6 addresses until it times out)"
grep -q '^precedence ::ffff:0:0/96  100' /etc/gai.conf 2>/dev/null || echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
echo "✓ /etc/gai.conf"
# Diagnosed 2026-11-26: at MTU 1500, large TLS ClientHellos (rustls/uv, post-quantum key share)
# were dropped on the path to PyPI's CDN; curl/Python (small hellos) worked. MTU 1400 fixes it.
ip link set dev eth0 mtu 1400
cat > /etc/systemd/system/spok1-mtu.service <<'UNIT'
[Unit]
Description=spok1: MTU 1400 on eth0 (large TLS handshakes were dropped at 1500)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ip link set dev eth0 mtu 1400
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable spok1-mtu.service >/dev/null
echo "✓ eth0 MTU 1400 (now and at every boot)"

echo "== 1. packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q unzip git >/dev/null
mkdir -p $BASE/bin
if [ ! -x $BASE/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=$BASE/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
echo "✓ uv $($BASE/bin/uv --version | cut -d' ' -f2)"

echo "== 2. root-owned copy of spok1 ($REF)"
if [ -d $SRC/.git ]; then
  git -C $SRC fetch -q origin "$REF" && git -C $SRC checkout -q --detach FETCH_HEAD
else
  git clone -q --branch "$REF" "$REPO" $SRC
fi
chown -R root:root $BASE && chmod -R go-w $BASE
echo "✓ $SRC at $(git -C $SRC rev-parse --short HEAD)"
# If this run started from an older copy of this script, continue with the version just fetched.
if [ -z "${SPOK1_SETUP_REEXEC:-}" ] && ! cmp -s "$0" "$SRC/vm/root-setup.sh" 2>/dev/null; then
  echo "· continuing with the updated root-setup.sh"
  SPOK1_SETUP_REEXEC=1 exec bash "$SRC/vm/root-setup.sh"
fi

echo "== 3. guard (VM profile)"
cd $SRC
export UV_PYTHON_INSTALL_DIR=$BASE/uv-python UV_CACHE_DIR=$BASE/uv-cache UV_PROJECT_ENVIRONMENT=$BASE/venv
$BASE/bin/uv sync --locked --no-dev -q
PATH="$BASE/bin:$PATH" $BASE/venv/bin/spok1 guard install --profile vm
chown -R root:root $BASE && chmod -R go-w $BASE

echo "== 4. agentd user, state, settings, sudo wrapper"
id spok1d >/dev/null 2>&1 || useradd --system --home-dir /var/lib/spok1d --shell /usr/sbin/nologin spok1d
usermod -aG spok1 spok1d          # read spok1's brain (job files, registry); not its 0600 secrets
install -d -m 0700 -o spok1d -g spok1d /var/lib/spok1d
install -d -m 0755 /etc/spok1
if [ ! -f /etc/spok1/agentd.env ]; then
  install -m 0600 -o spok1d -g spok1d /dev/null /etc/spok1/agentd.env
  cat > /etc/spok1/agentd.env <<'ENV'
# spok1 agentd settings. SECRETS: readable only by spok1d (0600). Edit with: sudo nano /etc/spok1/agentd.env
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_OWNER_ID=
HEALTHCHECK_URL=
CLAUDE_CODE_OAUTH_TOKEN=
MEM0_API_KEY=
TIMEZONE=Asia/Kolkata
QUIET_HOURS=23:00-07:30
EOD_REPORT=19:00
ENV
  chown spok1d:spok1d /etc/spok1/agentd.env && chmod 0600 /etc/spok1/agentd.env
fi
install -m 0755 -o root -g root $SRC/vm/spok1-runas $BASE/bin/spok1-runas
cat > /etc/sudoers.d/spok1-agentd <<'SUDO'
# agentd may run exactly one root-owned wrapper as spok1 (see /opt/spok1/bin/spok1-runas)
Defaults:spok1d env_keep += "CLAUDE_CODE_OAUTH_TOKEN"
spok1d ALL=(spok1) NOPASSWD: /opt/spok1/bin/spok1-runas
SUDO
chmod 0440 /etc/sudoers.d/spok1-agentd
visudo -cf /etc/sudoers.d/spok1-agentd >/dev/null && echo "✓ spok1d, /var/lib/spok1d, /etc/spok1/agentd.env, sudo wrapper"
install -m 0644 $SRC/vm/systemd/agentd.service /etc/systemd/system/agentd.service

echo "== 4b. approval broker folders + trash"
# requests: the guard (as spok1) writes here; agentd (spok1d) reads and cleans up (setgid + sticky)
install -d -m 0755 /var/spool/spok1
install -d -m 3770 -o spok1d -g spok1 /var/spool/spok1/requests
# decisions: ONLY agentd can write; spok1 (the agents) can only read
install -d -m 0755 -o spok1d -g spok1d /var/lib/spok1-approvals
install -d -m 0700 -o spok1d -g spok1d /var/lib/spok1d/backups
DEBIAN_FRONTEND=noninteractive apt-get install -y -q trash-cli >/dev/null
cat > /etc/cron.daily/spok1-trash <<'CRON'
#!/bin/sh
# empty spok1's trash of files deleted more than 7 days ago
sudo -u spok1 -H trash-empty -f 7 >/dev/null 2>&1 || true
CRON
chmod 0755 /etc/cron.daily/spok1-trash
echo "✓ broker spool + approvals, trash-cli (7-day purge)"

echo "== 5. claude-manager.service"
install -m 0644 $SRC/vm/systemd/claude-manager.service /etc/systemd/system/claude-manager.service
systemctl daemon-reload
systemctl enable claude-manager.service agentd.service >/dev/null
echo "✓ enabled claude-manager + agentd (agentd starts working once /etc/spok1/agentd.env is filled in)"
echo
echo "Done. Next, as spok1: ssh spok1@spok1-vm, then: bash ~/spok1/vm/bootstrap.sh"
