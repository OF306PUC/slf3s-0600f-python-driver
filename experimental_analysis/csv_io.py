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


def load_csv(csv_path: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, comment="#", thousands=",")
    if df.empty:
        raise ValueError(f"No data rows in {csv_path}")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["UTC_Time", "Flow_ul_min"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
