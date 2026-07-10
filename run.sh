#!/bin/bash
set -euo pipefail

# ── load experiment parameters ────────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

# ── config ────────────────────────────────────────────────────────────────────
IMAGE="slf3s-logger"
DEVICE="${DEVICE:-/dev/ttyUSB0}"

# ── guards ────────────────────────────────────────────────────────────────────
if [ ! -e "$DEVICE" ]; then
    echo "ERROR: serial device $DEVICE not found." >&2
    exit 1
fi

# ── host-side output directories ──────────────────────────────────────────────
mkdir -p data logs

# ── build ─────────────────────────────────────────────────────────────────────
docker build -t "$IMAGE" .

# ── run ───────────────────────────────────────────────────────────────────────
# --privileged + --volume /dev:/dev mounts the live host /dev tree into the
# container, giving reliable access to /dev/ttyUSB0 (or whichever DEVICE is
# set). This is more robust on Raspberry Pi than --device + --group-add.
# NOTE: avoid exposing extra ports on this container given the wider /dev access.
docker run --rm -d \
    --name slf3s-logger \
    --privileged \
    --volume /dev:/dev \
    -v "$(pwd)/data:/app/raspberry/Temp" \
    -v "$(pwd)/logs:/app/raspberry/Logs" \
    -e TZ="${TZ:-America/Santiago}" \
    "$IMAGE" \
        --configuration    "${CONFIG:-UNKNOWN}" \
        --experiment-rep   "${EXPERIMENT_REP:-UNKNOWN}" \
        --pump-lot         "${PUMP_LOT:-UNKNOWN}" \
        --fluid            "${FLUID:-UNKNOWN}" \
        --hours-to-log     "${HOURS:-48}" \
        --sampling-ms      "${SAMPLING_MS:-1000}" \
        --raspberry-id     "${RASPBERRY_PI_ID:-UNKNOWN}"