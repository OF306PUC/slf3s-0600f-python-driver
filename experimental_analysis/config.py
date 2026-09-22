"""
config.py — study configuration and declared experimental methodology.

Two things live here and nothing else: WHICH experiments the study consists of,
and WHAT parameters they were declared to run under. Both are referenced from
almost every other module, and both are the kind of value that must have exactly
one home — a second copy of "the conditions are these eight" or "ambient is 22 °C"
is a future contradiction.

Nothing in this module reads data or computes results. Values MEASURED from the
data never appear here; they are computed per run in stats.py.
"""


# The 8 conditions of the study, mirroring raspberry/core.py CONFIG_NAMES. Kept as
# a literal because experimental_analysis/ runs on a workstation and must not
# import from the Pi-side driver package (raspberry/ is deliberately standalone).
# If a condition is added there, add it here too.
STUDY_CONDITIONS = (
    "C0a", "C0b", "C0c", "C1a", "C1b", "C2", "C3", "C4",
)

# Replicates the protocol calls for, per condition. `C0c` is a single run by
# design; every other condition expects three. This is what makes "not performed"
# a computable category rather than something a reader has to notice: any expected
# (condition, replicate) with no file is reported as missing.
EXPECTED_REPLICATES = {
    "C0a": 3, "C0b": 3, "C0c": 1, "C1a": 3,
    "C1b": 3, "C2": 3, "C3": 3, "C4": 3,
}

# What each condition code actually IS — the catheter, filter and pump reuse in
# line. Mirrors raspberry/core.py CONFIG_NAMES, a literal for the same reason
# STUDY_CONDITIONS is.
#
# This is the authoritative source for the analysis side, NOT the
# `configuration_name` field in each CSV's metadata header. That field is unusable
# for the current data: the logger fills it from a lookup that did not contain
# C0a/C0b/C0c when those runs were made, so it fell back to echoing the bare code,
# and the C1a runs were launched as `C1` so theirs reads "C1". Taking the
# description from the file would label half the campaign with a code instead of a
# catheter.
CONDITION_DESCRIPTIONS = {
    "C0a": "Sin catéter — bomba primera vez",
    "C0b": "Sin catéter — bomba segunda vez",
    "C0c": "Sin catéter — solución con bupivacaína (NaCl 240 mL + BuPi 60 mL)",
    "C1a": "Contiplex 40 cm (3 orificios laterales) — bomba primera vez",
    "C1b": "Contiplex 40 cm (3 orificios laterales) — bomba segunda vez",
    "C2":  "Contiplex 40 cm + filtro Perifix 0,2 µm",
    "C3":  "Contiplex 100 cm (3 orificios laterales)",
    "C4":  "Catéter peridural pediátrico (orificio terminal)",
}


def description_for(condition: str) -> str:
    """The catheter configuration behind a condition code, or a visible placeholder."""
    return CONDITION_DESCRIPTIONS.get(condition, "—")


# One fixed colour PER CONDITION, keyed by the condition itself — never derived
# from a run's position in the input list, so a colour means the same thing in
# every figure of the study.
#
# Eight-slot categorical palette, validated for colour-vision deficiency and for
# normal-vision separation on a light (print) surface: worst adjacent CVD ΔE 9.1,
# worst adjacent normal-vision ΔE 19.6. Three slots (aqua, yellow, magenta) fall
# below 3:1 contrast against white, so every figure using them ships a legend and
# a companion table (comparison_summary.csv) rather than relying on colour alone.
CONDITION_COLOURS = {
    "C0a": "#2a78d6",   # blue
    "C0b": "#eb6834",   # orange
    "C0c": "#1baf7a",   # aqua
    "C1a": "#eda100",   # yellow
    "C1b": "#e87ba4",   # magenta
    "C2":  "#008300",   # green
    "C3":  "#4a3aa7",   # violet
    "C4":  "#e34948",   # red
}
FALLBACK_COLOUR = "#52514e"

# Replicates share their condition's hue and are separated by line style, so hue
# always means "condition" and never "which replicate".
REP_LINESTYLES = {"rep_1": "-", "rep_2": "--", "rep_3": ":"}
FALLBACK_LINESTYLE = "-."

# Ink colours for text and guide lines — never a series colour.
INK_PRIMARY   = "#0b0b0b"
INK_SECONDARY = "#52514e"
AIR_SHADE     = "#c3c2b7"


def resolve_condition(metadata_cfg: str, folder_name: str) -> tuple:
    """
    (condition, source) for a run — the metadata label if it is one of the 8,
    otherwise the containing folder's name if that is.

    The fallback exists because it is needed: the three C1a runs were launched as
    `--configuration C1` and filed into Temp/C1a/ by hand afterwards, so their
    in-file metadata says `C1`, which is not a study condition. Trusting metadata
    alone would drop them out of their own condition and make averaging its
    replicates (§14) impossible. The chosen source is recorded in stats.json so
    the mismatch stays visible instead of being papered over.
    """
    if metadata_cfg in CONDITION_COLOURS:
        return metadata_cfg, "metadata"
    if folder_name in CONDITION_COLOURS:
        return folder_name, "folder"
    return "", "unresolved"


def colour_for(condition: str) -> str:
    return CONDITION_COLOURS.get(condition, FALLBACK_COLOUR)


def linestyle_for(experiment_rep: str) -> str:
    return REP_LINESTYLES.get(experiment_rep, FALLBACK_LINESTYLE)


# Every value here is a DECLARED experimental parameter, traceable to
# methodology.txt or to the pump/sensor documentation. Values measured FROM the
# data are never hard-coded here — they are computed per run.

UL_MIN_TO_ML_HR = 60.0 / 1000.0     # µL/min → mL/hr

# ── Pump and fluid ────────────────────────────────────────────────────────────
NOM_FLOW_ML_HR     = 5.0       # pump-set nominal flow rate (mL/hr)
NOM_VOLUME_ML      = 300.0     # nominal reservoir volume (mL)
CALIBRATION_MEDIUM = "water"   # methodology.txt: sensor calibrated on water

# methodology.txt: the saline solution runs 10 % above the water calibration.
NACL_FACTOR = 1.10

# Pump viscosity/temperature correction: flow falls 2.3 % per °C below the
# manufacturer's calibration reference temperature.
T_NOM_C         = 31.1     # pump calibration reference temperature (°C)
T_OP_DECLARED_C = 22.0     # methodology.txt: "Temperature: 22 °C" (ambient)
TEMP_CORR_PER_C = 0.023    # 2.3 % flow reduction per °C below reference

# methodology.txt: the distal restrictor sits level with the fill port, so the
# hydrostatic head across the line is zero and contributes no pressure term.
# Recorded rather than merely assumed, so a future run at a different height is
# not silently compared against these.
HYDROSTATIC_HEAD_M = 0.0

# ── Sensor uncertainty ────────────────────────────────────────────────────────
REL_ERROR = 0.05           # ±5 % relative sensor error

# ── Post-processing defaults (methodology.txt § Curvas) ───────────────────────
OFFSET_WINDOW_H = 2.0      # "corregir offset en base a las primeras 1.5~2 horas"

# Filtering window. Provisional at 10 min: a 60-minute mean smeared the
# end-of-infusion decay — the very feature the curves exist to show — across
# roughly half its own width. Better filtering is a decision deferred to later; the
# window is a CLI flag so that decision does not need a code change.
MA_WINDOW_MIN = 10.0

# Baseline plausibility gate: an offset above this fraction of nominal flow means
# the run was ALREADY infusing when logging started, so there is no zero-flow
# period to measure and everything derived from it is meaningless.
OFFSET_IMPLAUSIBLE_FRAC = 0.10

# End-of-infusion threshold, as a fraction of the corrected nominal flow: the
# infusion is over once the filtered profile settles below it.
#
# Expressed against nominal rather than against the offset window's own 3σ, which
# was the first attempt and was wrong by orders of magnitude: that σ is 0.0–0.15
# µL/min, so the threshold sat essentially at zero and no run ever crossed it —
# every C3 replicate was declared "never reached end of infusion" when all three
# plainly decay around 79–80 h. Post-infusion residual noise is far larger than the
# noise of a quiet 2-hour window, so the gate has to be scaled to the signal.
TEFF_FRAC_OF_NOMINAL = 0.05

# ── Bubble transit rejection ──────────────────────────────────────────────────
# A bubble crossing the measurement section does not read as "air" throughout.
# It reads as a burst: a large flow excursion as the gas front enters, a near-zero
# plateau while the bubble occupies the sensor, and a second excursion as it
# leaves. `Flag_Air` covers the plateau but not reliably the flanking spikes —
# measured across this campaign, 143 of 167 excursions above 1.5x nominal carry no
# air flag at all, and in C1a_rep_3 and both C4 runs not one of the 97 excursions
# is flagged. Left in, they enter the curves and the volume integral as if they
# were flow.
#
# Detection is the UNION of two criteria because neither is sufficient alone:
# C1a_rep_3 has 58 excursions with zero `Flag_High_Flow` set, and C2_rep_3 has 3
# high-flow flags with no large excursion.
BUBBLE_SPIKE_FRAC = 1.5    # |q| above this multiple of nominal is an excursion
BUBBLE_ZERO_FRAC = 0.10    # |q| below this multiple of nominal is "plateau"
# Samples of guard on each side of an excursion. The sensor's own IIR smoothing
# spreads a step over neighbouring samples, so the sample next to a 3000 µL/min
# spike is already contaminated even when it looks ordinary.
BUBBLE_GUARD_SAMPLES = 2

# Longest near-zero run still attributable to a bubble sitting in the sensor.
# WITHOUT this cap the plateau clause is actively destructive: a spike landing at
# the start of the end-of-infusion decay makes the entire post-infusion tail one
# contiguous near-zero run that "touches a spike", and the whole thing is deleted.
# It happened — C2_rep_2 lost 18.5 h (19 % of its record) and was demoted to
# "incomplete" because find_teff could no longer see the flow settle.
#
# 5 min is where the data separates, not a round number: across the campaign, 89
# near-zero runs touch a spike, with a median of 0.67 min and a 90th percentile of
# 2.17 min. The next four are 7.5, 151, 552 and 1107 min — and all four are
# identifiable as the purge or an infusion tail, never a bubble. Physically the cap
# is generous: at 83 µL/min a bubble crosses the measurement section in seconds.
BUBBLE_MAX_PLATEAU_MIN = 5.0


# ── Sensor internal filter ────────────────────────────────────────────────────
# methodology.txt: "f_ro = 0.1 Hz también por ende el sensor utiliza el filtro
# recursivo ... (Exponential smoother (IIR))". Because the readout rate is 0.1 Hz
# the sensor selects its recursive smoother, and these are its parameters.
#
# Duplicated from SLF3S-0600F_filters.py rather than imported: that filename begins
# with a digit and contains a hyphen, so it is not a valid module name. Keep the
# two in step.
SENSOR_IIR_ALPHA   = 0.0125     # exponential smoother coefficient
SENSOR_INTERNAL_FS = 2000.0     # Hz, sensor internal sampling rate


def corrected_nominal_flow_ml_hr(t_op_c: float = T_OP_DECLARED_C) -> float:
    """
    Nominal flow corrected for fluid and temperature, in mL/hr.

        q_corr = q_nom · NaCl_factor · (1 − k·(T_ref − T_op))

    Parameterised on T_op instead of closing over a module constant so §11 can
    evaluate it at both the DECLARED operating temperature and the temperature
    actually measured during the run, and report the sensitivity between them.
    """
    return (
        NOM_FLOW_ML_HR * NACL_FACTOR
        * (1.0 - TEMP_CORR_PER_C * (T_NOM_C - t_op_c))
    )


NOM_FLOW_CORR_ML_HR = corrected_nominal_flow_ml_hr()


def methodology_provenance() -> dict:
    """
    The declared methodology, emitted verbatim into every stats.json.

    A result read months from now must carry the assumptions it was computed
    under. Recording them in the output — not only in methodology.txt — means a
    figure cannot drift away from the parameters that produced it.
    """
    return {
        "source": "methodology.txt",
        "ambient_temperature_declared_C": T_OP_DECLARED_C,
        "sensor_calibration_medium": CALIBRATION_MEDIUM,
        "fluid_excess_over_calibration": NACL_FACTOR,
        "pump_calibration_reference_C": T_NOM_C,
        "temperature_coefficient_per_C": TEMP_CORR_PER_C,
        "hydrostatic_head_m": HYDROSTATIC_HEAD_M,
        "hydrostatic_head_note": (
            "distal flow restrictor level with the fill port — zero head, no "
            "pressure term"
        ),
        "device_temperature_note": (
            "sensor temperature approximates the temperature at the flow "
            "measurement point; it is not a direct fluid measurement and "
            "includes sensor self-heating"
        ),
        "air_detection_note": (
            "air-flagged samples are excluded from all curves and from the "
            "volume integral; the plotted line interpolates across the gap and "
            "the interval is shaded"
        ),
        "bubble_rejection_spike_frac_of_nominal": BUBBLE_SPIKE_FRAC,
        "bubble_rejection_zero_frac_of_nominal": BUBBLE_ZERO_FRAC,
        "bubble_rejection_guard_samples": BUBBLE_GUARD_SAMPLES,
        "bubble_rejection_max_plateau_min": BUBBLE_MAX_PLATEAU_MIN,
        "bubble_rejection_note": (
            "a bubble crossing the sensor reads as a large flow excursion, a "
            "near-zero plateau, and a second excursion — not as air throughout. "
            "Samples matching that signature (|q| above the spike fraction OR "
            "Flag_High_Flow set, any contiguous near-zero run touching such a "
            "spike and no longer than the plateau cap, plus a guard on each "
            "side) are excluded exactly as air is. "
            "The near-zero criterion applies ONLY next to a spike: the end of "
            "infusion is legitimately near zero and must not be rejected"
        ),
        "nominal_flow_ml_hr": NOM_FLOW_ML_HR,
        "corrected_nominal_flow_ml_hr_at_declared_T": round(NOM_FLOW_CORR_ML_HR, 4),
    }
