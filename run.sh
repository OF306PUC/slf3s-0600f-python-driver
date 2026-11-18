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
# DEVICE: the sensor cable's serial port. Default: the single entry under
# /dev/serial/by-id/. That name is fixed by the adapter's serial number, so it
# survives a USB disconnect/reconnect — /dev/ttyUSB0 does not (the kernel may
# bring the adapter back as ttyUSB1, and the logger could never reopen it).
if [ -z "${DEVICE:-}" ]; then
    shopt -s nullglob
    ids=(/dev/serial/by-id/*)
    shopt -u nullglob
    if [ "${#ids[@]}" -eq 1 ]; then
        DEVICE="${ids[0]}"
    else
        echo "ERROR: found ${#ids[@]} serial devices under /dev/serial/by-id/; set DEVICE" >&2
        echo "       to the sensor cable's entry:" >&2
        ls -l /dev/serial/by-id/ >&2 2>/dev/null || true
        exit 1
    fi
fi

# ── guards ────────────────────────────────────────────────────────────────────
# MANIFEST must be chosen explicitly. A default would let a study run go out
# unvalidated just because one host's .env missed a line; "none" is a deliberate,
# visible choice for a measurement that belongs to no study.
if [ -z "${MANIFEST:-}" ]; then
    echo "ERROR: set MANIFEST in .env — a file under manifests/ (e.g." >&2
    echo "       MANIFEST=example-sensor-zero.toml), or MANIFEST=none for a run outside" >&2
    echo "       any study (recorded as such, nothing validated)." >&2
    exit 1
fi
# MANIFESTS_DIR: where the experiment manifests live. Default: manifests/ next to
# this script. A study repository that carries the driver as a submodule points
# it at its own manifests (MANIFESTS_DIR=../manifests).
MANIFESTS_DIR="$(cd "$(dirname "$0")" && cd "${MANIFESTS_DIR:-manifests}" 2>/dev/null && pwd || echo "${MANIFESTS_DIR:-manifests}")"
if [ "$MANIFEST" != "none" ] && [ ! -f "$MANIFESTS_DIR/$MANIFEST" ]; then
    echo "ERROR: $MANIFESTS_DIR/$MANIFEST not found." >&2
    exit 1
fi
if [ ! -e "$DEVICE" ]; then
    echo "ERROR: serial device $DEVICE not found." >&2
    exit 1
fi

# ── host-side output directories ──────────────────────────────────────────────
mkdir -p data logs

# ── build ─────────────────────────────────────────────────────────────────────
# The driver version, recorded in every data file (header key driver_commit).
# "-dirty" when the driver's own files differ from that commit, so a file can
# never claim a version it was not written by.
DRIVER_DIR="$(cd "$(dirname "$0")" && pwd)"
DRIVER_COMMIT="$(git -C "$DRIVER_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ "$DRIVER_COMMIT" != "unknown" ] && \
   [ -n "$(git -C "$DRIVER_DIR" status --porcelain -- slf3s Dockerfile 2>/dev/null)" ]; then
    DRIVER_COMMIT="${DRIVER_COMMIT}-dirty"
fi
echo "── driver ${DRIVER_COMMIT} ──"
docker build --build-arg DRIVER_COMMIT="$DRIVER_COMMIT" -t "$IMAGE" "$DRIVER_DIR"

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
    --port             "$DEVICE"
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
MANIFEST_MOUNT=(-v "$MANIFESTS_DIR:/app/manifests:ro")

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
