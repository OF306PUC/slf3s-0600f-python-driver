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
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
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
                   label="air / bubble (samples excluded)" if i == 0 else None)


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


def _tex_escape(s: str) -> str:
    """
    Escape what LaTeX would otherwise read as markup.

    Run names carry underscores (`C0a_rep_1`), and with `text.usetex=True` an
    unescaped `_` is a subscript operator: the label renders as "C0arep1" with the
    tail lowered, or LaTeX aborts outright. Only run names reach on-plot text, so
    escaping the underscore is enough.
    """
    return s.replace("_", r"\_")


def plot_replicate_matrix(runs: list, out_dir: pathlib.Path, ma_window_min: float,
                          ncols: int = 3,
                          stem: str = "comparison_replicate_matrix") -> None:
    """
    Every replicate's flow profile as small multiples — raw and filtered only.

    One panel per run instead of one crowded axis: 18 overlaid profiles cannot be
    read, and the question this figure answers is per-run (does any single
    replicate misbehave?), not cross-run. The cross-run comparison already has its
    own figure, `comparison_condition_means`.

    Panels are packed row-major with no empty cells and share BOTH axes, so a
    difference in shape is a real difference and never a difference in scale.
    """
    if not runs:
        return

    # Condition order comes from the study definition, never from discovery order:
    # a run added or removed must not reshuffle the grid.
    order = {c: i for i, c in enumerate(STUDY_CONDITIONS)}
    runs = sorted(runs, key=lambda r: (order.get(r["condition"], len(order)),
                                       r["replicate"]))
    n = len(runs)
    nrows = int(np.ceil(n / ncols))

    # One scale for all panels, derived from the FILTERED curves — the same rule
    # the per-run figure uses. In the noisiest run the raw trace swings an order of
    # magnitude wider than the signal; letting it set the shared scale would flatten
    # all 18 panels to defend one. Raw clips instead, exactly as it already does in
    # the single-run figure.
    y_lo = min(-0.5, min(float(np.nanmin(r["flow_ml_hr"])) for r in runs))
    y_hi = max(NOM_FLOW_ML_HR + 2.0,
               max(float(np.nanmax(r["flow_ml_hr"])) for r in runs))
    x_hi = max(float(r["t_h"][-1]) for r in runs)

    fig, axes = utils_mpl.get_fig_subplots(
        nrows, ncols, size=(3.7 * ncols, 2.15 * nrows), dpi=150,
        sharex=True, sharey=True,
    )
    axes = np.atleast_1d(axes).reshape(nrows, ncols)

    for k, run in enumerate(runs):
        ax = axes[k // ncols][k % ncols]
        colour = run["colour"]
        # Air shading without labels: the figure-level legend carries one entry for
        # all of them, so per-panel labels would repeat it up to 18 times.
        for a, b in run.get("air_intervals", []):
            ax.axvspan(a, b, color=AIR_SHADE, alpha=0.5, lw=0, zorder=0)
        ax.plot(run["t_h"], run["flow_raw_ml_hr"], lw=0.35, color=colour, alpha=0.35)
        ax.plot(run["t_h"], run["flow_ml_hr"], lw=1.1, color=colour)
        ax.axhline(NOM_FLOW_ML_HR, color=INK_PRIMARY, lw=0.7, ls="--", zorder=1)
        ax.set_title(_tex_escape(run["experiment_name"]), fontsize=9, pad=3)
        utils_mpl.set_grid(fig, ax, major=True, minor=False)

    for k in range(n, nrows * ncols):
        axes[k // ncols][k % ncols].axis("off")

    xticks = _nice_ticks(0.0, x_hi, n=5)
    yticks = _nice_ticks(y_lo, y_hi, n=5)
    for r_i in range(nrows):
        for c_i in range(ncols):
            if r_i * ncols + c_i >= n:
                continue
            ax = axes[r_i][c_i]
            utils_mpl.set_format(ax.xaxis, ticks=xticks,
                                 fmt=utils_mpl.make_formatter(".0f"))
            utils_mpl.set_format(ax.yaxis, ticks=yticks,
                                 fmt=utils_mpl.make_formatter(".1f"))
            utils_mpl.set_x_axis(ax, bnd=(0.0, x_hi), margin=0.02)
            utils_mpl.set_y_axis(ax, bnd=(y_lo, y_hi), margin=0.05)
            # Axis labels only on the outer edge: repeating them in every cell is
            # the fastest way to make a small-multiples grid unreadable.
            # "Outer edge" is the last panel in each COLUMN, which is not always the
            # last row — a partly-filled final row leaves some columns ending early.
            # Those panels also need their tick labels switched back on: `sharex`
            # hides them everywhere but the bottom row, which would otherwise leave
            # an axis labelled in words and unlabelled in numbers.
            if r_i == nrows - 1 or (r_i + 1) * ncols + c_i >= n:
                ax.set_xlabel(r"Time since onset (h)", fontsize=9)
                ax.tick_params(labelbottom=True)
            if c_i == 0:
                ax.set_ylabel(r"$q(t)$ (mL/hr)", fontsize=9)

    # Proxy handles in neutral ink: this legend explains the ENCODING (raw vs
    # filtered vs guide), not identity. Identity is the panel title, and hue still
    # means condition as everywhere else in the study.
    handles = [
        Line2D([], [], color=INK_SECONDARY, lw=1.0, alpha=0.5,
               label="offset-corrected flow (unfiltered)"),
        Line2D([], [], color=INK_SECONDARY, lw=1.6,
               label=fr"filtered ({ma_window_min:.0f} min mean)"),
        Line2D([], [], color=INK_PRIMARY, lw=0.9, ls="--",
               label=fr"$q_{{nom}} = {NOM_FLOW_ML_HR:.0f}$ mL/hr"),
        Patch(facecolor=AIR_SHADE, alpha=0.5,
              label="air / bubble (samples excluded)"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), fontsize=9,
               bbox_to_anchor=(0.5, 1.0), borderaxespad=0.0)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    _save_fig(fig, str(out_dir / stem))


def plot_condition_overlay(curves: dict, out_dir: pathlib.Path,
                           stem: str = "comparison_condition_means") -> None:
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
    _save_fig(fig, str(out_dir / stem))
