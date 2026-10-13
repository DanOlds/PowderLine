"""Round-trip: TOPAS results -> PowderLine standardized tables (D10).

The INP writes a comment-free ``<base>_results.csv`` (``parameter,value,esd``)
via a dedicated ``out`` block (writer.``_emit_results_export``), so this parser
never has to read the ``.OUT`` (INP-with-comments) — the format the maintainer
asked for (2026-07-27).

``build_roundtrip(recipe, results_csv_text)`` re-renders the recipe to recover
the authoritative parameter index (name -> category/descriptive/phase/atom +
units transform), then maps each refined value + ESD onto the same schema the
GSAS-II path emits in ``refined_parameters.csv`` and ``<phase>_unit_cell_report.csv``.

Units: values are reported in GSAS-II conventions where an exact conversion
exists — ``beq_to_uiso`` (Uiso = beq / 8pi^2, ESD scaled likewise). The refined
histogram ``scale`` is left in its TOPAS magnitude (engine-specific; flagged via
the ``topas_scale`` transform), not force-converted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import conversions as cv
from .errors import TopasTranslationError
from .peak_list import PEAK_LIST_COLUMNS, _rows_of_floats, parse_peak_list  # noqa: F401 (re-exported; native-owned, re/07)
from .writer import render_topas

# Fit-quality scalars the INP exports as value-only rows (no ESD).
_FIT_STATS = ("r_wp", "r_exp", "gof")

# Column order matching the GSAS-II refined_parameters.csv schema.
REFINED_COLUMNS = (
    "parameter_name",
    "descriptive_name",
    "phase_name",
    "phase_idx",
    "atom_name",
    "atom_idx",
    "value",
    "esd",
    "category",
)


@dataclass
class RoundTripResult:
    """Standardized round-trip tables parsed from a TOPAS results CSV."""

    fit: dict  # {r_wp, r_exp, gof}
    refined_parameters: list[dict] = field(default_factory=list)
    unit_cell: dict = field(default_factory=dict)  # phase_name -> [ {parameter,value,esd} ]


# --- parsing ----------------------------------------------------------------


def _safe_float(token: str) -> float | None:
    """Parse a TOPAS/MSVC numeric token, returning None for non-finite/garbage.

    Windows TOPAS can emit ``1.#INF`` / ``1.#QNAN`` (and Python emits ``inf`` /
    ``nan``) for undetermined ESDs on the degenerate profile prms (findings §D.6);
    treat all of those as "no value" rather than propagating inf/nan.
    """
    if token == "":
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def parse_results_csv(text: str) -> tuple[dict, dict]:
    """Parse ``parameter,value,esd`` rows into (fit_stats, {name: (value, esd)}).

    ``esd`` is ``None`` for value-only rows (fit scalars) and for non-finite ESD
    tokens — a bad ESD blanks only the ESD, never drops the whole value row.
    Blank lines and a leading ``parameter,value,esd`` header are ignored.
    """
    fit: dict = {}
    values: dict[str, tuple[float, float | None]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("parameter,"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        value = _safe_float(parts[1])
        if value is None:
            continue  # a row with no usable value carries nothing
        esd = _safe_float(parts[2]) if len(parts) >= 3 else None
        if name in _FIT_STATS:
            fit[name] = value
        else:
            values[name] = (value, esd)
    return fit, values


# --- units transforms -------------------------------------------------------


def _apply_transform(transform: str, value: float, esd: float | None):
    if transform == "beq_to_uiso":
        scaled_esd = None if esd is None else esd / cv.EIGHT_PI_SQ
        return value / cv.EIGHT_PI_SQ, scaled_esd
    # "topas_scale" and "" leave the value untouched (see module docstring)
    return value, esd


# --- table construction -----------------------------------------------------


def build_roundtrip(recipe, results_csv_text: str, base_name: str = "topas") -> RoundTripResult:
    """Map a TOPAS ``<base>_results.csv`` onto PowderLine's standardized tables.

    The results CSV is the **single** source of refined values + ESDs — there is
    no `.OUT` fallback (maintainer, 2026-07-28): a missing/unusable results file
    is a loud failure at the CLI, not a silent switch to another source. An
    individual undetermined ESD legitimately stays blank (non-finite guard in
    :func:`parse_results_csv`).
    """
    rendered = render_topas(recipe, base_name)
    index = rendered.param_index
    fit, values = parse_results_csv(results_csv_text)

    rows: list[dict] = []
    for name, (value, esd) in values.items():
        meta = index.get(name)
        if meta is None:
            # A refined name TOPAS reported that we did not emit an index entry
            # for -> surface it rather than silently dropping it.
            rows.append(_row(name, name, "", "", "", value, esd, "unmapped"))
            continue
        if meta["category"] in ("spf_peak", "cell_report"):
            # spf_peak -> spf_peaks table; cell_report -> unit-cell report below.
            continue
        tvalue, tesd = _apply_transform(meta["transform"], value, esd)
        rows.append(
            _row(
                name,
                meta["descriptive_name"],
                meta["phase_name"],
                meta["phase_idx"],
                meta["atom_name"],
                tvalue,
                tesd,
                meta["category"],
            )
        )

    unit_cell = _unit_cell_reports(recipe, values, index)
    return RoundTripResult(fit=fit, refined_parameters=rows, unit_cell=unit_cell)


def _row(parameter_name, descriptive, phase_name, phase_idx, atom_name, value, esd, category) -> dict:
    return {
        "parameter_name": parameter_name,
        "descriptive_name": descriptive,
        "phase_name": phase_name,
        "phase_idx": phase_idx,
        "atom_name": atom_name,
        "atom_idx": "",
        "value": value,
        "esd": esd,
        "category": category,
    }


_CELL_KEYS = ("a", "b", "c", "alpha", "beta", "gamma")


def _unit_cell_reports(recipe, values, index) -> dict:
    """Per-phase cell reports (cell_a..cell_gamma, cell_volume) mirroring GSAS-II.

    **TOPAS-authoritative (D10-update):** when the results file carries a phase's
    ``cell_*``/``cell_volume`` rows (a real refinement), the value **and** ESD come
    straight from TOPAS -- so a cubic phase reports ``a=b=c`` with a shared ESD (all
    reference one prm) and the volume ESD is covariance-propagated, matching the
    GSAS-II method, not just its numbers.

    **Fallback:** a simulation (calculate-only, zero refined prms) writes no
    ``_results.csv`` and therefore no cell rows -- for such a phase the report falls
    back to the recipe structure values (ESD 0), with the volume computed here.
    """
    payload = recipe.get("payload", {}) if isinstance(recipe, dict) else {}
    phases = payload.get("phases", {}) or {}

    # Gather TOPAS-authoritative cell-report rows by (phase, key) from the results.
    reported: dict[str, dict[str, tuple]] = {}
    for name, meta in index.items():
        if meta.get("category") != "cell_report" or name not in values:
            continue
        key = meta["descriptive_name"].replace("cell_", "")  # a..gamma or volume
        reported.setdefault(meta["phase_name"], {})[key] = values[name]

    reports = {}
    for phase_name, phase in phases.items():
        if phase_name in reported:
            reports[phase_name] = _authoritative_cell_report(reported[phase_name])
        else:
            reports[phase_name] = _fallback_cell_report(phase)
    return reports


def _authoritative_cell_report(cell: dict[str, tuple]) -> list[dict]:
    """Build a report straight from TOPAS-emitted cell rows (value + ESD)."""
    report = []
    for key in _CELL_KEYS:
        value, esd = cell.get(key, (None, None))
        report.append({"parameter": f"cell_{key}", "value": value, "esd": esd if esd is not None else 0.0})
    vol_value, vol_esd = cell.get("volume", (None, None))
    if vol_value is None:  # defensive: derive from the reported lengths/angles
        vol_value = _cell_volume({k: cell.get(k, (None, None))[0] for k in _CELL_KEYS})
    report.append({"parameter": "cell_volume", "value": vol_value, "esd": vol_esd if vol_esd is not None else 0.0})
    return report


def _fallback_cell_report(phase: dict) -> list[dict]:
    """Recipe-value cell report (simulation path): no ESDs, computed volume."""
    ucell = phase.get("structure", {}).get("unit_cell", {})
    cell_vals = {}
    report = []
    for key in _CELL_KEYS:
        value = ucell.get(key)
        cell_vals[key] = value
        report.append({"parameter": f"cell_{key}", "value": value, "esd": 0.0})
    report.append({"parameter": "cell_volume", "value": _cell_volume(cell_vals), "esd": 0.0})
    return report


def _cell_volume(cell: dict) -> float:
    """Triclinic cell volume from a,b,c (Å) and alpha,beta,gamma (deg)."""
    try:
        a, b, c = float(cell["a"]), float(cell["b"]), float(cell["c"])
        ca = math.cos(math.radians(float(cell["alpha"])))
        cb = math.cos(math.radians(float(cell["beta"])))
        cg = math.cos(math.radians(float(cell["gamma"])))
    except (TypeError, ValueError, KeyError):
        return 0.0
    factor = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
    return a * b * c * math.sqrt(max(factor, 0.0))


# --- CSV serialisation ------------------------------------------------------


def _fmt_cell(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return "%.10g" % value
    return str(value)


def refined_parameters_csv(result: RoundTripResult) -> str:
    lines = [",".join(REFINED_COLUMNS)]
    for r in result.refined_parameters:
        lines.append(",".join(_fmt_cell(r[col]) for col in REFINED_COLUMNS))
    return "\n".join(lines) + "\n"


def unit_cell_report_csv(rows: list[dict]) -> str:
    lines = ["parameter,value,esd"]
    for r in rows:
        lines.append(f"{r['parameter']},{_fmt_cell(r['value'])},{_fmt_cell(r['esd'])}")
    return "\n".join(lines) + "\n"


# --- fit profile & peak list (Stage 2: TOPAS numeric -> GSAS-II-matching) ----

#: GSAS-II fit_profile.txt columns (tab-delimited), matched exactly.
FIT_PROFILE_COLUMNS = (
    "two_theta", "y_obs", "y_weights", "y_calc", "y_diff", "y_bkg", "q_values", "d_spacings",
)


def recipe_wavelength(recipe) -> float:
    """Resolve the wavelength (Å): parameterization value if non-null, else Iparm1 Lam."""
    payload = recipe.get("payload", {}) if isinstance(recipe, dict) else {}
    instr = payload.get("instrument", {})
    wl = (instr.get("parameterization", {}) or {}).get("wavelength")
    if wl and wl[0] is not None:
        return float(wl[0])
    init = instr.get("initialization", [{}])
    lam = (init[0] if init else {}).get("Lam")
    if isinstance(lam, list) and len(lam) > 1 and lam[1] is not None:
        return float(lam[1])
    raise TopasTranslationError("could not resolve wavelength from recipe")


def parse_fit_profile(profile_text: str, wavelength: float) -> list[dict]:
    """TOPAS profile (X, 1/sig^2, Yobs, Ycalc, Yobs-Ycalc, bkg) -> GSAS-II rows.

    The INP writes columns ``X Yobs 1/SigmaYobs^2 Ycalc Yobs-Ycalc Get(bkg)``.
    q and d are computed here from 2theta and the recipe wavelength
    (``q = 4 pi sin(theta) / lambda``, ``d = lambda / (2 sin(theta))``).
    """
    out = []
    for x, yobs, yw, ycalc, ydiff, ybkg in _rows_of_floats(profile_text, 6):
        theta = math.radians(x / 2.0)
        sin_t = math.sin(theta)
        d = wavelength / (2.0 * sin_t) if sin_t > 0 else 0.0
        q = 4.0 * math.pi * sin_t / wavelength if wavelength else 0.0
        out.append({
            "two_theta": x, "y_obs": yobs, "y_weights": yw, "y_calc": ycalc,
            "y_diff": ydiff, "y_bkg": ybkg, "q_values": q, "d_spacings": d,
        })
    return out


def _fmt8(value) -> str:
    if isinstance(value, bool):  # bool is an int subclass -> keep True/False verbatim
        return str(value)
    return f"{value:.8f}" if isinstance(value, (int, float)) else str(value)


def fit_profile_txt(rows: list[dict]) -> str:
    """GSAS-II-matching fit_profile.txt: tab-delimited, %.8f, GSAS-II header/order."""
    lines = ["\t".join(FIT_PROFILE_COLUMNS)]
    for r in rows:
        lines.append("\t".join(_fmt8(r[c]) for c in FIT_PROFILE_COLUMNS))
    return "\n".join(lines) + "\n"


def peak_list_report_csv(rows: list[dict]) -> str:
    """GSAS-II-matching <phase>_peak_list_report.csv (comma, %.8f, phase name)."""
    lines = [",".join(PEAK_LIST_COLUMNS)]
    for r in rows:
        lines.append(",".join(_fmt8(r[c]) for c in PEAK_LIST_COLUMNS))
    return "\n".join(lines) + "\n"


# --- single-peak fitting (GSASII_SPF) ---------------------------------------

#: GSAS-II spf_peaks columns (kicker._extract_spf_peak_report), matched exactly.
SPF_PEAK_COLUMNS = (
    "position_2theta", "intensity", "sigma", "sigma_squared", "gamma",
    "fwhm_gaussian", "fwhm_lorentzian", "fwhm_pseudovoigt",
    "integral_breadth_gaussian", "integral_breadth_lorentzian",
    "integral_breadth_pseudovoigt", "fwhm_gsas_verification",
    "converged", "convergence_detail",
)
SPF_DIAG_COLUMNS = ("peak_index", "position_2theta", "final_sigma_sq", "final_gamma", "status", "notes")

# Aphysical-value floors, mirroring kicker.DEFAULT_SPF_SIGMA_MIN / _GAMMA_MIN.
_SPF_SIGMA_MIN = 0.0001
_SPF_GAMMA_MIN = 0.0001


def parse_spf_peaks(recipe, results_csv_text: str):
    """TOPAS SPF results -> (spf_peaks rows, convergence-diagnostic rows).

    Refined peak params come from the results CSV; a peak param that was fixed
    (not exported) falls back to its recipe starting value. Derived widths match
    GSAS-II's report: ``peak_widths(sigma/100, gamma*0.5/100)`` in degrees, with
    the raw sigma/sigma^2/gamma kept in centidegrees (findings §C.3 / kicker
    `_extract_spf_peak_report`).
    """
    payload = (recipe.model_dump() if hasattr(recipe, "model_dump") else recipe).get("payload", {})
    sp = payload.get("single_peaks", {}) or {}
    positions = sp.get("positions") or []
    intensities = sp.get("intensities") or []
    sigsqs = sp.get("pv_gaussian_sigma_sq") or []
    gammas = sp.get("pv_lorentzian_gamma") or []
    _, values = parse_results_csv(results_csv_text)

    def resolve(name, recipe_list, i):
        if name in values:
            return values[name][0]
        return recipe_list[i][0] if i < len(recipe_list) and recipe_list[i] else None

    spf_rows, diag_rows = [], []
    for i in range(len(positions)):
        pos = resolve(f"spfpk{i}_pos", positions, i)
        intensity = resolve(f"spfpk{i}_int", intensities, i)
        sig_sq = resolve(f"spfpk{i}_sigsq", sigsqs, i)
        gamma = resolve(f"spfpk{i}_gam", gammas, i)

        status = "converged"
        if sig_sq is None or gamma is None or not (math.isfinite(sig_sq) and math.isfinite(gamma)):
            status = "nan_warning"
        elif sig_sq <= 0 and gamma < 0:
            status = "negative_sigma_sq_and_gamma_warning"
        elif sig_sq <= 0:
            status = "zero_or_negative_sigma_sq_warning"
        elif gamma < 0:
            status = "negative_gamma_warning"

        sigma = math.sqrt(sig_sq) if (sig_sq is not None and sig_sq > 0) else _SPF_SIGMA_MIN
        gamma_calc = gamma if (gamma is not None and gamma > 0) else _SPF_GAMMA_MIN
        fg, fl, fpv, ibg, ibl, ibpv = cv.peak_widths(sigma / 100.0, gamma_calc * 0.5 / 100.0)
        spf_rows.append({
            "position_2theta": pos, "intensity": intensity, "sigma": sigma,
            "sigma_squared": sig_sq, "gamma": gamma if gamma is not None else gamma_calc,
            "fwhm_gaussian": fg, "fwhm_lorentzian": fl, "fwhm_pseudovoigt": fpv,
            "integral_breadth_gaussian": ibg, "integral_breadth_lorentzian": ibl,
            "integral_breadth_pseudovoigt": ibpv, "fwhm_gsas_verification": fpv,
            "converged": status == "converged", "convergence_detail": status,
        })
        if status != "converged":
            diag_rows.append({
                "peak_index": i, "position_2theta": pos, "final_sigma_sq": sig_sq,
                "final_gamma": gamma, "status": status, "notes": "",
            })
    return spf_rows, diag_rows


def spf_peaks_report_csv(rows: list[dict]) -> str:
    """single_peaks_report.csv (comma; %.8f numerics, bool/str verbatim)."""
    lines = [",".join(SPF_PEAK_COLUMNS)]
    for r in rows:
        lines.append(",".join(_fmt8(r[c]) for c in SPF_PEAK_COLUMNS))
    return "\n".join(lines) + "\n"
