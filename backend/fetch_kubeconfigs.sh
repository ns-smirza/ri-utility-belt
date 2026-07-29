#!/usr/bin/env bash
# Periodically refresh Rancher kubeconfigs for nonprod clusters whose embedded
# bearer tokens can expire. Generates a fresh kubeconfig per cluster via the
# Rancher management API and atomically writes it to $RANCHER_DIR/<name>.yaml.
#
# Run via cron (e.g. daily). The API token lives in $RANCHER_TOKEN_FILE
# (default ~/.rancher_prime_token, mode 600).
#
# To extend: add "cluster-id|filename.yaml" pairs to the CLUSTERS array.
set -euo pipefail

TOKEN_FILE="${RANCHER_TOKEN_FILE:-$HOME/.rancher_prime_token}"
RANCHER="${RANCHER_DIR:-$HOME/rancher}"
API="${RANCHER_API:-https://rancher.prime.iad0.netskope.com}"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "ERROR: token file $TOKEN_FILE not found" >&2
  exit 1
fi
TOKEN="$(cat "$TOKEN_FILE")"

# cluster-id | target filename (must match the dashboard's DISPLAY_NAMES key)
CLUSTERS=(
  "c-wfc98|stork-npe02-mp-iad0-nc4.yaml"
  "c-czc66|stork-fed1mp-iad0-nc1.yaml"
  "c-rsnsj|stork-perf01-mp-iad0-nc6.yaml"
)

for entry in "${CLUSTERS[@]}"; do
  cid="${entry%%|*}"
  fname="${entry##*|}"
  cfg="$(curl -s --max-time 30 -H "Authorization: Bearer $TOKEN" -X POST "$API/v3/clusters/$cid?action=generateKubeconfig")"
  yaml="$(printf '%s' "$cfg" | python3 -c 'import sys,json; print(json.load(sys.stdin)["config"], end="")')"
  if [ -z "$yaml" ] || ! printf '%s' "$yaml" | grep -q "apiVersion: v1"; then
    echo "ERROR: bad kubeconfig for $cid ($fname) — skipped" >&2
    continue
  fi
  tmp="$RANCHER/.${fname}.tmp"
  printf '%s' "$yaml" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$RANCHER/$fname"
  echo "refreshed $fname ($cid)"
done
