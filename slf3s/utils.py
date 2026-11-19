from collections import deque
from threading import Lock
import os
import time  


class ErrorCodes: 
    SHDLC_ERROR_STATE = 1 
    QUEUE_FULL = 2
    LOGGER_FAILURE = 3
    COMMUNICATION_FAILURE = 4

    # SHDLC error codes: -----------------------------
    # Address of non-volatile memory out of range 0x21
    SHDLC_ADDR_OUT_OF_RANGE = 33


class MeasurementRingBuffer:
    """
    A thread-safe ring buffer to store measurements.
    """
    def __init__(self, max_size=1000):
        self._buffer = deque(maxlen=max_size)
        self._lock = Lock()

    def push(self, measurement):
        with self._lock:
            self._buffer.append(measurement)
            
    def snapshot(self):
        with self._lock:
            return list(self._buffer)
        

class Logger:
    def __init__(self, path):
        self.path = path
        self._lock = Lock()
        os.makedirs(path, exist_ok=True)

    def log(self, message, context=None):
        ts = time.time()
        path = os.path.join(self.path, "logs.txt")
        with self._lock, open(path, "a") as f:
            f.write(f"[{ts:.6f}] {message}\n")
            if context is not None:
                f.write(f"    CONTEXT: {context}\n")

    def log_error(self, code, message, context=None):
        ts = time.time()
        path = os.path.join(self.path, "error_logs.txt")
        with self._lock, open(path, "a") as f:
            f.write(f"[{ts:.6f}] ERROR {code}: {message}\n")
            if context is not None:
                f.write(f"    CONTEXT: {context}\n")


class FlowStopDetector:
    """
    Flags the end of a flow: once the flow has been SUSTAINED for a while
    (armed), it fires when the mean flow over a sliding window stays below a
    threshold for `hold_sec`. Generic — in the pump study it marks the end of an
    infusion, hence the CLI flag --no-end-of-infusion and the header key
    `end_of_infusion`.

    Arming exists because a run can legitimately sit at zero flow BEFORE its flow
    starts: a dry recording with the sensor full of air, or a line filled and
    then capped for an hour to check the sensor's zero. Without arming, either
    would end the run before it began. The detector arms after `arm_minutes`
    CONSECUTIVE one-minute blocks whose mean non-air flow is ≥ `arm_flow_ulmin`.
    A minute made only of air readings neither counts nor resets the streak (a
    bubble crossing the sensor is not the flow stopping); a minute with liquid
    below the threshold resets it — a filled-then-capped line never arms.

    Once armed, air readings count as zero flow (no liquid is being delivered),
    and the criterion is the MEAN, not the RMS: a liquid-filled sensor at rest
    reads its offset (≈ −0.1 µL/min), so an RMS threshold below |offset| may
    never fire, and one above it depends on each sensor's offset.

    Running sums keep each update O(1): the window spans 1000 s, i.e. 50 000
    readings at 50 Hz. The sum is recomputed once per window to bound
    floating-point drift over a multi-day run.
    """
    def __init__(self, window_size, hold_sec, mean_flow_ulmin_threshold,
                 arm_flow_ulmin, arm_minutes):
        self._window_size = int(window_size)
        self._hold_sec = float(hold_sec)
        self._threshold = float(mean_flow_ulmin_threshold)
        self._arm_flow = float(arm_flow_ulmin)
        self._arm_minutes = int(arm_minutes)

        self._flow_buffer = deque()
        self._sum = 0.0
        self._since_resum = 0
        self._below_since = None

        self.armed = False
        self.armed_at = None
        self._streak = 0                  # consecutive qualifying minutes
        self._block_start = None
        self._block_sum = 0.0
        self._block_n = 0                 # non-air readings in the block

    def _close_block(self) -> None:
        if self._block_n == 0:
            return                        # air only: neither counts nor resets
        if self._block_sum / self._block_n >= self._arm_flow:
            self._streak += 1
        else:
            self._streak = 0

    def update(self, timestamp, flow_ulmin, air=False) -> bool:
        """
        Feed one reading. Returns True when the end of the flow is detected.

        :param flow_ulmin: flow in µL/min as read (not offset-corrected).
        :param air: the reading's air-in-line flag.
        """
        f = float(flow_ulmin)

        # ── arming: one-minute blocks of non-air readings ──
        if not self.armed:
            if self._block_start is None:
                self._block_start = timestamp
            elif timestamp - self._block_start >= 60.0:
                self._close_block()
                self._block_start, self._block_sum, self._block_n = timestamp, 0.0, 0
                if self._streak >= self._arm_minutes:
                    self.armed, self.armed_at = True, timestamp
            if not air:
                self._block_sum += f
                self._block_n += 1

        # ── sliding window of delivered flow (air = nothing delivered) ──
        v = 0.0 if air else f
        self._flow_buffer.append(v)
        self._sum += v
        if len(self._flow_buffer) > self._window_size:
            self._sum -= self._flow_buffer.popleft()
        self._since_resum += 1
        if self._since_resum >= self._window_size:
            self._sum, self._since_resum = sum(self._flow_buffer), 0

        if not self.armed or len(self._flow_buffer) < self._window_size:
            self._below_since = None
            return False

        if self._sum / len(self._flow_buffer) < self._threshold:
            if self._below_since is None:
                self._below_since = timestamp
            elif timestamp - self._below_since >= self._hold_sec:
                return True
        else:
            self._below_since = None
        return False
