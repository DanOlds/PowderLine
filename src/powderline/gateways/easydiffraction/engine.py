"""easydiffraction engine adapter for ``powderline.run(engine="easydiffraction")``.

Composes the builder/policy pieces and returns the **same dict shape** the GSAS-II
engine returns: DataFrame ``fit_profile`` / ``refined_parameters``, ``dict{phase: DataFrame}``
``unit_cell_data`` / ``peak_list_data``, and every structured key normalized to an
empty DataFrame on failure. Additionally returns fit stats ``rwp``/``r_exp``/``gof``
(TOPAS parity). Imports pandas but **no module-level easydiffraction import**.
"""

from __future__ import annotations

import time
import traceback as tb
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .conversions import q_and_d, rwp_fraction_to_percent
from .errors import EasyDiffractionTranslationError
from .policy import check_unsupported


def _as_dict(recipe):
    return recipe.model_dump() if hasattr(recipe, "model_dump") else recipe


def _empty_df(columns=None) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns)) if columns else pd.DataFrame()


def _num(x):
    """Extract numeric value from easydiffraction descriptor objects or plain floats."""
    return getattr(x, "value", x)


def _write_report(path, text) -> None:
    """Write a report file deterministically across platforms.

    UTF-8 encoding (never the Windows cp1252 default, which would choke on any
    non-ASCII phase name) and LF newlines on every OS (``newline=""`` disables
    the Windows CRLF translation), matching the GSAS-II report writers.
    """
    path.write_text(text, encoding="utf-8", newline="")


def _validate_only_result(recipe) -> dict:
    """Slim summary matching the GSAS-II ``validate_only`` shape (no run_id)."""
    payload = recipe.get("payload", {})
    controls = payload.get("refinement_controls", {}) or {}
    cycles = controls.get("refinement_cycles")
    return {
        "success": True,
        "rwp": None,
        "elapsed_time": 0.0,
        "method": "validate_only",
        "schema_name": recipe.get("schema_name"),
        "schema_version": recipe.get("schema_version"),
        "phases": len(payload.get("phases") or {}),
        "refinement_cycles": cycles,
        "simulation_mode": cycles == 1,
    }


def _unit_cell_rows(phase_struct) -> list[dict]:
    """cell_a..cell_gamma + cell_volume rows (GSAS-II/TOPAS report parity).

    Shared by the on-disk report and the in-memory result dict so the two
    cannot drift. The volume ESD is first-order propagation in quadrature
    from the six cell parameters' uncertainties (uncorrelated approximation;
    GSAS-II propagates with the covariance matrix, so ESDs can differ
    slightly while values match).
    """
    cell = phase_struct.cell
    names = ["cell_a", "cell_b", "cell_c", "cell_alpha", "cell_beta", "cell_gamma"]
    attrs = ["length_a", "length_b", "length_c",
             "angle_alpha", "angle_beta", "angle_gamma"]
    values, esds, rows = [], [], []
    for name, attr in zip(names, attrs):
        param = getattr(cell, attr)
        value = float(_num(param.value))
        esd = float(_num(param.uncertainty if param.uncertainty is not None else 0.0))
        values.append(value)
        esds.append(esd)
        rows.append({"parameter": name, "value": value, "esd": esd})

    def _volume(p):
        a, b, c, al, be, ga = p
        ca, cb, cg = (np.cos(np.radians(x)) for x in (al, be, ga))
        arg = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
        return a * b * c * float(np.sqrt(max(arg, 0.0)))

    vol = _volume(values)
    var = 0.0
    for i, esd in enumerate(esds):
        if esd:
            perturbed = list(values)
            perturbed[i] += esd
            var += (_volume(perturbed) - vol) ** 2
    rows.append({"parameter": "cell_volume", "value": vol, "esd": float(np.sqrt(var))})
    return rows


def _write_reports(build_result, arrays, output_dir) -> None:
    """Write standardized GSAS-II-matching report files to output_dir."""
    from .builder import BuildResult

    br: BuildResult = build_result
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # refined_parameters.csv
    rows = []
    for entry in br.manifest:
        param = entry.parameter
        value = _num(param.value) * entry.scale_to_recipe
        esd_raw = param.uncertainty if param.uncertainty is not None else 0.0
        esd = _num(esd_raw) * entry.scale_to_recipe
        rows.append({
            "parameter_name": entry.parameter_name,
            "descriptive_name": entry.descriptive_name,
            "phase_name": entry.phase_name,
            "phase_idx": entry.phase_idx,
            "atom_name": entry.atom_name,
            "atom_idx": entry.atom_idx,
            "value": value,
            "esd": esd,
            "category": entry.category,
        })
    refined_df = pd.DataFrame(rows, columns=[
        "parameter_name", "descriptive_name", "phase_name", "phase_idx",
        "atom_name", "atom_idx", "value", "esd", "category"
    ])
    _write_report(output_dir / "refined_parameters.csv",
                  refined_df.to_csv(index=False, lineterminator="\n"))

    # fit_profile.txt — scatter masked arrays back to full grid
    full_calc = np.zeros(len(br.tth))
    full_diff = np.zeros(len(br.tth))
    full_bkg = np.zeros(len(br.tth))

    if arrays:
        full_calc[br.mask] = arrays["calc"]
        full_diff[br.mask] = arrays["diff"]
        full_bkg[br.mask] = arrays["bkg"]

    q_full, d_full = q_and_d(br.tth, br.wavelength)

    profile_rows = {
        "two_theta": br.tth,
        "y_obs": br.itth,
        "y_weights": br.weights,
        "y_calc": full_calc,
        "y_diff": full_diff,
        "y_bkg": full_bkg,
        "q_values": q_full,
        "d_spacings": d_full,
    }
    profile_df = pd.DataFrame(profile_rows)

    # Tab-separated %.8f format matching GSAS-II. UTF-8 + LF on every platform
    # (newline="\n" disables the Windows CRLF translation) for deterministic output.
    with (output_dir / "fit_profile.txt").open("w", encoding="utf-8", newline="\n") as f:
        # Header
        f.write("\t".join(profile_df.columns) + "\n")
        # Data rows
        for _, row in profile_df.iterrows():
            f.write("\t".join(f"{v:.8f}" for v in row) + "\n")

    # Per-phase unit_cell_report.csv
    for recipe_name, slug in br.phase_slugs.items():
        # Access phase structure from project.structures
        try:
            phase_struct = br.project.structures[slug]
        except (KeyError, AttributeError):
            continue

        cell_df = pd.DataFrame(_unit_cell_rows(phase_struct))
        _write_report(output_dir / f"{recipe_name}_unit_cell_report.csv",
                      cell_df.to_csv(index=False, lineterminator="\n"))

    # Per-phase peak_list_report.csv (only if refln exists)
    if br.experiment.refln is not None:
        for recipe_name, slug in br.phase_slugs.items():
            refln = br.experiment.refln
            # Filter by structure_id == slug
            mask = refln.structure_id == slug

            if not np.any(mask):
                continue

            peak_rows = {
                "h": refln.index_h[mask],
                "k": refln.index_k[mask],
                "l": refln.index_l[mask],
                "d_spacing": refln.d_spacing[mask],
                "2theta": refln.two_theta[mask],
                "F_calc_squared": refln.f_squared_calc[mask],
            }
            peak_df = pd.DataFrame(peak_rows)
            _write_report(output_dir / f"{recipe_name}_peak_list_report.csv",
                          peak_df.to_csv(index=False, lineterminator="\n"))


def _result_dict(*, success, method, output_dir, build_result=None, arrays=None,
                 rwp=None, r_exp=None, gof=None, elapsed=0.0, error=None, error_traceback=None) -> dict:
    """Assemble the locked run() dict shape from easydiffraction outputs (or empties)."""
    refined_columns = [
        "parameter_name", "descriptive_name", "phase_name", "phase_idx",
        "atom_name", "atom_idx", "value", "esd", "category"
    ]
    profile_columns = [
        "two_theta", "y_obs", "y_weights", "y_calc", "y_diff", "y_bkg",
        "q_values", "d_spacings"
    ]

    refined = _empty_df(refined_columns)
    fit_profile = _empty_df()
    unit_cell_data = {}
    peak_list_data = {}

    if build_result and success:
        # Build refined_parameters DataFrame
        rows = []
        for entry in build_result.manifest:
            param = entry.parameter
            value = _num(param.value) * entry.scale_to_recipe
            esd_raw = param.uncertainty if param.uncertainty is not None else 0.0
            esd = _num(esd_raw) * entry.scale_to_recipe
            rows.append({
                "parameter_name": entry.parameter_name,
                "descriptive_name": entry.descriptive_name,
                "phase_name": entry.phase_name,
                "phase_idx": entry.phase_idx,
                "atom_name": entry.atom_name,
                "atom_idx": entry.atom_idx,
                "value": value,
                "esd": esd,
                "category": entry.category,
            })

        if rows:
            refined = pd.DataFrame(rows, columns=refined_columns)
        else:
            refined = _empty_df(refined_columns)

        # Build fit_profile DataFrame
        full_calc = np.zeros(len(build_result.tth))
        full_diff = np.zeros(len(build_result.tth))
        full_bkg = np.zeros(len(build_result.tth))

        if arrays:
            full_calc[build_result.mask] = arrays["calc"]
            full_diff[build_result.mask] = arrays["diff"]
            full_bkg[build_result.mask] = arrays["bkg"]

        q_full, d_full = q_and_d(build_result.tth, build_result.wavelength)

        fit_profile = pd.DataFrame({
            "two_theta": build_result.tth,
            "y_obs": build_result.itth,
            "y_weights": build_result.weights,
            "y_calc": full_calc,
            "y_diff": full_diff,
            "y_bkg": full_bkg,
            "q_values": q_full,
            "d_spacings": d_full,
        })

        # Build unit_cell_data
        for recipe_name, slug in build_result.phase_slugs.items():
            try:
                phase_struct = build_result.project.structures[slug]
            except (KeyError, AttributeError):
                continue

            unit_cell_data[recipe_name] = pd.DataFrame(_unit_cell_rows(phase_struct))

        # Build peak_list_data
        if build_result.experiment.refln is not None:
            for recipe_name, slug in build_result.phase_slugs.items():
                refln = build_result.experiment.refln
                mask = refln.structure_id == slug

                if not np.any(mask):
                    continue

                peak_rows = {
                    "h": refln.index_h[mask],
                    "k": refln.index_k[mask],
                    "l": refln.index_l[mask],
                    "d_spacing": refln.d_spacing[mask],
                    "2theta": refln.two_theta[mask],
                    "F_calc_squared": refln.f_squared_calc[mask],
                }
                peak_list_data[recipe_name] = pd.DataFrame(peak_rows)

    return {
        "success": success,
        "run_id": str(uuid.uuid4()),
        "rwp": rwp,
        "r_exp": r_exp,
        "gof": gof,
        "elapsed_time": elapsed,
        "method": method,
        "output_files": sorted(str(f) for f in Path(output_dir).glob("*") if f.is_file()),
        "fit_profile": fit_profile,
        "unit_cell_data": unit_cell_data,
        "peak_list_data": peak_list_data,
        "refined_parameters": refined,
        "spf_peaks": _empty_df(),  # easydiffraction has no SPF mode
        "spf_convergence_diagnostics": _empty_df(),
        "error": error,
        "traceback": error_traceback,
    }


def run_easydiffraction_recipe(
    recipe, output_dir, *, verbose=False, validate_only=False
) -> dict:
    """Drive the easydiffraction engine and return the standardized ``run()`` dict.

    ``validate_only`` checks for unsupported features (no easydiffraction import)
    and returns a slim summary. Otherwise: build project → fit or calculate →
    extract stats → write reports. Translation errors (``EasyDiffractionTranslationError``,
    ``ImportError``) propagate; post-build runtime failures return ``success=False``
    with ``error`` + ``traceback`` strings.
    """
    recipe = _as_dict(recipe)
    output_dir = Path(output_dir)

    if validate_only:
        # No easydiffraction import in validate_only path
        # check_unsupported raises for actual errors, returns warnings list
        _ = check_unsupported(recipe)  # may raise EasyDiffractionTranslationError
        return _validate_only_result(recipe)

    # Import build_project lazily (may raise ImportError)
    from .builder import build_project

    # build_project may raise EasyDiffractionTranslationError or ImportError — let them propagate
    workdir = output_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    br = build_project(recipe, str(workdir))

    if verbose:
        print(f"[easydiff] built project, {len(br.manifest)} parameters in manifest")
        for w in br.warnings:
            print(f"[easydiff] warning: {w}")

    # Everything past this point wrapped in try/except — runtime failures return success=False
    is_simulation = not br.manifest
    method = "easydiffraction_simulation" if is_simulation else "easydiffraction"

    try:
        if is_simulation:
            # No refinement, just calculate pattern
            br.project.analysis.calculate()
            arrays = br.experiment.data.fit_data_arrays()
            rwp = None
            r_exp = None
            gof = None
        else:
            # Fit sequence
            br.project.analysis.minimizer.type = "lmfit (leastsq)"
            br.project.analysis.fit()

            # Extract stats
            fit_result = br.project.analysis.fit_result
            fit_results = br.project.analysis.fit_results

            rwp_frac = _num(fit_result.prof_wr_factor)
            rexp_frac = _num(fit_result.prof_wr_expected)
            gof_val = _num(fit_results.reduced_chi_square)

            rwp = rwp_fraction_to_percent(rwp_frac)
            r_exp = rwp_fraction_to_percent(rexp_frac)
            gof = gof_val

            arrays = br.experiment.data.fit_data_arrays()

        # Harvest hkl reflections when the fitting calculator can't provide
        # them (TCH profile runs on CrysFML, which has no refln support).
        # Reflection positions and F^2 depend on the refined structure, not
        # the peak shape, so a one-off CrysPy calculation on the converged
        # model recovers the list. Must run AFTER fit_data_arrays(): the
        # swap-back changes the calculated pattern, so freeze copies first.
        if br.experiment.refln is None:
            arrays = {k: np.array(v) for k, v in arrays.items()}
            try:
                br.experiment.peak.type = "cwl-pseudo-voigt"
                br.experiment.calculator.type = "cryspy"
                br.project.analysis.calculate()
            except Exception as exc:  # peak lists stay empty; not fatal
                br.warnings.append(f"hkl harvest via CrysPy failed: {exc}")

        # Write reports
        _write_reports(br, arrays, output_dir)

        elapsed = time.time() - t0

        return _result_dict(
            success=True,
            method=method,
            output_dir=output_dir,
            build_result=br,
            arrays=arrays,
            rwp=rwp,
            r_exp=r_exp,
            gof=gof,
            elapsed=elapsed,
        )

    except Exception as exc:
        elapsed = time.time() - t0
        return _result_dict(
            success=False,
            method=method,
            output_dir=output_dir,
            elapsed=elapsed,
            error=str(exc),
            error_traceback=tb.format_exc(),
        )
