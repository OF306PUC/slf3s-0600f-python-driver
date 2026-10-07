#!/usr/bin/env python3
"""
offset_report.py — zero-flow offset of one sensor, from a static run's binary.

    python3 offset_report.py Temp/<RUN>.bin [--settle-min 10] [--from-min M]

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bin_file")
    ap.add_argument("--settle-min", type=float, default=10.0,
                    help="minutes discarded at the start while the sensor and "
                         "liquid reach thermal equilibrium (default 10)")
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
    kept = [r for r in recs if r[0] - t0 >= args.settle_min * 60]
    if len(kept) < 600:
        print(f"only {len(kept)} readings after {origin} + {args.settle_min:.0f} min "
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
    print(f"  readings used     {len(kept):,} ({(kept[-1][0]-kept[0][0])/60:.1f} min, "
          f"{args.settle_min:.0f} min of settling discarded)")
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
