"""
aggregate.py — averaging a condition's replicates.

All runs arrive already re-based so t = 0 is their own infusion onset, which is
what makes averaging meaningful at all. This module decides how far the common
grid extends and how many replicates back each point, and writes the
condition-level stats.json.
"""
import json
import pathlib

import numpy as np

from config import EXPECTED_REPLICATES, methodology_provenance


def average_condition(runs: list, dt_h: float = None) -> dict:
    """
    Mean ± SD of the three curves across a condition's replicates
    (methodology.txt § Curvas 3).

    All runs already share t = 0 at their own onset, which is what makes averaging
    meaningful — averaging on the raw logging timebase would smear the onset step
    across however much dead time each run happened to have.

    The grid runs to the LONGEST replicate, not the shortest. Stopping at the
    shortest keeps n constant but truncates the end-of-infusion decay out of the
    figure for every other replicate — and that decay is the feature these curves
    exist to show. So instead: each point averages the replicates that still have
    data there, `n_at_t` records how many that is, and `full_coverage_h` marks
    where n first drops below the total. The plot shades past that mark, so the
    reader can see exactly where the mean stops being backed by all replicates
    rather than having to infer it.
    """
    if not runs:
        return {}
    t_end = max(float(r["t_h"][-1]) for r in runs)
    t_full = min(float(r["t_h"][-1]) for r in runs)
    if dt_h is None:
        dt_h = float(np.median([np.median(np.diff(r["t_h"])) for r in runs]))
    if not np.isfinite(dt_h) or dt_h <= 0 or t_end <= 0:
        return {}
    grid = np.arange(0.0, t_end, dt_h)
    if len(grid) < 4:
        return {}

    out = {"t_h": grid, "n_runs": len(runs),
           "runs": [r["experiment_name"] for r in runs],
           "span_h": round(float(t_end), 4),
           "full_coverage_h": round(float(t_full), 4)}

    for kind, key in (("flow", "flow_ml_hr"),
                      ("temperature", "temperature_C"),
                      ("volume", "volume_ml")):
        rows = []
        for r in runs:
            # Interpolate inside each run's own span and leave NaN beyond its end,
            # so a finished run is never extrapolated flat into another's tail.
            y = np.interp(grid, r["t_h"], r[key], left=np.nan, right=np.nan)
            rows.append(y)
        stack = np.vstack(rows)
        n_at_t = np.sum(np.isfinite(stack), axis=0)
        mean = np.full(len(grid), np.nan)
        sd = np.zeros(len(grid))
        has_any = n_at_t >= 1
        # Column-wise, guarding the two degenerate cases explicitly instead of
        # letting numpy warn: a grid point with no replicate left stays NaN, and one
        # with a single replicate has no spread to estimate (ddof=1 would divide by
        # zero), so its SD is 0 rather than NaN — the mean there is still a real
        # measurement, just an unreplicated one, which `n_at_t` records.
        if has_any.any():
            mean[has_any] = np.nanmean(stack[:, has_any], axis=0)
        multi = n_at_t >= 2
        if multi.any():
            sd[multi] = np.nanstd(stack[:, multi], axis=0, ddof=1)
        out[f"{kind}_mean"] = mean
        out[f"{kind}_sd"] = sd
        if kind == "flow":
            out["n_at_t"] = n_at_t
    return out


def write_condition_stats(condition: str, curve: dict, runs: list,
                          out_dir: pathlib.Path) -> None:
    """Condition-level stats.json alongside the three averaged curves."""
    v = [r["stats"]["volume"]["V_dispensed_mL"] for r in runs]
    teff = [r["stats"]["teff"]["T_eff_h"] for r in runs
            if r["stats"]["teff"]["T_eff_h"] is not None]
    stats = {
        "condition": condition,
        "n_replicates": len(runs),
        "replicates": [r["experiment_name"] for r in runs],
        "averaging": {
            "aligned_on": "infusion onset (t = 0)",
            "common_grid_span_h": curve.get("span_h"),
            "grid_step_h": round(float(curve["t_h"][1] - curve["t_h"][0]), 6),
            "note": "grid stops at the shortest replicate so n is constant across it",
        },
        "V_dispensed_mL": {
            "mean": round(float(np.mean(v)), 4),
            "sd": round(float(np.std(v, ddof=1)), 4) if len(v) > 1 else None,
            "min": round(float(np.min(v)), 4),
            "max": round(float(np.max(v)), 4),
            "per_replicate": {r["experiment_name"]:
                              r["stats"]["volume"]["V_dispensed_mL"] for r in runs},
        },
        "T_eff_h": {
            "mean": round(float(np.mean(teff)), 4) if teff else None,
            "n_with_teff": len(teff),
        },
        "flow_mean_ml_hr_over_grid": round(float(np.nanmean(curve["flow_mean"])), 4),
        "temperature_mean_C_over_grid": round(
            float(np.nanmean(curve["temperature_mean"])), 4),
        "methodology": methodology_provenance(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
