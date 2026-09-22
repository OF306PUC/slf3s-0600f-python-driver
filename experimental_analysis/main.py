#!/usr/bin/env python3
"""
main.py — entry point and orchestration for the flow-rate analysis.

Run from this directory:

    python3 main.py [csv_files...] [--output-dir results/]
                    [--offset-window-h 2.0] [--ma-window-min 10]
                    [--plot-format png] [--dpi 250]

With no CSV arguments, auto-discovers every run of the 8 study conditions under
Temp/C*/.

This module decides WHAT runs and IN WHAT ORDER, and nothing else — no maths, no
plotting, no formatting. Each step belongs to one module:

    config              the 8 conditions and the declared methodology
    csv_io              reading the logger's files
    signal_processing   air handling, offset, filtering, integration, events
    stats               noise, correlation, temperature, filter chain
    figures             the three curves, per run and per condition
    pipeline            one CSV → one set of results (or IncompleteRun)
    aggregate           averaging a condition's replicates
    report              summary.md

Outputs, per run: three curves (flow, temperature, volume) + stats.json.
Outputs, per condition: the same three curves averaged across its replicates.
Plus a cross-condition overlay, a replicate matrix (every run's raw + filtered flow
as small multiples), and summary.md.

Runs whose acquisition did not finish cleanly, that never reach an infusion onset,
or that never reach the end of infusion produce NO figures and NO stats: they are
inventoried in summary.md together with the replicates that were never performed.
"""
import argparse
import pathlib
import sys

import aggregate
import figures
import pipeline
import report
from config import EXPECTED_REPLICATES, MA_WINDOW_MIN, OFFSET_WINDOW_H, STUDY_CONDITIONS, colour_for
from figures import DEFAULT_DPI


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyse SLF3S-0600F DataLog CSV files."
    )
    parser.add_argument("csv_files", nargs="*",
        help="One or more {CONFIG}_{REP}.csv paths (default: all study-condition CSVs in Temp/C*/)")
    parser.add_argument("--output-dir", default="results/",
        help="Root output directory (default: results/)")
    parser.add_argument("--offset-window-h", type=float, default=OFFSET_WINDOW_H,
        help=f"Hours from logging start used to measure the zero-flow offset "
             f"(default: {OFFSET_WINDOW_H})")
    parser.add_argument("--ma-window-min", type=float, default=MA_WINDOW_MIN,
        help=f"Moving-average filter window in minutes (default: {MA_WINDOW_MIN:.0f})")
    parser.add_argument("--plot-format", choices=["png", "pdf", "svg"], default="png",
        help="Single figure output format (default: png)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
        help=f"Raster DPI, 200–300 recommended (default: {DEFAULT_DPI})")
    return parser.parse_args()


def discover_csv_files() -> tuple:
    """(files to analyse, files skipped) from Temp/C*/, filtered to the 8 conditions."""
    temp_dir = pathlib.Path(__file__).parent / "Temp"
    discovered = sorted(temp_dir.glob("C*/*.csv"))
    # `C0_baseline` is a PRELIMINARY run predating this protocol: logged at 1 Hz
    # instead of 10 s, no metadata header, no sample_index column. Auto-including
    # it would silently mix a different timebase into the comparison, so it is
    # skipped; name its path explicitly to analyse it alone.
    keep = [p for p in discovered if p.parent.name in STUDY_CONDITIONS]
    return keep, [p for p in discovered if p not in keep]


def main() -> None:
    args = parse_args()
    out_root = pathlib.Path(args.output_dir)

    figures.configure(args.plot_format, args.dpi)

    csv_files = [pathlib.Path(p) for p in args.csv_files]
    # The never-performed inventory only means anything when the whole campaign is
    # in view. With an explicit file list, every replicate the user did not name
    # would be reported as missing — 17 false entries in a 5-file test run.
    full_campaign = not csv_files
    if not csv_files:
        csv_files, skipped = discover_csv_files()
        if not csv_files:
            print("[ERROR] No study-condition CSV files found in Temp/C*/",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[analyse] Auto-discovered {len(csv_files)} file(s) across the "
              f"{len(STUDY_CONDITIONS)} study conditions")
        if skipped:
            print(f"[analyse] Skipped {len(skipped)} file(s) outside the study "
                  f"conditions (pass a path explicitly): "
                  + ", ".join(p.name for p in skipped))

    runs, incomplete, failed = [], [], []
    for csv_path in csv_files:
        if not csv_path.exists():
            print(f"[ERROR] File not found: {csv_path}", file=sys.stderr)
            failed.append(str(csv_path))
            continue
        try:
            runs.append(pipeline.process_file(csv_path, out_root, args.offset_window_h,
                                     args.ma_window_min))
        except pipeline.IncompleteRun as inc:
            print(f"  [INCOMPLETE] {inc.reason}")
            print("  → no figures and no stats written for this run")
            incomplete.append({
                "condition": inc.condition or csv_path.parent.name,
                "replicate": inc.replicate,
                "file": csv_path.name,
                "reason": inc.reason,
                "detail": inc.detail,
            })
        except Exception as exc:
            print(f"[ERROR] {csv_path}: {exc}", file=sys.stderr)
            failed.append(str(csv_path))

    # ── never-performed replicates ────────────────────────────────────────────
    # Grouped with the incomplete ones: from the standpoint of the study both are
    # conditions with no usable result, and a reader needs to see them together to
    # know what the campaign is actually missing.
    not_performed = []
    if full_campaign:
        seen = {(r["condition"], r["replicate"]) for r in runs}
        seen |= {(i["condition"], i["replicate"]) for i in incomplete}
        for cond, expected in EXPECTED_REPLICATES.items():
            for k in range(1, expected + 1):
                rep = f"rep_{k}"
                if (cond, rep) not in seen:
                    not_performed.append({"condition": cond, "replicate": rep})

    # ── per-condition averages ────────────────────────────────────────────────
    curves = {}
    for cond in STUDY_CONDITIONS:
        members = [r for r in runs if r["condition"] == cond]
        if not members:
            continue
        curve = aggregate.average_condition(members)
        if not curve:
            print(f"[analyse] {cond}: could not build a common grid — skipped")
            continue
        curves[cond] = curve
        cond_dir = out_root / f"_condition_{cond}"
        aggregate.write_condition_stats(cond, curve, members, cond_dir)
        for kind in ("flow", "temperature", "volume"):
            figures.plot_condition_mean(curve, kind, cond_dir, cond, colour_for(cond))
        print(f"[analyse] {cond}: mean of {curve['n_runs']} replicate(s) over "
              f"{curve['span_h']} h → {cond_dir}/")

    if runs:
        params = {"offset_window_h": args.offset_window_h,
                  "ma_window_min": args.ma_window_min,
                  "plot_format": args.plot_format, "dpi": args.dpi}
        report_path = report.write_summary_report(runs, incomplete, not_performed, curves,
                                      params, out_root)
        print(f"\n[analyse] summary → {report_path}")
    if runs:
        figures.plot_replicate_matrix(runs, out_root, args.ma_window_min)
        print(f"[analyse] replicate matrix ({len(runs)} runs) → "
              f"{out_root / 'comparison_replicate_matrix'}.{args.plot_format}")
    if len(curves) > 1:
        figures.plot_condition_overlay(curves, out_root)
        print(f"[analyse] condition overlay → "
              f"{out_root / 'comparison_condition_means'}.{args.plot_format}")

    if incomplete or not_performed:
        print(f"\n[analyse] {len(incomplete)} incomplete + {len(not_performed)} "
              f"never-performed run(s) produced NO results:")
        for i in incomplete:
            print(f"    incomplete    {i['condition']}/{i['replicate']}: {i['reason']}")
        for n in not_performed:
            print(f"    not performed {n['condition']}/{n['replicate']}")

    air_window = [r["experiment_name"] for r in runs
                  if (r["stats"]["offset_correction"]["air_fraction_in_window"] or 0) > 0.5]
    if air_window:
        print(f"\n[analyse] NOTE — in {len(air_window)}/{len(runs)} run(s) the "
              f"offset window is mostly air-flagged. That is expected: the purge "
              f"period IS the first ~2 h, and no liquid flows during it, which is "
              f"the condition an offset should be measured under.")

    if failed:
        print(f"\n[analyse] {len(failed)} file(s) failed: {failed}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[analyse] Done. Results in {out_root.resolve()}")


if __name__ == "__main__":
    main()
