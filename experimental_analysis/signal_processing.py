"""
signal_processing.py — turning raw samples into the signal the results rest on.

The order here is the order it is applied, and each step exists because of
something the sensor or the protocol does:

  air handling        the sensor flags air in the line; those samples are not
                      measurements of liquid flow and are excluded
  offset correction   the zero-flow reading is not zero, so it is measured during
                      the purge and subtracted from every sample
  filtering           a centred moving average over the corrected flow
  integration         cumulative trapezoid for the dispensed volume
  event detection     infusion onset (which becomes t = 0) and its end

Everything is a pure function of arrays: no plotting, no file access, no reporting.
"""
import numpy as np
import pandas as pd

from config import (
    MA_WINDOW_MIN, NOM_FLOW_ML_HR, OFFSET_IMPLAUSIBLE_FRAC, UL_MIN_TO_ML_HR,
)


# methodology.txt § Curvas 6: air-flagged samples are not considered, leaving a
# gap; the renderer interpolates a straight line across it and the interval is
# shaded grey. Dropping the samples (rather than setting them to NaN) is what
# produces that interpolation — NaN would break the line instead.

def air_mask_of(df: pd.DataFrame) -> np.ndarray:
    return df["Flag_Air"].to_numpy(dtype=np.float64) > 0.5


def contiguous_intervals(mask: np.ndarray, t: np.ndarray) -> list:
    """[(start, end), …] for each contiguous True run in `mask`, in units of `t`."""
    if not mask.any():
        return []
    edges = np.diff(mask.astype(np.int8))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0])
    if mask[0]:
        starts.insert(0, 0)
    if mask[-1]:
        ends.append(len(mask) - 1)
    return [(float(t[s]), float(t[e])) for s, e in zip(starts, ends)]


def air_report(df: pd.DataFrame, air: np.ndarray, t_h: np.ndarray,
               offset_window_h: float) -> dict:
    """
    Air-in-line QC, plus the two sensor flags worth recording alongside it.

    `leading_air_end_h` is the end of the air block starting at t = 0 — the
    objective, data-derived instant the line finished purging. Measured across
    this campaign it lands at 1.16–2.22 h and coincides with the detected onset to
    within one sample, which is what makes it trustworthy.
    """
    intervals = contiguous_intervals(air, t_h)
    leading_end = None
    if intervals and intervals[0][0] <= t_h[0] + 1e-9:
        leading_end = intervals[0][1]

    high = df.get("Flag_High_Flow")
    smoothing = df.get("Exp_Smoothing")
    in_window = t_h <= offset_window_h

    return {
        "fraction_total": round(float(air.mean()), 6),
        "n_samples": int(air.sum()),
        "n_intervals": len(intervals),
        "leading_air_end_h": round(leading_end, 4) if leading_end is not None else None,
        "fraction_in_offset_window": (
            round(float(air[in_window].mean()), 6) if in_window.any() else None
        ),
        "intervals_h": [[round(a, 4), round(b, 4)] for a, b in intervals[:20]],
        "high_flow_fraction": (
            round(float((high.to_numpy(dtype=np.float64) > 0.5).mean()), 6)
            if high is not None else None
        ),
        "exp_smoothing_fraction": (
            round(float((smoothing.to_numpy(dtype=np.float64) > 0.5).mean()), 6)
            if smoothing is not None else None
        ),
    }


def bubble_mask(flow_corr: np.ndarray, df: pd.DataFrame, spike_frac: float,
                zero_frac: float, guard: int, max_plateau: int) -> np.ndarray:
    """
    Samples belonging to a bubble transit, which `Flag_Air` does not fully cover.

    A gas front crossing the measurement section reads as a burst, not as air: a
    large excursion as it enters, a near-zero plateau while it occupies the sensor,
    and a second excursion as it leaves. The sensor flags the plateau far more
    reliably than the spikes — across this campaign 143 of 167 excursions above
    1.5x nominal carry no air flag, and they integrate into the dispensed volume as
    if they were flow.

    The mask is the union of three parts:

      spike    |q| over `spike_frac` x nominal, OR Flag_High_Flow set. Both are
               needed: C1a_rep_3 has 58 excursions the sensor never flagged, and
               C2_rep_3 has flags with no large excursion.
      plateau  a contiguous near-zero run that TOUCHES a spike and is no longer
               than `max_plateau` samples. BOTH conditions are load-bearing. The
               end of infusion is legitimately near zero, so an unconditional
               near-zero rule would delete the decay this study exists to measure;
               and "touches a spike" alone is not enough either, because a spike
               landing at the start of that decay makes the whole tail one
               contiguous run — C2_rep_2 lost 18.5 h that way and was wrongly
               demoted to "incomplete" before the length cap existed.
      guard    `guard` samples on each side of a spike, because the sensor's IIR
               smoothing spreads a step into its neighbours.

    Returns a boolean mask over all samples. Pure function of its inputs.
    """
    nominal = NOM_FLOW_ML_HR / UL_MIN_TO_ML_HR
    high = df.get("Flag_High_Flow")
    high_mask = (
        high.to_numpy(dtype=np.float64) > 0.5 if high is not None
        else np.zeros(len(flow_corr), dtype=bool)
    )
    spike = (np.abs(flow_corr) > spike_frac * nominal) | high_mask
    if not spike.any():
        return np.zeros(len(flow_corr), dtype=bool)

    # Guard band: dilate the spike mask by `guard` samples on each side.
    mask = spike.copy()
    for k in range(1, guard + 1):
        mask[k:] |= spike[:-k]
        mask[:-k] |= spike[k:]

    # Plateau: keep only the near-zero runs that touch the dilated spike mask.
    near_zero = np.abs(flow_corr) < zero_frac * nominal
    if near_zero.any():
        # Label contiguous near-zero runs, then keep a run whole if any of its
        # samples is adjacent to a spike. Done run-wise rather than sample-wise so
        # a long plateau is removed entirely, not just at its two ends.
        edges = np.diff(near_zero.astype(np.int8))
        starts = list(np.where(edges == 1)[0] + 1)
        ends = list(np.where(edges == -1)[0])
        if near_zero[0]:
            starts.insert(0, 0)
        if near_zero[-1]:
            ends.append(len(near_zero) - 1)
        for s, e in zip(starts, ends):
            if (e - s + 1) <= max_plateau and mask[s:e + 1].any():
                mask[s:e + 1] = True
    return mask


def bubble_report(bubble: np.ndarray, t_h: np.ndarray, flow_corr: np.ndarray,
                  t_s: np.ndarray) -> dict:
    """
    What the bubble mask removed — reported so a result can be audited against it.

    `volume_removed_mL` is the excess those samples CARRIED, relative to the median
    of what survives. It is deliberately NOT the net change in dispensed volume,
    and the two differ substantially: dropping a sample does not subtract its
    contribution, it hands the span to the trapezoid, which bridges the gap at the
    level of the surviving neighbours. In C1a_rep_3 this field reads +1.549 mL while
    the dispensed volume moves by 0.05 mL. Read it as "how much artefact was in
    these samples", never as "how much the result changed".
    """
    intervals = contiguous_intervals(bubble, t_h)
    kept = flow_corr[~bubble & np.isfinite(flow_corr)]
    baseline = float(np.median(kept)) if kept.size else 0.0
    removed_ml = 0.0
    if bubble.any() and len(t_s) == len(flow_corr):
        Ts = float(np.median(np.diff(t_s))) if len(t_s) > 1 else 0.0
        removed_ml = float(np.sum(flow_corr[bubble] - baseline) * Ts / 60000.0)
    return {
        "n_samples": int(bubble.sum()),
        "fraction_total": round(float(bubble.mean()), 6),
        "n_events": len(intervals),
        "volume_removed_mL": round(removed_ml, 4),
        "baseline_ul_min": round(baseline, 4),
        "intervals_h": [[round(a, 4), round(b, 4)] for a, b in intervals[:20]],
    }


def offset_report(flow_raw: np.ndarray, t_h: np.ndarray, air: np.ndarray,
                  window_h: float, leading_air_end_h: float = None) -> dict:
    """
    Zero-flow offset, subtracted from EVERY sample.

    methodology.txt § Curvas 1 sets the window at the first 1.5–2 h; § Curvas 6
    excludes air samples from the curves. Those two collide here, because the
    measured purge period (1.16–2.22 h across this campaign) IS that window:
    excluding air would leave it empty in 17 of 18 runs.

    Resolved by scope. The offset window KEEPS its air samples — during the purge
    no liquid is flowing, which is precisely the zero-flow condition an offset
    should be measured under — while the air exclusion applies to the curves and
    the volume integral, where an air reading produces the unwanted overshoot.

    The window is additionally CLIPPED at the end of the purge, and that clip is
    load-bearing rather than cosmetic. The purge end varies per run (1.16–2.22 h),
    so a fixed 2 h window overruns it in the fast runs: C0b's three replicates
    purge at 1.16–1.39 h, and measuring their "zero-flow offset" out to 2 h
    averaged in an hour of real infusion. The offset came out an order of magnitude
    too large, tripped the implausibility gate, and knocked all three out of their
    own condition average — a whole condition lost to a constant. Clipping at the
    sensor-derived purge end keeps the window inside the no-flow period on every
    run while staying within the instructed 1.5–2 h range.
    """
    requested_h = window_h
    if leading_air_end_h is not None and 0.0 < leading_air_end_h < window_h:
        window_h = leading_air_end_h
    in_window = t_h <= window_h
    q = flow_raw[in_window]
    q = q[np.isfinite(q)]
    if q.size == 0:
        raise ValueError(f"No finite samples in the first {window_h} h.")

    mu = float(np.mean(q))
    sigma = float(np.std(q, ddof=1)) if q.size > 1 else 0.0
    nominal_ul_min = NOM_FLOW_ML_HR / UL_MIN_TO_ML_HR

    report = {
        "window_requested_h": requested_h,
        "window_h": round(window_h, 4),
        "window_clipped_to_purge_end": bool(window_h < requested_h),
        "n_samples": int(q.size),
        "offset_ul_min": round(mu, 4),
        "sigma_ul_min": round(sigma, 4),
        "detection_threshold_ul_min": round(mu + 3.0 * sigma, 4),
        "air_fraction_in_window": (
            round(float(air[in_window].mean()), 6) if in_window.any() else None
        ),
        "implausible": bool(abs(mu) > OFFSET_IMPLAUSIBLE_FRAC * nominal_ul_min),
        "implausible_threshold_ul_min": round(
            OFFSET_IMPLAUSIBLE_FRAC * nominal_ul_min, 4
        ),
        "airfree_variant": None,
    }

    airfree = in_window & (~air) & np.isfinite(flow_raw)
    if airfree.sum() >= 10:
        qa = flow_raw[airfree]
        report["airfree_variant"] = {
            "n_samples": int(qa.size),
            "offset_ul_min": round(float(np.mean(qa)), 4),
            "sigma_ul_min": round(float(np.std(qa, ddof=1)), 4),
        }
    return report


def moving_average(flow: np.ndarray, window: int) -> np.ndarray:
    """Centred rolling mean over `window` samples, NaN-tolerant."""
    s = pd.Series(flow, dtype="float64")
    return s.rolling(window, center=True, min_periods=1).mean().to_numpy()


def cumulative_volume_ml(t_s: np.ndarray, q_ul_min: np.ndarray) -> np.ndarray:
    """
    Cumulative dispensed volume (mL) from flow (µL/min) by the trapezoidal rule on
    a non-uniform grid.

    This replaces an RK4 implementation that could not do better: with q known
    only at the sample instants, RK4's midpoint stages collapse to
    k₂ = k₃ = ½(q[i−1]+q[i]) and Simpson's weights reduce the step to exactly
    (h/2)(q[i−1]+q[i]) — the trapezoid. Same numbers, a quarter of the code, and
    it no longer claims an accuracy it never had.

    Because air samples are dropped before this is called, a gap is bridged by one
    wide trapezoid — the same straight-line interpolation the plotted curve shows.
    """
    n = len(t_s)
    if n != len(q_ul_min):
        raise ValueError("t_s and q_ul_min must have the same length.")
    if n < 2:
        return np.zeros(n)
    q_ml_s = np.asarray(q_ul_min, dtype=np.float64) / 60000.0
    dv = 0.5 * (q_ml_s[1:] + q_ml_s[:-1]) * np.diff(t_s)
    return np.concatenate(([0.0], np.cumsum(dv)))


def _sustained_crossing(t: np.ndarray, q: np.ndarray, q_th: float,
                        sustained_s: float, above: bool,
                        valid: np.ndarray = None):
    """
    Start time of the first run where q stays on one side of q_th long enough.

    `valid` (optional) additionally requires the sample to be usable — used by
    find_onset to demand an air-free line. A sample that fails it breaks the run
    rather than being skipped over, so a crossing cannot be assembled out of
    fragments separated by air.
    """
    run_start = None
    for i in range(len(t)):
        val = q[i]
        usable = np.isfinite(val) and (valid is None or bool(valid[i]))
        if not usable:
            run_start = None
            continue
        on_side = (val >= q_th) if above else (val < q_th)
        if on_side:
            if run_start is None:
                run_start = t[i]
            elif t[i] - run_start >= sustained_s:
                return run_start
        else:
            run_start = None
    return None


def find_onset(t: np.ndarray, q_corrected: np.ndarray, q_th: float,
               no_air: np.ndarray, sustained_min: float = 2.0):
    """
    Infusion onset — the instant that becomes t = 0 in every figure.

    Defined as the first sustained sample that is BOTH above the detection
    threshold AND air-free. Requiring air-free is not a refinement, it is what
    makes the alignment correct: the purge produces readings above threshold while
    the line still holds air, so a threshold-only test locks t = 0 onto the purge
    instead of the infusion. With the air condition, t = 0 is the first moment
    liquid is actually moving past the sensor, which is what makes the
    end-of-infusion decay land at a comparable place across replicates.

    Runs on the OFFSET-CORRECTED RAW flow, not the filtered profile: a moving
    average lags a step by about half its window, which would push every t = 0
    late by a window-dependent amount and defeat the point of aligning at all.
    Debounced over `sustained_min`, so a single noise spike cannot fire it.
    """
    return _sustained_crossing(t, q_corrected, q_th, sustained_min * 60.0,
                               above=True, valid=no_air)


def find_teff(t: np.ndarray, q: np.ndarray, q_th: float,
              sustained_min: float = 5.0):
    """
    First sustained crossing BELOW q_th on the filtered profile — the effective end
    of infusion, i.e. the foot of the soft decay the curves are meant to show.
    """
    return _sustained_crossing(t, q, q_th, sustained_min * 60.0, above=False)
