#!/usr/bin/env python3
"""
analyse.py — Post-experiment analysis for SLF3S-0600F DataLog CSV files.

Usage:
    python3 analyse.py <csv_files...> [--output-dir results/] [--plot-format pdf]
                       [--zero-drift-min 60] [--empty-pump-min 30]

Produces per-experiment stats (JSON) and three figures (flow, temperature,
volume) plus a cross-experiment comparison overlay when more than one CSV
is supplied.
"""
import argparse
import json
import pathlib
import sys

import utils_mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import correlate, correlation_lags

# ── constants ─────────────────────────────────────────────────────────────────

_UL_MIN_TO_ML_HR    = 60.0 / 1000.0    # µL/min → mL/hr
_MA_WINDOW_SEC      = 3600.0            # 1-hour moving-average window
_NOM_FLOW_ML_HR     = 5.0              # pump-set nominal flow rate in mL/hr
_NOM_VOLUME_ML      = 300.0            # nominal bag volume in mL
_ACTUAL_VOLUME_ML   = 245.0            # volume measured in beaker at end of experiment (mL)
_REL_ERROR          = 0.05             # ±5 % relative sensor error

# NaCl 0.9% temperature-corrected nominal flow
# The pump delivers 10% above calibration with NaCl 0.9% at the reference
# temperature. Operational temperature is lower than the calibration reference,
# so viscosity increases and flow drops by 2.3% per °C below the reference.
_NACL_FACTOR        = 1.10             # NaCl 0.9% excess above water calibration
_T_NOM_C            = 31.1            # pump calibration reference temperature (°C)
_T_OP_C             = 22.0            # actual operational temperature (°C)
_TEMP_CORR_PER_C    = 0.023           # 2.3% flow reduction per 1°C below reference
_NOM_FLOW_CORR_ML_HR = (
    _NOM_FLOW_ML_HR * _NACL_FACTOR
    * (1.0 - _TEMP_CORR_PER_C * (_T_NOM_C - _T_OP_C))
)

# Output figure formats, controlled by --plot-format (set in main()).
_PLOT_FORMATS = ("pdf", "png")


def _save_fig(fig, path_no_ext: str) -> None:
    """Save `fig` in each format selected via --plot-format (pdf and/or png)."""
    if "pdf" in _PLOT_FORMATS:
        utils_mpl.save_pdf(fig, f"{path_no_ext}.pdf")
    if "png" in _PLOT_FORMATS:
        utils_mpl.save_png(fig, f"{path_no_ext}.png")


# ── data loading ──────────────────────────────────────────────────────────────

def parse_metadata(csv_path: pathlib.Path) -> dict:
    metadata = {}
    with csv_path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped.startswith("#"):
                break
            if ":" in stripped:
                key, _, value = stripped[1:].partition(":")
                metadata[key.strip()] = value.strip()
    return metadata


def load_csv(csv_path: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, comment="#", thousands=",")
    if df.empty:
        raise ValueError(f"No data rows in {csv_path}")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["UTC_Time", "Flow_ul_min"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ── phase segmentation ────────────────────────────────────────────────────────

def segment_phases(df: pd.DataFrame, zero_drift_min: float, empty_pump_min: float) -> dict:
    t0 = df["UTC_Time"].iloc[0]
    elapsed = df["UTC_Time"] - t0
    z_end = zero_drift_min * 60.0
    e_end = z_end + empty_pump_min * 60.0
    return {
        "zero_drift": df[elapsed <= z_end],
        "empty_pump": df[(elapsed > z_end) & (elapsed <= e_end)],
        "active":     df[elapsed > e_end],
    }


def phase_boundaries_s(df: pd.DataFrame, zero_drift_min: float, empty_pump_min: float) -> tuple:
    t_zero_end = zero_drift_min * 60.0
    t_active_start = t_zero_end + empty_pump_min * 60.0
    return t_zero_end, t_active_start


# ── statistics ────────────────────────────────────────────────────────────────

def zero_drift_stats(phase_zero: pd.DataFrame) -> tuple:
    if phase_zero.empty:
        raise ValueError("Zero-drift phase is empty.")
    q = phase_zero["Flow_ul_min"].to_numpy(dtype=np.float64)
    mu = float(np.mean(q))
    sigma = float(np.std(q, ddof=1) if len(q) > 1 else 0.0)
    return mu, sigma, mu + 3.0 * sigma


# ── moving average (vectorized, ignores zeros) ────────────────────────────────

def _moving_avg(flow: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(flow)
    s_masked = s.where(s != 0.0, other=np.nan)
    result = s_masked.rolling(window, center=True, min_periods=1).mean()
    return result.fillna(0.0).to_numpy()


# ── RK4 volume integration ────────────────────────────────────────────────────

def rk4_integrate(t: np.ndarray, q_ul_min: np.ndarray) -> np.ndarray:
    n = len(t)
    if n != len(q_ul_min):
        raise ValueError("t and q_ul_min must have the same length.")
    if n < 2:
        return np.zeros(n)
    q_ml_s = q_ul_min / 60000.0
    volume = np.zeros(n)
    for i in range(1, n):
        h = t[i] - t[i - 1]
        k1 = q_ml_s[i - 1]
        k2 = 0.5 * (q_ml_s[i - 1] + q_ml_s[i])
        k3 = k2
        k4 = q_ml_s[i]
        volume[i] = volume[i - 1] + (h / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    return volume


# ── effective infusion duration ───────────────────────────────────────────────

def find_teff(t: np.ndarray, q: np.ndarray, q_th: float, sustained_min: float = 5.0):
    sustained_s = sustained_min * 60.0
    below_start = None
    for i in range(len(t)):
        if q[i] < q_th:
            if below_start is None:
                below_start = t[i]
            elif t[i] - below_start >= sustained_s:
                return below_start
        else:
            below_start = None
    return None


# ── infusion onset (mirror of find_teff) ──────────────────────────────────────

def find_onset(t: np.ndarray, q: np.ndarray, q_th: float, sustained_min: float = 2.0):
    """First *sustained* crossing ABOVE q_th — the infusion onset time.

    Mirror of find_teff. Operates on the RAW flow (not the 1-hour moving
    average) on purpose: the MA lags the true onset by ~half its window
    (~30 min), which would bias the alignment late. Debounced — q must stay at
    or above q_th for `sustained_min` minutes to count, and the returned onset
    is the time that sustained run *began* (so a single noise spike above the
    zero-drift threshold q_th = mu_q0 + 3σ does not trigger it). Returns the
    onset time in the same units as `t`, or None if it never crosses.
    """
    sustained_s = sustained_min * 60.0
    above_start = None
    for i in range(len(t)):
        if q[i] >= q_th:
            if above_start is None:
                above_start = t[i]
            elif t[i] - above_start >= sustained_s:
                return above_start
        else:
            above_start = None
    return None


# ── cross-correlation ─────────────────────────────────────────────────────────

def cross_correlate_q_T(q: np.ndarray, T: np.ndarray, dt_s: float) -> tuple:
    if len(q) != len(T):
        raise ValueError("q and T must have the same length.")
    if len(q) < 2:
        return 0, 0.0, 0.0, np.array([0.0])
    q_norm = (q - np.mean(q)) / (np.std(q) + 1e-12)
    T_norm = (T - np.mean(T)) / (np.std(T) + 1e-12)
    corr = np.correlate(q_norm, T_norm, mode="full") / len(q)
    peak_idx = int(np.argmax(np.abs(corr)))
    lag_samples = peak_idx - (len(q) - 1)
    r_max = float(corr[peak_idx])
    return lag_samples, lag_samples * dt_s, r_max, corr


# ── inter-run alignment refinement (cross-correlation) ────────────────────────

def _xcorr_residual_lag_h(t_ref, q_ref, t_run, q_run, max_lag_h: float = 2.0):
    """Residual time shift (h) to ADD to `t_run` so its flow profile best
    overlaps the reference, by cross-correlating the two q(t) curves.

    Both curves are resampled onto a common uniform grid over their *overlap*
    window (no zero-padding artifacts). We correlate their *derivatives* rather
    than the curves themselves: the flow profile is a long ~constant plateau,
    and a plateau's overlap triangle would bias a plain correlation toward
    lag 0, while de-meaning a one-sided profile injects a pedestal that biases
    it the other way. Differentiating turns the plateau into impulses at the
    onset rise / end-of-infusion fall (plus ripples) — exactly the alignable
    features — with no DC term. The search is capped at ±max_lag_h since onset
    alignment already removes the bulk offset. Returns 0.0 if no usable overlap.
    """
    lo = max(float(np.min(t_ref)), float(np.min(t_run)))
    hi = min(float(np.max(t_ref)), float(np.max(t_run)))
    if hi - lo <= 0:
        return 0.0
    dt_h = float(np.median(np.diff(t_ref)))
    if not np.isfinite(dt_h) or dt_h <= 0:
        return 0.0
    grid = np.arange(lo, hi, dt_h)
    if len(grid) < 4:
        return 0.0
    r = np.gradient(np.interp(grid, t_ref, q_ref))
    s = np.gradient(np.interp(grid, t_run, q_run))
    corr = correlate(r, s, mode="full")
    lags = correlation_lags(len(r), len(s), mode="full")
    # Restrict the search window to ±max_lag_h.
    max_lag_samples = int(round(max_lag_h / dt_h))
    keep = np.abs(lags) <= max_lag_samples
    corr, lags = corr[keep], lags[keep]
    if len(corr) == 0:
        return 0.0
    best_lag = int(lags[int(np.argmax(corr))])
    return best_lag * dt_h


# ── phase-boundary overlay ────────────────────────────────────────────────────

def _phase_vlines(ax, boundaries_h: list, labels: list, color: str = "grey") -> None:
    trans = ax.get_xaxis_transform()
    i = 0
    for x_h, lbl in zip(boundaries_h, labels):
        ha = "right" if i % 2 == 0 else "left"
        ax.axvline(x_h, color=color, linestyle="--", linewidth=0.9, alpha=0.8)
        ax.text(x_h, 0.95, lbl, transform=trans,
                rotation=90, va="top", ha=ha, fontsize=8, color=color)
        i += 1


# ── phase-boundary overlay ────────────────────────────────────────────────────

def _volume_level_vlines(ax, vol_levels: list, labels: list, color: str = "black") -> None:
    trans = ax.get_yaxis_transform()  # x: axes fraction, y: data coords
    for level, lbl in zip(vol_levels, labels):
        ax.axhline(level, color=color, linestyle="-.", linewidth=0.9, alpha=0.8)
        ax.text(0.02, level, lbl, transform=trans,
                va='bottom', ha='left', fontsize=9, color=color)


# ── three separate publication figures ───────────────────────────────────────

def plot_flow_rate(
    time_h: np.ndarray,
    flow_ml_hr: np.ndarray,
    mv_avg_ml_hr: np.ndarray,
    hi_band_ml_hr: np.ndarray,
    lo_band_ml_hr: np.ndarray,
    out_dir: pathlib.Path,
    stem: str,
    boundaries_h: list = None,
    boundary_labels: list = None,
) -> None:
    y_lo = min(-1.0, float(np.nanmin(lo_band_ml_hr)) - 0.5)
    y_hi = max(10.0, float(np.nanmax(hi_band_ml_hr)) + 0.5)
    flow_range = (y_lo, y_hi)

    fig_q, ax_q = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    ax_q.step(time_h, flow_ml_hr,   where='post', lw=0.6,
              label='flow rate',  color='steelblue', alpha=0.7)
    ax_q.step(time_h, mv_avg_ml_hr, where='post', lw=1.2, color='crimson')
    ax_q.fill_between(time_h, lo_band_ml_hr, hi_band_ml_hr,
                      step='post', color='crimson', alpha=0.15,
                      label=r'$\pm 5\%$ + drift')
    ax_q.axhline(_NOM_FLOW_ML_HR, color='k', lw=1.0, ls='--',
                 label=fr'$q_{{nom}} = {_NOM_FLOW_ML_HR:.0f}$ mL/hr (pump set)')
    ax_q.axhline(_NOM_FLOW_CORR_ML_HR, color='darkolivegreen', lw=1.0, ls='--',
                 label=(fr'$q_{{corr}} = {_NOM_FLOW_CORR_ML_HR:.2f}$ mL/hr'
                        fr' (NaCl $\times${_NACL_FACTOR:.2f},'
                        fr' $\Delta T={_T_NOM_C - _T_OP_C:.1f}$°C)'))
    ax_q.axhspan(_NOM_FLOW_CORR_ML_HR * 0.9, _NOM_FLOW_CORR_ML_HR * 1.1,
                 color='darkolivegreen', alpha=0.10, label=r'$q_{corr} \pm 10\%$')

    xticks = np.linspace(0, time_h[-1], 10)
    yticks = np.linspace(flow_range[0], flow_range[1], 12)
    utils_mpl.set_format(ax_q.xaxis, ticks=xticks, fmt=utils_mpl.make_formatter(".1f"))
    utils_mpl.set_format(ax_q.yaxis, ticks=yticks, fmt=utils_mpl.make_formatter(".1f"))
    ax_q.set_xlabel(r'Time (hours)')
    ax_q.set_ylabel(r'$q(t)$ (mL/hr)')
    utils_mpl.set_x_axis(ax_q, bnd=(0, time_h[-1]), margin=0.02)
    utils_mpl.set_y_axis(ax_q, bnd=flow_range,      margin=0.05)
    utils_mpl.set_grid(fig_q, ax_q, major=True, minor=True)

    if boundaries_h:
        _phase_vlines(ax_q, boundaries_h, boundary_labels or [""] * len(boundaries_h))

    ax_q.legend(loc='upper right', fontsize=9)
    _save_fig(fig_q, str(out_dir / f'{stem}_flow_rate'))
    plt.close(fig_q)


def plot_temperature(
    time_h: np.ndarray,
    temp: np.ndarray,
    out_dir: pathlib.Path,
    stem: str,
) -> None:
    temp_range = (19.0, 40.0)

    fig_T, ax_T = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    ax_T.step(time_h, temp, where='post', lw=0.8, label='Temperature', color='darkorange')
    xticks = np.linspace(0, time_h[-1], 10)
    yticks = np.linspace(temp_range[0], temp_range[1], 9)
    utils_mpl.set_format(ax_T.xaxis, ticks=xticks, fmt=utils_mpl.make_formatter(".1f"))
    utils_mpl.set_format(ax_T.yaxis, ticks=yticks, fmt=utils_mpl.make_formatter(".1f"))
    ax_T.set_xlabel(r'Time (hours)')
    ax_T.set_ylabel(r'$T(t)$ ($^\circ$C)')
    utils_mpl.set_x_axis(ax_T, bnd=(0, time_h[-1]), margin=0.02)
    utils_mpl.set_y_axis(ax_T, bnd=temp_range,      margin=0.05)
    utils_mpl.set_grid(fig_T, ax_T, major=True, minor=True)
    ax_T.legend(fontsize=9)
    _save_fig(fig_T, str(out_dir / f'{stem}_temperature'))
    plt.close(fig_T)


def plot_volume(
    time_h: np.ndarray,
    vol_csv_ml: np.ndarray,
    vol_upper_csv: np.ndarray,
    vol_lower_csv: np.ndarray,
    vol_corr_ml: np.ndarray,
    vol_corr_upper_ml: np.ndarray,
    vol_corr_lower_ml: np.ndarray,
    actual_volume_ml: float,
    out_dir: pathlib.Path,
    stem: str,
) -> None:
    vol_max = float(np.ceil(max(vol_csv_ml[-1], vol_corr_ml[-1], actual_volume_ml, _NOM_VOLUME_ML) / 50) * 50 + 50)
    vol_range = (0.0, vol_max)

    fig_V, ax_V = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    ax_V.plot(time_h, vol_csv_ml, lw=0.9, label='Volume (CSV)', color='steelblue')
    ax_V.fill_between(time_h, vol_lower_csv, vol_upper_csv,
                      color='steelblue', alpha=0.15, label='Sensor uncertainty')
    ax_V.plot(time_h, vol_corr_ml, lw=1.0, ls='--', color='darkolivegreen',
              label=fr'$q_{{corr}}$ nominal ({_NOM_FLOW_CORR_ML_HR:.2f} mL/hr)')
    ax_V.fill_between(time_h, vol_corr_lower_ml, vol_corr_upper_ml,
                      color='darkolivegreen', alpha=0.12, label=r'$q_{corr} \pm 10\%$')

    xticks = np.linspace(0, time_h[-1], 10)
    yticks = np.linspace(vol_range[0], vol_range[1], 10)
    utils_mpl.set_format(ax_V.xaxis, ticks=xticks, fmt=utils_mpl.make_formatter(".1f"))
    utils_mpl.set_format(ax_V.yaxis, ticks=yticks, fmt=utils_mpl.make_formatter(".0f"))
    ax_V.set_xlabel(r'Time (hours)')
    ax_V.set_ylabel(r'$V(t)$ (mL)')
    utils_mpl.set_x_axis(ax_V, bnd=(0, time_h[-1]), margin=0.02)
    utils_mpl.set_y_axis(ax_V, bnd=vol_range,        margin=0.05)
    utils_mpl.set_grid(fig_V, ax_V, major=True, minor=True)

    vol_levels = [_NOM_VOLUME_ML, actual_volume_ml, vol_csv_ml[-1]]
    labels = [
        fr'$V_b(0) = {_NOM_VOLUME_ML:.0f}$ mL', 
        fr'$V_{{act}} = {actual_volume_ml:.0f}$ mL (beaker)', 
        fr'$V(\mathrm{{end}}) = {vol_csv_ml[-1]:.1f}$ mL'
    ]
    _volume_level_vlines(ax_V, vol_levels, labels)

    ax_V.legend(loc='lower right', fontsize=8)
    _save_fig(fig_V, str(out_dir / f'{stem}_volume'))
    plt.close(fig_V)


def plot_correlation(
    corr: np.ndarray,
    lag_samples: int,
    r_max: float,
    Ts: float,
    out_dir: pathlib.Path,
    stem: str,
) -> None:
    n = (len(corr) + 1) // 2
    lags_h = np.arange(-(n - 1), n) * Ts / 3600.0
    peak_lag_h = lag_samples * Ts / 3600.0

    fig_r, ax_r = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    ax_r.plot(lags_h, corr, lw=0.8, color='steelblue', label=r'$R_{qT}(\ell)$')
    ax_r.axvline(peak_lag_h, color='crimson', lw=1.0, ls='--',
                 label=fr'$\ell^* = {peak_lag_h:.2f}$ h,  $r_{{max}} = {r_max:.3f}$')
    ax_r.axhline(0.0, color='k', lw=0.6)

    xticks = np.linspace(lags_h[0], lags_h[-1], 11)
    utils_mpl.set_format(ax_r.xaxis, ticks=xticks, fmt=utils_mpl.make_formatter(".1f"))
    ax_r.set_xlabel(r'Lag $\ell$ (hours)')
    ax_r.set_ylabel(r'$R_{qT}(\ell)$')
    utils_mpl.set_x_axis(ax_r, bnd=(lags_h[0], lags_h[-1]), margin=0.02)
    utils_mpl.set_grid(fig_r, ax_r, major=True, minor=True)
    ax_r.legend(loc='upper right')
    _save_fig(fig_r, str(out_dir / f'{stem}_correlation'))
    plt.close(fig_r)


# ── cross-experiment comparison ───────────────────────────────────────────────

def plot_comparison(runs: list, out_dir: pathlib.Path, align: str = "onset") -> None:
    """Overlay the moving-average flow profiles of several runs.

    align:
      "none"  — raw logging-start timebase, no offset correction (original).
      "onset" — re-anchor each run at its detected infusion onset (t=0) and use
                the offset-corrected curve so different dead-times overlay.
      "xcorr" — onset alignment, then refine the residual lag of each run by
                cross-correlating its q(t) against the longest run.
    """
    colours = utils_mpl.make_colors(len(runs), cmap_name="plasma")

    # Build the per-run (time, curve) series according to the alignment mode.
    series = []   # list of dicts: name, t_h, q
    aligned = False
    for run in runs:
        if align == "none":
            t_h = run["time_h"]
            q   = run["mv_avg_ml_hr"]
        else:
            onset_h = run.get("t_onset_h")
            t_h = run["time_h"] - onset_h if onset_h is not None else run["time_h"]
            if onset_h is not None:
                aligned = True
            q = run.get("mv_avg_ml_hr_corr", run["mv_avg_ml_hr"])
        series.append({"name": run["experiment_name"], "t_h": t_h, "q": q})

    # xcorr refinement: align every run to the longest one (most overlap).
    if align == "xcorr" and len(series) > 1:
        ref = max(range(len(series)), key=lambda k: np.ptp(series[k]["t_h"]))
        t_ref, q_ref = series[ref]["t_h"], series[ref]["q"]
        for k, s in enumerate(series):
            if k == ref:
                continue
            shift = _xcorr_residual_lag_h(t_ref, q_ref, s["t_h"], s["q"])
            s["t_h"] = s["t_h"] + shift
            print(f"  [xcorr] {s['name']} ← residual shift {shift*60:+.1f} min "
                  f"(ref: {series[ref]['name']})")

    fig_c, ax_c = utils_mpl.get_fig(size=(11.0, 5.0), dpi=150)
    ax_c.axhline(_NOM_FLOW_ML_HR, color='k', lw=0.8, ls=':',
                 label=fr'$q_{{nom}} = {_NOM_FLOW_ML_HR:.0f}$ mL/hr')
    for i, s in enumerate(series):
        ax_c.plot(s["t_h"], s["q"], lw=1.0, color=colours[i], label=s["name"])
    if aligned:
        ax_c.axvline(0.0, color='grey', lw=0.8, ls='--', label='infusion onset')
    ax_c.set_xlabel(r'Time since infusion onset (hours)' if aligned
                    else r'Time (hours)')
    ax_c.set_ylabel(r'$\bar{q}(t)$ (mL/hr)')
    utils_mpl.set_grid(fig_c, ax_c, major=True, minor=True)
    ax_c.legend(fontsize=9)
    out_dir.mkdir(parents=True, exist_ok=True)
    _save_fig(fig_c, str(out_dir / 'comparison_q_profiles'))
    plt.close(fig_c)


# ── per-file processing ───────────────────────────────────────────────────────

def process_file(
    csv_path: pathlib.Path,
    out_root: pathlib.Path,
    zero_drift_min: float,
    empty_pump_min: float,
) -> dict:
    actual_volume_ml = _ACTUAL_VOLUME_ML
    print(f"\n[analyse] {csv_path.name}")
    metadata = parse_metadata(csv_path)
    experiment_cfg = metadata.get("configuration", csv_path.stem)
    experiment_rep = metadata.get("experiment_rep", csv_path.stem)

    df = load_csv(csv_path)
    print(f"  Rows loaded: {len(df):,}")

    t      = df["UTC_Time"].to_numpy(dtype=np.float64)
    t0     = t[0]
    time_h = (t - t0) / 3600.0
    Ts     = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    print(f"  Median Ts = {Ts*1000:.1f} ms   Total = {time_h[-1]:.2f} h")

    flow      = np.nan_to_num(df["Flow_ul_min"].to_numpy(dtype=np.float64),          nan=0.0)
    temp      = np.nan_to_num(df["DeviceTemperature_degC"].to_numpy(dtype=np.float64), nan=0.0)
    vol_csv_ml = np.nan_to_num(df["Volume_uL"].to_numpy(dtype=np.float64),             nan=0.0) / 1000.0

    # 1-hour moving average (auto-scaled to actual sampling rate)
    ma_window = max(10, int(_MA_WINDOW_SEC / Ts))
    print(f"  Moving-avg window: {ma_window} samples ({ma_window * Ts / 3600:.2f} h)")
    mv_avg = _moving_avg(flow, ma_window)

    # Phase segmentation & zero-drift baseline
    phases = segment_phases(df, zero_drift_min, empty_pump_min)
    t_zero_end, t_active_start = phase_boundaries_s(df, zero_drift_min, empty_pump_min)
    try:
        mu_q0, sigma_q0, q_th = zero_drift_stats(phases["zero_drift"])
    except ValueError:
        print("  [WARN] Zero-drift phase empty — setting mu_q0=0.")
        mu_q0, sigma_q0, q_th = 0.0, 0.0, 0.0

    # Uncertainty band: ±5 % relative + zero-drift offset
    eps_flow      = _REL_ERROR * np.abs(mv_avg) + abs(mu_q0)
    flow_ml_hr    = flow   * _UL_MIN_TO_ML_HR
    mv_avg_ml_hr  = mv_avg * _UL_MIN_TO_ML_HR
    hi_band_ml_hr = (mv_avg + eps_flow) * _UL_MIN_TO_ML_HR
    lo_band_ml_hr = (mv_avg - eps_flow) * _UL_MIN_TO_ML_HR

    # RK4 integration of moving average (used for V_disp fallback in stats)
    vol_int_ml = rk4_integrate(t, mv_avg)

    # Sensor volume uncertainty band: integrate eps_flow and offset from the CSV volume
    vol_eps_ml    = rk4_integrate(t, eps_flow)
    vol_upper_csv = vol_csv_ml + vol_eps_ml
    vol_lower_csv = vol_csv_ml - vol_eps_ml

    # Corrected nominal volume: integrate constant q_corr starting at t_active_start.
    # No saturation cap — runs to end of recording for theoretical vs empirical comparison.
    q_corr_ul_min = _NOM_FLOW_CORR_ML_HR / _UL_MIN_TO_ML_HR
    elapsed_s     = t - t0
    active_mask   = elapsed_s > t_active_start

    vol_corr_ml       = np.zeros(len(t))
    vol_corr_upper_ml = np.zeros(len(t))
    vol_corr_lower_ml = np.zeros(len(t))
    if np.any(active_mask):
        t_active_only = t[active_mask]
        n_act = len(t_active_only)
        vol_corr_ml[active_mask]       = rk4_integrate(t_active_only, np.full(n_act, q_corr_ul_min))
        vol_corr_upper_ml[active_mask] = rk4_integrate(t_active_only, np.full(n_act, q_corr_ul_min * 1.1))
        vol_corr_lower_ml[active_mask] = rk4_integrate(t_active_only, np.full(n_act, q_corr_ul_min * 0.9))

    # V_disp from active phase only
    active = phases["active"]
    if not active.empty:
        t_act = active["UTC_Time"].to_numpy(dtype=np.float64)
        q_act = active["Flow_ul_min"].to_numpy(dtype=np.float64)
        v_disp = float(rk4_integrate(t_act, q_act)[-1])
    else:
        v_disp = float(vol_int_ml[-1])

    # Effective infusion end time
    elapsed_s = t - t0
    teff_s    = find_teff(elapsed_s, mv_avg, np.abs(q_th))
    teff_h    = float(teff_s / 3600.0)

    # Infusion onset — first sustained crossing of q_th on the RAW flow. Used to
    # re-anchor runs (t=0 at onset) so the comparison overlay aligns them.
    onset_s  = find_onset(elapsed_s, flow, np.abs(q_th))
    onset_h  = float(onset_s / 3600.0) if onset_s is not None else None

    # Cross-correlation q–T on moving average
    lag_samples, lag_s, r_max, corr = cross_correlate_q_T(mv_avg, temp, Ts)

    # Stats JSON
    experiment_name = f"{experiment_cfg}_{experiment_rep}"
    stats = {
        "experiment_name": experiment_name,
        "metadata": metadata,
        "sampling_interval_s": round(Ts, 4),
        "ma_window_samples": ma_window,
        "zero_drift": {
            "mu_q0_ul_min":        round(mu_q0,    4),
            "sigma_q0_ul_min":     round(sigma_q0, 4),
            "q_threshold_ul_min":  round(q_th,     4),
        },
        "volume_integration": {
            "V_disp_mL":            round(v_disp, 4),
            "deviation_from_300mL": round(v_disp - _NOM_VOLUME_ML, 4),
        },
        "teff": {
            "T_eff_h": round(teff_h, 4) if teff_h is not None else None,
        },
        "onset": {
            "t_onset_h": round(onset_h, 4) if onset_h is not None else None,
        },
        "cross_correlation": {
            "lag_samples":                  int(lag_samples),
            "lag_s":                        round(lag_s, 2),
            "r_max":                        round(r_max, 4),
        },
    }

    out_dir = out_root / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"  stats.json  → {out_dir / 'stats.json'}")
    print(f"  mu_q0={mu_q0:.2f} µL/min  sigma={sigma_q0:.3f}  q_th={q_th:.2f}")
    print(f"  V_disp={v_disp:.2f} mL  (deviation={v_disp - _NOM_VOLUME_ML:+.2f} mL)")
    if teff_h is not None:
        print(f"  T_eff={teff_h:.3f} h")
    if onset_h is not None:
        print(f"  t_onset={onset_h:.3f} h")
    else:
        print("  [WARN] onset not detected — comparison will not re-anchor this run")
    print(f"  lag={lag_s:.1f} s  r_max={r_max:.3f}")

    # Three publication figures
    utils_mpl.set_global()
    boundaries_h = [t_zero_end / 3600.0, t_active_start / 3600.0]
    plot_flow_rate(time_h, flow_ml_hr, mv_avg_ml_hr, hi_band_ml_hr, lo_band_ml_hr,
                   out_dir, experiment_name, boundaries_h,
                   ["zero-drift end", "active start"])
    plot_temperature(time_h, temp, out_dir, experiment_name)
    plot_volume(time_h, vol_csv_ml, vol_upper_csv, vol_lower_csv,
                vol_corr_ml, vol_corr_upper_ml, vol_corr_lower_ml,
                actual_volume_ml, out_dir, experiment_name)
    plot_correlation(corr, lag_samples, r_max, Ts, out_dir, experiment_name)
    print(f"  figures     → {out_dir}/")

    return {
        "experiment_name": experiment_name,
        "time_h":        time_h,
        "mv_avg_ml_hr":  mv_avg_ml_hr,
        # Offset-corrected moving average (zero-drift baseline mu_q0 removed) for
        # overlaying runs on a common, offset-free scale.
        "mv_avg_ml_hr_corr": (mv_avg - mu_q0) * _UL_MIN_TO_ML_HR,
        # Infusion onset (h) for re-anchoring in the comparison overlay; None if
        # not detected → that run is left on its raw logging-start timebase.
        "t_onset_h":     onset_h,
        "stats":         stats,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyse SLF3S-0600F DataLog CSV files."
    )
    parser.add_argument("csv_files", nargs="*",
        help="One or more {CONFIG}_{REP}.csv paths (default: all CSVs in Temp/C*/)")
    parser.add_argument("--output-dir", default="results/",
        help="Root output directory (default: results/)")
    parser.add_argument("--zero-drift-min", type=float, default=60.0,
        help="Zero-drift phase duration in minutes (default: 60)")
    parser.add_argument("--empty-pump-min", type=float, default=30.0,
        help="Empty-pump phase duration in minutes (default: 30)")
    parser.add_argument("--align", choices=["none", "onset", "xcorr"], default="onset",
        help="Comparison overlay alignment: none | onset (default) | "
             "xcorr (onset + cross-correlation refinement)")
    parser.add_argument("--plot-format", choices=["png", "pdf", "both"], default="both",
        help="Figure output format(s): png | pdf | both (default)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_root = pathlib.Path(args.output_dir)

    global _PLOT_FORMATS
    _PLOT_FORMATS = ("pdf", "png") if args.plot_format == "both" else (args.plot_format,)

    csv_files = args.csv_files
    if not csv_files:
        temp_dir = pathlib.Path(__file__).parent / "Temp"
        csv_files = sorted(temp_dir.glob("C*/*.csv"))
        if not csv_files:
            print(f"[ERROR] No CSV files found in {temp_dir}/C*/", file=sys.stderr)
            sys.exit(1)
        print(f"[analyse] Auto-discovered {len(csv_files)} file(s) in {temp_dir}/C*/")

    runs   = []
    failed = []
    for raw_path in csv_files:
        csv_path = pathlib.Path(raw_path)
        if not csv_path.exists():
            print(f"[ERROR] File not found: {csv_path}", file=sys.stderr)
            failed.append(raw_path)
            continue
        try:
            result = process_file(
                csv_path, out_root,
                args.zero_drift_min, args.empty_pump_min,
            )
            runs.append(result)
        except Exception as exc:
            print(f"[ERROR] {csv_path}: {exc}", file=sys.stderr)
            failed.append(raw_path)

    if len(runs) > 1:
        plot_comparison(runs, out_root, align=args.align)
        print(f"\n[analyse] comparison ({args.align}) → {out_root / 'comparison_q_profiles'}.{{{','.join(_PLOT_FORMATS)}}}")

    if failed:
        print(f"\n[analyse] {len(failed)} file(s) failed: {failed}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[analyse] Done.  Results in {out_root.resolve()}")


if __name__ == "__main__":
    main()
