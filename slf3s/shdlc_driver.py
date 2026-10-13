"""
Sensirion SHDLC Driver Module
- Runs a dual threaded architecture to handle SHDLC communication via serial port.
- Uses Sensirion SCC1-RS485 and SCC1-USB adapters for communication.
"""
from shdlc_command import ShdlcGetContinuousMeasurementStatus, \
    ShdlcStopContinuousMeasurement
from i2c_command import ShdlcCmdI2cTransceive
from interface import ShdlcInterface
from port import ShdlcSerialPort
from utils import ErrorCodes

import logging
import time
import core
import traceback
import queue as queue_module

log = logging.getLogger(__name__)

STARTUP_ATTEMPTS = 3
PORT_SETTLE_S = 0.5
# Persistent failure handling. A USB-serial adapter that drops off the bus (seen
# on hardware: "USB disconnect" in the kernel log, then Input/output error on
# every read) never recovers on the old file descriptor, so after this many
# consecutive failed reads the port is closed and reopened. At 50 Hz, 250 = 5 s.
RECONNECT_AFTER_FAILURES = 250
RECONNECT_EVERY_S = 5.0
FAILURE_LOG_EVERY = 500          # summary line every N consecutive failures
OVERRUN_LOG_EVERY_S = 60.0


def _execute_startup(interface, slave_address, command, label, port):
    """Execute a start-up command, retrying when the adapter does not answer."""
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            return interface.execute(slave_address, command)
        except RuntimeError as exc:
            log.warning("%s: no response from the adapter (attempt %d/%d): %s",
                        label, attempt, STARTUP_ATTEMPTS, exc)
            if attempt == STARTUP_ATTEMPTS:
                raise RuntimeError(
                    f"the adapter on {port} never answered. Check that no other "
                    f"process or container is using the port (docker ps; fuser "
                    f"{port}), that {port} is the sensor cable and not another "
                    f"USB-serial device, and unplug/replug the cable") from exc
            time.sleep(1)


def in_device_communication(
        port, baudrate, queue, slave_address, logger, ring_buffer, stop_logger_event,
        stop_main_thread_event, hours_to_log=core.HOURS_TO_LOG, sampling_interval=core.SAMPLING_INTERVAL): 
    """
    Threaded SHDLC device communication via serial port.
    - Reads data from the SHDLC device and puts it into a queue.
    """
    try:
        _run_communication(port, baudrate, queue, slave_address, logger, ring_buffer,
                           stop_logger_event, stop_main_thread_event, hours_to_log, sampling_interval)
    except Exception as e:
        log.error("Fatal: communication thread failed on startup: %s", e, exc_info=True)
    finally:
        stop_main_thread_event.set()

def _start_session(port, baudrate, slave_address, sampling_interval):
    """
    Open the port and start the sensor: the start-up sequence (1)-(3).

    Used at launch and again after the USB adapter has dropped off the bus.
    Returns (shdlc_port, interface). On failure the port is closed and the
    exception propagates.
    """
    shdlc_port = ShdlcSerialPort(port=port, baudrate=baudrate)
    try:
        interface = ShdlcInterface(port=shdlc_port)

        # The adapter does not always answer the first frames after the port is
        # opened (seen intermittently on hardware), so give it a moment and retry
        # every start-up command before giving up.
        time.sleep(PORT_SETTLE_S)
        _, error = _execute_startup(interface, slave_address, _adapter_stop_cmd(),
                                    "(1) stop", port)
        log.info("(1) Stopping continuous measurement")
        if error:
            log.warning("Stop command returned error state: %s", error)
        time.sleep(1)

        # Start the SENSOR's own continuous measurement over a plain I2C write,
        # and NOT the adapter's continuous measurement (SHDLC 0x33).
        i2c_start_cmd = ShdlcCmdI2cTransceive(
            i2c_addr=ShdlcCmdI2cTransceive._I2C_ADDRESS,
            i2c_timeout=ShdlcCmdI2cTransceive._I2C_TIMEOUT_MS,
            tx_data=ShdlcCmdI2cTransceive._MEDIUM_WATER,   # 0x3608: water calibration
            rx_length=0,                                    # write only; waits 60 ms warm-up
            max_response_time=0.1,
        )
        _, error = _execute_startup(interface, slave_address, i2c_start_cmd,
                                    "(2) start", port)
        log.info(
            "(2) Sensor continuous measurement started over I2C (single reader, "
            "f_ro = f_s = %d ms)", sampling_interval,
        )
        if error:
            log.warning("Start command returned error state: %s", error)
        time.sleep(1)

        status_data, error = _execute_startup(interface, slave_address,
                                              ShdlcGetContinuousMeasurementStatus(),
                                              "(3) status", port)
        if status_data is None:
            log.info("(3) Adapter continuous measurement: OFF — single reader (correct)")
        else:
            log.warning(
                "(3) Adapter continuous measurement is ACTIVE (interval %s ms): the "
                "sensor has two readers and readings will NOT cover their interval",
                status_data,
            )
        if error:
            log.warning("Status command returned error state: %s", error)
        time.sleep(1)
        return shdlc_port, interface
    except Exception:
        shdlc_port.close()
        raise


def _adapter_stop_cmd():
    return ShdlcStopContinuousMeasurement(
        stop_code=ShdlcStopContinuousMeasurement._I2C_STOP_CODE
    )


def _end_session(shdlc_port, interface, slave_address):
    """Stop the sensor (I2C 0x3FF9) and the adapter, then close the port. Best effort."""
    try:
        interface.execute(slave_address, ShdlcCmdI2cTransceive(
            i2c_addr=ShdlcCmdI2cTransceive._I2C_ADDRESS,
            i2c_timeout=ShdlcCmdI2cTransceive._I2C_TIMEOUT_MS,
            tx_data=ShdlcCmdI2cTransceive._STOP_CODE,
            rx_length=0,
            max_response_time=0.1,
        ))
        _, error = interface.execute(slave_address, _adapter_stop_cmd())
        log.info("Stopping continuous measurement (shutdown)")
        if error:
            log.warning("Stop command (shutdown) returned error state: %s", error)
    except Exception as exc:
        log.warning("Sensor stop (shutdown) failed: %s", exc)
    try:
        shdlc_port.close()
    except Exception:
        pass


def _run_communication(
        port,
        baudrate,
        queue,
        slave_address,
        logger,
        ring_buffer,
        stop_logger_event,
        stop_main_thread_event,
        hours_to_log,
        sampling_interval
    ):
    # A failure here is fatal: nothing has been recorded yet, and the operator is
    # watching the launch.
    shdlc_port, interface = _start_session(port, baudrate, slave_address, sampling_interval)

    # Read-only transaction: the adapter builds the I2C address/read header
    transceive_cmd = ShdlcCmdI2cTransceive(
        i2c_addr=ShdlcCmdI2cTransceive._I2C_ADDRESS,
        i2c_timeout=ShdlcCmdI2cTransceive._I2C_TIMEOUT_MS,
        tx_data=[],                 # nothing to write: read header only
        rx_length=9,                # 9 bytes max for SLF3S-0600F sensor
        max_response_time=0.1
    )

    period = sampling_interval / 1000
    num_measurements = int(3600 * hours_to_log * 1000 // sampling_interval)
    log.info(
        "Acquisition started — duration: %.2f h  interval: %d ms  total samples: %d",
        hours_to_log, sampling_interval, num_measurements,
    )
    measurement_count = 0
    consecutive_failures = 0
    connected = True
    lost_since = None
    next_reconnect = 0.0
    overrun_missed, overrun_events, last_overrun_log = 0, 0, time.time()
    time.sleep(1)

    try:
        deadline = time.time()
        while not stop_logger_event.is_set():
            if measurement_count >= num_measurements:
                stop_logger_event.set()
                break
            deadline += period

            if not connected:
                # The run's clock keeps going while the adapter is away: every
                # skipped slot is a missing reading, and the CSV shows the gap.
                now = time.time()
                if now >= next_reconnect:
                    try:
                        shdlc_port, interface = _start_session(
                            port, baudrate, slave_address, sampling_interval)
                        connected = True
                        logger.log_error(
                            ErrorCodes.COMMUNICATION_FAILURE,
                            f"Reconnected to {port} after {now - lost_since:.1f} s "
                            f"without readings")
                        log.warning("Reconnected to %s after %.1f s without readings",
                                    port, now - lost_since)
                        consecutive_failures = 0
                    except Exception as exc:
                        log.debug("Reconnect to %s failed: %s", port, exc)
                        next_reconnect = time.time() + RECONNECT_EVERY_S
                        if int(now - lost_since) // 600 != int(next_reconnect - lost_since) // 600:
                            log.error("Still no adapter on %s after %.0f min — retrying "
                                      "every %.0f s", port, (now - lost_since) / 60,
                                      RECONNECT_EVERY_S)
                    # Resynchronise: the reconnect took seconds; count them as missed.
                    late = time.time() - deadline
                    if late > 0:
                        missed = int(late / period) + 1
                        deadline += missed * period
                        measurement_count += missed
                measurement_count += 1
                _sleep_until(deadline)
                continue

            # A transient SHDLC/I2C glitch (e.g. device error 35 → empty MISO
            # frame, or a CRC error) raises inside interface.execute(): skip just
            # this sample. Only the FIRST failure of a streak is logged in full;
            # a fault that persists is summarised, never one entry per reading.
            sample = None
            failure = None
            try:
                data, error = interface.execute(slave_address, transceive_cmd)
                if error:
                    failure = f"error state {error}"
                elif data is None:
                    failure = "no data"
                else:
                    sample = data
            except Exception as e:
                failure = f"{type(e).__name__}: {e}"

            if sample is None:
                consecutive_failures += 1
                if consecutive_failures == 1:
                    logger.log_error(ErrorCodes.COMMUNICATION_FAILURE,
                                     f"Read failure — skipping sample: {failure}",
                                     context=ring_buffer.snapshot())
                    log.warning("Read failure — skipping sample: %s", failure)
                elif consecutive_failures % FAILURE_LOG_EVERY == 0:
                    log.error("%d consecutive failed reads (last: %s)",
                              consecutive_failures, failure)
                if consecutive_failures >= RECONNECT_AFTER_FAILURES:
                    # Persistent failure: the adapter is gone (USB disconnect →
                    # Input/output error) or hung. Close and reopen from scratch.
                    lost_since = time.time()
                    logger.log_error(ErrorCodes.COMMUNICATION_FAILURE,
                                     f"{consecutive_failures} consecutive failed reads "
                                     f"(last: {failure}) — closing {port} and reconnecting")
                    log.error("%d consecutive failed reads (last: %s) — closing %s and "
                              "reconnecting every %.0f s", consecutive_failures, failure,
                              port, RECONNECT_EVERY_S)
                    try:
                        shdlc_port.close()
                    except Exception:
                        pass
                    connected = False
                    next_reconnect = lost_since + RECONNECT_EVERY_S
            else:
                if consecutive_failures:
                    log.info("Read recovered after %d failed attempt(s).", consecutive_failures)
                consecutive_failures = 0

                flow_raw, temp_raw, flags_raw = sample
                item = (float(time.time()), int(flow_raw), int(temp_raw), int(flags_raw))
                ring_buffer.push(item)
                try:
                    queue.put(item, timeout=1.0)
                except queue_module.Full:
                    logger.log_error(ErrorCodes.QUEUE_FULL,
                                     "Data queue is full. Dropping measurement.")
                    log.warning("Data queue full — measurement dropped.")

            # Pace to the schedule on EVERY iteration — success or skip.
            missed = 0
            late = time.time() - deadline
            if late > 0:
                missed = int(late / period) + 1
                deadline += missed * period
                overrun_missed += missed
                overrun_events += 1
            else:
                _sleep_until(deadline)
            # Overruns are summarised every OVERRUN_LOG_EVERY_S, not logged each time.
            if overrun_events and time.time() - last_overrun_log >= OVERRUN_LOG_EVERY_S:
                logger.log_error(ErrorCodes.COMMUNICATION_FAILURE,
                                 f"Sampling overrun: {overrun_events} late iteration(s), "
                                 f"{overrun_missed} sample(s) missed in the last "
                                 f"{time.time() - last_overrun_log:.0f} s")
                overrun_missed, overrun_events = 0, 0
                last_overrun_log = time.time()
            measurement_count += 1 + missed

    except Exception as e:
        logger.log_error(
            ErrorCodes.COMMUNICATION_FAILURE,
            f"Exception in device communication thread (crashed): {e}",
            context=traceback.format_exc(),
        )
        log.error("Communication thread crashed: %s", e, exc_info=True)

    finally:
        if connected:
            _end_session(shdlc_port, interface, slave_address)
        stop_main_thread_event.set()


def _sleep_until(deadline):
    delay = deadline - time.time()
    if delay > 0:
        time.sleep(delay)
