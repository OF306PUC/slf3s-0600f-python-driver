"""
data_logger.py — Improved dual CSV+binary logger for SLF3S-0600F.

Replaces dual_logger from shdlc_driver.py with:
  - Experiment metadata comment block at the top of every CSV
  - monotonic sample_index as first data column
  - COMPLETE / INTERRUPTED footer sentinel on exit
  - 16-byte magic header in every binary file
  - dry_run_communication for hardware-free testing
  - verify_binary for offline binary-file integrity checks
"""
import datetime
import logging
import pathlib
import queue as queue_module
import socket
import struct
import time
import traceback

import core
from utils import ErrorCodes

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    """Return current UTC time as an ISO 8601 string with millisecond precision."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _write_csv_metadata(f, metadata: dict, start_utc: str) -> None:
    """
    Write the experiment metadata comment block to an open CSV file.

    Args:
        f: Open writable file object.
        metadata: Experiment metadata dict.
        start_utc: ISO 8601 start timestamp string.

    Returns:
        None.
    """
    hostname = f"raspberrypi-{metadata.get('raspberry_id', socket.gethostname())}"
    f.write(
        "# ── Experiment metadata ───────────────────────────────────────\n"
        f"# configuration   : {metadata.get('configuration', 'UNKNOWN')}\n"
        f"# configuration_name: {metadata.get('configuration_name', 'UNKNOWN')}\n"
        f"# experiment_rep  : {metadata.get('experiment_rep', 'UNKNOWN')}\n"
        f"# pump_lot        : {metadata.get('pump_lot', 'UNKNOWN')}\n"
        f"# fluid           : {metadata.get('fluid', 'UNKNOWN')}\n"
        f"# raspberry_id    : {metadata.get('raspberry_id', 'UNKNOWN')}\n"
        f"# f_ro_hz         : {metadata.get('f_ro_hz', 'UNKNOWN')}\n"
        f"# sampling_ms     : {metadata.get('sampling_ms', 'UNKNOWN')}\n"
        f"# start_utc       : {start_utc}\n"
        f"# hostname        : {hostname}\n"
        "# ─────────────────────────────────────────────────────────────\n"
    )


# ── main logger thread ────────────────────────────────────────────────────────

def dual_logger(
    csv_filename: str,
    bin_filename: str,
    queue,
    end_of_infusion_detector,
    logger,
    stop_event,
    sampling_interval: int = core.SAMPLING_INTERVAL,
    metadata: dict = None,
    interrupted_event=None,
) -> None:
    """
    Threaded CSV+binary logger for SHDLC sensor data.

    Reads (timestamp, flow_raw, temp_raw, flags_raw) tuples from *queue* and
    writes them to a CSV file (with metadata header and sample_index column)
    and a binary file (with 16-byte magic header).  Appends a footer sentinel
    on exit so truncated files are distinguishable from complete ones.

    Args:
        csv_filename: CSV output filename, placed inside core.DATA_DIR.
        bin_filename: Binary output filename, placed inside core.DATA_DIR.
        queue: threading.Queue of (timestamp, flow_raw, temp_raw, flags_raw).
        end_of_infusion_detector: EndOfInfusionDetector instance.
        logger: Logger instance for error/info logging.
        stop_event: threading.Event; logger stops when set and queue is empty.
        sampling_interval: Sampling interval in milliseconds.
        metadata: Dict with experiment metadata keys (experiment_id, etc.).
        interrupted_event: threading.Event set only by the signal handler;
            used to write INTERRUPTED instead of COMPLETE in the footer.

    Returns:
        None.

    Raises:
        Does not raise; exceptions are caught, logged, and stop_event is set.
    """
    if metadata is None:
        metadata = {}

    configuration = metadata.get("configuration", "UNKNOWN")
    experiment_rep = metadata.get("experiment_rep", "UNKNOWN")
    start_utc = _utc_now_iso()
    sample_index = 0
    integrated_volume = 0.0

    data_dir = pathlib.Path(core.DATA_DIR)
    csv_path = data_dir / csv_filename
    bin_path = data_dir / bin_filename

    try:
        data_dir.mkdir(parents=True, exist_ok=True)

        with csv_path.open("w") as f_csv, bin_path.open("wb") as f_bin:
            # Binary magic header
            f_bin.write(struct.pack(core.BIN_HEADER_FMT, core.BIN_MAGIC, core.BIN_VERSION))

            # CSV metadata block
            _write_csv_metadata(f_csv, metadata, start_utc)
            f_csv.write(
                "sample_index,UTC_Time,Flow_ul_min,Volume_uL,"
                "DeviceTemperature_degC,Flag_Air,Flag_High_Flow,"
                "Exp_Smoothing,Flags_Value\n"
            )

            while not stop_event.is_set() or not queue.empty():
                try:
                    item = queue.get(timeout=1.0)
                except queue_module.Empty:
                    continue

                timestamp, flow_raw, temp_raw, flags_raw = item
                flow_ul_min, temp_c = core.interpret_flow_temp_raw(flow_raw, temp_raw)
                flag_air, flag_high_flow, exp_smoothing, flags_value = (
                    core.interpret_flags_raw(flags_raw)
                )
                integrated_volume += (
                    flow_ul_min * core.MIN_TO_SEC * (sampling_interval / 1000.0)
                )

                flow_raw_s = core.u16_to_i16(flow_raw)
                temp_raw_s = core.u16_to_i16(temp_raw)

                if end_of_infusion_detector.update(
                    timestamp=timestamp, flow_ulmin=flow_ul_min
                ):
                    logger.log(
                        f"End-of-infusion detected. "
                        f"start_utc={start_utc}, volume_uL={integrated_volume:.2f}",
                        context={"integrated_volume_uL": integrated_volume},
                    )
                    stop_event.set()

                f_csv.write(
                    f"{sample_index},{timestamp},{flow_ul_min:.4f},"
                    f"{integrated_volume:.4f},{temp_c:.4f},"
                    f"{flag_air},{flag_high_flow},{exp_smoothing},{flags_value}\n"
                )
                f_bin.write(
                    struct.pack(
                        core.BIN_RECORD_FMT,
                        timestamp,
                        flow_raw_s,
                        temp_raw_s,
                        flags_raw,
                    )
                )

                sample_index += 1
                if sample_index % core.FLUSH_EVERY == 0:
                    f_csv.flush()
                    f_bin.flush()

    except Exception as exc:
        logger.log_error(
            ErrorCodes.LOGGER_FAILURE,
            f"Logger exception: {exc}",
            context=traceback.format_exc(),
        )
        stop_event.set()

    finally:
        interrupted = interrupted_event is not None and interrupted_event.is_set()
        status = "INTERRUPTED" if interrupted else "COMPLETE"
        try:
            with csv_path.open("a") as f:
                f.write(
                    f"# END experiment={configuration}_{experiment_rep} "
                    f"samples={sample_index} status={status}\n"
                )
        except Exception:
            pass


# ── dry-run simulation ────────────────────────────────────────────────────────

def dry_run_communication(
    queue,
    stop_logger_event,
    stop_main_thread_event,
    hours_to_log: float,
    sampling_interval: int,
) -> None:
    """
    Simulate sensor communication without physical hardware, for testing.

    Generates synthetic flow (~80 µL/min) and temperature (~25 °C) samples
    at the specified rate and pushes them onto *queue*, mirroring the timing
    contract of in_device_communication.

    Args:
        queue: threading.Queue to receive (timestamp, flow_raw, temp_raw, flags_raw).
        stop_logger_event: threading.Event; communication stops when set.
        stop_main_thread_event: threading.Event; set when simulation ends.
        hours_to_log: Duration to simulate in hours.
        sampling_interval: Sampling interval in milliseconds.

    Returns:
        None.

    Raises:
        Does not raise; queue-full warnings are printed to stdout.
    """
    seconds_to_log = 3600.0 * hours_to_log
    num_measurements = int(seconds_to_log * 1000 // sampling_interval)

    # Synthetic: ~80 µL/min (nominal 5 mL/hr), 25 °C, no flags
    flow_raw = int(max(-32768, min(32767, 80.0 * core.SCALE_FLOW)))
    temp_raw = int(max(-32768, min(32767, 25.0 * core.SCALE_TEMPERATURE)))
    flags_raw = 0

    count = 0
    deadline = time.time()

    while not stop_logger_event.is_set() and count < num_measurements:
        deadline += sampling_interval / 1000.0
        timestamp = time.time()
        item = (float(timestamp), flow_raw, temp_raw, flags_raw)
        try:
            queue.put(item, timeout=1.0)
        except queue_module.Full:
            log.warning("Queue full (dry-run) — sample dropped.")
        count += 1
        sleep_time = deadline - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)

    stop_logger_event.set()
    stop_main_thread_event.set()


# ── binary verification ───────────────────────────────────────────────────────

def verify_binary(bin_path) -> int:
    """
    Validate a binary log file and print a human-readable summary.

    Checks the magic header (warns if absent for legacy files), counts records,
    reports expected vs actual file size, and prints the first/last timestamp.

    Args:
        bin_path: Path to the .bin file (str or pathlib.Path).

    Returns:
        0 on success, 1 if the file does not exist.

    Raises:
        Does not raise; all errors are printed to stdout.
    """
    bin_path = pathlib.Path(bin_path)
    if not bin_path.exists():
        print(f"ERROR: file not found: {bin_path}")
        return 1

    raw = bin_path.read_bytes()
    offset = 0
    has_magic = False

    if len(raw) >= core.BIN_HEADER_SIZE:
        magic, version = struct.unpack_from(core.BIN_HEADER_FMT, raw, 0)
        if magic == core.BIN_MAGIC:
            has_magic = True
            offset = core.BIN_HEADER_SIZE
            print(f"Magic header     : OK  (version={version})")
        else:
            print("WARNING: no valid magic header — treating as legacy file (offset=0)")
    else:
        print("WARNING: file too small for magic header — treating as legacy file")

    records = []
    while offset + core.BIN_RECORD_SIZE <= len(raw):
        records.append(struct.unpack_from(core.BIN_RECORD_FMT, raw, offset))
        offset += core.BIN_RECORD_SIZE

    trailing = len(raw) - offset
    if trailing:
        print(f"WARNING: {trailing} trailing byte(s) — possible truncated record")

    total = len(records)
    header_size = core.BIN_HEADER_SIZE if has_magic else 0
    expected_size = header_size + total * core.BIN_RECORD_SIZE
    actual_size = len(raw)

    print(f"Records found    : {total}")
    print(f"Expected size    : {expected_size} bytes")
    print(f"Actual size      : {actual_size} bytes")
    if records:
        def _iso(ts):
            return datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).isoformat()

        print(f"First timestamp  : {records[0][0]:.3f}  ({_iso(records[0][0])})")
        print(f"Last  timestamp  : {records[-1][0]:.3f}  ({_iso(records[-1][0])})")

    return 0
