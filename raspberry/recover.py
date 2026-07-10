#!/usr/bin/env python3
"""
recover.py — Standalone recovery tool for SLF3S-0600F binary log files.

No dependency on any other project module; uses only the Python standard
library and numpy.

Usage:
    python3 recover.py DataLog.bin [--output recovered.csv]
"""
import argparse
import datetime
import pathlib
import struct
import sys

import numpy as np   # only external dependency allowed


# ── binary format constants (must match core.py) ──────────────────────────────

MAGIC        = b'SLF3SLOG\x00\x00\x00\x01'   # 12 bytes
HEADER_FMT   = '>12sI'                         # 12 magic + 4 version uint32
HEADER_SIZE  = struct.calcsize(HEADER_FMT)     # 16 bytes

RECORD_FMT   = '>dhhH'                         # big-endian: float64, int16, int16, uint16
RECORD_SIZE  = struct.calcsize(RECORD_FMT)     # 14 bytes

SCALE_FLOW   = 10.0
SCALE_TEMP   = 200.0

CSV_HEADER = (
    "sample_index,UTC_Time,Flow_ul_min,Volume_uL,"
    "DeviceTemperature_degC,Flag_Air,Flag_High_Flow,Exp_Smoothing,Flags_Value"
)

# Unit conversion for volume integration (trapezoidal)
# q [µL/min] / 60000 → [mL/s]; integrating over seconds gives mL
_UL_MIN_TO_ML_S = 1.0 / 60000.0


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso(ts: float) -> str:
    """Convert a Unix timestamp to an ISO 8601 UTC string."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _parse_flags(flags_raw: int):
    """
    Extract individual flag bits from the raw flags uint16.

    Args:
        flags_raw: Raw 16-bit flags value from the sensor.

    Returns:
        Tuple (air_in_line, high_flow, exp_smoothing) as ints (0 or 1).
    """
    air_in_line   = int(flags_raw & 0x0001)
    high_flow     = int(flags_raw & 0x0002)
    exp_smoothing = int(flags_raw & 0x0020)
    return air_in_line, high_flow, exp_smoothing


# ── core logic ────────────────────────────────────────────────────────────────

def read_binary(bin_path: pathlib.Path):
    """
    Read all records from a binary log file.

    Detects whether the file begins with the 16-byte magic header or is a
    legacy file (no header).  Stops and warns on any truncated record.

    Args:
        bin_path: Path to the .bin file.

    Returns:
        Tuple (records, has_magic, offset_after_header) where *records* is a
        list of (timestamp, flow_raw, temp_raw, flags_raw) tuples.

    Raises:
        SystemExit if the file does not exist or cannot be read.
    """
    if not bin_path.exists():
        print(f"ERROR: file not found: {bin_path}", file=sys.stderr)
        sys.exit(1)

    raw = bin_path.read_bytes()
    offset = 0
    has_magic = False

    if len(raw) >= HEADER_SIZE:
        magic, version = struct.unpack_from(HEADER_FMT, raw, 0)
        if magic == MAGIC:
            has_magic = True
            offset = HEADER_SIZE
            print(f"[INFO] Magic header detected  (version={version})")
        else:
            print("[WARN] No valid magic header — treating as legacy file (offset=0)")
    else:
        print("[WARN] File too small for magic header — treating as legacy file")

    records = []
    while offset + RECORD_SIZE <= len(raw):
        records.append(struct.unpack_from(RECORD_FMT, raw, offset))
        offset += RECORD_SIZE

    trailing = len(raw) - offset
    if trailing:
        print(
            f"[WARN] {trailing} trailing byte(s) at offset {offset} "
            f"— possible truncated record, stopped reading there."
        )

    return records, has_magic, offset


def write_csv(records, out_path: pathlib.Path) -> None:
    """
    Write recovered records to a CSV file with the standard column schema.

    Integrates flow using the trapezoidal rule to produce cumulative volume.
    Includes the sample_index column as the first column.

    Args:
        records: List of (timestamp, flow_raw, temp_raw, flags_raw) tuples.
        out_path: Destination CSV path.

    Returns:
        None.

    Raises:
        IOError on write failure.
    """
    n = len(records)
    if n == 0:
        print("[WARN] No records to write.")
        return

    # Convert raw values
    timestamps = np.array([r[0] for r in records], dtype=np.float64)
    flow_raw   = np.array([r[1] for r in records], dtype=np.int16)
    temp_raw   = np.array([r[2] for r in records], dtype=np.int16)
    flags_raw  = np.array([r[3] for r in records], dtype=np.uint16)

    flow_ul_min = flow_raw.astype(np.float64) / SCALE_FLOW
    temp_degc   = temp_raw.astype(np.float64) / SCALE_TEMP

    # Cumulative volume via trapezoidal integration [mL]
    dt = np.diff(timestamps, prepend=timestamps[0])
    dt[0] = 0.0
    q_ml_s = flow_ul_min * _UL_MIN_TO_ML_S
    volume_ml = np.cumsum(
        0.5 * (q_ml_s[:-1] + q_ml_s[1:]) * dt[1:]
        if n > 1 else np.array([0.0])
    )
    # prepend 0 for sample 0
    volume_ml = np.concatenate([[0.0], volume_ml]) if n > 1 else np.array([0.0])
    volume_ul = volume_ml * 1000.0   # mL → µL to match DataLog.csv convention

    with out_path.open("w") as f:
        f.write(CSV_HEADER + "\n")
        for i in range(n):
            air, hf, es = _parse_flags(int(flags_raw[i]))
            f.write(
                f"{i},{timestamps[i]},"
                f"{flow_ul_min[i]:.4f},{volume_ul[i]:.4f},"
                f"{temp_degc[i]:.4f},"
                f"{air},{hf},{es},{int(flags_raw[i])}\n"
            )


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Recover a SLF3S-0600F binary log file to CSV."
    )
    parser.add_argument("bin_file", help="Path to the .bin log file")
    parser.add_argument(
        "--output", "-o",
        default="recovered.csv",
        help="Output CSV path (default: recovered.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bin_path = pathlib.Path(args.bin_file)
    out_path = pathlib.Path(args.output)

    records, has_magic, end_offset = read_binary(bin_path)

    total = len(records)
    bytes_processed = end_offset if has_magic else end_offset  # same value
    actual_size = bin_path.stat().st_size

    if total == 0:
        print("No records recovered — output file not written.")
        sys.exit(1)

    write_csv(records, out_path)

    first_ts = records[0][0]
    last_ts  = records[-1][0]

    print(f"\n── Recovery summary ──────────────────────────────────")
    print(f"Input file       : {bin_path}  ({actual_size} bytes)")
    print(f"Records recovered: {total}")
    print(f"Bytes processed  : {bytes_processed}")
    print(f"First timestamp  : {first_ts:.3f}  ({_iso(first_ts)})")
    print(f"Last  timestamp  : {last_ts:.3f}  ({_iso(last_ts)})")
    print(f"Output written   : {out_path}")
    print(f"──────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
