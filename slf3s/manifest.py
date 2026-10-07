"""
manifest.py — load and validate an experiment manifest.

An experiment manifest is a TOML file that declares ONE campaign of ONE experiment:
which conditions exist, what each one means, how many replicates are planned, and
which per-run fields the operator must supply. The logger reads it, refuses a run
that does not fit it, and records it in the file header.

This is what keeps the driver generic. The logger measures flow and knows nothing
about catheters, pumps or any other study: every study-specific word lives in a
manifest file next to the data, not in this package. Adding an experiment means
writing a manifest, never editing the driver. See `manifests/TEMPLATE.toml` for the
annotated format.

TOML because it is in the standard library from Python 3.11 (`tomllib`), so the
format costs no dependency, and unlike JSON it allows comments — a manifest is read
by people deciding what to launch, and the reason a condition exists belongs next
to it.

Running WITHOUT a manifest is allowed and deliberate: someone measuring a flow
outside any study must not have to invent one. In that case nothing is validated
and the header says so (`experiment : none`).
"""
import hashlib
import pathlib
import re
import tomllib
from dataclasses import dataclass

# Version of the manifest FORMAT (not of any one experiment). Bumped only when a
# manifest written for the old format could be misread by this loader.
SCHEMA_VERSION = 1

# Header keys the logger itself owns. A manifest field with one of these names
# would silently overwrite a value the logger computes, so it is rejected.
RESERVED_FIELDS = frozenset({
    "format_version", "experiment", "campaign", "configuration",
    "configuration_name", "experiment_rep", "device_id", "f_ro_hz",
    "sampling_ms", "start_utc", "hostname", "manifest", "manifest_sha256",
})

# Values that mean "nobody filled this in". A required field holding one of them is
# treated as missing, which is the generic form of the LOT-XXXX problem of
# campaign 1: a placeholder that passes every check because it is non-empty.
PLACEHOLDERS = frozenset({"", "unknown", "none", "n/a", "na", "tbd", "todo", "-"})

_TOP_LEVEL = {"schema", "experiment", "conditions", "replicates", "fields",
              "plan", "constraints"}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REPLICATE = re.compile(r"^rep_([1-9][0-9]*)$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ManifestError(ValueError):
    """A manifest is malformed, or a run does not fit the manifest."""


@dataclass(frozen=True)
class Manifest:
    path: pathlib.Path
    sha256: str
    experiment: str
    campaign: object          # int or lowercase name, e.g. 2 or 'main'
    title: str
    status: str
    conditions: dict          # code -> description
    replicates: dict          # code -> planned count (may be empty)
    required: tuple           # field names the operator must supply
    defaults: dict            # field name -> value used when not supplied
    patterns: dict            # field name -> compiled regex the value must match
    plan: dict                # condition -> replicate -> {field: value} it MUST run with

    def resolve_run(self, condition: str, replicate: str, supplied: dict) -> dict:
        """
        Check one run against this manifest and return its per-run fields.

        Returns the fields to record (defaults overlaid by what the operator
        supplied). Raises ManifestError naming every problem at once, so a
        misconfigured launch is fixed in one round trip instead of one per error.
        """
        problems = []
        if self.status != "open":
            problems.append(
                f"campaign {self.campaign} of '{self.experiment}' is {self.status!r}: "
                f"its data is acquired and no new run may be added to it"
            )
        if condition not in self.conditions:
            problems.append(
                f"condition {condition!r} is not in this manifest; valid: "
                f"{', '.join(self.conditions)}"
            )
        if not _REPLICATE.match(replicate or ""):
            problems.append(f"replicate must look like rep_1, rep_2, …; got {replicate!r}")

        fields = {**self.defaults, **supplied}
        for name in self.required:
            if str(fields.get(name, "")).strip().lower() in PLACEHOLDERS:
                problems.append(f"required field {name!r} is missing "
                                f"(pass --meta {name}=…)")
        for name, rx in self.patterns.items():
            value = fields.get(name)
            if value is not None and not rx.fullmatch(str(value)):
                problems.append(f"field {name!r}={value!r} does not match the "
                                f"pattern {rx.pattern!r} declared in the manifest")
        problems += self._check_plan(condition, replicate, fields)
        if problems:
            raise ManifestError(
                f"run rejected by {self.path.name}:\n  - " + "\n  - ".join(problems))
        return fields

    def _check_plan(self, condition: str, replicate: str, fields: dict) -> list:
        """
        Hold the run to the manifest's run plan, if it has one.

        A planned run must be launched with exactly the field values the plan gives
        it (tanda, sensor, lot…): the balance of the design lives in those values,
        and one pump taken from the wrong box breaks it silently.

        An UNPLANNED replicate of a planned condition is a replacement for a failed
        run. It must say which run it replaces (--meta replaces=rep_N) and use the
        same values as that run for every planned field except `tanda` — a
        replacement has to keep the replaced run's lot and sensor, or the design
        stops being balanced.
        """
        cond_plan = self.plan.get(condition)
        if not cond_plan:
            return []
        planned = cond_plan.get(replicate)
        if planned is not None:
            return [f"{condition} {replicate} is planned with {k}={v!r} but was "
                    f"launched with {k}={fields.get(k)!r}"
                    for k, v in planned.items() if str(fields.get(k)) != v]
        target = fields.get("replaces")
        if target not in cond_plan:
            return [f"{condition} {replicate} is not in the plan. A replacement for a "
                    f"failed run must name it: --meta replaces=rep_N (planned: "
                    f"{', '.join(cond_plan)})"]
        return [f"replacement {condition} {replicate} must keep {k}={v!r} of the run "
                f"it replaces ({target}), got {k}={fields.get(k)!r}"
                for k, v in cond_plan[target].items()
                if k != "tanda" and str(fields.get(k)) != v]

    def planned_replicates(self, condition: str):
        return self.replicates.get(condition)


def _require(cond: bool, path: pathlib.Path, message: str) -> None:
    if not cond:
        raise ManifestError(f"{path.name}: {message}")


def load(path) -> Manifest:
    """Read and validate a manifest file. Raises ManifestError with a precise reason."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise ManifestError(f"manifest not found: {path}")
    raw = path.read_bytes()
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{path.name}: not valid TOML — {exc}") from exc

    # Unknown sections are almost always typos ([condition] for [conditions]); a
    # typo silently ignored would mean a manifest that validates nothing.
    unknown = set(doc) - _TOP_LEVEL
    _require(not unknown, path, f"unknown section(s): {', '.join(sorted(unknown))}")
    _require(doc.get("schema") == SCHEMA_VERSION, path,
             f"schema must be {SCHEMA_VERSION} (got {doc.get('schema')!r})")

    exp = doc.get("experiment", {})
    _require(isinstance(exp, dict), path, "[experiment] must be a table")
    exp_id = exp.get("id")
    _require(isinstance(exp_id, str) and _SLUG.match(exp_id or ""), path,
             "[experiment].id must be a lowercase slug, e.g. 'pump-flow'")
    # A campaign is a positive number or a lowercase name ("main", "pilot"…).
    # Names exist because the definitive campaign of a study is not necessarily
    # its second one, and numbering it as such invites reading it as a sequel.
    campaign = exp.get("campaign")
    _require((isinstance(campaign, int) and not isinstance(campaign, bool)
              and campaign >= 1)
             or (isinstance(campaign, str) and _SLUG.match(campaign)), path,
             "[experiment].campaign must be a positive integer or a lowercase "
             "name such as 'main'")
    status = exp.get("status", "open")
    _require(status in ("open", "closed"), path,
             "[experiment].status must be 'open' or 'closed'")

    conditions = doc.get("conditions", {})
    _require(isinstance(conditions, dict) and conditions, path,
             "[conditions] must list at least one condition")
    for code, desc in conditions.items():
        _require(isinstance(desc, str) and desc.strip(), path,
                 f"condition {code!r} needs a non-empty description")

    replicates = doc.get("replicates", {})
    _require(isinstance(replicates, dict), path, "[replicates] must be a table")
    for code, n in replicates.items():
        _require(code in conditions, path,
                 f"[replicates] names {code!r}, which is not a condition")
        _require(isinstance(n, int) and n >= 1, path,
                 f"[replicates].{code} must be a positive integer")

    fields = doc.get("fields", {})
    _require(isinstance(fields, dict), path, "[fields] must be a table")
    required = fields.get("required", [])
    defaults = fields.get("defaults", {})
    raw_patterns = fields.get("patterns", {})
    _require(isinstance(required, list) and all(isinstance(r, str) for r in required),
             path, "[fields].required must be a list of field names")
    _require(isinstance(defaults, dict), path, "[fields.defaults] must be a table")
    _require(isinstance(raw_patterns, dict), path, "[fields.patterns] must be a table")
    for name in set(required) | set(defaults) | set(raw_patterns):
        _require(_FIELD_NAME.match(name), path,
                 f"field name {name!r} must be lowercase snake_case")
        _require(name not in RESERVED_FIELDS, path,
                 f"field name {name!r} is reserved — the logger writes it itself")
    patterns = {}
    for name, rx in raw_patterns.items():
        try:
            patterns[name] = re.compile(rx)
        except (re.error, TypeError) as exc:
            raise ManifestError(f"{path.name}: bad pattern for {name!r} — {exc}") from exc

    plan = _load_plan(doc.get("plan", {}), path, conditions, required, defaults,
                      patterns)
    _check_constraints(doc.get("constraints", {}), path, plan)

    return Manifest(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        experiment=exp_id,
        campaign=campaign,
        title=str(exp.get("title", "")),
        status=status,
        conditions=dict(conditions),
        replicates=dict(replicates),
        required=tuple(required),
        defaults={k: str(v) for k, v in defaults.items()},
        patterns=patterns,
        plan=plan,
    )


def _load_plan(raw, path, conditions, required, defaults, patterns) -> dict:
    """[plan.<condition>] rep_N = { field = "value", … } — validated, as strings."""
    _require(isinstance(raw, dict), path, "[plan] must be a table")
    declared = set(required) | set(defaults) | set(patterns)
    plan = {}
    for cond, reps in raw.items():
        _require(cond in conditions, path, f"[plan] names {cond!r}, which is not a condition")
        _require(isinstance(reps, dict), path, f"[plan.{cond}] must be a table")
        plan[cond] = {}
        for rep, values in reps.items():
            _require(bool(_REPLICATE.match(rep)), path,
                     f"[plan.{cond}] key {rep!r} must look like rep_1, rep_2, …")
            _require(isinstance(values, dict) and values, path,
                     f"[plan.{cond}].{rep} must be a non-empty inline table")
            for k, v in values.items():
                _require(k in declared, path, f"[plan.{cond}].{rep} sets {k!r}, which "
                         f"is not a declared field (required/defaults/patterns)")
                rx = patterns.get(k)
                _require(rx is None or bool(rx.fullmatch(str(v))), path,
                         f"[plan.{cond}].{rep} {k}={v!r} does not match its pattern")
            plan[cond][rep] = {k: str(v) for k, v in values.items()}
    return plan


def _check_constraints(raw, path, plan) -> None:
    """
    Design rules checked against the plan when the manifest is loaded, so an edit
    that breaks the design is caught before any run, not discovered in analysis.

      vary_within_condition = ["pump_lot"]   each condition's planned runs must use
                                             at least two values of each field
      unique_together = [["tanda", "sensor"]] no two planned runs share these values
                                             (one sensor cannot run two things at once)
    """
    _require(isinstance(raw, dict), path, "[constraints] must be a table")
    unknown = set(raw) - {"vary_within_condition", "unique_together"}
    _require(not unknown, path, f"unknown constraint(s): {', '.join(sorted(unknown))}")
    runs = [(c, r, v) for c, reps in plan.items() for r, v in reps.items()]
    # A rule over a field the plan does not set would compare missing values with
    # each other and report a meaningless collision; require the field instead.
    for field in set(raw.get("vary_within_condition", [])) | {
            f for g in raw.get("unique_together", []) for f in g}:
        for cond, rep, v in runs:
            _require(field in v, path,
                     f"[constraints] uses {field!r}, but [plan.{cond}].{rep} does not "
                     f"set it — every planned run must give a value to a constrained field")
    for field in raw.get("vary_within_condition", []):
        for cond, reps in plan.items():
            values = {v.get(field) for v in reps.values()}
            _require(len(values) >= 2, path,
                     f"constraint vary_within_condition: every planned run of {cond} "
                     f"uses {field}={next(iter(values))!r} — a condition measured with a "
                     f"single {field} cannot be separated from it")
    for group in raw.get("unique_together", []):
        seen = {}
        for cond, rep, v in runs:
            key = tuple(v.get(f) for f in group)
            _require(key not in seen, path,
                     f"constraint unique_together {group}: {cond} {rep} and "
                     f"{seen.get(key)} share {dict(zip(group, key))}")
            seen[key] = f"{cond} {rep}"


def parse_meta(pairs) -> dict:
    """Turn ['k=v', …] from repeated --meta flags into a dict, rejecting bad pairs."""
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not _FIELD_NAME.match(key):
            raise ManifestError(f"--meta expects key=value with a snake_case key; got {pair!r}")
        if key in RESERVED_FIELDS:
            raise ManifestError(f"--meta {key}=… is reserved — the logger writes it itself")
        out[key] = value.strip()
    return out
