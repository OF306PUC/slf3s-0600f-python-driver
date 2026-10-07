"""
core.py library module.

Provides core functionality for SHDLC data interpretation.
- Flow rate: 16-bit signed integer number. (two's complement range: -32768 to 32767).
    Sensor output internally saturates in +-3250 uLmin range so it will output within that range (scaled by 10 (ul/min)^{-1}).
- Temperature: 16-bit signed integer number. (two's complement range: -32768 to 32767).
"""
import struct

# Default params (Linux-based system):
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 115200
SHDLC_SLAVE_ADDRESS = 0x00    # Default I2C slave address for the SL
QUEUE_MAXSIZE = 1000          # Max size of the data queue
HOURS_TO_LOG = 48             # Default logging duration in hours (Accoding to Baxter infusion pump lasting time)

# Acquisition rate: 
SAMPLING_INTERVAL = 20        # ms — f_ro = f_s = 20 ms (50 Hz): datasheet's recommended 50–200 Hz
AGGREGATE_S = 60

# Discrete measurement intervals the SLF3S-0600F accepts over SHDLC.
SHDLC_SUPPORTED_INTERVALS_MS = (20, 50, 100, 1000, 10000, 60000)

DATA_DIR = "Temp/"
LOGGER_PATH = "Logs/"
BUFF_QUEUE_MAXSIZE = 100      # Max size of the ring buffer for measurements

# Default scaling factors:
SCALE_FLOW = 10.0
SCALE_TEMPERATURE = 200.0

UL_MIN_TO_ML_HR = (60.0 / 1000.0)
UL_MIN_TO_ML_SEC = (1.0 / 1000.0 / 60.0)
MIN_TO_SEC = (1.0 / 60.0)

# End of infusion detector params:
# The window is defined in SECONDS so it keeps the time span it had when it was
# tuned (100 samples × 10 s = 1000 s) regardless of the readout rate.
EoI_WINDOW_S = 1000
EoI_WINDOW_SIZE = int(EoI_WINDOW_S * 1000 // SAMPLING_INTERVAL)  # samples
EoI_HOLD_SEC = 300                   # Hold time in seconds (5 min.)
EoI_RMS_FLOW_ULMIN_THRESHOLD = 0.09  # uL/min (Empirical threshold for RMS flow rate to consider "near zero" flow)

# >  big-endian
# d  float64 timestamp
# h  int16 flow
# h  int16 temp
# H  uint16 flags
BIN_RECORD_FMT = ">dhhH"
BIN_RECORD_SIZE = struct.calcsize(BIN_RECORD_FMT)   # 14 bytes per record

# Binary file magic header: 12-byte magic string + 4-byte version uint32 = 16 bytes
# Version of the CSV header + column layout this logger writes. Bumped whenever a
# reader could misinterpret a file written by a newer logger: a renamed column, a
# removed metadata key, a changed unit. Adding a NEW metadata line does not require
# a bump, because a reader that ignores unknown keys is unaffected.
CSV_FORMAT_VERSION = 2

BIN_MAGIC = b'SLF3SLOG\x00\x00\x00\x01'             # 12-byte magic
BIN_VERSION = 1                                       # uint32
BIN_HEADER_FMT = '>12sI'
BIN_HEADER_SIZE = struct.calcsize(BIN_HEADER_FMT)    # 16 bytes

# Flushing period for logger
FLUSH_EVERY = 250 # raw samples (5 s at 50 Hz)


def measurement_interval_bytes(interval_ms):
    """
    Encode a measurement interval in ms as the 2-byte big-endian payload that the
    SHDLC start-continuous-measurement command expects.

    Deriving the bytes here — rather than picking a hand-written constant such as
    `_MEASUREMENT_INTERVAL_10000_MS` at the call site — is what keeps f_ro tied to
    SAMPLING_INTERVAL. With two independent literals the sensor rate and the
    polling rate can drift apart in a single careless edit, and nothing observable
    fails: the logger keeps writing rows, only the rate metadata becomes a lie.

    :param int interval_ms: interval in milliseconds; must be in
        SHDLC_SUPPORTED_INTERVALS_MS.
    :return list: two bytes, most significant first.
    :raises ValueError: if the sensor does not support the interval.
    """
    if interval_ms not in SHDLC_SUPPORTED_INTERVALS_MS:
        raise ValueError(
            f"measurement interval {interval_ms} ms is not supported by the "
            f"SLF3S-0600F; supported: {SHDLC_SUPPORTED_INTERVALS_MS}"
        )
    return list(struct.pack(">H", interval_ms))


def u16_to_i16(x):
    """
    Convert unsigned 16-bit integer to signed 16-bit integer.
    signed = x - 2**16 if masked(x)
    
    :param x:
    """
    return x - 0x10000 if x & 0x8000 else x

def interpret_flow_temp_raw(flow_raw, temp_raw):
    """
    interpret raw data bytes from SHDLC device and return flow and temperature values.

    raw response for the SLF3S-0600F sensor is 18-bit long: 
    raw_data = (flow[15:0], temp[15:0], signaling flags 8msb, signaling flags 8lsb) 
    
    :param raw_data: raw data bytes from SHDLC device
    :return: flow in uL/min (or) mL/hr. and temperature in degC.
    """ 
    raw_flow = u16_to_i16(flow_raw)
    raw_temp = u16_to_i16(temp_raw)

    flow_ul_min = float(raw_flow) / SCALE_FLOW
    temperature_degC = float(raw_temp) / SCALE_TEMPERATURE

    return flow_ul_min, temperature_degC

def get_bit(value, n): 
    """Return bit n of value (n=0 is LSB)"""
    return (value >> n) & 1

def interpret_flags_raw(flags_raw):
    """
    Interpret signaling flags raw data from SHDLC device.

    :param flags_raw: raw flags data bytes from SHDLC device
    :return: air_in_line_flag (bool), high_flow_flag (bool), exp_smoothing (bool)
    """
    air_in_line_flag = get_bit(flags_raw, 0)  # Bit 0: Air in line flag
    high_flow_flag  = get_bit(flags_raw, 1)   # Bit 1: High flow flag
    exp_smoothing = get_bit(flags_raw, 5)     # Bit 5: Exponential smoothing active flag
    flags_value = int((flags_raw & 0xFFFF))

    return air_in_line_flag, high_flow_flag, exp_smoothing, flags_value




