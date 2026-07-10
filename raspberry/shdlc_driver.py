"""
Sensirion SHDLC Driver Module
- Runs a dual threaded architecture to handle SHDLC communication via serial port.
- Uses Sensirion SCC1-RS485 and SCC1-USB adapters for communication.
"""
from shdlc_command import ShdlcStartContinuousMeasurement, \
    ShdlcGetContinuousMeasurementStatus, ShdlcStopContinuousMeasurement
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
    with ShdlcSerialPort(port=port, baudrate=baudrate) as shdlc_port:
        interface = ShdlcInterface(port=shdlc_port)

        # Stopping continuous measurement 
        i2c_transceive_stop_cmd = ShdlcStopContinuousMeasurement(
            stop_code=ShdlcStopContinuousMeasurement._I2C_STOP_CODE
        )
        _, error  = interface.execute(slave_address, i2c_transceive_stop_cmd)
        log.info("(1) Stopping continuous measurement")
        if error:
            log.warning("Stop command returned error state: %s", error)
        time.sleep(1)

        # I2C Transceive command to start continuous measurement
        i2c_transceive_start_cmd = ShdlcStartContinuousMeasurement(
            measurement_interval=ShdlcStartContinuousMeasurement._MEASUREMENT_INTERVAL_10000_MS,
            i2c_medium_command=ShdlcStartContinuousMeasurement._I2C_MEAS_CMD_MEDIUM_WATER
        )
        _, error  = interface.execute(slave_address, i2c_transceive_start_cmd)
        log.info("(2) Starting continuous measurement")
        if error:
            log.warning("Start command returned error state: %s", error)
        time.sleep(1)

        # I2C Transceive command to check continuous measurement status
        i2c_transceive_status_cmd = ShdlcGetContinuousMeasurementStatus()
        status_data, error  = interface.execute(slave_address, i2c_transceive_status_cmd)
        log.info("(3) Measurement status — interval: %s ms", status_data)
        if error:
            log.warning("Status command returned error state: %s", error)
        time.sleep(1)

        # Read measurement data in a loop
        i2c_header = (ShdlcCmdI2cTransceive._I2C_ADDRESS << 1) | \
                ShdlcCmdI2cTransceive._READ_BIT 
        transceive_cmd = ShdlcCmdI2cTransceive(
            i2c_addr=ShdlcCmdI2cTransceive._I2C_ADDRESS,
            i2c_timeout=ShdlcCmdI2cTransceive._I2C_TIMEOUT_MS,
            tx_data=[i2c_header],       # Read measurement command
            rx_length=9,                # 9 bytes max for SLF3S-0600F sensor
            max_response_time=0.1
        )  

        seconds_to_log = 3600 * hours_to_log
        num_measurements = int(seconds_to_log * 1000 // sampling_interval)
        log.info(
            "Acquisition started — duration: %.2f h  interval: %d ms  total samples: %d",
            hours_to_log, sampling_interval, num_measurements,
        )
        measurement_count = 0
        consecutive_failures = 0
        time.sleep(1)

        try: 

            deadline = time.time()
            while not stop_logger_event.is_set(): 
                if measurement_count >= num_measurements:
                    stop_logger_event.set()
                
                deadline += sampling_interval / 1000
                # reading data from sensor: 
                # data is: (flow_ul_min, temp_c, flag_air, flag_high_flow, exp_smoothing)
                # A transient SHDLC/I2C glitch (e.g. device error 35 → empty MISO
                # frame → "expected 9 bytes, got 0", or a CRC error) raises inside
                # interface.execute(). Catch it here, log it, and skip just this
                # sample — a single bad read must NOT tear down a multi-day run.
                sample = None
                try:
                    data, error = interface.execute(slave_address, transceive_cmd)
                    if error:
                        logger.log_error(
                            ErrorCodes.SHDLC_ERROR_STATE,
                            "Error state received during measurement read.",
                            context=ring_buffer.snapshot()
                        )
                        log.error("Measurement read returned error state — skipping sample.")
                    elif data is None:
                        log.warning("Measurement read returned no data — skipping sample.")
                    else:
                        sample = data
                except Exception as e:
                    logger.log_error(
                        ErrorCodes.COMMUNICATION_FAILURE,
                        f"Transient read failure — skipping sample: {e}",
                        context=ring_buffer.snapshot()
                    )
                    log.warning("Transient read failure — skipping sample: %s", e)

                if sample is None:
                    consecutive_failures += 1
                    if consecutive_failures % 10 == 0:
                        log.error(
                            "%d consecutive failed reads — acquisition still running.",
                            consecutive_failures,
                        )
                else:
                    if consecutive_failures:
                        log.info("Read recovered after %d failed attempt(s).", consecutive_failures)
                    consecutive_failures = 0

                    flow_raw, temp_raw, flags_raw = sample
                    timestamp = time.time()
                    item = (float(timestamp), int(flow_raw), int(temp_raw), int(flags_raw))
                    ring_buffer.push(item)

                    try:
                        queue.put(item, timeout=1.0)
                    except queue_module.Full:
                        logger.log_error(
                            ErrorCodes.QUEUE_FULL,
                            "Data queue is full. Dropping measurement.",
                            context=ring_buffer.snapshot()
                        )
                        log.warning("Data queue full — measurement dropped.")

                # Pace to the schedule on EVERY iteration — success or skip — so a
                # persistent fault retries at the sampling cadence instead of
                # busy-spinning and flooding the logs.
                t_now = time.time()
                sleep_time = deadline - t_now
                missed = 0
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    missed = int(-sleep_time / (sampling_interval / 1000)) + 1
                    deadline += missed * (sampling_interval / 1000)
                    logger.log_error(
                        ErrorCodes.COMMUNICATION_FAILURE,
                        f"Sampling overrun: {-sleep_time*1000:.1f} ms late, missed {missed} sample(s)",
                    )

                measurement_count += (1 + missed)

        except Exception as e:
            logger.log_error(
                ErrorCodes.COMMUNICATION_FAILURE,
                f"Exception in device communication thread (crashed): {e}",
                context=traceback.format_exc(),
            )

        finally:
            _, error  = interface.execute(slave_address, i2c_transceive_stop_cmd)
            log.info("Stopping continuous measurement (shutdown)")
            if error:
                log.warning("Stop command (shutdown) returned error state: %s", error)
            stop_main_thread_event.set()
            
