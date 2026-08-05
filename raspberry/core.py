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

# ── Acquisition rate: SINGLE SOURCE OF TRUTH ──────────────────────────────────
# The sensor's internal readout period (f_ro) and the serial polling period (f_s)
# are LOCKED TOGETHER at this value:
#
#     f_ro = f_s = 10 s  (0.1 Hz)
#
# They must not diverge. If polling were faster than the readout, consecutive
# polls would return the SAME internal measurement while the CSV metadata field
# `f_ro_hz` — derived from the polling interval — would claim a rate the sensor
# never produced. The recorded metadata would then be silently wrong, which is
# the worst outcome for multi-day runs nobody re-observes.
#
# Enforcement: `shdlc_driver` configures the sensor FROM this same value (so they
# agree by construction), and `main.py` rejects a `--sampling-ms` that differs.
# To change the project's acquisition rate, change it HERE and nowhere else —
# and it must be one of SHDLC_SUPPORTED_INTERVALS_MS below.
SAMPLING_INTERVAL = 10000     # ms — f_ro = f_s = 10 s (0.1 Hz)

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
EoI_WINDOW_SIZE = 100                # Number of samples in the sliding window
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
BIN_MAGIC = b'SLF3SLOG\x00\x00\x00\x01'             # 12-byte magic
BIN_VERSION = 1                                       # uint32
BIN_HEADER_FMT = '>12sI'
BIN_HEADER_SIZE = struct.calcsize(BIN_HEADER_FMT)    # 16 bytes

# Flushing period for logger
FLUSH_EVERY = 10 # samples

# ── The experiment matrix: EXACTLY these 8 conditions ─────────────────────────
# Catheter configuration codes → descriptive names. This dict is the canonical
# list of the study's conditions: `main.py` REJECTS a --configuration that is not
# a key here, so a run cannot be launched under an ad-hoc label.
#
# Why the rejection matters: the first campaign produced three runs launched as
# `C1` and renamed to `C1a_*` by hand afterwards. Their in-file metadata still
# says `configuration: C1`, so any analysis that groups by the recorded metadata
# groups them wrong — and the files LOOK correct in a directory listing. The
# validation makes that class of error impossible at the source.
#
# The `a`/`b` suffix denotes pump reuse (a = first use, b = second use), so the
# same catheter type can be compared against itself as the pump degrades.
CONFIG_NAMES = {
    "C0a": "Sin catéter — bomba primera vez",
    "C0b": "Sin catéter — bomba segunda vez",
    "C0c": "Sin catéter — solución con bupivacaína (NaCl 240 mL + BuPi 60 mL)",
    "C1a": "Contiplex 40 cm (3 orificios laterales) — bomba primera vez",
    "C1b": "Contiplex 40 cm (3 orificios laterales) — bomba segunda vez",
    "C2":  "Contiplex 40 cm + filtro Perifix 0,2 µm",
    "C3":  "Contiplex 100 cm (3 orificios laterales)",
    "C4":  "Catéter peridural pediátrico (orificio terminal)",
}


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




