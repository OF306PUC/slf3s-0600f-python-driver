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
| **The application** | [`experimental_analysis/`](experimental_analysis/), `.env` experiment metadata, [`experiment_notes/`](experiment_notes/) | The **elastomeric-bomb** experiment protocol: the 8 condition codes (`C0a`–`C4`), pump-reuse tracking (`a`/`b`), and the analysis + alignment that turns raw logs into comparable flow profiles. |

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

The analysis is a package of single-responsibility modules; `main.py` is the entry
point and must be run **from inside `experimental_analysis/`** (the modules import
each other by name, the same convention `raspberry/` uses).

```bash
pip install -r experimental_analysis/requirements.txt
cd experimental_analysis

python3 main.py                          # the whole campaign, auto-discovered
python3 main.py Temp/C2/C2_rep_1.csv     # or specific runs
```

Options:

| Flag | Purpose | Default |
|---|---|---|
| `--output-dir` | root output directory | `results/` |
| `--offset-window-h` | hours from logging start used to measure the zero-flow offset | `2.0` |
| `--ma-window-min` | moving-average filter window, in minutes | `10` |
| `--plot-format` | **one** of `png` / `pdf` / `svg` | `png` |
| `--dpi` | raster DPI (200–300 recommended) | `250` |

With no CSV arguments it auto-discovers every `Temp/C*/*.csv` belonging to the 8
study conditions.

#### Module layout

| Module | Responsibility |
|---|---|
| `main.py` | entry point: what runs, in what order — no maths, no plotting |
| `config.py` | the 8 conditions and the declared methodology; one home for every declared parameter |
| `csv_io.py` | reading the logger's files: metadata header, data rows, footer status |
| `signal_processing.py` | air handling, offset correction, filtering, integration, event detection |
| `stats.py` | quantities computed from the data: noise + FFT, correlation, temperature, filter chain |
| `figures.py` | every plot; hue means condition, line style means replicate |
| `pipeline.py` | one CSV → one set of results, or `IncompleteRun` |
| `aggregate.py` | averaging a condition's replicates onto a common grid |
| `report.py` | `summary.md` |

---

## Experiment conditions

The study has **exactly 8 conditions**. This is the canonical list: `main.py`
**rejects** a `--configuration` that is not one of them, so a run cannot be
launched under an ad-hoc label. The same list lives in `raspberry/core.py`
(`CONFIG_NAMES`) and in `experimental_analysis/config.py` (`STUDY_CONDITIONS`).

| Code | Description | Reps |
|------|-------------|------|
| `C0a` | Sin catéter — bomba primera vez | 3 |
| `C0b` | Sin catéter — bomba segunda vez | 3 |
| `C0c` | Sin catéter — solución con bupivacaína (NaCl 240 mL + BuPi 60 mL) | 1 |
| `C1a` | Contiplex 40 cm (3 orificios laterales) — bomba primera vez | 3 |
| `C1b` | Contiplex 40 cm (3 orificios laterales) — bomba segunda vez | 3 |
| `C2`  | Contiplex 40 cm + filtro Perifix 0,2 µm | 3 |
| `C3`  | Contiplex 100 cm (3 orificios laterales) | 3 |
| `C4`  | (Contiplex C) Catéter peridural pediátrico (orificio terminal) | 3 |

The `a`/`b` suffix denotes **pump reuse** — `a` is a first-use pump, `b` a
second-use pump — so the same catheter type is compared against itself as the pump
deteriorates. `C0c` is the no-catheter condition run with the bupivacaine solution
instead of plain saline, and is a **single run** by design.

Per-condition field notes live in [`experiment_notes/`](experiment_notes/).

> **`C0_baseline` is not one of the 8.** It is a preliminary run predating this
> protocol: logged at 1 Hz instead of 10 s, with no metadata header and no
> `sample_index` column. Auto-discovery **skips it** so it
> cannot be silently mixed into the cross-condition comparison; pass its path
> explicitly if you want to analyse it on its own.

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
│   ├── main.py                 ← entry point: orchestrates the analysis
│   ├── config.py               ← the 8 conditions + declared methodology
│   ├── csv_io.py               ← metadata header, data rows, footer status
│   ├── signal_processing.py    ← air, offset, filtering, integration, events
│   ├── stats.py                ← noise + FFT, correlation, temperature, filter chain
│   ├── figures.py              ← the three curves, per run and per condition
│   ├── pipeline.py             ← one CSV → one set of results
│   ├── aggregate.py            ← averaging a condition's replicates
│   ├── report.py               ← summary.md
│   ├── utils.py utils_mpl.py   ← analysis + matplotlib helpers
│   ├── SLF3S-0600F_filters.py  ← filter frequency-response explorer
│   ├── fetch_data.sh           ← scp data from the Raspberry Pis
│   ├── requirements.txt
│   └── Temp/                   ← sample data for local analysis
│
└── experiment_notes/           ← APPLICATION: per-condition field notes (the 8 codes)
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
| `--sampling-ms` | `int` | Acquisition period — **locked**, only `10000` accepted | `10000` |
| `--configuration` | `str` | Condition code; **must** be one of the 8 (see table) | `UNKNOWN` → rejected |
| `--experiment-rep` | `str` | Replicate id, e.g. `rep_1` | `UNKNOWN` |
| `--pump-lot` | `str` | Pump manufacturing lot number | `UNKNOWN` |
| `--fluid` | `str` | Fluid description | `UNKNOWN` |
| `--raspberry-id` | `str` | Raspberry Pi identifier (fleet in use: 1, 2, 3, 8, 9, 10) | `UNKNOWN` |
| `--dry-run` | flag | Generate synthetic data without a sensor | — |
| `--verify-binary` | `str` | Validate an existing `.bin` and exit | — |

### Acquisition rate: `f_ro = f_s = 10 s`

The sensor's internal readout period (`f_ro`) and the serial polling period
(`f_s`) are **locked together at 10 s (0.1 Hz)** and cannot be set independently:

- The single source of truth is **`core.SAMPLING_INTERVAL`** in
  `raspberry/core.py`. `shdlc_driver` configures the sensor from that same value
  via `core.measurement_interval_bytes()`, so the two agree by construction —
  there is no second literal to fall out of sync.
- `main.py` **rejects** a `--sampling-ms` different from it, with an error naming
  the intervals the sensor supports (20, 50, 100, 1000, 10000, 60000 ms).
- To change the project's acquisition rate, edit `core.SAMPLING_INTERVAL` and
  nothing else.

**Why it is enforced rather than documented.** The CSV metadata records `f_ro_hz`
derived from the polling period. If polling ran faster than the sensor's readout,
consecutive polls would return the *same* internal measurement while that field
claimed a rate the sensor never produced. Nothing would fail — the logger would
keep writing rows — and a multi-day file nobody re-derives would carry a wrong
sampling rate. Failing at launch is cheaper than discovering it after 96 hours.

All events (INFO/WARNING/ERROR) go to stdout and `Logs/events.log` with ms
timestamps.
