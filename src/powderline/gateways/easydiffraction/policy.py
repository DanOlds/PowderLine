"""Unsupported-feature policy for the easydiffraction engine.

Honesty rule (see the design spec): a FIXED unmappable value is dropped with a
recorded warning; a value the user asked to REFINE but that cannot be
represented raises EasyDiffractionTranslationError. This module must stay
importable without easydiffraction installed (validate_only relies on it).
"""

from __future__ import annotations

from .errors import EasyDiffractionTranslationError


def param_flag(spec) -> bool:
    """Refine flag of a [value, refine_flag, min, max] 4-tuple (or None)."""
    try:
        return bool(spec[1])
    except (TypeError, IndexError):
        return False


def param_value(spec, default=None):
    """Value of a 4-tuple, falling back to default when null/absent."""
    try:
        return default if spec[0] is None else spec[0]
    except (TypeError, IndexError):
        return default


def _iparm_current(iparm1: dict, key: str, default=0.0):
    entry = iparm1.get(key)
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return entry[1]
    return default


def check_unsupported(recipe: dict) -> list[str]:
    from powderline.exceptions import SymmetryError
    from powderline.symmetry import cell_constraints, resolve_space_group

    warnings: list[str] = []
    schema_name = recipe.get("schema_name")
    if schema_name != "GSASII_Rietveld":
        raise EasyDiffractionTranslationError(
            f"schema_name {schema_name!r} is not supported by the easydiffraction "
            "engine; only GSASII_Rietveld recipes are translatable (v1)"
        )
    payload = recipe.get("payload", {}) or {}
    inst = payload.get("instrument", {}) or {}
    init = inst.get("initialization") or [{}, {}]
    iparm1 = init[0] if init else {}
    for key in ("Lam1", "Lam2"):
        if key in iparm1:
            raise EasyDiffractionTranslationError(
                f"two-wavelength instrument parameter {key!r} found; the "
                "easydiffraction engine supports single-wavelength (Lam) data only"
            )

    param = inst.get("parameterization") or {}
    broadening = param.get("broadening") or {}
    if param_flag(broadening.get("Z")):
        raise EasyDiffractionTranslationError(
            "instrument broadening term Z is flagged for refinement but has no "
            "easydiffraction equivalent (no Gaussian Z term)"
        )
    if param_flag(param.get("polarization")):
        raise EasyDiffractionTranslationError(
            "polarization is flagged for refinement but is non-refinable in easydiffraction"
        )
    corrections = param.get("corrections") or {}
    if param_flag(corrections.get("axial_divergence")):
        raise EasyDiffractionTranslationError(
            "axial_divergence is flagged for refinement; the easydiffraction engine "
            "models SH/L as a FIXED Thompson-Cox-Hastings/FCJ asymmetry but does "
            "not support refining it (the two FCJ parameters are degenerate at "
            "typical values and destroy ESD estimation)"
        )

    z_val = _iparm_current(iparm1, "Z", 0.0)
    if z_val:
        warnings.append(
            f"instrument Z={z_val} ignored (no Gaussian Z term in easydiffraction)"
        )
    shl = _iparm_current(iparm1, "SH/L", 0.0)
    if shl:
        warnings.append(
            f"SH/L={shl} modeled via the Thompson-Cox-Hastings profile with "
            "fixed FCJ asymmetry (CrysFML calculator); hkl peak lists are "
            "harvested with a post-fit CrysPy calculation"
        )

    background = payload.get("background") or {}
    bkg_peaks = background.get("single_peaks")
    if bkg_peaks:
        specs = list(bkg_peaks.get("positions") or []) + list(bkg_peaks.get("intensities") or [])
        if any(param_flag(s) for s in specs):
            raise EasyDiffractionTranslationError(
                "background single_peaks are flagged for refinement; easydiffraction "
                "has no background-peaks concept"
            )
        warnings.append("background peaks ignored (no background-peaks concept)")

    for phase_name, phase in (payload.get("phases") or {}).items():
        # Validate space group
        structure = phase.get("structure", {}) or {}
        sg_name = structure.get("space_group")
        if sg_name:
            try:
                if resolve_space_group(sg_name).ext == "R":
                    raise EasyDiffractionTranslationError(
                        f"rhombohedral ':R' setting not supported for {sg_name!r}; "
                        "use the hexagonal (:H) setting"
                    )
                cell_constraints(sg_name)
            except SymmetryError as exc:
                raise EasyDiffractionTranslationError(str(exc)) from exc

        pz = phase.get("parameterization") or {}
        # Check Uaniso
        for atom_name, atom in (pz.get("atoms") or {}).items():
            uaniso = atom.get("Uaniso")
            if isinstance(uaniso, dict):
                # Schema keys are uppercase (U11..U23); match case-insensitively
                # so lowercase variants cannot slip past the honesty rule.
                comps = {str(k).upper(): v for k, v in uaniso.items()}
                keys = ["U11", "U22", "U33", "U12", "U13", "U23"]
                flagged_components = [k for k in keys if param_flag(comps.get(k))]
                if flagged_components:
                    raise EasyDiffractionTranslationError(
                        f"anisotropic ADPs ({phase_name}/{atom_name}) flagged for refinement; "
                        "easydiffraction supports only isotropic ADPs (Uiso)"
                    )
                # Check for fixed non-null values
                has_values = any(
                    isinstance(comps.get(k), (list, tuple)) and len(comps[k]) >= 1 and comps[k][0] is not None
                    for k in keys
                )
                if has_values:
                    warnings.append(
                        f"atom {phase_name}/{atom_name}: anisotropic ADPs not mapped (Uaniso ignored)"
                    )

            for key in ("x", "y", "z", "occupancy", "Uiso"):
                if param_flag(atom.get(key)):
                    raise EasyDiffractionTranslationError(
                        f"atom-level refinement ({phase_name}/{atom_name}/{key}) is "
                        "not supported by the easydiffraction engine in v1"
                    )
        pb = pz.get("peak_broadening") or {}

        def _has_content(v):
            # Only actual values count: 4-tuples with a value, or bare numbers.
            # Strings like {"model": "isotropic"} with null magnitudes do not.
            if isinstance(v, (list, tuple)):
                return bool(v) and v[0] is not None
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        flat = [v for group in pb.values() if isinstance(group, dict)
                for v in group.values()]
        flat += [v for v in pb.values() if not isinstance(v, dict)]
        if any(_has_content(v) for v in flat):
            warnings.append(
                f"phase {phase_name!r} peak_broadening (size/strain) not mapped"
            )

    warnings.append("refinement_cycles not used; lmfit runs to convergence")
    return warnings
