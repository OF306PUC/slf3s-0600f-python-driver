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
# MANIFEST must be chosen explicitly. A default would let a study run go out
# unvalidated just because one host's .env missed a line; "none" is a deliberate,
# visible choice for a measurement that belongs to no study.
if [ -z "${MANIFEST:-}" ]; then
    echo "ERROR: set MANIFEST in .env — a file under manifests/ (e.g." >&2
    echo "       MANIFEST=pump-flow-main.toml), or MANIFEST=none for a run outside" >&2
    echo "       any study (recorded as such, nothing validated)." >&2
    exit 1
fi
if [ "$MANIFEST" != "none" ] && [ ! -f "manifests/$MANIFEST" ]; then
    echo "ERROR: manifests/$MANIFEST not found." >&2
    exit 1
fi
if [ ! -e "$DEVICE" ]; then
    echo "ERROR: serial device $DEVICE not found." >&2
    exit 1
fi

# ── host-side output directories ──────────────────────────────────────────────
mkdir -p data logs

# ── build ─────────────────────────────────────────────────────────────────────
docker build -t "$IMAGE" .

# ── arguments shared by the check and the real launch ─────────────────────────
ARGS=(
    --configuration    "${CONFIG:-UNKNOWN}"
    --experiment-rep   "${EXPERIMENT_REP:-UNKNOWN}"
    --hours-to-log     "${HOURS:-48}"
    # f_ro = f_s = 20 ms (50 Hz); the CSV stores one 60 s block mean per row. main.py
    # rejects any other readout; the single source of truth is
    # core.SAMPLING_INTERVAL. An older environment still setting SAMPLING_MS=10000
    # is rejected by the pre-launch check below — remove it or set it to 20.
    --sampling-ms      "${SAMPLING_MS:-20}"
    --device-id        "${DEVICE_LOGGER_ID:-UNKNOWN}"
)
if [ "$MANIFEST" != "none" ]; then ARGS+=(--manifest "/app/manifests/$MANIFEST"); fi
# Shorthands for the two fields existing launch environments already set. Any
# other per-run field goes in META as space-separated key=value pairs.
if [ -n "${PUMP_LOT:-}" ]; then ARGS+=(--pump-lot "$PUMP_LOT"); fi
if [ -n "${FLUID:-}" ];    then ARGS+=(--fluid    "$FLUID");    fi
for kv in ${META:-}; do ARGS+=(--meta "$kv"); done
# Static tests (offset with a capped sensor) must not stop when the flow reaches
# zero: END_OF_INFUSION=off disables the detector.
if [ "${END_OF_INFUSION:-on}" = "off" ]; then ARGS+=(--no-end-of-infusion); fi

# Manifests are MOUNTED, not baked into the image: editing one or adding a new
# experiment must not require a rebuild.
MANIFEST_MOUNT=(-v "$(pwd)/manifests:/app/manifests:ro")

# ── validate in the foreground FIRST ──────────────────────────────────────────
# The real launch below is detached and --rm. If the logger rejected its
# arguments there, the container would exit, be removed together with its logs,
# and `docker run -d` would still have reported success — the operator would see
# a launch and get no data. Running the same arguments with --check here makes a
# rejection visible, and stops before anything is started.
echo "── checking run against ${MANIFEST} ──"
if ! docker run --rm "${MANIFEST_MOUNT[@]}" "$IMAGE" "${ARGS[@]}" --check; then
    echo "ERROR: run rejected — nothing was launched." >&2
    exit 1
fi

# ── run ───────────────────────────────────────────────────────────────────────
# --privileged + --volume /dev:/dev mounts the live host /dev tree into the
# container, giving reliable access to /dev/ttyUSB0 (or whichever DEVICE is
# set). This is more robust on single-board Linux hosts than --device +
# --group-add, which depends on the device node's group surviving a reboot.
# NOTE: avoid exposing extra ports on this container given the wider /dev access.
docker run --rm -d \
    --name slf3s-logger \
    --privileged \
    --volume /dev:/dev \
    -v "$(pwd)/data:/app/slf3s/Temp" \
    -v "$(pwd)/logs:/app/slf3s/Logs" \
    "${MANIFEST_MOUNT[@]}" \
    -e TZ="${TZ:-America/Santiago}" \
    "$IMAGE" "${ARGS[@]}"
