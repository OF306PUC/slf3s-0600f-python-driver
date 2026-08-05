"""
stats.py — quantities computed FROM the data, each returned as a plain dict.

Every function here answers one question about a run and returns a JSON-ready
report, so the same object can go into stats.json and into the markdown summary
without a second formatting layer:

  filter_chain_report   what the measurement can actually resolve
  noise_report          how much noise there is, from the residual, with an FFT
  cross_correlate_q_T   how flow and temperature move together
  temperature_report    measured temperature against the declared operating point

None of these mutate their inputs or touch the filesystem.
"""
import numpy as np
from scipy.signal import freqz

from config import (
    SENSOR_IIR_ALPHA, SENSOR_INTERNAL_FS, T_OP_DECLARED_C,
    corrected_nominal_flow_ml_hr,
)


# methodology.txt: "f_ro = 0.1 Hz también por ende el sensor utiliza el filtro
# recursivo ... (Exponential smoother (IIR))".
#
# Three low-pass stages sit between the fluid and the plotted curve, and only the
# narrowest limits what the analysis can resolve:
#   1. sensor internal IIR exponential smoother   (α, internal f_s)
#   2. readout / logging at f_ro = f_s = 10 s     (Nyquist = f_s/2)
#   3. the analysis moving average                (signal_processing.moving_average)
#
# The sensor's own filter constants are DECLARED parameters, so they live in
# config.py (SENSOR_IIR_ALPHA, SENSOR_INTERNAL_FS) alongside every other declared
# value — not here, where only computed quantities belong.


def _minus3db_cutoff_hz(freqs: np.ndarray, mag_db: np.ndarray) -> float:
    """First frequency at or below −3 dB, or NaN."""
    below = np.where(mag_db <= -3.0)[0]
    return float(freqs[below[0]]) if len(below) else float("nan")


def sensor_iir_cutoff_hz(alpha: float = SENSOR_IIR_ALPHA,
                         fs: float = SENSOR_INTERNAL_FS) -> float:
    """
    −3 dB cutoff of the sensor's exponential smoother, closed form.

    For H(z) = α / (1 − β·z⁻¹) with β = 1 − α, the DC gain is α/(1−β) = 1 and

        |H(ω)|² = α² / (1 − 2β·cos ω + β²)

    Setting |H|² = ½ gives  cos ω_c = (1 + β² − 2α²) / (2β).

    NOTE — this corrects the expression in SLF3S-0600F_filters.py, which used
    (2β² + 2β − 1)/(2β). For α = 0.0125 that evaluates to 1.481: outside the
    domain of arccos, so the script's own `if abs(arg) <= 1` guard silently
    skipped the line and the closed-form value was never printed. The form above
    gives 4.004 Hz, matching a numerical freqz sweep of the same filter.
    """
    beta = 1.0 - alpha
    arg = (1.0 + beta ** 2 - 2.0 * alpha ** 2) / (2.0 * beta)
    if abs(arg) > 1.0:
        return float("nan")
    return float(fs / (2.0 * np.pi) * np.arccos(arg))


def moving_average_response(n_samples: int, fs_hz: float, n_fft: int = 16384):
    """Magnitude response (Hz, dB) of an n-sample boxcar moving average at fs_hz."""
    b = np.ones(int(n_samples)) / float(n_samples)
    freqs, h = freqz(b, 1.0, worN=n_fft, fs=fs_hz)
    return freqs, 20.0 * np.log10(np.abs(h) + 1e-12)


def filter_chain_report(fs_hz: float, ma_window_samples: int) -> dict:
    """
    The acquisition + analysis low-pass chain, as numbers.

    `effective_bandwidth_hz` is the narrowest stage — the real resolution limit of
    the reported profile — and `effective_period_h` expresses that limit as the
    fastest feature the curve can still show. For this study the limiting stage is
    the analysis moving average, some 300× narrower than the sensor's own filter,
    which is four decades away and irrelevant to infusion dynamics.
    """
    f_iir = sensor_iir_cutoff_hz()
    f_nyquist = fs_hz / 2.0
    freqs, mag_db = moving_average_response(ma_window_samples, fs_hz)
    f_ma = _minus3db_cutoff_hz(freqs, mag_db)

    stages = {
        "1_sensor_iir_hz": round(f_iir, 6) if np.isfinite(f_iir) else None,
        "2_readout_nyquist_hz": round(f_nyquist, 6),
        "3_analysis_moving_average_hz": round(f_ma, 8) if np.isfinite(f_ma) else None,
    }
    known = {k: v for k, v in stages.items() if v is not None}
    bandwidth = min(known.values()) if known else None

    return {
        "sensor_iir": {
            "active": True,
            "reason": "f_ro = f_s = 10 s selects the sensor's recursive smoother",
            "alpha": SENSOR_IIR_ALPHA,
            "internal_fs_hz": SENSOR_INTERNAL_FS,
        },
        "readout": {"f_ro_hz": round(fs_hz, 6), "f_s_hz": round(fs_hz, 6)},
        "analysis_moving_average": {
            "window_samples": int(ma_window_samples),
            "window_h": round(ma_window_samples / fs_hz / 3600.0, 4),
        },
        "minus3db_cutoff_hz": stages,
        "effective_bandwidth_hz": bandwidth,
        "effective_period_h": round(1.0 / bandwidth / 3600.0, 4) if bandwidth else None,
        "limiting_stage": min(known, key=known.get) if known else None,
    }


def noise_report(t_s: np.ndarray, q_raw: np.ndarray, q_filt: np.ndarray,
                 fs_hz: float, n_peaks: int = 3) -> dict:
    """
    Noise in the flow signal, from the residual (raw − filtered).

    methodology.txt § Curvas 1 asks to quantify the noise and suggests an FFT.
    The residual is the honest target: the raw signal's variance is dominated by
    the infusion profile itself, so quantifying THAT would report the signal, not
    the noise. Subtracting the filtered profile leaves what the filter removed.

    The spectrum is taken on a uniform grid — the samples are 10 s apart to within
    a few ms, so `t_s` is treated as uniform at `fs_hz` — with the residual mean
    removed and a Hann window applied to stop spectral leakage from the record
    edges masquerading as low-frequency content. Reported peaks skip the DC bin.
    """
    resid = q_raw - q_filt
    finite = np.isfinite(resid)
    resid = resid[finite]
    if resid.size < 8:
        return {"available": False}

    rms = float(np.sqrt(np.mean(resid ** 2)))
    signal_level = float(np.nanmean(np.abs(q_filt[finite])))

    win = np.hanning(resid.size)
    spec = np.fft.rfft((resid - resid.mean()) * win)
    freqs = np.fft.rfftfreq(resid.size, d=1.0 / fs_hz)
    amp = np.abs(spec) * 2.0 / np.sum(win)

    peaks = []
    if amp.size > 2:
        order = np.argsort(amp[1:])[::-1] + 1
        for idx in order[:n_peaks]:
            f = float(freqs[idx])
            peaks.append({
                "frequency_hz": round(f, 8),
                "period_h": round(1.0 / f / 3600.0, 4) if f > 0 else None,
                "amplitude_ul_min": round(float(amp[idx]), 4),
            })

    return {
        "available": True,
        "n_samples": int(resid.size),
        "residual_rms_ul_min": round(rms, 4),
        "residual_std_ul_min": round(float(np.std(resid, ddof=1)), 4),
        "residual_peak_to_peak_ul_min": round(float(np.ptp(resid)), 4),
        "signal_mean_abs_ul_min": round(signal_level, 4),
        "snr_db": (
            round(float(20.0 * np.log10(signal_level / rms)), 3)
            if rms > 0 and signal_level > 0 else None
        ),
        "spectrum_note": (
            "FFT of (raw − filtered), mean removed, Hann window; DC bin excluded "
            "from the reported peaks"
        ),
        "dominant_peaks": peaks,
    }


def cross_correlate_q_T(q: np.ndarray, T: np.ndarray, dt_s: float) -> dict:
    """
    Normalised flow–temperature cross-correlation (methodology.txt § Curvas 4).

    Reported as numbers rather than a figure: the user asked for three curves, and
    a lag plus a peak coefficient say everything a fourth panel would.
    """
    valid = np.isfinite(q) & np.isfinite(T)
    if valid.sum() < 2:
        return {"available": False}
    q_v, T_v = q[valid], T[valid]
    q_n = (q_v - np.mean(q_v)) / (np.std(q_v) + 1e-12)
    T_n = (T_v - np.mean(T_v)) / (np.std(T_v) + 1e-12)
    corr = np.correlate(q_n, T_n, mode="full") / len(q_v)
    peak = int(np.argmax(np.abs(corr)))
    lag_samples = peak - (len(q_v) - 1)
    return {
        "available": True,
        "lag_samples": int(lag_samples),
        "lag_s": round(lag_samples * dt_s, 2),
        "lag_h": round(lag_samples * dt_s / 3600.0, 4),
        "r_max": round(float(corr[peak]), 4),
        "r_at_zero_lag": round(float(corr[len(q_v) - 1]), 4),
    }


def temperature_report(temp: np.ndarray) -> dict:
    """
    Measured device temperature vs the declared 22 °C operating point.

    methodology.txt states the sensor temperature approximates the temperature at
    the flow measurement point, which is what licenses using it at all. It is NOT
    substituted into the primary correction: the declared 22 °C keeps every run
    comparable against one reference, and the device reading includes sensor
    self-heating, so it over-estimates the fluid temperature by an unknown amount.
    Both variants are reported with the sensitivity between them, so the choice is
    visible instead of buried in a constant. Measured across this campaign the
    difference is +4.3 % to +13.9 % on q_corr — too large to leave implicit.
    """
    finite = temp[np.isfinite(temp)]
    if finite.size == 0:
        return {"available": False}
    t_mean = float(np.mean(finite))
    q_declared = corrected_nominal_flow_ml_hr(T_OP_DECLARED_C)
    q_measured = corrected_nominal_flow_ml_hr(t_mean)
    return {
        "available": True,
        "T_min_C": round(float(np.min(finite)), 3),
        "T_max_C": round(float(np.max(finite)), 3),
        "T_mean_C": round(t_mean, 3),
        "T_declared_C": T_OP_DECLARED_C,
        "T_offset_measured_minus_declared_C": round(t_mean - T_OP_DECLARED_C, 3),
        "q_corr_at_declared_T_ml_hr": round(q_declared, 4),
        "q_corr_at_measured_T_ml_hr": round(q_measured, 4),
        "q_corr_sensitivity_pct": round(
            100.0 * (q_measured - q_declared) / q_declared, 3
        ),
        "primary": "q_corr_at_declared_T_ml_hr",
    }
