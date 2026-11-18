"""
csv_io.py — reading the logger's CSV files.

The logger writes self-describing files: a metadata header of `# key : value`
lines, the data rows, and a footer recording how acquisition ended. All three are
read here, and the footer matters as much as the data — it is the only signal that
distinguishes a run that finished from one whose process died mid-write.
"""
import pathlib

import pandas as pd


def parse_metadata(csv_path: pathlib.Path) -> dict:
    """Read the leading '# key : value' header block written by the logger."""
    metadata = {}
    with csv_path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped.startswith("#"):
                break
            if ":" in stripped:
                key, _, value = stripped[1:].partition(":")
                metadata[key.strip()] = value.strip()
    return metadata


def read_footer_status(csv_path: pathlib.Path) -> dict:
    """
    The logger's own verdict on how the acquisition ended.

    `dual_logger` appends `# END experiment=… samples=N status=COMPLETE|INTERRUPTED`
    in its `finally` block, so the footer is the one signal that distinguishes a run
    that finished from one whose process died — a truncated CSV is otherwise
    indistinguishable from a short experiment. A missing footer means the writer
    never reached that block at all.
    """
    last = ""
    with csv_path.open() as f:
        for line in f:
            if line.strip():
                last = line.strip()
    if not last.startswith("# END"):
        return {"footer_present": False, "status": "NO_FOOTER", "samples": None}
    status, samples = "UNKNOWN", None
    for token in last.split():
        if token.startswith("status="):
            status = token.split("=", 1)[1]
        elif token.startswith("samples="):
            try:
                samples = int(token.split("=", 1)[1])
            except ValueError:
                pass
    return {"footer_present": True, "status": status, "samples": samples}


def format_version(metadata: dict) -> int:
    """
    The CSV layout version: 0 for the legacy per-reading files (campaign 1, written
    before versioning), 2 for one row per 60 s window (slf3s/CSV_FORMAT.md).
    """
    try:
        return int(metadata.get("format_version", 0))
    except ValueError:
        return 0


def load_csv(csv_path: pathlib.Path, keep_air_rows: bool = False) -> pd.DataFrame:
    """
    The data rows as numbers.

    `keep_air_rows` is for format 2, where a window whose readings were ALL air
    has `Flow_ul_min = nan` by design: dropping those rows would erase the purge
    from the record and with it the air intervals. They are kept, and the air
    mask excludes them downstream exactly as it excludes legacy air samples.
    """
    df = pd.read_csv(csv_path, comment="#", thousands=",")
    if df.empty:
        raise ValueError(f"No data rows in {csv_path}")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["UTC_Time"] if keep_air_rows else ["UTC_Time", "Flow_ul_min"],
              inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def device_id(metadata: dict, dash: str = "—") -> str:
    """
    The logging host's identifier, across both header schemas.

    The logger wrote `raspberry_id` before it was versioned, and writes `device_id`
    from format version 1 on — the rename came with dropping the Raspberry-Pi-specific
    framing, since the driver is a generic Linux logger. Campaign 1's files are all
    pre-version, so both spellings are live data and will stay that way: those files
    are acquired and are not going to be rewritten.

    Resolved here rather than at each call site so the fallback cannot be
    half-applied — one reader honouring it and another not is exactly how a column
    ends up empty for half a table with no error anywhere.
    """
    value = metadata.get("device_id") or metadata.get("raspberry_id")
    return value if value else dash
