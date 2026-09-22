"""
report.py — the markdown study summary.

Renders `summary.md`: the declared methodology, the post-processing pipeline with
its parameter values, the per-run acquisition configuration, the results, and an
inventory of the experiments that produced nothing.

The methodology section is rendered from `config.methodology_provenance()`, the
same object embedded in every stats.json, so the report cannot drift away from
what the per-run records claim was in force.
"""
import pathlib

import numpy as np

from config import (
    EXPECTED_REPLICATES, MA_WINDOW_MIN, NOM_FLOW_CORR_ML_HR, NOM_FLOW_ML_HR,
    NACL_FACTOR, SENSOR_IIR_ALPHA, STUDY_CONDITIONS, T_OP_DECLARED_C,
    TEFF_FRAC_OF_NOMINAL, description_for, methodology_provenance,
)


def _md_table(headers: list, rows: list, align: list = None) -> str:
    """Render a markdown table, columns padded so the source is readable too."""
    if not rows:
        return "_(none)_\n"
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], len(str(row[i])))
    align = align or ["l"] * cols

    def fmt(cells):
        return "| " + " | ".join(
            str(c).ljust(widths[i]) if align[i] == "l" else str(c).rjust(widths[i])
            for i, c in enumerate(cells)
        ) + " |"

    sep = "|" + "|".join(
        (":" + "-" * (widths[i] + 1) if align[i] == "l"
         else "-" * (widths[i] + 1) + ":") for i in range(cols)
    ) + "|"
    return "\n".join([fmt(headers), sep] + [fmt(r) for r in rows]) + "\n"


def _n(value, fmt="{:.2f}", dash="—"):
    """Format a possibly-None number for a table cell."""
    return dash if value is None else fmt.format(value)


def _conditions_section(runs: list, incomplete: list) -> list:
    """
    The code → catheter configuration lookup, with how many runs each code has.

    Without this table a reader holding `C2_rep_1` has no way to know what was in
    line for that run: the figures and every other table identify runs by code, and
    the code alone does not say "Contiplex 40 cm + Perifix 0.2 µm filter". Placed
    before the results so it is read first, and covering all eight conditions —
    including the ones with no data, since "C1b has none" is itself a result.
    """
    lines = [
        "## Study conditions",
        "",
        "What was in line for each condition code. The `a`/`b` suffix denotes **pump "
        "reuse** — `a` a first-use pump, `b` a second-use pump — so the same "
        "catheter is compared against itself as the pump deteriorates.",
        "",
    ]
    analysed = {}
    for r in runs:
        analysed.setdefault(r["condition"], []).append(r["replicate"])
    excluded = {}
    for i in incomplete:
        excluded.setdefault(i["condition"], []).append(i["replicate"])

    rows = []
    for cond in STUDY_CONDITIONS:
        got = sorted(analysed.get(cond, []))
        exc = sorted(excluded.get(cond, []))
        rows.append([
            cond,
            description_for(cond),
            f"{len(got)} / {EXPECTED_REPLICATES.get(cond, 0)}",
            ", ".join(got) if got else "—",
            ", ".join(exc) if exc else "—",
        ])
    lines.append(_md_table(
        ["code", "catheter configuration", "analysed", "replicates", "excluded"],
        rows, ["l", "l", "r", "l", "l"],
    ))
    return lines


def _methodology_section(runs: list) -> list:
    """
    The declared experimental methodology, rendered from methodology_provenance().

    Rendered from that dict rather than re-typed here on purpose: it is the same
    object embedded in every stats.json, so the report cannot drift away from what
    the per-run records claim was in force. Anything added to the provenance shows
    up here automatically.
    """
    prov = methodology_provenance()
    lines = [
        "## Experimental methodology",
        "",
        f"Declared configuration, from `{prov['source']}`. These are the parameters "
        "the experiments were run under, not values measured from the data.",
        "",
    ]
    lines.append(_md_table(
        ["parameter", "value", "note"],
        [["ambient temperature",
          f"{prov['ambient_temperature_declared_C']:.0f} °C",
          "declared operating point; the primary value for the flow correction"],
         ["acquisition rate", "0.1 Hz (10 s)",
          "sensor readout and serial polling locked together, f_ro = f_s"],
         ["sensor calibration medium", prov["sensor_calibration_medium"],
          "any other fluid needs a correction factor"],
         ["fluid excess over calibration",
          f"×{prov['fluid_excess_over_calibration']:.2f}",
          "saline solution runs 10 % above the water calibration"],
         ["pump calibration reference",
          f"{prov['pump_calibration_reference_C']:.1f} °C",
          "manufacturer reference for the viscosity correction"],
         ["temperature coefficient",
          f"{100*prov['temperature_coefficient_per_C']:.1f} % per °C",
          "flow falls this much per °C below the reference"],
         ["hydrostatic head",
          f"{prov['hydrostatic_head_m']:.1f} m",
          prov["hydrostatic_head_note"]],
         ["sensor internal filter",
          f"exponential IIR, α = {SENSOR_IIR_ALPHA}",
          "selected by the sensor because the readout rate is 0.1 Hz"],
         ["nominal flow", f"{prov['nominal_flow_ml_hr']:.1f} mL/hr", "pump setting"],
         ["corrected nominal flow",
          f"{prov['corrected_nominal_flow_ml_hr_at_declared_T']:.2f} mL/hr",
          "nominal × fluid factor × temperature correction, at the declared 22 °C"]],
    ))
    lines += [
        "",
        "**Caveats carried from the methodology, not resolved by it:**",
        "",
        f"- {prov['device_temperature_note'].capitalize()}.",
        f"- {prov['air_detection_note'].capitalize()} — see the post-processing "
        "pipeline for how those samples are treated.",
        "- The sensor saturates at ±3250 µL/min; readings outside that range are "
        "not measurements.",
    ]

    # Where the declared 22 °C sits against what the sensor actually recorded.
    temps = [r["stats"]["temperature"].get("T_mean_C") for r in runs]
    temps = [t for t in temps if t is not None]
    sens = [r["stats"]["temperature"].get("q_corr_sensitivity_pct") for r in runs]
    sens = [s for s in sens if s is not None]
    if temps and sens:
        lines += [
            f"- **Measured device temperature runs {min(temps):.1f}–{max(temps):.1f} "
            f"°C**, above the declared {T_OP_DECLARED_C:.0f} °C. Using the measured "
            f"value instead would raise the corrected nominal flow by "
            f"{min(sens):+.1f} % to {max(sens):+.1f} %. The declared value is kept "
            f"as primary for cross-run comparability; the per-run sensitivity is in "
            f"each `stats.json` under `temperature`.",
        ]
    return lines


def _configuration_section(runs: list) -> list:
    """Per-run acquisition configuration, straight from each CSV's metadata header."""
    lines = [
        "## Experiment configuration per run",
        "",
        "Read from the self-describing metadata block each CSV carries, so these "
        "are the parameters that actually ran — not what a launch script intended.",
        "",
    ]
    rows = []
    for r in sorted(runs, key=lambda x: x["experiment_name"]):
        m = r["stats"]["metadata"]
        start = m.get("start_utc", "—")
        rows.append([
            r["stats"]["experiment_name"],
            r["condition"] or "—",
            # The catheter, resolved from config — not from the file's own
            # `configuration_name`, which echoes a bare code for half these runs.
            description_for(r["condition"]),
            m.get("pump_lot", "—"),
            m.get("fluid", "—"),
            m.get("raspberry_id", "—"),
            m.get("sampling_ms", "—"),
            m.get("f_ro_hz", "—"),
            start[:10] if start != "—" else "—",
        ])
    lines.append(_md_table(
        ["run", "code", "catheter configuration", "pump lot", "fluid", "Pi",
         "sampling (ms)", "f_ro (Hz)", "start (UTC)"],
        rows, ["l", "l", "l", "l", "l", "r", "r", "r", "l"],
    ))
    mismatched = [r["stats"]["experiment_name"] for r in runs
                  if r["stats"]["condition_source"] == "folder"]
    if mismatched:
        lines += [
            "",
            f"> **Label mismatch.** {len(mismatched)} run(s) carry a `configuration` "
            f"in their metadata that is not one of the study conditions, so the "
            f"condition was taken from the containing folder instead: "
            f"{', '.join(mismatched)}. The files were renamed by hand after "
            f"acquisition; the recorded metadata was not. Anything that groups by "
            f"the in-file value alone would group these wrong.",
        ]
    return lines


def _pipeline_section(params: dict, runs: list) -> list:
    """The post-processing pipeline, in the order it is applied, with its values."""
    lines = [
        "## Post-processing pipeline",
        "",
        "Applied per run, in this order.",
        "",
    ]
    lines.append(_md_table(
        ["step", "what it does", "parameter / value"],
        [["1. acquisition check",
          "reject runs whose CSV footer is missing or reports INTERRUPTED",
          "the logger's own verdict"],
         ["2. air detection",
          "flag samples the sensor marked as air in line",
          "`Flag_Air`"],
         ["3. offset correction",
          "subtract the zero-flow offset from **every** flow sample",
          f"mean of the first {params['offset_window_h']} h, clipped to the purge end"],
         ["4. bubble-transit rejection",
          "flag the burst a bubble produces — excursion, near-zero plateau, "
          "second excursion — which `Flag_Air` does not fully cover",
          "|q| > 1.5x nominal **or** `Flag_High_Flow`, plus touching near-zero "
          "runs and a 2-sample guard"],
         ["5. onset detection",
          "find infusion onset and re-base time so t = 0 there; cut everything before",
          "first sustained air- and bubble-free sample above 3σ of the offset "
          "window, 2 min debounce"],
         ["6. exclusion",
          "drop air-flagged and bubble samples from curves and integration",
          "line interpolates across the gap; region shaded grey"],
         ["7. filtering",
          "centred moving average over the offset-corrected flow",
          f"{params['ma_window_min']:.0f} min window"],
         ["8. noise quantification",
          "RMS, peak-to-peak and FFT spectrum of (raw − filtered)",
          "mean removed, Hann window, DC bin excluded"],
         ["9. dispensed volume",
          "cumulative integral of the **offset-corrected** flow",
          "trapezoidal rule on the non-uniform grid"],
         ["10. end of infusion",
          "find where the filtered profile settles",
          f"below {100*TEFF_FRAC_OF_NOMINAL:.0f} % of corrected nominal, "
          f"sustained 5 min"],
         ["11. correlation",
          "normalised flow–temperature cross-correlation",
          "lag and peak coefficient, reported as numbers"],
         ["12. condition averaging",
          "mean ± SD of the three curves across replicates",
          "common grid to the longest replicate, all aligned at their own onset"]],
    ))

    if runs:
        chain = runs[0]["stats"]["filter_chain"]
        cuts = chain["minus3db_cutoff_hz"]
        lines += [
            "",
            "### Resolution limit of the reported curves",
            "",
            "Three low-pass stages sit between the fluid and the plotted profile. "
            "Only the narrowest matters, and it is the analysis filter — not the "
            "sensor, and not the sampling rate.",
            "",
        ]
        lines.append(_md_table(
            ["stage", "−3 dB cutoff"],
            [["1. sensor internal IIR smoother",
              f"{cuts['1_sensor_iir_hz']:.2f} Hz" if cuts["1_sensor_iir_hz"] else "—"],
             ["2. readout / sampling (Nyquist)",
              f"{cuts['2_readout_nyquist_hz']:.3f} Hz"],
             [f"3. analysis moving average ({params['ma_window_min']:.0f} min)",
              f"{cuts['3_analysis_moving_average_hz']:.2e} Hz"
              if cuts["3_analysis_moving_average_hz"] else "—"]],
            ["l", "r"],
        ))
        lines += [
            "",
            f"Effective bandwidth: **{chain['effective_bandwidth_hz']:.2e} Hz**, i.e. "
            f"the curves cannot show a feature faster than about "
            f"**{chain['effective_period_h']:.2f} h**. Limiting stage: "
            f"`{chain['limiting_stage']}`.",
        ]
    return lines


def write_summary_report(runs: list, incomplete: list, not_performed: list,
                         curves: dict, params: dict,
                         out_dir: pathlib.Path) -> pathlib.Path:
    """
    The study summary as markdown rather than CSV.

    A 23-column CSV is unreadable without loading it into something, and the parts
    a reader actually compares — dispensed volume, effective duration, noise — do
    not belong in the same block as acquisition bookkeeping. Markdown lets the same
    numbers be split into tables that each answer one question, and lets the
    inventory of incomplete and not-performed experiments sit beside the results
    instead of being absent from them.

    This is also the table view the palette's contrast check obligates: three of
    the eight condition hues fall below 3:1 against white, so identity must never
    rest on colour alone.
    """
    path = out_dir / "summary.md"
    lines = [
        "# Elastomeric infusion pump — flow characterization summary",
        "",
        f"- Runs analysed: **{len(runs)}**",
        f"- Runs excluded as incomplete: **{len(incomplete)}**",
        f"- Runs never performed: **{len(not_performed)}**",
        f"- Conditions with an averaged curve: **{len(curves)}** of "
        f"{len(EXPECTED_REPLICATES)}",
        f"- Figures: {params['plot_format']} at {params['dpi']} dpi",
        "",
        "Every run also carries this same methodology block inside its own "
        "`stats.json`, so a figure can always be traced to the parameters that "
        "produced it.",
        "",
    ]

    lines += _conditions_section(runs, incomplete)
    lines += ["", ""]
    lines += _methodology_section(runs)
    lines += ["", ""]
    lines += _pipeline_section(params, runs)
    lines += ["", ""]
    lines += _configuration_section(runs)
    lines += ["", ""]

    # ── results ───────────────────────────────────────────────────────────────
    lines += ["", "## Results per run", "",
              "`span` is the analysed window from onset to end of record; "
              "`T_eff` is the effective end of infusion.", ""]
    rows = []
    for r in sorted(runs, key=lambda x: x["experiment_name"]):
        s = r["stats"]
        rows.append([
            s["experiment_name"], s["condition"] or "—", s["replicate"],
            _n(s["analysed_span_h"], "{:.1f}"),
            _n(s["teff"]["T_eff_h"], "{:.1f}"),
            _n(s["volume"]["V_dispensed_mL"], "{:.1f}"),
            _n(s["volume"]["V_nominal_over_same_span_mL"], "{:.1f}"),
            _n(s["temperature"].get("T_mean_C"), "{:.1f}"),
            _n(s["noise"].get("residual_rms_ul_min"), "{:.2f}"),
            _n(s["noise"].get("snr_db"), "{:.1f}"),
            _n(s["flow_temperature_correlation"].get("r_max"), "{:+.3f}"),
        ])
    lines.append(_md_table(
        ["run", "cond.", "rep", "span (h)", "T_eff (h)", "V_disp (mL)",
         "V_nom (mL)", "T mean (°C)", "noise RMS (µL/min)", "SNR (dB)", "r_max"],
        rows, ["l", "l", "l"] + ["r"] * 8,
    ))

    # ── acquisition bookkeeping ───────────────────────────────────────────────
    lines += ["", "## Acquisition and correction detail", ""]
    rows = []
    for r in sorted(runs, key=lambda x: x["experiment_name"]):
        s = r["stats"]
        rows.append([
            s["experiment_name"],
            _n(s["acquisition"]["logged_duration_h"], "{:.1f}"),
            f"{s['acquisition']['n_samples_analysed']:,}",
            _n(s["air"]["fraction_total"] * 100.0, "{:.1f}"),
            _n(s["air"]["leading_air_end_h"], "{:.2f}"),
            _n(s["offset_correction"]["offset_ul_min"], "{:+.3f}"),
            _n(s["offset_correction"]["window_h"], "{:.2f}"),
            _n(s["onset"]["t_onset_h_on_logging_timebase"], "{:.2f}"),
            s["condition_source"],
        ])
    lines.append(_md_table(
        ["run", "logged (h)", "samples", "air (%)", "purge end (h)",
         "offset (µL/min)", "offset win (h)", "onset (h)", "cond. source"],
        rows, ["l"] + ["r"] * 7 + ["l"],
    ))

    # ── per condition ─────────────────────────────────────────────────────────
    lines += ["", "## Per condition", "",
              "`full coverage` is how far all replicates still have data; beyond "
              "it the mean is backed by fewer.", ""]
    rows = []
    for cond in STUDY_CONDITIONS:
        c = curves.get(cond)
        members = [r for r in runs if r["condition"] == cond]
        expected = EXPECTED_REPLICATES.get(cond, 0)
        if not members:
            rows.append([cond, description_for(cond), f"0 / {expected}",
                         "—", "—", "—", "—"])
            continue
        v = [r["stats"]["volume"]["V_dispensed_mL"] for r in members]
        te = [r["stats"]["teff"]["T_eff_h"] for r in members
              if r["stats"]["teff"]["T_eff_h"] is not None]
        rows.append([
            cond, description_for(cond), f"{len(members)} / {expected}",
            _n(c["span_h"] if c else None, "{:.1f}"),
            _n(c["full_coverage_h"] if c else None, "{:.1f}"),
            f"{np.mean(v):.1f}" + (f" ± {np.std(v, ddof=1):.1f}"
                                   if len(v) > 1 else ""),
            f"{np.mean(te):.1f}" if te else "—",
        ])
    lines.append(_md_table(
        ["code", "catheter configuration", "reps", "span (h)",
         "full coverage (h)", "V_disp mean ± SD (mL)", "T_eff mean (h)"],
        rows, ["l", "l", "r", "r", "r", "r", "r"],
    ))

    # ── inventory of what produced no result ──────────────────────────────────
    lines += ["", "## Experiments without results", "",
              "Incomplete runs and never-performed runs are grouped here: neither "
              "yields figures or statistics.", ""]
    rows = [[i["condition"] or "—", description_for(i["condition"]),
             i["replicate"], "incomplete", i["reason"]]
            for i in sorted(incomplete, key=lambda x: (x["condition"], x["replicate"]))]
    rows += [[n["condition"], description_for(n["condition"]),
              n["replicate"], "not performed", "no data file"]
             for n in not_performed]
    lines.append(_md_table(
        ["code", "catheter configuration", "replicate", "category", "reason"], rows))

    path.write_text("\n".join(lines))
    return path
