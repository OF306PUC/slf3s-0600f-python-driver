# SLF3S-0600F data logger

A long-running logger for the **Sensirion SLF3S-0600F** liquid flow sensor, read
over SHDLC through a Sensirion SCC1-RS485/USB adapter, on any Linux host.
Unattended multi-day runs, self-describing output, and nothing specific to any
one experiment: what an experiment is lives in a manifest file, not in the code.

Used by the [elastomeric pump flow study](https://github.com/OF306PUC/elastomeric-pump-flow-study),
which includes this repository as a git submodule.

## What it does

- Reads the sensor at **50 Hz** (every 20 ms, the low end of the datasheet's
  recommended 50–200 Hz) as its **only reader**: the adapter's own continuous
  measurement is never started, so every reading is the sensor's arithmetic mean
  over its whole interval.
- Writes **two files per run**:
  - `.bin` — every raw reading (flow, temperature, flags) with its timestamp.
  - `.csv` — one row per 60 s window: mean of the air-free readings, SD, min, max,
    cumulative volume, sensor temperature and counts. A metadata header says what
    was run, with which manifest and which driver version (`driver_commit`); a
    footer says how it ended and what fraction of the expected readings arrived
    (`coverage`). Specification: [`slf3s/CSV_FORMAT.md`](slf3s/CSV_FORMAT.md) and
    [`slf3s/BINARY_FORMAT.md`](slf3s/BINARY_FORMAT.md).
- **Survives a USB disconnect**: after 5 s of failed reads it closes the port and
  reopens it every 5 s, so an unplugged or re-enumerated adapter costs a gap, not
  the run. Persistent failures are summarised in the log, never one line per read.
- **Validates a run before starting it** against an experiment manifest (TOML):
  allowed conditions, required per-run fields and their patterns, design rules
  and an optional run plan. A run that does not fit is refused.
- Optionally stops on its own when the flow stays near zero (end of an infusion,
  end of a pumped volume); `END_OF_INFUSION=off` disables that for static tests.

## Hardware

- Any Linux host with Docker (developed on Raspberry Pi 4, Raspberry Pi OS Bookworm).
- Sensirion SLF3S-0600F + SCC1-RS485/USB cable (FTDI; appears under
  `/dev/serial/by-id/usb-Sensirion_AG_Sensirion_RS485-USB_Cable_…`).
- An NTP-synchronised clock (`timedatectl`), so timestamps from several hosts line up.

## Quick start

```bash
git clone https://github.com/OF306PUC/slf3s-0600f-python-driver.git
cd slf3s-0600f-python-driver
cp .env.template .env      # edit: manifest, condition, replicate, fields
./run.sh                   # validates, builds, launches detached
docker logs -f slf3s-logger
```

`run.sh` first runs the logger with `--check` in the foreground — a rejected run
stops there with the reason — and then launches the container detached, so the
run survives the SSH session. Data land in `./data/`, logs in `./logs/`.
`docker stop slf3s-logger` ends a run cleanly (footer `status=INTERRUPTED`).

The serial port defaults to the single entry under `/dev/serial/by-id/`, a name
that survives reconnection; set `DEVICE` only if the host has several.

### How it runs on a host

It is a **Docker container** (`slf3s-logger`), not a system service:

| | |
|---|---|
| Start | `./run.sh` (validate → build → `docker run -d`) |
| Running? | `docker ps` · `docker logs -f slf3s-logger` · `tail -2 data/<RUN>.csv` |
| Stop by hand | `docker stop slf3s-logger` |
| Stops by itself | flow near zero for ~20 min (unless `END_OF_INFUSION=off`), or `HOURS` |
| After a reboot / power cut | **not restarted** — on purpose: relaunching with the same `CONFIG`/`EXPERIMENT_REP` would overwrite the run's files. Relaunch under a new replicate name. |
| Verify a finished run | `python3 slf3s/check_readout.py data/<RUN>.bin` → PASS, and footer `coverage` ≥ 0.99 |

## Experiment manifests

One TOML file per campaign of an experiment, under `manifests/`:

- [`manifests/TEMPLATE.toml`](manifests/TEMPLATE.toml) — every option, commented.
- [`manifests/example-sensor-zero.toml`](manifests/example-sensor-zero.toml) — a
  minimal real example: the zero-flow offset of a liquid-filled, capped sensor.

Check a run without touching the serial port:

```bash
cd slf3s
python3 main.py --check --manifest ../manifests/example-sensor-zero.toml \
    --configuration ZERO --experiment-rep rep_1 --meta sensor=SEN1
```

`MANIFEST=none` launches a run outside any experiment: nothing is validated and
the header says so. A project that keeps its manifests elsewhere sets
`MANIFESTS_DIR` (relative to this directory, or absolute).

## Tools (standard library only — they run on the acquisition host)

```bash
cd slf3s
python3 check_readout.py ../data/<RUN>.bin    # readout rate, gaps > 100 ms, coverage → PASS/WARN/FAIL
python3 offset_report.py ../data/<RUN>.bin    # zero-flow offset of a static run
python3 recover.py ../data/<RUN>.bin --output <RUN>_raw.csv   # every reading as CSV
python3 main.py --verify-binary ../data/<RUN>.bin
python3 main.py --dry-run --hours-to-log 0.01                 # synthetic data, no sensor
```

## Use as a submodule

```bash
git submodule add https://github.com/OF306PUC/slf3s-0600f-python-driver.git driver
MANIFESTS_DIR=../manifests driver/run.sh      # run from the project root
```

Every data file records `driver_commit`, so a measurement can be traced to the
exact driver version that produced it.

## Layout

```
run.sh  Dockerfile  .env.template
manifests/      TEMPLATE.toml, example-sensor-zero.toml
slf3s/
  main.py           entry point and argument checks
  shdlc_driver.py   acquisition thread: start-up, 50 Hz loop, reconnection
  data_logger.py    .csv/.bin writer, 60 s aggregation, footer
  manifest.py       manifest loader and run validation
  port.py interface.py shdlc_command.py i2c_command.py …  SHDLC protocol
  check_readout.py offset_report.py recover.py            offline tools
  CSV_FORMAT.md BINARY_FORMAT.md                          file formats
```
