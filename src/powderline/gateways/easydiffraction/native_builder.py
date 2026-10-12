"""Native ``easydiffraction.rietveld`` recipe -> easydiffraction ``Project`` (re/06, Phase 3).

Consumes the **validated** :class:`~powderline.gateways.easydiffraction.schema.EasydiffractionRecipe`
(A39), never the recipe dict: every value handed to easydiffraction is the
model's exact value (A120, A121), in easydiffraction's native units (A4), so
there is no unit conversion here and no GSAS-II import anywhere in this module.

easydiffraction itself is imported lazily, inside the functions that build the
``Project`` (runtime layer only): the pure parts (space-group mapping, datablock
naming, window weighting, the CIF data text) run in the core-only environment
and are unit-tested there.

Rules implemented (decision log numbers in brackets):

- **Data (A158):** every data point is handed to easydiffraction; a point in the
  fit window (core :func:`~powderline.schema_core.fit_limits_on_data`, A149/A154)
  with a positive weight gets sigma = 1/sqrt(w), every other point
  sigma = :data:`SIGMA_WEIGHTLESS` (1e8), so reflection tails that cross the
  window are calculated but only the window is fitted. The data go in as a CIF
  loop at full precision, positional notation (EB-72, EB-89; A182).
- **Names:** a phase's datablock is its name lowercased (A112 guarantees
  uniqueness ignoring case; EB-45).
- **Space groups:** easydiffraction's short spaced symbol + ``coord_system_code``
  from :mod:`.space_group_support`, per calculator (A163, A174).
- **Atom order (A169 b):** anisotropic atoms are handed first (easydiffraction
  0.21.1 CrysPy refines a Uij wrongly when an isotropic atom precedes it,
  EB-86); reports stay keyed by label.
- **Background (A183):** the schema states a Chebyshev background per phase;
  easydiffraction has one per experiment, so at most one phase may state one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from powderline.exceptions import StructuredWarning
from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.schema import EasydiffractionPhase, EasydiffractionRecipe
from powderline.gateways.easydiffraction.space_group_support import map_space_group
from powderline.schema_core import fit_limits_on_data

#: sigma handed for a data point outside the fit window or with zero weight (A158).
SIGMA_WEIGHTLESS = 1e8
#: Name of the single experiment datablock (``data_powderline`` in the CIF).
EXPERIMENT_NAME = "powderline"
#: File the handed data loop is written to (a record of exactly what easydiffraction read).
DATA_CIF_NAME = "easydiffraction_data.cif"
#: Recipe ``peak_type`` -> easydiffraction ``peak.type`` (A162, EB-76).
PEAK_TYPES = {"pseudo-voigt": "cwl-pseudo-voigt", "tch": "cwl-thompson-cox-hastings"}
#: Core cell field -> easydiffraction ``cell`` attribute.
CELL_ATTRS = {"a": "length_a", "b": "length_b", "c": "length_c",
              "alpha": "angle_alpha", "beta": "angle_beta", "gamma": "angle_gamma"}
#: Core Uij component -> easydiffraction ``atom_site_aniso`` attribute.
UIJ_ATTRS = {"U11": "adp_11", "U22": "adp_22", "U33": "adp_33", "U12": "adp_12", "U13": "adp_13", "U23": "adp_23"}


@dataclass(frozen=True)
class BuildResult:
    """What :func:`build_project` produced."""

    project: Any
    #: easydiffraction parameter path -> ``{"path": recipe field path, "unit": ..., ...}`` (one per freed parameter).
    manifest: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)  # list[StructuredWarning]
    #: True for every data point in the fit window with a positive weight (the points core scores, A131).
    window_mask: Optional[np.ndarray] = None
    #: phase name -> easydiffraction datablock (structure) name.
    datablock_names: dict = field(default_factory=dict)


# --- pure helpers (core-only environment) ----------------------------------


def resolve_space_group(canonical: str, calculator: str) -> tuple[str, str]:
    """easydiffraction's ``(name_h_m, coord_system_code)`` for a canonical gemmi name, per calculator.

    Wraps :func:`~powderline.gateways.easydiffraction.space_group_support.map_space_group`;
    an unsupported setting raises :class:`EasyDiffractionTranslationError` naming the
    calculator and the remedy: the other calculator, or the ``:2`` origin for an
    origin-choice-1 setting refused for CrysPy (A174).
    """
    try:
        return map_space_group(canonical, calculator)
    except ValueError as exc:
        other = "crysfml" if calculator == "cryspy" else "cryspy"
        remedy = f"use calculator {other!r} if it supports this setting"
        if canonical.endswith(":1"):
            remedy = f"state the origin-choice-2 setting {canonical[:-2] + ':2'!r}, or {remedy}"
        raise EasyDiffractionTranslationError(
            f"space group {canonical!r} is not supported by the {calculator!r} calculator "
            f"(payload.refinement_controls.calculator): {remedy} (A163, A174)"
        ) from exc


def datablock_name(phase_name: str) -> str:
    """The easydiffraction structure (datablock) name of a phase: its name lowercased (A112, EB-45)."""
    return phase_name.lower()


def window_sigma(tth, weights, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """``(mask, sigma)`` for every data point (A158).

    ``mask`` is True where ``lo <= tth <= hi`` and ``w > 0``; there
    ``sigma = 1/sqrt(w)``, elsewhere :data:`SIGMA_WEIGHTLESS`.
    """
    tth = np.asarray(tth, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = (tth >= lo) & (tth <= hi) & (w > 0)
    sigma = np.full(tth.shape, SIGMA_WEIGHTLESS, dtype=float)
    sigma[mask] = 1.0 / np.sqrt(w[mask])
    return mask, sigma


def prepare_data(payload) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(tth, intensity, sigma, mask)`` of the full pattern, weighted per :func:`window_sigma`."""
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    tth = np.asarray(payload.xrd_data.tth, dtype=float)
    itth = np.asarray(payload.xrd_data.Itth, dtype=float)
    mask, sigma = window_sigma(tth, payload.xrd_data.Itth_weights, lo, hi)
    return tth, itth, sigma, mask


def cif_number(v: float) -> str:
    """A number for the CIF data loop: shortest digits that round-trip, positional notation.

    ``repr`` would write ``5e-05``, which easydiffraction's loop reader cannot parse (EB-89).
    """
    return np.format_float_positional(float(v), unique=True, trim="-")


def data_cif(tth, itth, sigma, name: str = EXPERIMENT_NAME) -> str:
    """The experiment CIF handed to ``ExperimentFactory.from_cif_str``: type + full-precision data loop (EB-72)."""
    head = [f"data_{name}",
            "_experiment_type.sample_form powder",
            '_experiment_type.beam_mode "constant wavelength"',
            "_experiment_type.radiation_probe xray",
            "_experiment_type.scattering_type bragg",
            "",
            "loop_",
            "_data.two_theta",
            "_data.intensity_meas",
            "_data.intensity_meas_su"]
    rows = [f"{cif_number(x)} {cif_number(y)} {cif_number(s)}" for x, y, s in zip(tth, itth, sigma)]
    return "\n".join(head + rows) + "\n"


def atom_order(phase: EasydiffractionPhase) -> list[tuple[str, Any]]:
    """``(label, atom)`` pairs in the order handed to easydiffraction: anisotropic atoms first (A169 b, EB-86)."""
    return sorted(phase.atoms.items(), key=lambda item: item[1].Uaniso is None)


def background_phase(payload) -> Optional[str]:
    """The name of the one phase stating a background (the experiment's), or ``None`` (A183).

    Raises :class:`EasyDiffractionTranslationError` when more than one phase states one:
    easydiffraction has a single background per experiment.
    """
    stated = [name for name, phase in payload.phases.items() if phase.background is not None]
    if len(stated) > 1:
        raise EasyDiffractionTranslationError(
            "easydiffraction has one background per experiment, but phases "
            f"{', '.join(repr(n) for n in stated)} each state one: state it in one phase only "
            "(payload.phases.<name>.background)"
        )
    return stated[0] if stated else None


# --- runtime layer (imports easydiffraction) -------------------------------


def _import_easydiffraction():
    """The easydiffraction names the builder uses; raises ``ImportError`` with the install hint."""
    try:
        from easydiffraction import ExperimentFactory, Project, StructureFactory
        from easydiffraction.utils.logging import Logger
    except ImportError as exc:
        raise ImportError(
            "easydiffraction is not installed. Install PowderLine's optional engine "
            "environment: `pixi install -e easydiff` and run via `pixi run -e easydiff ...`"
        ) from exc
    return ExperimentFactory, Project, StructureFactory, Logger


def _set(obj: Any, attr: str, value: Any) -> None:
    """``setattr`` that refuses an attribute easydiffraction does not have (a typo would otherwise be silent)."""
    if not hasattr(obj, attr):
        raise AttributeError(
            f"easydiffraction {type(obj).__name__} has no attribute {attr!r} "
            "(PowderLine's attribute map is out of date for this easydiffraction version)"
        )
    setattr(obj, attr, value)


def _build_structure(StructureFactory, name: str, phase: EasydiffractionPhase, calculator: str):
    """One easydiffraction structure from a validated phase: space group, cell and atoms, values only."""
    short_name, code = resolve_space_group(phase.space_group, calculator)
    structure = StructureFactory.from_scratch(name=datablock_name(name))
    _set(structure.space_group, "name_h_m", short_name)
    if code:
        _set(structure.space_group, "coord_system_code", code)
    for member, attr in CELL_ATTRS.items():
        _set(structure.cell, attr, getattr(phase.unit_cell, member).value)
    for label, atom in atom_order(phase):
        structure.atom_sites.create(id=label, type_symbol=atom.element,
                                    fract_x=atom.x.value, fract_y=atom.y.value, fract_z=atom.z.value,
                                    occupancy=atom.occupancy.value)
        site = structure.atom_sites[label]
        if atom.ADP == "Uiso":
            _set(site, "adp_type", "Uiso")
            _set(site, "adp_iso", atom.Uiso.value)
        else:
            _set(site, "adp_type", "Uani")
            aniso = structure.atom_site_aniso[label]
            for component, attr in UIJ_ATTRS.items():
                _set(aniso, attr, getattr(atom.Uaniso, component).value)
    return structure


def _build_experiment(ExperimentFactory, payload, cif: str, names: dict):
    """The experiment: data, calculator and peak type, instrument values, linked phases, background."""
    controls = payload.refinement_controls
    expt = ExperimentFactory.from_cif_str(cif)
    # The calculator must be chosen before the peak type (the setter validates against the
    # active calculator), and both before any peak parameter (switching the type resets them).
    _set(expt.calculator, "type", controls.calculator)
    _set(expt.peak, "type", PEAK_TYPES[controls.peak_type])
    if controls.calculator == "cryspy":  # the reflection cutoff is a CrysPy parameter (A162)
        _set(expt.peak, "cutoff_fwhm", controls.cutoff_fwhm)

    inst = payload.instrument
    _set(expt.instrument, "setup_wavelength", inst.radiation.wavelength.value)
    if inst.radiation.ka2_wavelength is not None:
        _set(expt.instrument, "setup_wavelength_2", inst.radiation.ka2_wavelength)
        _set(expt.instrument, "setup_wavelength_2_to_1_ratio", inst.radiation.ka2_ratio)
    corr = inst.corrections
    _set(expt.instrument, "calib_twotheta_offset", corr.zero.value)
    _set(expt.instrument, "setup_polarization_coefficient", corr.polarization.value)
    if corr.polarization_monochromator_angle is not None:
        _set(expt.instrument, "setup_monochromator_twotheta", corr.polarization_monochromator_angle.value)
    if corr.displacement is not None:
        _set(expt.instrument, "calib_sample_displacement", corr.displacement.value)
    if corr.transparency is not None:
        _set(expt.instrument, "calib_sample_transparency", corr.transparency.value)
    broad = inst.broadening
    for key in ("u", "v", "w"):
        _set(expt.peak, f"broad_gauss_{key}", getattr(broad, key).value)
    for key in ("x", "y"):
        _set(expt.peak, f"broad_lorentz_{key}", getattr(broad, key).value)

    for name, phase in payload.phases.items():
        expt.linked_structures.create(structure_id=names[name], scale=phase.scale.value)

    bg_phase = background_phase(payload)
    if bg_phase is not None:
        _set(expt.background, "type", "chebyshev")
        for k, coef in enumerate(payload.phases[bg_phase].background.coefficients):
            expt.background.create(id=str(k), order=k, coef=coef)
    return expt


def build_project(recipe: EasydiffractionRecipe, workdir) -> BuildResult:
    """Build the easydiffraction ``Project`` of a validated native recipe.

    Writes the handed data loop to ``workdir / DATA_CIF_NAME``. Values only here;
    the site check and the refinement flags are applied by the later steps.

    Raises ``ImportError`` without easydiffraction and
    :class:`EasyDiffractionTranslationError` for an unsupported setting.
    """
    ExperimentFactory, Project, StructureFactory, Logger = _import_easydiffraction()
    Logger.configure(reaction=Logger.Reaction.RAISE)  # easydiffraction's errors raise instead of logging (EB-48)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    payload = recipe.payload
    calculator = payload.refinement_controls.calculator

    tth, itth, sigma, mask = prepare_data(payload)
    cif = data_cif(tth, itth, sigma)
    (workdir / DATA_CIF_NAME).write_text(cif, encoding="utf-8", newline="\n")

    names = {name: datablock_name(name) for name in payload.phases}
    structures = [_build_structure(StructureFactory, name, phase, calculator)
                  for name, phase in payload.phases.items()]
    expt = _build_experiment(ExperimentFactory, payload, cif, names)

    project = Project()
    for structure in structures:
        project.structures.add(structure)
    project.experiments.add(expt)

    return BuildResult(project=project, manifest={}, warnings=[], window_mask=mask, datablock_names=names)
