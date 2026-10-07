# CSV Log Format — SLF3S-0600F Data Logger

Every `.csv` file written by `main.py` (`data_logger.dual_logger`) is
self-describing: a commented metadata block, one header row, one data row per
output window, and a closing footer. This document is the reference for every
field. The raw readings themselves are in the companion `.bin` file
(see `BINARY_FORMAT.md`); the CSV is a per-window summary of them.

**Current version: `format_version : 2`.**

```
# ── Experiment metadata ───────────────────────────────────────   ← 1. metadata
# format_version     : 2
# …
# ── Run fields ───────────────────────────────────────────────    ← 1b. run fields
# fluid              : NaCl_300mL
# …
# ─────────────────────────────────────────────────────────────
window_index,UTC_Time,Flow_ul_min,…                                 ← 2. column header
0,1791229016.237,74.6123,…                                          ← 3. one row per window
…
# END experiment=C1_rep_1 samples=4521 raw_samples=2712600 status=COMPLETE   ← 4. footer
```

---

## Parsing rules

- Every line starting with `#` is a comment. Metadata lines have the form
  `# <key> : <value>`; split on the **first** `:` only (values such as
  `start_utc` contain colons). Lines without a `:` are separators.
- Keys are padded with spaces for alignment; strip both key and value.
- Unknown keys must be ignored, not rejected: adding a metadata line does not
  bump the version. Removing or renaming one does.
- Missing numeric values are written as `nan`.
- With pandas: `pd.read_csv(path, comment="#")` reads the data rows and skips
  metadata and footer.

---

## 1. Metadata block

Always written, always in this order. `format_version` is first so a reader can
refuse an unknown version before interpreting anything else.

| # | Key | Example | Source | Meaning |
|---|---|---|---|---|
| 1 | `format_version` | `2` | `core.CSV_FORMAT_VERSION` | Version of this layout |
| 2 | `experiment` | `pump-flow` | manifest `[experiment].id` | Study identifier; `none` when launched without a manifest |
| 3 | `campaign` | `main` | manifest `[experiment].campaign` | Campaign within the study; `none` without a manifest |
| 4 | `configuration` | `C1` | `--configuration` | Condition code. **Only the pair (campaign, configuration) identifies a condition**: campaigns may reuse a code for a different setup |
| 5 | `configuration_name` | `Contiplex 40 cm (3 orificios laterales)` | manifest `[conditions]` | What the code physically is; equals the code without a manifest |
| 6 | `experiment_rep` | `rep_1` | `--experiment-rep` | Replicate identifier |
| 7 | `device_id` | `pi8` | `--device-id` | Logging host, free-form |
| 8 | `f_ro_hz` | `50.0` | derived | Sensor readout frequency, `1000 / sampling_ms` |
| 9 | `sampling_ms` | `20` | `core.SAMPLING_INTERVAL` | Interval between raw readings (ms). The program is the only reader of the sensor, so this is also the period each reading averages over |
| 10 | `aggregate_s` | `60` | `core.AGGREGATE_S` | Length of one output window (s) |
| 11 | `aggregation` | `block_mean_of_non_air_readings` | fixed | How a window's flow value is computed (see `Flow_ul_min`) |
| 12 | `start_utc` | `2026-10-05T19:36:25.224Z` | clock | Logger start, ISO 8601 UTC, ms precision |
| 13 | `hostname` | `lab-pi8` | `socket.gethostname()` | The machine's real hostname |
| 14 | `manifest` | `pump-flow-main.toml` | `--manifest` | Manifest file name; `none` without one |
| 15 | `manifest_sha256` | `0e3540…` | computed | SHA-256 of the manifest bytes, so a run can be tied to the exact vocabulary it was launched under |
| 16 | `end_of_infusion` | `on` | `--no-end-of-infusion` | `on`: the run stops when the flow stays near zero (end of the infusion). `off`: it runs for the full duration — static tests such as the sensor offset. Absent in files written before 2026-10-07 (read as `on`) |

### 1b. Run fields

Written after a `── Run fields ──` separator, **sorted alphabetically by key**.
Their keys and allowed values are defined by the manifest (`[fields]`), or are
whatever was passed with `--meta key=value` when there is no manifest. They
never reuse a key from the table above.

Fields used by the `pump-flow` study (campaign `main`):

| Key | Example | Meaning |
|---|---|---|
| `fluid` | `NaCl_300mL` | Fluid and fill volume (manifest default) |
| `pump_lot` | `LOTB` | Pump manufacturing lot, `^LOT[ABC]$` |
| `sensor` | `SEN1` | Physical sensor label, `^SEN[1-4]$` |
| `tanda` | `T03` | Block of simultaneous runs, `^T[0-9]{2}$` |
| `replaces` | `rep_2` | Present only on a replacement run: the replicate it replaces |

---

## 2–3. Columns

One row per `aggregate_s` window. Windows are aligned to the first reading of
the run: window *k* covers `[t0 + k·W, t0 + (k+1)·W)`. A window with no readings
produces **no row** — a gap in the stream shows as a gap in `window_index`, never
as an invented row.

| # | Column | Unit | Type | Definition |
|---|---|---|---|---|
| 1 | `window_index` | — | int | *k*, counted from the first reading |
| 2 | `UTC_Time` | s | float | **Unix epoch seconds** (despite the name), mean timestamp of the window's readings: the row sits at the centre of the data it summarises |
| 3 | `Flow_ul_min` | µL/min | float | Mean flow of the readings **without** the air flag. `nan` when every reading in the window had air |
| 4 | `Flow_std_ul_min` | µL/min | float | Sample standard deviation (n−1) of those same readings. `nan` with fewer than 2 |
| 5 | `Flow_min_ul_min` | µL/min | float | Minimum of those readings |
| 6 | `Flow_max_ul_min` | µL/min | float | Maximum of those readings |
| 7 | `Volume_uL` | µL | float | **Cumulative** volume since the start of the run, trapezoidal integral of **every** reading (air included) using the actual time between readings. Raw sensor output: not offset-corrected |
| 8 | `DeviceTemperature_degC` | °C | float | Mean sensor temperature. This is the sensor's own temperature — includes the self-heating of its heater — not the fluid's |
| 9 | `Flag_Air` | — | 0/1 | 1 if at least one reading in the window had the air-in-line flag |
| 10 | `Flag_High_Flow` | — | 0/1 | 1 if at least one reading had the high-flow flag |
| 11 | `N_samples` | — | int | Readings in the window (~3000 at 20 ms / 60 s) |
| 12 | `N_valid` | — | int | Readings without the air flag — those averaged in `Flow_ul_min` |
| 13 | `N_air` | — | int | Readings with the air flag |
| 14 | `N_high_flow` | — | int | Readings with the high-flow flag |
| 15 | `N_exp_smoothing` | — | int | Readings with sensor flag bit 5 set: the sensor had switched to exponential smoothing, so that reading covered only its last ~100 ms instead of its whole interval (datasheet §3.1) |

### Reading the counts

- `N_samples` well below `aggregate_s × 1000 / sampling_ms` → readings were lost
  (late or failed reads). The first and last windows are naturally partial.
- `N_air = N_samples` → the whole window had air, typically the purge at the
  start of a run; `Flow_ul_min` is then `nan` by design.
- `N_exp_smoothing` should be ~0. The first window has 1 (the first reading
  after start-up follows a 1 s pause). Persistent non-zero values mean the
  readout interval is too long — `check_readout.py` measures it on the binary.

---

## 4. Footer

```
# END experiment=<configuration>_<experiment_rep> samples=<rows> raw_samples=<readings> status=<COMPLETE|INTERRUPTED>
```

| Field | Meaning |
|---|---|
| `samples` | Number of **data rows** written — a reader can check it against the rows it parsed |
| `raw_samples` | Number of raw readings received (= records in the `.bin`) |
| `status` | `COMPLETE` when the run ended normally (duration reached or end of infusion detected); `INTERRUPTED` on SIGINT/SIGTERM |

**A file without a footer was not closed by the logger** — the process died — and
must not be treated as a complete run.

---

## Version history

| Version | Layout |
|---|---|
| none (legacy) | Files written before versioning, e.g. campaign 1 of the `pump-flow` study. **One row per reading**, columns `sample_index, UTC_Time, Flow_ul_min, Volume_uL, DeviceTemperature_degC, Flag_Air, Flag_High_Flow, Exp_Smoothing, Flags_Value`; metadata key `raspberry_id` instead of `device_id`; no `experiment`, `campaign` or `manifest` keys; footer `samples=` counts readings. Recognised by the absence of a `format_version` line |
| 1 | Defined but never used for real data |
| 2 | One row per window (this document). Introduced 2026-10-05 with a 100 ms readout; readout set to 20 ms (50 Hz) on 2026-10-06, the low end of the datasheet's recommended 50–200 Hz (the layout is unchanged — `sampling_ms` records the rate) |

`recover.py` rebuilds a **per-reading** CSV from a `.bin` file, with the legacy
column layout: it is the way to get the full-resolution stream as text, not a
version-2 file.
