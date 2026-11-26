#!/usr/bin/env bash
# spok1: delete EVERYTHING spok1 created in Azure (resource group rg-spok1). T3: run it by hand.
#
#   infra/azure-destroy.sh
#
# Safety: it only deletes a resource group tagged project=spok1, so it can never touch other
# projects (e.g. tgma-rg, argo-resource-group). You type the group's name to confirm.
# Take a backup first (Phase 9: vm/backup.sh); this cannot be undone.
set -euo pipefail

RG="${SPOK1_RG:-rg-spok1}"

az account show >/dev/null 2>&1 || { echo "✗ not signed in: run 'az login'"; exit 1; }
if [ "$(az group exists -n "$RG")" != "true" ]; then
  echo "· $RG doesn't exist: nothing to delete"; exit 0
fi
TAG="$(az group show -n "$RG" --query "tags.project" -o tsv)"
if [ "$TAG" != "spok1" ]; then
  echo "✗ $RG isn't tagged project=spok1 (tag: '${TAG:-none}'). Refusing."; exit 1
fi

echo "This permanently deletes resource group $RG and everything in it:"
az resource list -g "$RG" --query "[].{type:type, name:name}" -o table
echo
read -rp "Type the resource group name to confirm: " ANSWER
[ "$ANSWER" = "$RG" ] || { echo "✗ didn't match. Nothing deleted."; exit 1; }

az group delete -n "$RG" --yes
echo "✓ deleted $RG"
echo "· also remove the machine from Tailscale: https://login.tailscale.com/admin/machines"
