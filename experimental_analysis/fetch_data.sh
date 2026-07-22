#!/bin/bash
set -euo pipefail

DEST="$(dirname "$0")"
REMOTE_DIR="~/Desktop/Sensirion-SLF3S-0600F-driver/data/"
HOSTS=("pi2" "pi9" "pi10")
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
