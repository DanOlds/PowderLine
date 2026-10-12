"""Recipe → easydiffraction Project + free-parameter manifest.

Translates PowderLine GSAS-II recipe into an easydiffraction Project ready for fitting.
Converts units (cdeg² → deg², cdeg → deg), sets initial values from Iparm1, and builds
a manifest of all free parameters with their scaling factors for round-trip conversion.

API surface consumed by the easydiff engine (Task 4).
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from powderline.gateways.easydiffraction.conversions import (
    CDEG_TO_DEG,
    GAUSS_CDEG2_TO_DEG2,
    crop_and_sigma,
    datablock_slug,
    gauss_broadening_to_ed,
    lorentz_broadening_to_ed,
    zero_to_ed,
)
from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.policy import check_unsupported, param_flag, param_value
from powderline.exceptions import SymmetryError
from powderline.symmetry import cell_constraints


@dataclass
class ManifestEntry:
    parameter: object
    parameter_name: str
    descriptive_name: str
    phase_name: str = ""
    phase_idx: object = ""
    atom_name: str = ""
    atom_idx: object = ""
    category: str = ""
    scale_to_recipe: float = 1.0


@dataclass
class BuildResult:
    project: object
    experiment: object
    phase_slugs: dict
    manifest: list
    warnings: list
    tth: np.ndarray
    itth: np.ndarray
    weights: np.ndarray
    mask: np.ndarray
    wavelength: float


def build_project(recipe: dict, workdir) -> BuildResult:
    """Build easydiffraction Project from recipe.

    Args:
        recipe: GSAS-II recipe dict
        workdir: Path-like for easydiff_data.xye output

    Returns:
        BuildResult with fit-ready project + manifest

    Raises:
        ImportError: if easydiffraction not installed
        EasyDiffractionTranslationError: if recipe has unsupported features
    """
    try:
        from easydiffraction import StructureFactory, ExperimentFactory, Project
    except ImportError as exc:
        raise ImportError(
            "easydiffraction is not installed. Install PowderLine's optional engine "
            "environment: `pixi install -e easydiff` and run via `pixi run -e easydiff ...`"
        ) from exc

    workdir = Path(workdir)

    # 1. Check for unsupported features
    warnings = check_unsupported(recipe)

    # 2. Prepare data
    payload = recipe["payload"]
    xrd = payload["xrd_data"]
    tth = np.array(xrd["tth"])
    itth = np.array(xrd["Itth"])
    weights = np.array(xrd["Itth_weights"])
    fit_range = payload.get("fit_range")

    mask, sigma = crop_and_sigma(tth, itth, weights, fit_range)

    # Write masked data (sigma is already masked)
    masked_tth = tth[mask]
    masked_itth = itth[mask]
    data_path = workdir / "easydiff_data.xye"
    np.savetxt(
        data_path,
        np.column_stack([masked_tth, masked_itth, sigma]),
        fmt="%.6f"
    )

    # 3. Build structures per phase
    structures = []
    phase_slugs = {}
    used_slugs = set()

    for phase_name, phase_data in payload["phases"].items():
        slug = datablock_slug(phase_name)
        # Uniquify on collision with incrementing suffix
        original_slug = slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{original_slug}_{suffix}"
            suffix += 1
        used_slugs.add(slug)
        phase_slugs[phase_name] = slug

        model = StructureFactory.from_scratch(name=slug)

        # Space group
        struct = phase_data["structure"]
        sg_name = struct["space_group"]
        try:
            model.space_group.name_h_m = sg_name
        except Exception as exc:
            raise EasyDiffractionTranslationError(
                f"easydiffraction rejected space group {sg_name!r}: {exc}"
            ) from exc

        # Unit cell - use initial values from structure
        uc = struct["unit_cell"]
        model.cell.length_a = uc["a"]
        model.cell.length_b = uc["b"]
        model.cell.length_c = uc["c"]
        model.cell.angle_alpha = uc["alpha"]
        model.cell.angle_beta = uc["beta"]
        model.cell.angle_gamma = uc["gamma"]

        # Atoms
        for label, atom_data in struct["atoms"].items():
            model.atom_sites.create(
                id=label,
                type_symbol=atom_data["element"],
                fract_x=atom_data["x"],
                fract_y=atom_data["y"],
                fract_z=atom_data["z"],
                occupancy=atom_data["occupancy"]
            )
            # Set Uiso explicitly (default is Biso)
            site = model.atom_sites[label]
            site.adp_type = "Uiso"
            uiso_val = atom_data.get("Uiso")
            if uiso_val is not None:
                site.adp_iso = uiso_val
            else:
                warnings.append(
                    f"atom {label}: no Uiso value; easydiffraction default ADP used"
                )

        structures.append(model)

    # 4. Build experiment
    expt = ExperimentFactory.from_data_path(
        name="powderline",
        data_path=str(data_path),
        sample_form="powder",
        beam_mode="constant wavelength",
        radiation_probe="xray",
        scattering_type="bragg"
    )

    # 5. Instrument parameters
    inst = payload["instrument"]
    iparm = inst["initialization"][0]  # Iparm1 is first element of initialization
    inst_pz = inst.get("parameterization") or {}

    # Profile selection: nonzero SH/L needs the Thompson-Cox-Hastings profile
    # (FCJ axial-divergence correction), available only from the CrysFML
    # calculator. Two ordering constraints: the calculator must be switched
    # before the profile (the setter validates against the active calculator),
    # and the whole selection must precede every peak-parameter assignment
    # below, because switching peak.type REPLACES the peak category and
    # resets its values. FCJ parameters stay FIXED at the SH/L seed: freeing
    # them is degenerate at typical values and destroys ESD estimation.
    shl_entry = iparm.get("SH/L")
    shl = (
        shl_entry[1]
        if isinstance(shl_entry, (list, tuple)) and len(shl_entry) >= 2
        else 0.0
    )
    if shl:
        expt.calculator.type = "crysfml"
        expt.peak.type = "cwl-thompson-cox-hastings"
        expt.peak.asym_fcj_1 = shl
        expt.peak.asym_fcj_2 = shl

    # Wavelength - value override takes precedence over Iparm
    wavelength_spec = inst_pz.get("wavelength")
    if wavelength_spec is not None and wavelength_spec[0] is not None:
        wavelength = wavelength_spec[0]
    else:
        wavelength = iparm["Lam"][1]
    expt.instrument.setup_wavelength = wavelength

    # Zero (4-tuple override takes precedence)
    corrections = inst_pz.get("corrections") or {}
    zero_spec = corrections.get("zero_shift")
    if zero_spec is not None and zero_spec[0] is not None:
        zero_val = zero_to_ed(zero_spec[0])
    else:
        zero_val = zero_to_ed(iparm["Zero"][1])
    expt.instrument.calib_twotheta_offset = zero_val

    # Broadening - Gauss
    bz = inst_pz.get("broadening") or {}
    for key in ["U", "V", "W"]:
        spec = bz.get(key)
        if spec is not None and spec[0] is not None:
            val = gauss_broadening_to_ed(spec[0])
        else:
            val = gauss_broadening_to_ed(iparm[key][1])
        setattr(expt.peak, f"broad_gauss_{key.lower()}", val)

    # Broadening - Lorentz
    for key in ["X", "Y"]:
        spec = bz.get(key)
        if spec is not None and spec[0] is not None:
            val = lorentz_broadening_to_ed(spec[0])
        else:
            val = lorentz_broadening_to_ed(iparm[key][1])
        setattr(expt.peak, f"broad_lorentz_{key.lower()}", val)

    # Polarization (may not exist)
    try:
        polariz_spec = inst_pz.get("polarization")
        if polariz_spec is not None and polariz_spec[0] is not None:
            expt.instrument.setup_polarization_coefficient = polariz_spec[0]
        else:
            expt.instrument.setup_polarization_coefficient = iparm["Polariz."][1]
    except (KeyError, AttributeError) as e:
        warnings.append(f"Could not set polarization_coefficient: {e}")

    # 6. Linked structures
    for i, (phase_name, slug) in enumerate(phase_slugs.items()):
        phase_pz = payload["phases"][phase_name].get("parameterization") or {}
        scale_spec = phase_pz.get("scale")
        scale_val = param_value(scale_spec, default=1.0)
        expt.linked_structures.create(structure_id=slug, scale=scale_val)

    # 7. Background
    background = payload.get("background") or {}
    bg = background.get("chebyshev")
    if bg is not None:
        expt.background.type = "chebyshev"
        for k, c in enumerate(bg["coefficients"]):
            expt.background.create(id=str(k), order=k, coef=c)
    else:
        warnings.append("no background model in recipe; none applied")

    # 8. Set free parameters (before building project)
    # Track which ones to add to manifest (check after project build for unit_cell)
    pending_params = []

    # Wavelength
    wavelength_spec = inst_pz.get("wavelength")
    if wavelength_spec is not None and param_flag(wavelength_spec):
        param = expt.instrument.setup_wavelength
        param.free = True
        if wavelength_spec[2] is not None:
            param.fit_min = wavelength_spec[2]
        if wavelength_spec[3] is not None:
            param.fit_max = wavelength_spec[3]
        pending_params.append((
            param, ":0:Lam", "wavelength",
            "", "", "instrument", 1.0
        ))

    # Broadening U/V/W
    for key in ["U", "V", "W"]:
        spec = bz.get(key)
        if param_flag(spec):
            param = getattr(expt.peak, f"broad_gauss_{key.lower()}")
            param.free = True
            # Set bounds if present
            if spec[2] is not None:
                param.fit_min = gauss_broadening_to_ed(spec[2])
            if spec[3] is not None:
                param.fit_max = gauss_broadening_to_ed(spec[3])
            pending_params.append((
                param, f":0:{key}", f"instrument_broadening_{key}",
                "", "", "instrument_broadening", 1.0 / GAUSS_CDEG2_TO_DEG2
            ))

    # Broadening X/Y
    for key in ["X", "Y"]:
        spec = bz.get(key)
        if param_flag(spec):
            param = getattr(expt.peak, f"broad_lorentz_{key.lower()}")
            param.free = True
            if spec[2] is not None:
                param.fit_min = lorentz_broadening_to_ed(spec[2])
            if spec[3] is not None:
                param.fit_max = lorentz_broadening_to_ed(spec[3])
            pending_params.append((
                param, f":0:{key}", f"instrument_broadening_{key}",
                "", "", "instrument_broadening", 1.0 / CDEG_TO_DEG
            ))

    # Zero shift
    zero_spec = corrections.get("zero_shift")
    if zero_spec is not None and param_flag(zero_spec):
        param = expt.instrument.calib_twotheta_offset
        param.free = True
        if zero_spec[2] is not None:
            param.fit_min = zero_to_ed(zero_spec[2])
        if zero_spec[3] is not None:
            param.fit_max = zero_to_ed(zero_spec[3])
        pending_params.append((
            param, ":0:Zero", "zero_shift",
            "", "", "corrections", 1.0 / CDEG_TO_DEG
        ))

    # Scale per phase
    for i, (phase_name, slug) in enumerate(phase_slugs.items()):
        phase_pz = payload["phases"][phase_name].get("parameterization") or {}
        scale_spec = phase_pz.get("scale")
        if param_flag(scale_spec):
            param = expt.linked_structures[slug].scale
            param.free = True
            if scale_spec[2] is not None:
                param.fit_min = scale_spec[2]
            if scale_spec[3] is not None:
                param.fit_max = scale_spec[3]
            pending_params.append((
                param, f"{i}:0:Scale", f"phase_{i}_scale_factor",
                phase_name, i, "scale", 1.0
            ))

    # Background coefficients
    if bg is not None and bg.get("refine_flag"):
        for k in range(len(bg["coefficients"])):
            param = expt.background[str(k)].coef
            param.free = True
            pending_params.append((
                param, f":0:Back;{k}", f"background_coefficient_{k}",
                "", "", "background", 1.0
            ))

    # Unit cell axes per phase - use gemmi-backed symmetry constraints
    cell_params_to_add = []
    for i, (phase_name, slug) in enumerate(phase_slugs.items()):
        phase_data = payload["phases"][phase_name]
        phase_pz = phase_data.get("parameterization") or {}
        cell_pz = phase_pz.get("unit_cell") or {}
        model = structures[i]
        sg_name = phase_data["structure"]["space_group"]

        # Get symmetry rules
        try:
            rules = cell_constraints(sg_name)
        except SymmetryError as e:
            raise EasyDiffractionTranslationError(str(e)) from e

        length_map = {
            "a": "length_a",
            "b": "length_b",
            "c": "length_c"
        }
        angle_map = {
            "alpha": "angle_alpha",
            "beta": "angle_beta",
            "gamma": "angle_gamma"
        }

        # Handle length groups: free only the first axis in each group
        for length_group in rules.length_groups:
            # Check if any axis in this group is flagged (schema 0.26 allows a
            # subset of unit_cell parameters -- absent means fixed)
            flagged_in_group = [ax for ax in length_group if param_flag(cell_pz.get(ax))]
            if not flagged_in_group:
                continue

            # Free only the representative (first in group)
            rep_axis = length_group[0]
            param = getattr(model.cell, length_map[rep_axis])
            param.free = True

            # Use bounds and value override from the first flagged axis
            # (present by construction: it carried the flag)
            bounds_spec = cell_pz.get(flagged_in_group[0])
            if bounds_spec[2] is not None:
                param.fit_min = bounds_spec[2]
            if bounds_spec[3] is not None:
                param.fit_max = bounds_spec[3]
            cell_params_to_add.append((
                param, f"{i}::{rep_axis}", f"phase_{i}_cell_{rep_axis}",
                phase_name, i
            ))

            # Warn for other flagged axes in the group
            for ax in flagged_in_group:
                if ax != rep_axis:
                    warnings.append(
                        f"cell axis '{ax}' is tied to '{rep_axis}' by symmetry; "
                        f"refine flag folded into '{rep_axis}'"
                    )

        # Handle fixed angles: warn if flagged
        for angle in rules.fixed_angles:
            if param_flag(cell_pz.get(angle)):
                warnings.append(
                    f"cell angle '{angle}' is fixed by symmetry; refine flag ignored"
                )

        # Handle free angles: free normally
        for angle in rules.free_angles:
            spec = cell_pz.get(angle)
            if param_flag(spec):
                param = getattr(model.cell, angle_map[angle])
                param.free = True
                if spec[2] is not None:
                    param.fit_min = spec[2]
                if spec[3] is not None:
                    param.fit_max = spec[3]
                cell_params_to_add.append((
                    param, f"{i}::{angle}", f"phase_{i}_cell_{angle}",
                    phase_name, i
                ))

    # 9. Build project
    project = Project()
    for model in structures:
        project.structures.add(model)
    project.experiments.add(expt)

    # Build manifest
    manifest = []
    for param, pname, dname, phase, pidx, cat, scale in pending_params:
        manifest.append(ManifestEntry(
            parameter=param,
            parameter_name=pname,
            descriptive_name=dname,
            phase_name=phase,
            phase_idx=pidx,
            category=cat,
            scale_to_recipe=scale
        ))

    # Add unit cell parameters
    for param, pname, dname, phase, pidx in cell_params_to_add:
        manifest.append(ManifestEntry(
            parameter=param,
            parameter_name=pname,
            descriptive_name=dname,
            phase_name=phase,
            phase_idx=pidx,
            category="unit_cell",
            scale_to_recipe=1.0
        ))

    return BuildResult(
        project=project,
        experiment=expt,
        phase_slugs=phase_slugs,
        manifest=manifest,
        warnings=warnings,
        tth=tth,
        itth=itth,
        weights=weights,
        mask=mask,
        wavelength=wavelength
    )
