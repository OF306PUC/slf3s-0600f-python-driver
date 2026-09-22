#!/bin/bash
set -euo pipefail

DEST="$(dirname "$0")"
REMOTE_DIR="~/Desktop/slf3s-0600f-python-driver/data/"
HOSTS="pi9"
USER="control"

mkdir -p "$DEST"

for host in "${HOSTS[@]}"; do
    echo "── Fetching from ${USER}@${host} ────────────────────────────"
    rsync -av --progress \
        --include="*.csv" \
        --include="*.bin" \
        --exclude="*" \
        "${USER}@${host}:${REMOTE_DIR}" \
        "$DEST/"
done

echo ""
echo "Done. Files written to: $DEST"
