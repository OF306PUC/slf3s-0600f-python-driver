"""
pipeline.py — the per-run pipeline: one CSV in, one set of results out.

`process_file` is the only place that knows the full order of operations, and it
delegates every actual computation to signal_processing and stats. It is also
where a run is judged usable: `IncompleteRun` is raised — not caught — when the
acquisition did not finish, when no infusion onset can be found, or when the
record ends before the infusion does. Those runs produce no figures and no stats,
and are inventoried by report.py instead.
"""
import json
import pathlib

import numpy as np

import figures
import signal_processing as sp
import stats as st
import utils_mpl
from config import (
    BUBBLE_GUARD_SAMPLES, BUBBLE_MAX_PLATEAU_MIN, BUBBLE_SPIKE_FRAC,
    BUBBLE_ZERO_FRAC,
    MA_WINDOW_MIN, NOM_FLOW_CORR_ML_HR, NOM_VOLUME_ML, OFFSET_WINDOW_H, REL_ERROR,
    STUDY_CONDITIONS, TEFF_FRAC_OF_NOMINAL, UL_MIN_TO_ML_HR,
    colour_for, description_for, linestyle_for, methodology_provenance,
    resolve_condition,
)
from csv_io import load_csv, parse_metadata, read_footer_status


class IncompleteRun(Exception):
    """
    A run that exists but cannot yield a valid result.

    Distinct from an unexpected failure: this is a diagnosis, so it carries the
    reason and lands in the inventory of incomplete + not-performed experiments
    rather than in the error list. No figures and no stats.json are written for it.
    """

    def __init__(self, reason: str, condition: str, replicate: str,
                 detail: dict = None):
        super().__init__(reason)
        self.reason = reason
        self.condition = condition
        self.replicate = replicate
        self.detail = detail or {}

def process_file(csv_path: pathlib.Path, out_root: pathlib.Path,
                 offset_window_h: float, ma_window_min: float) -> dict:
    print(f"\n[analyse] {csv_path.name}")

    # ── input ─────────────────────────────────────────────────────────────────
    metadata = parse_metadata(csv_path)
    metadata_cfg = metadata.get("configuration", "")
    experiment_rep = metadata.get("experiment_rep", csv_path.stem)
    condition, cond_source = resolve_condition(metadata_cfg, csv_path.parent.name)
    experiment_name = f"{condition or metadata_cfg or csv_path.stem}_{experiment_rep}"
    colour = colour_for(condition)
    if cond_source == "folder":
        print(f"  [WARN] metadata says configuration='{metadata_cfg}', not a study "
              f"condition — using folder name '{condition}'")
    elif cond_source == "unresolved":
        print(f"  [WARN] condition unresolved (metadata='{metadata_cfg}', "
              f"folder='{csv_path.parent.name}') — excluded from condition averaging")

    # A run whose acquisition never finished cleanly is rejected here, before any
    # figure or stat is produced: the logger itself is the authority on that, and
    # publishing a curve from a truncated record would present an accident as a
    # result. Rejected runs are inventoried in the markdown report alongside the
    # ones that were never performed.
    footer = read_footer_status(csv_path)
    if not footer["footer_present"] or footer["status"] != "COMPLETE":
        raise IncompleteRun(
            f"acquisition did not finish cleanly (footer: {footer['status']})",
            condition, experiment_rep, {"footer": footer},
        )

    df = load_csv(csv_path)
    t = df["UTC_Time"].to_numpy(dtype=np.float64)
    t_log_h = (t - t[0]) / 3600.0
    Ts = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    fs_hz = 1.0 / Ts
    print(f"  Rows {len(df):,}   Ts = {Ts*1000:.0f} ms   Total = {t_log_h[-1]:.2f} h")

    flow_raw = df["Flow_ul_min"].to_numpy(dtype=np.float64)
    temp_raw = df["DeviceTemperature_degC"].to_numpy(dtype=np.float64)

    # ── air-in-line ───────────────────────────────────────────────────────────
    air = sp.air_mask_of(df)
    air_info = sp.air_report(df, air, t_log_h, offset_window_h)
    print(f"  air {100*air_info['fraction_total']:.1f}% of run in "
          f"{air_info['n_intervals']} interval(s); purge ends at "
          f"{air_info['leading_air_end_h']} h")

    # ── offset correction (applied to EVERY sample) ───────────────────────────
    offs = sp.offset_report(flow_raw, t_log_h, air, offset_window_h,
                         air_info["leading_air_end_h"])
    flow_corr = flow_raw - offs["offset_ul_min"]
    clip = " (clipped to purge end)" if offs["window_clipped_to_purge_end"] else ""
    print(f"  offset {offs['offset_ul_min']:+.3f} µL/min from first "
          f"{offs['window_h']} h{clip} ({offs['n_samples']} samples, "
          f"{100*(offs['air_fraction_in_window'] or 0):.0f}% air)")
    if offs["implausible"]:
        raise IncompleteRun(
            f"already infusing when logging started (offset "
            f"{offs['offset_ul_min']:+.1f} µL/min exceeds "
            f"{offs['implausible_threshold_ul_min']:.1f}), so there is no zero-flow "
            f"period to correct against",
            condition, experiment_rep, {"offset_ul_min": offs["offset_ul_min"]},
        )

    # ── bubble transits ───────────────────────────────────────────────────────
    # Run AFTER the offset because the spike test is on the corrected flow, and
    # BEFORE onset/keep because a bubble sample must not qualify as flow anywhere.
    # The offset window itself is deliberately left alone: it was checked across
    # all 18 runs and none contains a spike, so the zero is not contaminated and
    # excluding bubbles there would change nothing except add a code path.
    max_plateau = max(1, int(round(BUBBLE_MAX_PLATEAU_MIN * 60.0 / Ts)))
    bubble = sp.bubble_mask(flow_corr, df, BUBBLE_SPIKE_FRAC, BUBBLE_ZERO_FRAC,
                            BUBBLE_GUARD_SAMPLES, max_plateau)
    bubble_info = sp.bubble_report(bubble, t_log_h, flow_corr, t)
    excluded = air | bubble
    if bubble_info["n_samples"]:
        print(f"  bubbles {bubble_info['n_samples']} sample(s) in "
              f"{bubble_info['n_events']} event(s), "
              f"{bubble_info['volume_removed_mL']:+.3f} mL removed")

    # ── onset → t = 0, and cut everything before it ───────────────────────────
    # Threshold is 3σ of the offset window above the corrected zero; the air-free
    # requirement is what keeps t = 0 off the purge (see find_onset). Bubble
    # samples are excluded from the same test: a transit spike is above any
    # sensible threshold and must never be allowed to define t = 0.
    onset_threshold = 3.0 * offs["sigma_ul_min"]
    onset_s = sp.find_onset(t - t[0], flow_corr, onset_threshold, ~excluded)
    onset_h = float(onset_s / 3600.0) if onset_s is not None else None
    if onset_h is None:
        raise IncompleteRun(
            "infusion onset not detected — no sustained air-free flow above "
            f"{onset_threshold:.3f} µL/min",
            condition, experiment_rep,
        )

    keep = (t_log_h >= onset_h) & (~excluded) & np.isfinite(flow_corr)
    if keep.sum() < 10:
        raise ValueError("fewer than 10 usable samples after onset/air filtering")

    # Air samples are DROPPED, not NaN-filled: matplotlib then joins the
    # surviving points with a straight segment across the gap, which is the
    # interpolation methodology.txt § Curvas 6 asks for. The gap itself stays
    # visible because the interval is shaded.
    t_s = t[keep]
    t_h = t_log_h[keep] - onset_h
    q_corr = flow_corr[keep]
    temp = temp_raw[keep]

    # Shaded intervals: every excluded sample, air and bubble alike. They get one
    # shading because they mean the same thing to a reader — "no measurement of
    # liquid flow here" — while `stats.json` keeps the two counted separately for
    # anyone who needs to know which mechanism removed what.
    air_intervals = [
        [max(a - onset_h, float(t_h[0])), b - onset_h]
        for a, b in sp.contiguous_intervals(excluded, t_log_h) if b > onset_h
    ]

    # ── filtering, noise, volume ──────────────────────────────────────────────
    ma_window = max(3, int(round(ma_window_min * 60.0 / Ts)))
    q_filt = sp.moving_average(q_corr, ma_window)
    noise = st.noise_report(t_s, q_corr, q_filt, fs_hz)
    if noise.get("available"):
        print(f"  noise RMS {noise['residual_rms_ul_min']:.3f} µL/min, "
              f"SNR {noise['snr_db']} dB")

    # Dispensed volume integrates `q_corr` — the OFFSET-CORRECTED flow, air samples
    # already dropped. Integrating the raw signal would accumulate the zero-flow
    # offset over the whole run: at −0.5 µL/min across 80 h that is a ~2.4 mL bias,
    # small per sample and not small in the total.
    eps = REL_ERROR * np.abs(q_filt)
    vol_ml = sp.cumulative_volume_ml(t_s, q_corr)
    vol_hi = sp.cumulative_volume_ml(t_s, q_corr + eps)
    vol_lo = sp.cumulative_volume_ml(t_s, q_corr - eps)
    q_nom_ul_min = NOM_FLOW_CORR_ML_HR / UL_MIN_TO_ML_HR
    vol_nominal = sp.cumulative_volume_ml(t_s, np.full(len(t_s), q_nom_ul_min))
    v_disp = float(vol_ml[-1])

    # The end-of-infusion decay is the feature the curves exist to show, so a run
    # that never reaches it is incomplete by definition: the pump had not emptied
    # when logging stopped, and its profile cannot be compared end-to-end against
    # one that did.
    teff_threshold = TEFF_FRAC_OF_NOMINAL * (NOM_FLOW_CORR_ML_HR / UL_MIN_TO_ML_HR)
    teff_s = sp.find_teff(t_s - t_s[0], q_filt, teff_threshold)
    if teff_s is None:
        raise IncompleteRun(
            "end-of-infusion never reached — filtered flow does not settle below "
            f"{teff_threshold:.1f} µL/min ({100*TEFF_FRAC_OF_NOMINAL:.0f}% of "
            f"nominal) before the record ends, so the pump had not emptied",
            condition, experiment_rep,
        )
    teff_h = float(teff_s / 3600.0)

    # ── reports ───────────────────────────────────────────────────────────────
    corr = st.cross_correlate_q_T(q_filt, temp, Ts)
    temps = st.temperature_report(temp)
    chain = st.filter_chain_report(fs_hz, ma_window)

    stats = {
        "experiment_name": experiment_name,
        "condition": condition or None,
        # The catheter configuration in words, resolved from config — so a
        # stats.json read on its own says what was in line, not just a code.
        "condition_description": description_for(condition),
        "condition_source": cond_source,
        "metadata_configuration": metadata_cfg,
        "replicate": experiment_rep,
        "metadata": metadata,
        "methodology": methodology_provenance(),
        "acquisition": {
            "sampling_interval_s": round(Ts, 4),
            "f_s_hz": round(fs_hz, 6),
            "logged_duration_h": round(float(t_log_h[-1]), 4),
            "n_samples_logged": int(len(df)),
            "n_samples_analysed": int(keep.sum()),
        },
        "air": air_info,
        "bubbles": bubble_info,
        "offset_correction": offs,
        "onset": {"t_onset_h_on_logging_timebase": round(onset_h, 4),
                  "detected": onset_s is not None,
                  "note": "series are re-based so t = 0 is the onset"},
        "filtering": {"window_min": ma_window_min,
                      "window_samples": int(ma_window)},
        "filter_chain": chain,
        "noise": noise,
        "volume": {
            "V_dispensed_mL": round(v_disp, 4),
            "V_dispensed_lower_mL": round(float(vol_lo[-1]), 4),
            "V_dispensed_upper_mL": round(float(vol_hi[-1]), 4),
            "V_nominal_over_same_span_mL": round(float(vol_nominal[-1]), 4),
            "deviation_from_reservoir_mL": round(v_disp - NOM_VOLUME_ML, 4),
            "integration_method": "cumulative trapezoid on a non-uniform grid",
        },
        "teff": {"T_eff_h": round(teff_h, 4) if teff_h is not None else None},
        "temperature": temps,
        "flow_temperature_correlation": corr,
        "analysed_span_h": round(float(t_h[-1]), 4),
    }

    out_dir = out_root / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"  V_disp={v_disp:.2f} mL   span={t_h[-1]:.2f} h   "
          f"T_eff={teff_h if teff_h is None else round(teff_h, 2)} h")

    # ── three curves ──────────────────────────────────────────────────────────
    utils_mpl.set_global()
    q_raw_ml_hr = q_corr * UL_MIN_TO_ML_HR
    q_filt_ml_hr = q_filt * UL_MIN_TO_ML_HR
    band_hi = (q_filt + eps) * UL_MIN_TO_ML_HR
    band_lo = (q_filt - eps) * UL_MIN_TO_ML_HR
    figures.plot_flow(t_h, q_raw_ml_hr, q_filt_ml_hr, band_lo, band_hi, air_intervals,
              out_dir, experiment_name, colour, ma_window_min)
    figures.plot_temperature(t_h, temp, air_intervals, out_dir, experiment_name, colour)
    figures.plot_volume(t_h, vol_ml, vol_lo, vol_hi, vol_nominal, air_intervals,
                out_dir, experiment_name, colour)
    print(f"  → {out_dir}/")

    return {
        "experiment_name": experiment_name,
        "condition": condition,
        "replicate": experiment_rep,
        "colour": colour,
        "linestyle": linestyle_for(experiment_rep),
        "t_h": t_h,
        "flow_ml_hr": q_filt_ml_hr,
        # The unfiltered (offset-corrected) trace is kept alongside the filtered one
        # so the replicate matrix can show both without re-reading and re-processing
        # every CSV. Condition averaging uses `flow_ml_hr` and ignores this.
        "flow_raw_ml_hr": q_raw_ml_hr,
        "air_intervals": air_intervals,
        "temperature_C": temp,
        "volume_ml": vol_ml,
        "stats": stats,
    }
