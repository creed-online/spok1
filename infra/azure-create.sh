#!/usr/bin/env bash
# spok1: create the Azure VM (Phase 3). Safe to rerun: existing resources are kept.
#
#   infra/azure-create.sh          # dry run: shows the plan and the cost, changes nothing
#   infra/azure-create.sh --yes    # creates it (costs money: ~$6.65/month, see below)
#
# What it creates, in resource group rg-spok1 (tagged project=spok1):
#   VNet + subnet, an NSG with NO inbound rules, a Standard static public IP (outbound only),
#   VM spok1-vm: Standard_B2pts_v2 (2 ARM vCPU, 1 GB; free tier 750 h/month),
#   Ubuntu 24.04 arm64, 32 GB Standard SSD (E4), auto-shutdown OFF.
# Then joins Tailscale with a ONE-TIME key you paste (never stored, never in cloud-init).
# Run it yourself, in Git Bash, signed in with `az login`.
set -euo pipefail

RG="${SPOK1_RG:-rg-spok1}"
LOCATION="${SPOK1_LOCATION:-centralindia}"
VM="spok1-vm"
SIZE="Standard_B2pts_v2"
IMAGE="Canonical:ubuntu-24_04-lts:server-arm64:latest"
DISK_GB=32
ADMIN="azureuser"
KEY="$HOME/.ssh/spok1_vm_ed25519"   # break-glass admin key (normal access is Tailscale SSH)
TAGS="project=spok1 owner=spok1"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD_INIT="$HERE/cloud-init.yaml"

YES=0
[ "${1:-}" = "--yes" ] && YES=1

say()  { printf '%s\n' "$*"; }
step() { if [ "$YES" = 1 ]; then printf '\n✓ %s\n' "$*"; else printf 'would: %s\n' "$*"; fi; }
run()  { if [ "$YES" = 1 ]; then "$@"; fi; }

# ── preflight ──────────────────────────────────────────────────────────
command -v az >/dev/null || { say "✗ Azure CLI not found"; exit 1; }
az account show >/dev/null 2>&1 || { say "✗ not signed in: run 'az login'"; exit 1; }
SUB="$(az account show --query name -o tsv)"
say "subscription : $SUB"
say "resource grp : $RG ($LOCATION)"
say "vm           : $VM  $SIZE (2 ARM vCPU, 1 GB RAM)  Ubuntu 24.04 arm64  ${DISK_GB} GB Standard SSD"
say "network      : NSG with no inbound rules; Standard static public IP (outbound only)"
say "cost         : VM \$0 (free tier, 750 h/month)  + disk ~\$3.00 + IP ~\$3.65  ≈ \$6.65/month"
say "               (if the free hours don't apply: VM \$10.80 → ≈ \$17.45/month)"
say ""

if [ "$(az vm list-skus -l "$LOCATION" --size "$SIZE" --query "[0].restrictions[0].reasonCode" -o tsv)" != "" ]; then
  say "✗ $SIZE isn't available to this subscription in $LOCATION"; exit 1
fi

# ── admin key (break-glass) ────────────────────────────────────────────
if [ ! -f "$KEY" ]; then
  step "create admin SSH key $KEY"
  run ssh-keygen -t ed25519 -N "" -C "spok1-vm-admin" -f "$KEY" >/dev/null
fi

# ── resource group + network ───────────────────────────────────────────
if [ "$(az group exists -n "$RG")" = "true" ]; then
  say "· resource group $RG exists"
else
  step "create resource group $RG"
  run az group create -n "$RG" -l "$LOCATION" --tags $TAGS -o none
fi

if [ "$YES" = 1 ] && az network nsg show -g "$RG" -n "$VM-nsg" >/dev/null 2>&1; then
  say "· NSG exists"
else
  step "create NSG $VM-nsg (no inbound rules: Azure's defaults deny inbound from the internet)"
  run az network nsg create -g "$RG" -n "$VM-nsg" --tags $TAGS -o none
fi

if [ "$YES" = 1 ] && az network vnet show -g "$RG" -n "$VM-vnet" >/dev/null 2>&1; then
  say "· VNet exists"
else
  step "create VNet $VM-vnet 10.42.0.0/24 with subnet main (NSG attached)"
  run az network vnet create -g "$RG" -n "$VM-vnet" --address-prefixes 10.42.0.0/24 \
    --subnet-name main --subnet-prefixes 10.42.0.0/26 --network-security-group "$VM-nsg" --tags $TAGS -o none
fi

# ── VM ─────────────────────────────────────────────────────────────────
if [ "$YES" = 1 ] && az vm show -g "$RG" -n "$VM" >/dev/null 2>&1; then
  say "· VM $VM exists"
else
  step "create VM $VM (takes ~2 minutes)"
  run az vm create -g "$RG" -n "$VM" -l "$LOCATION" \
    --image "$IMAGE" --size "$SIZE" \
    --admin-username "$ADMIN" --ssh-key-values "$KEY.pub" --authentication-type ssh \
    --os-disk-size-gb "$DISK_GB" --storage-sku StandardSSD_LRS \
    --vnet-name "$VM-vnet" --subnet main --nsg "" --nsg-rule NONE \
    --public-ip-address "$VM-ip" --public-ip-sku Standard --public-ip-address-allocation static \
    --os-disk-delete-option Delete --nic-delete-option Delete \
    --custom-data "$CLOUD_INIT" --tags $TAGS -o none
fi

# ── wait for cloud-init ────────────────────────────────────────────────
step "wait for cloud-init to finish (packages, swap, gh, Tailscale, uv)"
if [ "$YES" = 1 ]; then
  az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
    --scripts "cloud-init status --wait >/dev/null; cat /var/log/spok1-cloud-init.done; free -m | head -3; swapon --show" \
    --query "value[0].message" -o tsv
fi

# ── Tailscale ──────────────────────────────────────────────────────────
step "join Tailscale as $VM with Tailscale SSH (you paste a one-time key)"
if [ "$YES" = 1 ]; then
  if az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
       --scripts "tailscale status >/dev/null 2>&1 && echo JOINED || echo NOT" --query "value[0].message" -o tsv | grep -q JOINED; then
    say "· already on your tailnet"
  else
    say ""
    say "Create a key: https://login.tailscale.com/admin/settings/keys → Generate auth key"
    say "  one-off (not reusable), expires in 1 hour, NOT ephemeral, pre-approved."
    read -rsp "Paste the Tailscale auth key (hidden): " TS_KEY; echo
    [ -n "$TS_KEY" ] || { say "✗ no key given"; exit 1; }
    az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
      --scripts "tailscale up --auth-key='$TS_KEY' --ssh --hostname=$VM && tailscale ip -4" \
      --query "value[0].message" -o tsv
    unset TS_KEY
  fi
fi

# ── budget alert (best effort; not every subscription type supports it) ──
step "budget: \$20/month (free); add the email alert in the portal (Cost Management → Budgets)"
if [ "$YES" = 1 ]; then
  az consumption budget create --budget-name spok1-monthly --amount 20 --category cost \
    --time-grain monthly --start-date "$(date -u +%Y-%m-01)" --end-date "$(date -u -d '+2 years' +%Y-%m-01 2>/dev/null || date -u -v+2y +%Y-%m-01)" \
    -o none 2>/dev/null && say "· budget created" \
    || say "! couldn't create a budget here: set one in the portal (Cost Management → Budgets)"
fi

say ""
if [ "$YES" = 1 ]; then
  IP="$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)"
  say "Done. Next:"
  say "  1. ssh spok1@$VM          (over Tailscale; no inbound port is open)"
  say "  2. check no inbound ports: nmap -Pn -p 1-65535 $IP   (or https://www.shodan.io/host/$IP)"
else
  say "Dry run only. Run 'infra/azure-create.sh --yes' to create it."
fi
