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

echo "== 3. guard (VM profile)"
cd $SRC
export UV_PYTHON_INSTALL_DIR=$BASE/uv-python UV_CACHE_DIR=$BASE/uv-cache UV_PROJECT_ENVIRONMENT=$BASE/venv
$BASE/bin/uv sync --locked --no-dev -q
PATH="$BASE/bin:$PATH" $BASE/venv/bin/spok1 guard install --profile vm
chown -R root:root $BASE && chmod -R go-w $BASE

echo "== 4. claude-manager.service"
install -m 0644 $SRC/vm/systemd/claude-manager.service /etc/systemd/system/claude-manager.service
systemctl daemon-reload
systemctl enable claude-manager.service >/dev/null
echo "✓ enabled (it starts once spok1 has run vm/bootstrap.sh and /login)"
echo
echo "Done. Next, as spok1: ssh spok1@spok1-vm, then: bash ~/spok1/vm/bootstrap.sh"
