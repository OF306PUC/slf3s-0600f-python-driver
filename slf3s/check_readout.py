#!/usr/bin/env python3
"""
check_readout.py — verify, from a run's binary file, that the sensor was read at
the intended rate and that every reading covered its interval.

    python3 check_readout.py Temp/<RUN>.bin [--expected-ms 100]

Standard library only, so it runs on the acquisition host itself.

Why the binary and not the CSV: the binary holds every raw reading with its own
timestamp; the CSV holds one block mean per minute and has already hidden the
timing. Everything this script checks is a property of the raw stream:

  1. Polling rate and jitter — the time between consecutive readings.
  2. Coverage gaps — intervals longer than 100 ms. This, not lateness as such, is
     what loses data: with a single reader the sensor averages arithmetically
     everything since the previous read as long as less than 100 ms have passed,
     so a reading that is merely late still covers its whole interval. Past
     100 ms the sensor switches to exponential smoothing and part of the interval
     is lost. Lateness relative to the nominal interval is reported as a warning.
  3. Coverage — sensor flag bit 5. The sensor averages arithmetically only while
     the time since the previous read is under 100 ms; past that it switches to
     exponential smoothing and the value covers just its last ~100 ms (datasheet
     §3.1). A reading with bit 5 set therefore did NOT cover its whole interval.
  4. Stale readings — two consecutive readings identical in flow, temperature and
     flags. At a steady flow some of these are pure coincidence (the temperature
     barely moves and the flow is quantised in 0.1 µL/min steps), so they are
     reported for information and only fail when they are so frequent that they
     cannot be chance — the signature of receiving cached values.

Exit status: 0 = PASS, 1 = WARN, 2 = FAIL, 3 = file error.
"""
import argparse
import pathlib
import statistics
import struct
import sys

import core

# Thresholds, relative to the expected interval. Chosen so that ordinary OS
# scheduling jitter on a single-board computer passes and a systematic problem
# does not.
RATE_TOL = 0.02          # effective rate within ±2 % of the expected one → else WARN
LATE_FACTOR = 1.5        # an interval > 1.5× expected is "late" (regularity only)
LATE_WARN = 0.01         # >1 % of intervals late → WARN
COVERAGE_MS = 100.0      # datasheet §3.1: arithmetic averaging holds below this
COVER_WARN = 0.001       # >0.1 % of intervals over 100 ms → WARN
COVER_FAIL = 0.01        # >1 % → FAIL
EXP_WARN = 0.01          # >1 % of readings in exponential smoothing → WARN
EXP_FAIL = 0.05          # >5 % → FAIL
STALE_FAIL = 0.40       # chance repeats at steady flow are a few %; cached ≈ ≥50 %


def read_records(path: pathlib.Path):
    raw = path.read_bytes()
    offset = 0
    if raw[:core.BIN_HEADER_SIZE][:len(core.BIN_MAGIC)] == core.BIN_MAGIC:
        offset = core.BIN_HEADER_SIZE
    size = struct.calcsize(core.BIN_RECORD_FMT)
    usable = (len(raw) - offset) // size * size
    return [struct.unpack_from(core.BIN_RECORD_FMT, raw, offset + i)
            for i in range(0, usable, size)]


def pct(xs, q):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bin_file")
    ap.add_argument("--expected-ms", type=float, default=core.SAMPLING_INTERVAL,
                    help=f"intended readout interval (default {core.SAMPLING_INTERVAL} ms)")
    args = ap.parse_args()

    path = pathlib.Path(args.bin_file)
    if not path.is_file():
        print(f"file not found: {path}", file=sys.stderr)
        return 3
    recs = read_records(path)
    if len(recs) < 10:
        print(f"only {len(recs)} readings — too few to judge", file=sys.stderr)
        return 3

    exp_s = args.expected_ms / 1000.0
    ts = [r[0] for r in recs]
    dts = [b - a for a, b in zip(ts, ts[1:])]
    duration = ts[-1] - ts[0]
    rate = (len(ts) - 1) / duration if duration > 0 else float("nan")
    late = [d for d in dts if d > LATE_FACTOR * exp_s]
    uncovered = [d for d in dts if d * 1000 > COVERAGE_MS]
    exp_flag = sum(1 for r in recs if (r[3] >> 5) & 1)
    stale = sum(1 for a, b in zip(recs, recs[1:]) if a[1:] == b[1:])
    air = sum(1 for r in recs if r[3] & 1)
    high = sum(1 for r in recs if (r[3] >> 1) & 1)

    per_min = {}
    for t in ts:
        per_min[int((t - ts[0]) // 60)] = per_min.get(int((t - ts[0]) // 60), 0) + 1
    full = [n for k, n in per_min.items() if k < max(per_min)]   # drop last partial

    ms = [d * 1000 for d in dts]
    print(f"\nReadout check — {path.name}")
    print(f"  readings            {len(recs):,} over {duration/60:.1f} min")
    print(f"  expected interval   {args.expected_ms:.0f} ms  ({1000/args.expected_ms:.2f} Hz)")
    print(f"  effective rate      {rate:.3f} Hz  ({(rate*exp_s-1)*100:+.2f} %)")
    print(f"  interval  median    {statistics.median(ms):.1f} ms   "
          f"p1 {pct(ms,0.01):.1f}  p99 {pct(ms,0.99):.1f}  max {max(ms):.1f}  "
          f"sd {statistics.pstdev(ms):.1f}")
    print(f"  late intervals      {len(late):,} (> {LATE_FACTOR*args.expected_ms:.0f} ms)  "
          f"[regularity only: a late reading still covers its interval]")
    print(f"  coverage gaps       {len(uncovered):,} intervals > {COVERAGE_MS:.0f} ms  "
          f"[data lost: the sensor no longer averaged the whole interval]")
    if full:
        print(f"  readings per minute min {min(full)}  median {statistics.median(full):.0f}  "
              f"max {max(full)}  (expected {60/exp_s:.0f})")
    print(f"  exp. smoothing (b5) {exp_flag:,} readings ({exp_flag/len(recs)*100:.2f} %) "
          f"— did not cover their interval")
    print(f"  stale repeats       {stale:,} ({stale/len(recs)*100:.2f} %)  "
          f"[informative; fails only above {STALE_FAIL*100:.0f} %]")
    print(f"  air / high-flow     {air:,} / {high:,} readings")

    verdict, reasons = 0, []

    def flag(level, msg):
        nonlocal verdict
        verdict = max(verdict, level)
        reasons.append(("WARN" if level == 1 else "FAIL") + ": " + msg)

    if abs(rate * exp_s - 1) > RATE_TOL:
        flag(1, f"effective rate off by {(rate*exp_s-1)*100:+.1f} % — the host is not "
                f"keeping the schedule (data is complete while gaps stay under "
                f"{COVERAGE_MS:.0f} ms)")
    frac_late = len(late) / len(dts)
    if frac_late > LATE_WARN:
        flag(1, f"{frac_late*100:.2f} % of intervals late")
    frac_unc = len(uncovered) / len(dts)
    if frac_unc > COVER_FAIL:
        flag(2, f"{frac_unc*100:.2f} % of intervals over {COVERAGE_MS:.0f} ms — readings "
                f"are losing part of their interval")
    elif frac_unc > COVER_WARN:
        flag(1, f"{frac_unc*100:.2f} % of intervals over {COVERAGE_MS:.0f} ms")
    frac_exp = exp_flag / len(recs)
    if frac_exp > EXP_FAIL:
        flag(2, f"{frac_exp*100:.1f} % of readings in exponential smoothing — "
                f"readings are not covering their interval (consider 50 ms)")
    elif frac_exp > EXP_WARN:
        flag(1, f"{frac_exp*100:.1f} % of readings in exponential smoothing")
    frac_stale = stale / len(recs)
    if frac_stale > STALE_FAIL:
        flag(2, f"{frac_stale*100:.2f} % stale repeats — cached values, not fresh reads")

    print("\n  " + ("PASS" if verdict == 0 else "WARN" if verdict == 1 else "FAIL"))
    for r in reasons:
        print("   - " + r)
    print()
    return verdict


if __name__ == "__main__":
    sys.exit(main())
