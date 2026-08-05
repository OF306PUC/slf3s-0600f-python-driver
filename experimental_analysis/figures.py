"""
figures.py — every plot the analysis produces.

Three curves per run (flow, temperature, volume), the same three averaged per
condition, and one cross-condition overlay. Consistent by construction:

  - hue always means CONDITION, never a run's position in a list
  - line style means replicate
  - guide lines carry no on-plot text; the legend says what they are
  - t = 0 is the infusion onset and is not drawn
  - air-excluded intervals are shaded, and the line interpolates across them

`configure()` sets the single output format and DPI for the whole run, so no
caller has to thread them through every plotting call.
"""
import pathlib

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

import utils_mpl
from config import (
    AIR_SHADE, CONDITION_COLOURS, INK_PRIMARY, INK_SECONDARY, NOM_FLOW_CORR_ML_HR,
    NOM_FLOW_ML_HR, NOM_VOLUME_ML, NACL_FACTOR, STUDY_CONDITIONS, T_OP_DECLARED_C,
    colour_for,
)


# Three curves per run and per condition: flow, temperature, volume.
# methodology.txt § Curvas 7: guide lines carry no on-plot text — everything they
# mean goes in the legend. § Curvas 8: onset is t = 0 and is not drawn.

DEFAULT_FORMAT = "png"
DEFAULT_DPI = 250

_PLOT_FORMAT = DEFAULT_FORMAT
_PLOT_DPI = DEFAULT_DPI


def configure(plot_format: str = DEFAULT_FORMAT, dpi: int = DEFAULT_DPI) -> None:
    """
    Set the single output format and DPI for every figure in this run.

    methodology.txt § Curvas 5 asks for one format per run, not two or three, so
    the choice is made once here instead of being threaded through every plotting
    call — which is how a stray second format gets emitted.
    """
    global _PLOT_FORMAT, _PLOT_DPI
    if plot_format not in ("png", "pdf", "svg"):
        raise ValueError(f"unsupported figure format: {plot_format!r}")
    _PLOT_FORMAT, _PLOT_DPI = plot_format, dpi


def _save_fig(fig, path_no_ext: str) -> None:
    """Save in the single selected format (methodology.txt § Curvas 5)."""
    path = f"{path_no_ext}.{_PLOT_FORMAT}"
    if _PLOT_FORMAT == "png":
        utils_mpl.save_png(fig, path, dpi=_PLOT_DPI)
    elif _PLOT_FORMAT == "pdf":
        utils_mpl.save_pdf(fig, path)
    else:
        utils_mpl.save_svg(fig, path)
    plt.close(fig)


def _shade_air(ax, intervals: list) -> None:
    """
    Shade air-in-line intervals — the sensor's own validity verdict, on the plot.

    One legend entry for all of them, never one per interval.
    """
    for i, (a, b) in enumerate(intervals):
        ax.axvspan(a, b, color=AIR_SHADE, alpha=0.5, lw=0, zorder=0,
                   label="air in line (samples excluded)" if i == 0 else None)


def _nice_ticks(lo: float, hi: float, n: int = 9) -> np.ndarray:
    """
    Round tick values inside [lo, hi].

    `np.linspace` over data-derived bounds produces ticks like 6.2 / 5.3 / 4.5,
    which are hard to read off and hard to compare between figures. MaxNLocator
    picks human-round steps instead; the result is clipped so no tick sits outside
    the axis bounds.
    """
    ticks = MaxNLocator(nbins=n, steps=[1, 2, 2.5, 5, 10]).tick_values(lo, hi)
    inside = ticks[(ticks >= lo - 1e-9) & (ticks <= hi + 1e-9)]
    return inside if inside.size >= 2 else np.linspace(lo, hi, n)


def _finish(fig, ax, xlabel, ylabel, x_bnd, y_bnd, x_fmt=".1f", y_fmt=".1f",
            legend_loc="best") -> None:
    xticks = _nice_ticks(x_bnd[0], x_bnd[1])
    yticks = _nice_ticks(y_bnd[0], y_bnd[1])
    utils_mpl.set_format(ax.xaxis, ticks=xticks, fmt=utils_mpl.make_formatter(x_fmt))
    utils_mpl.set_format(ax.yaxis, ticks=yticks, fmt=utils_mpl.make_formatter(y_fmt))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    utils_mpl.set_x_axis(ax, bnd=x_bnd, margin=0.02)
    utils_mpl.set_y_axis(ax, bnd=y_bnd, margin=0.05)
    utils_mpl.set_grid(fig, ax, major=True, minor=True)
    ax.legend(loc=legend_loc, fontsize=8)


def plot_flow(t_h, q_raw_ml_hr, q_filt_ml_hr, band_lo, band_hi, air_intervals,
              out_dir, stem, colour, ma_window_min) -> None:
    fig, ax = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    _shade_air(ax, air_intervals)
    ax.plot(t_h, q_raw_ml_hr, lw=0.5, color=colour, alpha=0.4,
            label="offset-corrected flow")
    ax.plot(t_h, q_filt_ml_hr, lw=1.4, color=colour,
            label=fr"filtered ({ma_window_min:.0f} min mean)")
    ax.fill_between(t_h, band_lo, band_hi, color=colour, alpha=0.15,
                    label=r"$\pm 5\%$ sensor uncertainty")
    ax.axhline(NOM_FLOW_ML_HR, color=INK_PRIMARY, lw=1.0, ls="--",
               label=fr"$q_{{nom}} = {NOM_FLOW_ML_HR:.0f}$ mL/hr")
    ax.axhline(NOM_FLOW_CORR_ML_HR, color=INK_SECONDARY, lw=1.0, ls=":",
               label=fr"$q_{{corr}} = {NOM_FLOW_CORR_ML_HR:.2f}$ mL/hr")
    y_lo = min(-0.5, float(np.nanmin(band_lo)))
    y_hi = max(NOM_FLOW_ML_HR + 2.0, float(np.nanmax(band_hi)))
    _finish(fig, ax, r"Time since infusion onset (hours)", r"$q(t)$ (mL/hr)",
            (float(t_h[0]), float(t_h[-1])), (y_lo, y_hi), legend_loc="upper right")
    _save_fig(fig, str(out_dir / f"{stem}_flow"))


def plot_temperature(t_h, temp, air_intervals, out_dir, stem, colour) -> None:
    fig, ax = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    _shade_air(ax, air_intervals)
    ax.plot(t_h, temp, lw=1.0, color=colour, label=r"$T_{device}(t)$")
    ax.axhline(T_OP_DECLARED_C, color=INK_PRIMARY, lw=1.0, ls="--",
               label=fr"declared $T_{{op}} = {T_OP_DECLARED_C:.0f}$\,$^\circ$C")
    finite = temp[np.isfinite(temp)]
    lo = float(np.floor(min(finite.min(), T_OP_DECLARED_C) - 1.0))
    hi = float(np.ceil(finite.max() + 1.0))
    _finish(fig, ax, r"Time since infusion onset (hours)",
            r"$T(t)$ ($^\circ$C)", (float(t_h[0]), float(t_h[-1])), (lo, hi))
    _save_fig(fig, str(out_dir / f"{stem}_temperature"))


def plot_volume(t_h, vol_ml, vol_lo, vol_hi, vol_nominal, air_intervals,
                out_dir, stem, colour) -> None:
    fig, ax = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    _shade_air(ax, air_intervals)
    ax.plot(t_h, vol_ml, lw=1.4, color=colour,
            label=fr"$V(t)$, dispensed $= {float(vol_ml[-1]):.1f}$ mL")
    ax.fill_between(t_h, vol_lo, vol_hi, color=colour, alpha=0.15,
                    label=r"$\pm 5\%$ sensor uncertainty")
    ax.plot(t_h, vol_nominal, lw=1.0, ls=":", color=INK_SECONDARY,
            label=fr"$q_{{corr}}$ nominal ({NOM_FLOW_CORR_ML_HR:.2f} mL/hr)")
    ax.axhline(NOM_VOLUME_ML, color=INK_PRIMARY, lw=1.0, ls="--",
               label=fr"$V_b(0) = {NOM_VOLUME_ML:.0f}$ mL (reservoir)")
    v_max = float(np.ceil(max(float(np.nanmax(vol_hi)), float(vol_nominal[-1]),
                              NOM_VOLUME_ML) / 50.0) * 50.0)
    _finish(fig, ax, r"Time since infusion onset (hours)", r"$V(t)$ (mL)",
            (float(t_h[0]), float(t_h[-1])), (0.0, v_max), y_fmt=".0f",
            legend_loc="lower right")
    _save_fig(fig, str(out_dir / f"{stem}_volume"))


def plot_condition_mean(curve: dict, kind: str, out_dir: pathlib.Path,
                        condition: str, colour: str) -> None:
    """
    One averaged curve for a condition, mean ± SD across its replicates
    (methodology.txt § Curvas 3).

    The SD band is drawn only when there is more than one replicate; with n = 1
    there is no spread to show and a zero-width band would imply a precision the
    single run does not have.
    """
    t_h = curve["t_h"]
    mean = curve[f"{kind}_mean"]
    sd = curve[f"{kind}_sd"]
    n = curve["n_runs"]

    labels = {
        "flow": (r"$\bar{q}(t)$ (mL/hr)", "mL/hr"),
        "temperature": (r"$\bar{T}(t)$ ($^\circ$C)", r"$^\circ$C"),
        "volume": (r"$\bar{V}(t)$ (mL)", "mL"),
    }
    ylabel, _ = labels[kind]

    fig, ax = utils_mpl.get_fig(size=(10.0, 4.5), dpi=150)
    # Region where fewer than all replicates still have data — the tail that lets
    # the end-of-infusion decay be shown at all. Shaded so the reader can see where
    # the mean stops being backed by every replicate.
    full_h = curve.get("full_coverage_h")
    if n > 1 and full_h is not None and full_h < float(t_h[-1]):
        ax.axvspan(full_h, float(t_h[-1]), color=AIR_SHADE, alpha=0.30, lw=0,
                   zorder=0, label=fr"$n<{n}$ (replicates ended)")
    ax.plot(t_h, mean, lw=1.5, color=colour,
            label=fr"{condition} mean ($n={n}$)")
    if n > 1:
        ax.fill_between(t_h, mean - sd, mean + sd, color=colour, alpha=0.20,
                        label=r"$\pm 1$ SD across replicates")
    if kind == "flow":
        ax.axhline(NOM_FLOW_ML_HR, color=INK_PRIMARY, lw=1.0, ls="--",
                   label=fr"$q_{{nom}} = {NOM_FLOW_ML_HR:.0f}$ mL/hr")
        ax.axhline(NOM_FLOW_CORR_ML_HR, color=INK_SECONDARY, lw=1.0, ls=":",
                   label=fr"$q_{{corr}} = {NOM_FLOW_CORR_ML_HR:.2f}$ mL/hr")
        y_bnd = (min(-0.5, float(np.nanmin(mean - sd))),
                 max(NOM_FLOW_ML_HR + 2.0, float(np.nanmax(mean + sd))))
        y_fmt = ".1f"
    elif kind == "temperature":
        y_bnd = (float(np.floor(np.nanmin(mean - sd) - 1.0)),
                 float(np.ceil(np.nanmax(mean + sd) + 1.0)))
        ax.axhline(T_OP_DECLARED_C, color=INK_PRIMARY, lw=1.0, ls="--",
                   label=fr"declared $T_{{op}} = {T_OP_DECLARED_C:.0f}$\,$^\circ$C")
        y_fmt = ".1f"
    else:
        ax.axhline(NOM_VOLUME_ML, color=INK_PRIMARY, lw=1.0, ls="--",
                   label=fr"$V_b(0) = {NOM_VOLUME_ML:.0f}$ mL (reservoir)")
        y_bnd = (0.0, float(np.ceil(max(float(np.nanmax(mean + sd)),
                                        NOM_VOLUME_ML) / 50.0) * 50.0))
        y_fmt = ".0f"

    _finish(fig, ax, r"Time since infusion onset (hours)", ylabel,
            (float(t_h[0]), float(t_h[-1])), y_bnd, y_fmt=y_fmt)
    _save_fig(fig, str(out_dir / f"{condition}_mean_{kind}"))


def plot_condition_overlay(curves: dict, out_dir: pathlib.Path) -> None:
    """All condition-mean flow profiles on one axis, fixed colour per condition."""
    fig, ax = utils_mpl.get_fig(size=(11.0, 5.0), dpi=150)
    ax.axhline(NOM_FLOW_ML_HR, color=INK_PRIMARY, lw=0.9, ls="--",
               label=fr"$q_{{nom}} = {NOM_FLOW_ML_HR:.0f}$ mL/hr")
    ax.axhline(NOM_FLOW_CORR_ML_HR, color=INK_SECONDARY, lw=0.9, ls=":",
               label=fr"$q_{{corr}} = {NOM_FLOW_CORR_ML_HR:.2f}$ mL/hr")
    t_max, y_hi = 0.0, NOM_FLOW_ML_HR + 2.0
    for cond in STUDY_CONDITIONS:
        c = curves.get(cond)
        if c is None:
            continue
        ax.plot(c["t_h"], c["flow_mean"], lw=1.3, color=colour_for(cond),
                label=fr"{cond} ($n={c['n_runs']}$)")
        t_max = max(t_max, float(c["t_h"][-1]))
        y_hi = max(y_hi, float(np.nanmax(c["flow_mean"])))
    _finish(fig, ax, r"Time since infusion onset (hours)",
            r"$\bar{q}(t)$ (mL/hr)", (0.0, t_max), (min(-0.5, 0.0), y_hi),
            legend_loc="upper right")
    _save_fig(fig, str(out_dir / "comparison_condition_means"))
