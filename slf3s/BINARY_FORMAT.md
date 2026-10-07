# Binary Log Format — SLF3S-0600F Data Logger

Every `.bin` file produced by `main.py` (or `data_logger.dual_logger`) starts
with a 16-byte magic header followed by fixed-size records.

---

## File header (16 bytes)

| Offset | Size | Type      | Value                                   |
|--------|------|-----------|-----------------------------------------|
| 0      | 12   | bytes     | `b'SLF3SLOG\x00\x00\x00\x01'` (magic)  |
| 12     | 4    | uint32 BE | File format version (currently `1`)     |

**Struct format string:** `'>12sI'`  
**Byte order:** big-endian (`>`)

Legacy files produced before this format was introduced have **no** header and
start directly with records at offset 0.  `recover.py` and `verify_binary()`
detect the absence of the magic bytes and warn accordingly.

---

## Record layout (14 bytes each)

| Field      | Offset | Size | Type      | Unit          | Notes                             |
|------------|--------|------|-----------|---------------|-----------------------------------|
| timestamp  | 0      | 8    | float64   | Unix seconds  | UTC wall-clock time               |
| flow_raw   | 8      | 2    | int16     | —             | Raw sensor int: `flow_uL_min = flow_raw / 10.0` |
| temp_raw   | 10     | 2    | int16     | —             | Raw sensor int: `temp_degC = temp_raw / 200.0`  |
| flags_raw  | 12     | 2    | uint16    | —             | Sensor status flags (see below)   |

**Struct format string:** `'>dhhH'`  
**Record size:** 14 bytes (`struct.calcsize('>dhhH') == 14`)

### Flag bits (flags_raw)

| Bit | Mask     | Name                |
|-----|----------|---------------------|
| 0   | `0x0001` | Air-in-line         |
| 1   | `0x0002` | High-flow           |
| 5   | `0x0020` | Exponential smoothing active |

---

## Conversion formulas

```python
flow_uL_min  = float(flow_raw)  / 10.0
temp_degC    = float(temp_raw)  / 200.0
```

---

## Minimal Python snippet — read one record

```python
import struct

MAGIC       = b'SLF3SLOG\x00\x00\x00\x01'
HEADER_FMT  = '>12sI'
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 16
RECORD_FMT  = '>dhhH'
RECORD_SIZE = struct.calcsize(RECORD_FMT)   # 14

with open('DataLog.bin', 'rb') as f:
    raw_header = f.read(HEADER_SIZE)
    magic, version = struct.unpack(HEADER_FMT, raw_header)
    assert magic == MAGIC, "unexpected magic bytes"
    print(f"version={version}")

    raw_record = f.read(RECORD_SIZE)
    timestamp, flow_raw, temp_raw, flags_raw = struct.unpack(RECORD_FMT, raw_record)

flow_uL_min = float(flow_raw)  / 10.0
temp_degC   = float(temp_raw)  / 200.0
print(f"t={timestamp:.3f}  q={flow_uL_min:.2f} µL/min  T={temp_degC:.2f} °C")
```

---

## Recovery

Use `recover.py` to convert any `.bin` file (legacy or versioned) to CSV:

```bash
python3 recover.py DataLog.bin --output recovered.csv
```

Use `main.py --verify-binary` for a quick integrity check without producing output:

```bash
python3 main.py --verify-binary Temp/DataLog.bin
```
