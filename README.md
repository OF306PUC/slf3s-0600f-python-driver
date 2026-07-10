# Elastomeric Infusion Pump — Flow Characterization

**An application for measuring and comparing the flow-rate behavior of elastomeric infusion pumps ("bombas") across catheter configurations — built on a general Sensirion SLF3S-0600F data logger.**

**Runtime:** Raspberry Pi (Raspbian Bookworm) · **Fluidics sensor:** Sensirion SLF3S-0600F over SHDLC/RS485

---

## What this is

Elastomeric infusion pumps deliver a drug at a nominally constant rate as an
elastomeric reservoir contracts. In practice the delivered flow rate drifts over
the hours-to-days of an infusion and depends on the **catheter** and **filter**
in line, and on **pump reuse**. This application characterizes that behavior:

1. **Acquire** — an unattended, multi-hour/multi-day flow-rate logger runs on a
   Raspberry Pi next to the pump, recording flow and temperature to durable,
   self-describing files.
2. **Analyze** — a post-experiment pipeline detects infusion onset, computes the
   flow profile *q(t)*, dispensed volume, effective duration, and overlays runs
   across catheter configurations for comparison.

### Two layers: a general driver + the application

| Layer | Path | Scope |
|-------|------|-------|
| **General driver** | [`raspberry/`](raspberry/) | A **reusable** long-running data logger for the Sensirion **SLF3S-0600F** flow sensor via SHDLC over RS485/USB. It is *not* specific to infusion pumps — use it for any Pi-based liquid-flow logging: dual-threaded acquisition, self-describing CSV + recoverable binary logs, ring-buffer error context, clean SIGTERM shutdown. |
| **The application** | [`experimental_analysis/`](experimental_analysis/), `.env` experiment metadata, [`experiment_notes/`](experiment_notes/) | The **elastomeric-bomb** experiment protocol: catheter configuration codes (C0–C4), pump-reuse tracking (a/b), and the analysis + alignment that turns raw logs into comparable flow profiles. |

If you only need the sensor logger, `raspberry/` stands alone. The rest of this
repo is the infusion-pump study built on top of it.

---

## Hardware requirements

- Raspberry Pi (developed on **Raspbian Bookworm**).
- Sensirion **SLF3S-0600F** liquid-flow sensor.
- Sensirion **SCC1-USB / SCC1-RS485** adapter (FTDI) → appears as `/dev/ttyUSB0`.
- Host user in the `dialout` group (`sudo usermod -aG dialout $USER`; re-login).
- NTP-synced system clock (`sudo timedatectl set-ntp true`) for meaningful timestamps.

Verify the adapter:
```bash
ls /dev/ttyUSB*        # expect /dev/ttyUSB0
dmesg | grep ttyUSB    # optional
```
> On Raspbian Bookworm the `ftdi_sio` driver loads automatically — no manual binding.

---

## Quickstart

### 1. Clone
```bash
git clone <your-new-repo-url>
cd <repo>
```

### 2. Configure the experiment
Every run is described by an `.env` file (injected into the container at runtime,
so the exact parameters that ran are always visible in the logs):
```bash
cp .env.template .env
nano .env    # CONFIG, EXPERIMENT_REP, PUMP_LOT, FLUID, HOURS, RASPBERRY_ID, ...
```

### 3. Acquire (Docker)
The logger runs **detached** inside a Docker container, so acquisition survives
SSH disconnects. Data persists on the host in `./data/` and `./logs/`.
```bash
bash run.sh                    # build (if needed) + launch detached
docker logs -f slf3s-logger    # follow live events
docker ps                      # status
docker stop slf3s-logger       # clean stop → writes INTERRUPTED footer
```
> `run.sh` passes `--privileged --volume /dev:/dev` for reliable `/dev/ttyUSB0` access.

Recorded data:
```bash
ls ./data/    # {CONFIG}_{REP}.csv   {CONFIG}_{REP}.bin
ls ./logs/    # events.log  logs.txt  error_logs.txt
```

### 4. Analyze
```bash
pip install -r experimental_analysis/requirements.txt

python3 experimental_analysis/analyse.py data/C2_rep_1.csv [more.csv ...] \
    --output-dir results/ \
    --zero-drift-min 60 \
    --empty-pump-min 30 \
    --align onset
```
With **no CSV arguments**, `analyse.py` auto-discovers every `Temp/C*/*.csv`
under `experimental_analysis/Temp/`.

---

## Catheter configurations

The application compares flow behavior across these in-line configurations:

| Code | Description |
|------|-------------|
| `C0`  | Sin catéter (línea base) |
| `C1a` | Contiplex 40 cm (3 orificios laterales) — bomba primera vez |
| `C1b` | Contiplex 40 cm (3 orificios laterales) — bomba segunda vez |
| `C2`  | Contiplex 40 cm + filtro Perifix 0,2 µm |
| `C3`  | Contiplex 100 cm (3 orificios laterales) |
| `C4`  | (Contiplex C) Catéter peridural pediátrico (orificio terminal) |

`C1a`/`C1b` share a catheter type; `a` is a first-use pump and `b` a second-use
pump, to evaluate deterioration with reuse. Per-configuration field notes live in
[`experiment_notes/`](experiment_notes/).

---

## Analysis outputs & alignment

Per-experiment outputs land in `results/{CONFIG}_{REP}/`:

| File | Content |
|------|---------|
| `stats.json` | Zero-drift µ/σ/threshold, **t_onset**, dispensed volume V_disp, effective duration T_eff, cross-correlation lag & r_max |
| `{NAME}_flow_rate.pdf/png` | Flow rate q(t) with 1-hr moving average, ±5 % band, nominal reference |
| `{NAME}_temperature.pdf/png` | Device temperature T(t) |
| `{NAME}_volume.pdf/png` | Cumulative dispensed volume V(t) vs corrected nominal |
| `{NAME}_correlation.pdf/png` | Cross-correlation R_qT(ℓ) between flow and temperature |

Figures are written as **both PDF and PNG** by default; `--plot-format {png,pdf,both}`
controls this.

**Comparison overlay.** With more than one run, a `comparison_q_profiles.pdf/png`
overlay is produced. Because runs aren't started in parallel (the logger is
launched some time before infusion actually begins), curves are re-anchored via
`--align`:

| `--align` | Behaviour |
|-----------|-----------|
| `none`  | Raw logging-start timebase, no offset correction. |
| `onset` *(default)* | Each run shifted so **t = 0 at detected infusion onset** (first sustained crossing of `µ₀ + 3σ` on raw flow); zero-drift offset `µ₀` subtracted. |
| `xcorr` | `onset`, then a residual per-run lag refined by cross-correlating the *derivative* of each q(t) against the longest run (robust to the flat plateau). |

---

## Offline tools (no sensor required)

```bash
# Integrity-check a binary log (record count, size, first/last timestamp)
python3 raspberry/main.py --verify-binary data/C2_rep_1.bin

# Recover any .bin (incl. legacy, header-less) back to CSV
python3 raspberry/recover.py data/C2_rep_1.bin --output recovered.csv
```
See [`raspberry/BINARY_FORMAT.md`](raspberry/BINARY_FORMAT.md) for the format spec.

---

## Project structure

```
<repo>/
├── README.md
├── Dockerfile
├── run.sh                      ← build & launch the logger container (detached)
├── .env.template               ← experiment parameters (copy → .env)
├── .dockerignore
│
├── raspberry/                  ← GENERAL SLF3S-0600F driver + logger
│   ├── main.py                 ← entry point
│   ├── data_logger.py          ← CSV + binary logger (metadata, sample_index, footer)
│   ├── shdlc_driver.py         ← device communication thread
│   ├── core.py                 ← constants, scaling, binary format spec
│   ├── utils.py                ← Logger, MeasurementRingBuffer, onset detector
│   ├── recover.py              ← standalone binary→CSV recovery tool
│   ├── BINARY_FORMAT.md        ← binary file-format documentation
│   ├── interface.py port.py shdlc_command.py i2c_command.py
│   ├── sensor_info.py serial_frame_builder.py command.py
│   └── requirements.txt
│
├── experimental_analysis/      ← APPLICATION: post-experiment analysis
│   ├── analyse.py              ← onset detection, flow profiles, comparison overlay
│   ├── utils.py utils_mpl.py   ← analysis + matplotlib helpers
│   ├── SLF3S-0600F_filters.py  ← filter frequency-response explorer
│   ├── requirements.txt
│   └── Temp/                   ← sample data for local analysis
│
└── experiment_notes/           ← APPLICATION: per-configuration field notes (C0–C4)
```

Generated at runtime (Docker host mounts): `./data/` (`{CONFIG}_{REP}.csv/.bin`)
and `./logs/` (`events.log`, `logs.txt`, `error_logs.txt`).

---

## Acquisition command-line arguments

| Argument | Type | Description | Default |
|---|---|---|---|
| `--port` | `str` | Serial port for SCC1-RS485 / SCC1-USB | `/dev/ttyUSB0` |
| `--baudrate` | `int` | Serial baud rate | `115200` |
| `--slave-address` | `int` | SHDLC slave address | `0x00` |
| `--hours-to-log` | `float` | Acquisition duration (hours) | `48` |
| `--sampling-ms` | `int` | Serial polling interval (ms) | `500` |
| `--configuration` | `str` | Catheter configuration code (see table) | `UNKNOWN` |
| `--experiment-rep` | `str` | Replicate id, e.g. `rep_1` | `UNKNOWN` |
| `--pump-lot` | `str` | Pump manufacturing lot number | `UNKNOWN` |
| `--fluid` | `str` | Fluid description | `UNKNOWN` |
| `--raspberry-id` | `str` | Raspberry Pi identifier | `UNKNOWN` |
| `--dry-run` | flag | Generate synthetic data without a sensor | — |
| `--verify-binary` | `str` | Validate an existing `.bin` and exit | — |

> `--sampling-ms` is the serial polling interval; the sensor's internal
> measurement rate is set separately in `raspberry/shdlc_command.py`
> (`ShdlcStartContinuousMeasurement._MEASUREMENT_INTERVAL_X_MS`). All events
> (INFO/WARNING/ERROR) go to stdout and `Logs/events.log` with ms timestamps.
