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


class EndOfInfusionDetector: 
    def __init__(self, window_size=100, hold_sec=60, 
                 rms_flow_ulmin_threshold=0.05):
        self._window_size = int(window_size)
        self._hold_sec = float(hold_sec)
        self._rms_threshold = float(rms_flow_ulmin_threshold)

        self._flow_buffer = deque(maxlen=window_size)
        self._last_non_zero_time = None

    def update(self, timestamp, flow_ulmin) -> bool: 
        """
        Returns True if end-of-infusion is detected.
        
        :param flow_ulmin: Current flow in uL/min.
        """
        self._flow_buffer.append(float(flow_ulmin))

        if len(self._flow_buffer) < self._window_size:
            self._last_non_zero_time = None
            return False
        
        rms = (sum(f**2 for f in self._flow_buffer) / len(self._flow_buffer)) ** 0.5
        near_zero = (rms < self._rms_threshold)

        if near_zero: 
            if self._last_non_zero_time is None:
                self._last_non_zero_time = timestamp
            elif (timestamp - self._last_non_zero_time) >= self._hold_sec:
                return True
            
        else:
            self._last_non_zero_time = None

        return False

        
        