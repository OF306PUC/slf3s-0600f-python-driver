#!/usr/bin/env python3
"""
offset_report.py — zero-flow offset of one sensor, from a static run's binary.

    python3 offset_report.py Temp/<RUN>.bin [--settle-min N] [--from-min M]

The measured stretch must be STATIC: the sensor filled with liquid, no bubbles,
capped, mounted in its operating orientation, so the true flow is zero and
whatever the sensor reads is its offset.

The run may start with a flowing phase (e.g. a pump flowing for a day before the
outlet is capped, so the sensor is wetted and at its operating temperature). The
moment of capping is found automatically as the last reading above
--flowing-ul-min; only what follows it counts. --from-min overrides that and
starts the static stretch at a fixed minute from the start of the run. Launch
such runs with END_OF_INFUSION=off, or the end-of-infusion detector stops them
~20 min after capping.

The offset must be measured with liquid in the channel, not air: the sensor is
calibrated for liquid, and with air it is measuring a different medium. In the
first campaign of the pump-flow study the offset taken during the air-filled
purge (-0.17 to -0.61 µL/min) did not match what the same sensors read with
stationary liquid at the end of the runs (-0.13 to +0.07 µL/min).

Reported: mean (the offset), standard deviation, standard error, linear drift
over the run, mean sensor temperature, and the readings flagged as air — which
must be ~0; air in a capped sensor means a bubble is trapped inside and the run
measured the wrong medium.

Standard library only. Exit status: 0 = usable, 1 = doubtful, 2 = not usable,
3 = file error.
"""
import argparse
import pathlib
import statistics
import sys

import core
from check_readout import read_records

AIR_MAX = 0.001          # >0.1 % of readings with air → bubble inside, not usable
DRIFT_WARN = 0.05        # |drift| above this many µL/min per hour → still settling
FLOWING_UL_MIN = 5.0     # |flow| above this is the flowing phase, not an offset
# Settling. After capping, the reading does not drop straight to the zero: it
# starts high and decays — the line relaxing under the pump's pressure — for
# 30 min to 6 h depending on the line (measured on the October 2026 offset runs).
# A fixed short discard averaged that transient into the offset and biased one
# sensor by +0.034 µL/min. The settled point is therefore found from the data:
# the first instant after which the 60-min moving mean (stepped every 10 min)
# stays within SETTLE_TOL of the final level (the mean of the last quarter).
# 60 min, not 10: single 10-min means wander by more than SETTLE_TOL with the
# room's temperature swings, and requiring each of them to hold discarded most
# of a 40 h record.
BLOCK_MIN = 10.0
WINDOW_BLOCKS = 6        # 6 × 10 min = the 60-min moving mean
SETTLE_TOL = 0.01        # µL/min — a tenth of the sensor's 0.1 µL/min output step
MIN_STABLE_MIN = 120.0   # less settled time than this → DOUBTFUL


def find_settling(recs, t0: float):
    """(minutes after t0 at which the zero is settled, final level), or (None, None)."""
    acc = {}
    for r in recs:
        k = int((r[0] - t0) // (BLOCK_MIN * 60))
        if k >= 0:
            a = acc.setdefault(k, [0.0, 0])
            a[0] += r[1]
            a[1] += 1
    blocks = [(k, a[0] / a[1] / core.SCALE_FLOW) for k, a in sorted(acc.items())
              if a[1] >= 100]
    if len(blocks) < WINDOW_BLOCKS + 2:
        return None, None
    tail = blocks[-max(3, len(blocks) // 4):]
    level = statistics.fmean(m for _, m in tail)
    rolling = [(blocks[i][0], statistics.fmean(m for _, m in blocks[i:i + WINDOW_BLOCKS]))
               for i in range(len(blocks) - WINDOW_BLOCKS + 1)]
    for i, (k, _) in enumerate(rolling):
        if all(abs(m - level) <= SETTLE_TOL for _, m in rolling[i:]):
            return k * BLOCK_MIN, level
    return None, level


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bin_file")
    ap.add_argument("--settle-min", type=float, default=None,
                    help="minutes discarded after the start of the static stretch "
                         "(default: detected — the first point after which the 60-min "
                         f"moving mean stays within ±{SETTLE_TOL} µL/min of the final level)")
    ap.add_argument("--from-min", type=float, default=None,
                    help="start of the static stretch, in minutes from the start of "
                         "the run (default: detected as the last flowing reading)")
    ap.add_argument("--flowing-ul-min", type=float, default=FLOWING_UL_MIN,
                    help=f"|flow| above which a reading belongs to the flowing phase "
                         f"(default {FLOWING_UL_MIN})")
    args = ap.parse_args()

    path = pathlib.Path(args.bin_file)
    if not path.is_file():
        print(f"file not found: {path}", file=sys.stderr)
        return 3
    recs = read_records(path)
    if not recs:
        print("no readings", file=sys.stderr)
        return 3

    t_run = recs[0][0]
    flowing = None
    if args.from_min is not None:
        t0 = t_run + args.from_min * 60
        origin = f"minute {args.from_min:.1f} (given)"
    else:
        thr = args.flowing_ul_min * core.SCALE_FLOW
        last = next((i for i in range(len(recs) - 1, -1, -1)
                     if abs(recs[i][1]) > thr), None)
        if last is None:
            t0, origin = t_run, "start of run (no flowing phase found)"
        else:
            t0 = recs[last][0]
            origin = f"capping detected at {(t0 - t_run)/3600:.2f} h"
            fl = [r[1] / core.SCALE_FLOW for r in recs[:last + 1]
                  if not r[3] & 1]
            if fl:
                flowing = (len(fl), statistics.fmean(fl), (t0 - t_run) / 3600)
    static = [r for r in recs if r[0] >= t0]
    if args.settle_min is not None:
        settle_min, how = args.settle_min, "given"
    else:
        settle_min, _ = find_settling(static, t0)
        how = "detected"
        if settle_min is None:
            print(f"the zero after {origin} never settles within ±{SETTLE_TOL} µL/min "
                  f"(or the static stretch is under {(WINDOW_BLOCKS+2)*BLOCK_MIN:.0f} min) — run longer "
                  f"after capping, or pass --settle-min", file=sys.stderr)
            return 3
    kept = [r for r in static if r[0] - t0 >= settle_min * 60]
    if len(kept) < 600:
        print(f"only {len(kept)} readings after {origin} + {settle_min:.0f} min "
              f"settling — run longer after capping", file=sys.stderr)
        return 3

    flow = [r[1] / core.SCALE_FLOW for r in kept]
    temp = [r[2] / core.SCALE_TEMPERATURE for r in kept]
    air = sum(1 for r in kept if r[3] & 1)
    ts = [(r[0] - kept[0][0]) / 3600.0 for r in kept]

    mean = statistics.fmean(flow)
    sd = statistics.stdev(flow)
    se = sd / len(flow) ** 0.5
    # least-squares slope, µL/min per hour
    tm = statistics.fmean(ts)
    sxx = sum((x - tm) ** 2 for x in ts)
    drift = sum((x - tm) * (y - mean) for x, y in zip(ts, flow)) / sxx if sxx else 0.0

    print(f"\nOffset report — {path.name}")
    if flowing:
        print(f"  flowing phase     {flowing[2]:.2f} h, mean {flowing[1]:.2f} µL/min "
              f"({flowing[1]*0.06:.2f} mL/h)")
    print(f"  static from       {origin}")
    stable_min = (kept[-1][0] - kept[0][0]) / 60
    print(f"  settling          {settle_min:.0f} min after the start of the static "
          f"stretch ({how})")
    print(f"  readings used     {len(kept):,} ({stable_min:.1f} min of settled zero)")
    print(f"  offset (mean)     {mean:+.4f} µL/min")
    print(f"  std deviation     {sd:.4f} µL/min")
    print(f"  standard error    {se:.5f} µL/min")
    print(f"  drift             {drift:+.4f} µL/min per hour")
    print(f"  sensor temp       {statistics.fmean(temp):.2f} °C "
          f"(min {min(temp):.2f}, max {max(temp):.2f})")
    print(f"  air readings      {air:,} ({air/len(kept)*100:.2f} %)")

    verdict, why = 0, []
    if air / len(kept) > AIR_MAX:
        verdict = 2
        why.append("air readings: a bubble is trapped in the sensor — refill and repeat")
    if stable_min < MIN_STABLE_MIN:
        verdict = max(verdict, 1)
        why.append(f"only {stable_min:.0f} min of settled zero (want ≥ "
                   f"{MIN_STABLE_MIN:.0f}): repeat with a longer static stretch")
    if abs(drift) > DRIFT_WARN:
        verdict = max(verdict, 1)
        why.append(f"drift {drift:+.3f} µL/min/h: the sensor may not be at equilibrium "
                   f"— use a longer settling period or a longer run")
    print("\n  " + ("USABLE" if verdict == 0 else "DOUBTFUL" if verdict == 1 else "NOT USABLE"))
    for w in why:
        print("   - " + w)
    print()
    return verdict


if __name__ == "__main__":
    sys.exit(main())
