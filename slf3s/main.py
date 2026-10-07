from data_logger import dual_logger, dry_run_communication, verify_binary
from shdlc_driver import in_device_communication
from utils import EndOfInfusionDetector, Logger, MeasurementRingBuffer

import argparse
import logging
import signal
import sys
import core
import manifest
import queue as queue_module
import threading
import time

log = logging.getLogger(__name__)

stop_logger_event = threading.Event()
stop_main_thread_event = threading.Event()
interrupted_event = threading.Event()


def _setup_logging() -> None:
    """Configure root logger: INFO+ to stdout AND Logs/events.log."""
    import pathlib
    log_dir = pathlib.Path(core.LOGGER_PATH)
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.FileHandler(log_dir / "events.log")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def handle_shutdown(signum, frame):
    """Signal handler for SIGINT / SIGTERM."""
    log.info("Shutdown signal received — stopping threads.")
    interrupted_event.set()
    stop_logger_event.set()
    stop_main_thread_event.set()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sensirion SLF3S-0600F SHDLC data logger"
    )
    # Hardware
    parser.add_argument("--port", type=str, default=core.SERIAL_PORT,
        help=f"Serial port (default: {core.SERIAL_PORT})")
    parser.add_argument("--baudrate", type=int, default=core.BAUDRATE,
        help=f"Baud rate (default: {core.BAUDRATE})")
    parser.add_argument("--slave-address", type=int, default=core.SHDLC_SLAVE_ADDRESS,
        help=f"SHDLC slave address (default: {core.SHDLC_SLAVE_ADDRESS:#04x})")

    # Logging parameters
    parser.add_argument("--hours-to-log", type=float, default=core.HOURS_TO_LOG,
        help=f"Logging duration in hours (default: {core.HOURS_TO_LOG})")
    parser.add_argument("--sampling-ms", type=int, default=core.SAMPLING_INTERVAL,
        help=f"Sampling interval in ms. Locked to the sensor readout period "
             f"(f_ro = f_s); only {core.SAMPLING_INTERVAL} is accepted "
             f"(default: {core.SAMPLING_INTERVAL})")

    # Experiment metadata.
    parser.add_argument("--manifest", type=str, default=None, metavar="TOML",
        help="Experiment manifest (one campaign of one experiment). The run is\n"
             "checked against it and its name + SHA-256 are written to the header.\n"
             "Without one, nothing is validated and the header records\n"
             "'experiment : none'. See manifests/TEMPLATE.toml.")
    parser.add_argument("--configuration", type=str, default="UNKNOWN",
        help="Condition code; must be one of the manifest's [conditions]")
    parser.add_argument("--experiment-rep", type=str, default="UNKNOWN",
        help="Replicate identifier: rep_1, rep_2, …")
    parser.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
        help="Per-run field, repeatable (e.g. --meta pump_lot=A-07). Which keys\n"
             "are required is declared by the manifest.")
    parser.add_argument("--pump-lot", type=str, default=None,
        help="Shorthand for --meta pump_lot=VALUE")
    parser.add_argument("--fluid", type=str, default=None,
        help="Shorthand for --meta fluid=VALUE")
    parser.add_argument("--device-id", type=str, default="UNKNOWN",
        help="Identifier of the logging host, free-form. Recorded verbatim so a\n"
             "run can be traced back to the machine that produced it.")

    # Execution modes
    parser.add_argument("--dry-run", action="store_true",
        help="Run without physical sensor; generates synthetic data for testing")
    parser.add_argument("--check", action="store_true",
        help="Validate every argument and the manifest, print what would be\n"
             "recorded, and exit WITHOUT opening the serial port (0 = would run,\n"
             "2 = rejected). run.sh calls this in the foreground before the\n"
             "detached launch, whose own errors would otherwise vanish with --rm.")
    parser.add_argument("--no-end-of-infusion", action="store_true",
        help="Disable the end-of-infusion detector, so a run only ends at\n"
             "--hours-to-log or on SIGINT/SIGTERM. Needed for static tests (e.g.\n"
             "offset with a capped sensor), where zero flow is the measurement.")
    parser.add_argument("--verify-binary", type=str, metavar="BIN_FILE",
        help="Validate an existing .bin file and exit")

    return parser, parser.parse_args()


def main():
    _setup_logging()
    parser, args = parse_args()

    if args.verify_binary:
        sys.exit(verify_binary(args.verify_binary))
    if args.hours_to_log <= 0:
        parser.error("--hours-to-log must be a positive number.")
    if args.sampling_ms <= 0:
        parser.error("--sampling-ms must be a positive integer.")

    if args.sampling_ms != core.SAMPLING_INTERVAL:
        parser.error(
            f"--sampling-ms must be {core.SAMPLING_INTERVAL} ms: the sensor readout "
            f"period and the polling period are locked together (f_ro = f_s), and "
            f"got {args.sampling_ms} ms. To change the project's acquisition rate, "
            f"edit core.SAMPLING_INTERVAL (allowed: "
            f"{', '.join(str(v) for v in core.SHDLC_SUPPORTED_INTERVALS_MS)} ms)."
        )

    try:
        supplied = manifest.parse_meta(args.meta)
    except manifest.ManifestError as exc:
        parser.error(str(exc))
    for name, value in (("pump_lot", args.pump_lot), ("fluid", args.fluid)):
        if value is not None and value.strip().upper() != "UNKNOWN":
            supplied.setdefault(name, value)

    if args.manifest:
        try:
            spec = manifest.load(args.manifest)
            fields = spec.resolve_run(args.configuration, args.experiment_rep, supplied)
        except manifest.ManifestError as exc:
            parser.error(str(exc))
        experiment_meta = {
            "experiment":         spec.experiment,
            "campaign":           spec.campaign,
            "configuration":      args.configuration,
            "configuration_name": spec.conditions[args.configuration],
            "manifest":           spec.path.name,
            "manifest_sha256":    spec.sha256,
        }
    else:
        logging.warning(
            "No --manifest: the condition and fields are recorded as given and "
            "NOT validated. Use a manifest for any run that belongs to a study.")
        fields = supplied
        experiment_meta = {
            "experiment":         "none",
            "campaign":           "none",
            "configuration":      args.configuration,
            "configuration_name": args.configuration,
            "manifest":           "none",
            "manifest_sha256":    "none",
        }

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    sampling_interval_ms = args.sampling_ms
    hours_to_log = args.hours_to_log

    metadata = {
        **experiment_meta,
        "experiment_rep":     args.experiment_rep,
        "device_id":          args.device_id,
        "f_ro_hz":            round(1000.0 / sampling_interval_ms, 4),
        "sampling_ms":        sampling_interval_ms,
        "end_of_infusion":    "off" if args.no_end_of_infusion else "on",
        "fields":             dict(sorted(fields.items())),
    }

    if args.check:
        print("Run accepted. Header would record:")
        for key, value in metadata.items():
            if key == "fields":
                for fk, fv in value.items():
                    print(f"  {fk:<18} : {fv}")
            else:
                print(f"  {key:<18} : {value}")
        sys.exit(0)

    queue_process = queue_module.Queue(maxsize=core.QUEUE_MAXSIZE)
    detector = None if args.no_end_of_infusion else EndOfInfusionDetector(
        window_size=core.EoI_WINDOW_SIZE,
        hold_sec=core.EoI_HOLD_SEC,
        rms_flow_ulmin_threshold=core.EoI_RMS_FLOW_ULMIN_THRESHOLD,
    )
    logger = Logger(path=core.LOGGER_PATH)
    ring_buffer = MeasurementRingBuffer(max_size=core.BUFF_QUEUE_MAXSIZE)

    # communication thread:
    if args.dry_run:
        log.info("DRY-RUN: generating synthetic sensor data — no serial port opened.")
        t_comm = threading.Thread(
            target=dry_run_communication,
            args=(
                queue_process,
                stop_logger_event,
                stop_main_thread_event,
                hours_to_log,
                sampling_interval_ms,
            ),
            daemon=True,
        )
    else:
        t_comm = threading.Thread(
            target=in_device_communication,
            args=(
                args.port, args.baudrate, queue_process, args.slave_address,
                logger, ring_buffer, stop_logger_event, stop_main_thread_event,
                hours_to_log, sampling_interval_ms,
            ),
            daemon=True,
        )

    # logger thread:
    filename_csv = f"{args.configuration}_{args.experiment_rep}.csv"
    filename_bin = f"{args.configuration}_{args.experiment_rep}.bin"

    t_logger = threading.Thread(
        target=dual_logger,
        args=(
            filename_csv, filename_bin, queue_process, detector,
            logger, stop_logger_event, sampling_interval_ms,
            metadata, interrupted_event,
        ),
        daemon=True,
    )

    t_comm.start()
    t_logger.start()

    while not stop_main_thread_event.is_set():
        time.sleep(1)

    # Wait for the logger to drain the queue and write the footer sentinel
    # before the process exits and daemon threads are killed.
    t_logger.join(timeout=60)


if __name__ == "__main__":
    main()
