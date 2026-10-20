"""GSAS-II parameter names -> recipe paths (re/20b, KI-04; non-frozen).

The frozen extractors report what GSAS-II varied by GSAS-II's own names
(``:0:Back;3``, ``0::dAx:4``, ``1:0:Mustrain;i`` ...). This module reads those
rows -- plus the unit-cell reports and the single-peak table -- back onto recipe
paths in recipe units, which is all :mod:`powderline.roundtrip` needs to build the
output recipe. Nothing here touches the project dictionary.

Facts the map rests on (GSAS-II 5.7.9, read-only):

* ``p::dAx:i`` / ``dAy`` / ``dAz`` are the varied **shifts** of atom ``i`` of phase
  ``p``; GSAS-II adds them to the coordinates once at the end
  (``GSASIIstrMath.ApplyXYZshifts``: ``Ax += dAx``), so the refined coordinate is
  the recipe's value plus the reported shift, with the shift's ESD.
* ``p::Afrac:i``, ``p::AUiso:i``, ``p::AU11:i`` ... ``AU23`` are absolute values in
  recipe units (occupancy; U in A^2).
* ``p::A0`` ... ``A5`` are the reciprocal-metric-tensor terms GSAS-II refines for the
  cell; the cell itself comes from the unit-cell report (``cell_a`` ... ``cell_gamma``
  with ESDs), which lists every member of a tie group consistently.
* ``p:h:Scale`` is the phase fraction; ``p:h:Size;i|mx`` and ``p:h:Mustrain;i|mx``
  the isotropic size (um) / microstrain and their Lorentzian fractions (``LG_eta``).
* ``:h:Back;N`` the Chebyshev coefficients; ``:h:BkPkpos|int|sig|gam;N`` the
  background peaks; ``:h:U`` ... ``Z``, ``Zero``, ``Polariz.``, ``SH/L``, ``Lam`` the
  instrument -- all in the units the recipe states (centideg, A).
* Single peak fitting reports no covariance rows; the peak table
  (``position_2theta``, ``intensity``, ``sigma_squared``, ``gamma``) maps by index onto
  ``payload.single_peaks``.

A name this module does not know raises :class:`UnmappedParameter`: an unmapped
refined value must never be dropped silently (the output recipe would quietly be
wrong).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from powderline.schema_core import refined_flags

_INSTRUMENT = {
    "U": "broadening.U", "V": "broadening.V", "W": "broadening.W",
    "X": "broadening.X", "Y": "broadening.Y", "Z": "broadening.Z",
    "Zero": "corrections.zero_shift", "Polariz.": "corrections.polarization",
    "SH/L": "corrections.axial_divergence", "Lam": "radiation.wavelength",
}
_ATOM = {
    "Afrac": "occupancy", "AUiso": "Uiso",
    "AU11": "Uaniso.U11", "AU22": "Uaniso.U22", "AU33": "Uaniso.U33",
    "AU12": "Uaniso.U12", "AU13": "Uaniso.U13", "AU23": "Uaniso.U23",
}
_SHIFT = {"dAx": "x", "dAy": "y", "dAz": "z"}
_BROADENING = {
    "Size;i": "size_broadening.isotropic_size", "Size;mx": "size_broadening.LG_eta",
    "Mustrain;i": "strain_broadening.isotropic_strain", "Mustrain;mx": "strain_broadening.LG_eta",
}
_PEAK = {"pos": "positions", "int": "intensities", "sig": "pv_gaussian_sigma_sq", "gam": "pv_lorentzian_gamma"}
_CELL = ("a", "b", "c", "alpha", "beta", "gamma")
_SPF_COLUMNS = {"position_2theta": "positions", "intensity": "intensities",
                "sigma_squared": "pv_gaussian_sigma_sq", "gamma": "pv_lorentzian_gamma"}


class UnmappedParameter(ValueError):
    """A refined GSAS-II parameter this module cannot place in the recipe."""


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _records(table) -> list:
    if table is None:
        return []
    if hasattr(table, "to_dict"):
        return table.to_dict("records")
    return list(table)


def _phase_name(model, row: Mapping, phase_index: str) -> str:
    name = row.get("phase_name")
    if name:
        return str(name)
    names = list(model.payload.phases)
    return names[int(phase_index)]


def _atom_label(model, phase: str, row: Mapping, atom_index: str) -> str:
    label = row.get("atom_name")
    if label:
        return str(label)
    return list(model.payload.phases[phase].atoms)[int(atom_index)]


def refined_by_path(model, result: Mapping[str, Any]) -> tuple:
    """``(refined, not_varied, at_limit)`` for :func:`powderline.roundtrip.build_output`.

    ``refined`` maps recipe paths to ``(value, esd)`` in recipe units for everything GSAS-II varied;
    ``not_varied`` lists the flagged paths GSAS-II reported nothing for (a flagged parameter that does
    not affect the pattern, or one GSAS-II dropped); ``at_limit`` is empty: gsasii 1.0.0 has no bounds.
    """
    refined: dict = {}
    payload = model.payload
    phases = getattr(payload, "phases", None) or {}

    for row in _records(result.get("refined_parameters")):
        name = str(row["parameter_name"])
        value, esd = _float(row.get("value")), _float(row.get("esd"))
        if value is None:
            continue
        parts = name.split(":")
        if len(parts) < 3:
            raise UnmappedParameter(f"GSAS-II parameter {name!r} is not of the form p:h:key[:atom]")
        phase_idx, hist_idx, key = parts[0], parts[1], parts[2]
        path = None
        if phase_idx == "":                                   # histogram parameter
            base, _, index = key.partition(";")
            if base == "Back":
                path = f"payload.background.chebyshev.coefficients.{int(index)}"
            elif base.startswith("BkPk") and base[4:] in _PEAK:
                path = f"payload.background.single_peaks.{_PEAK[base[4:]]}.{int(index)}"
            elif key in _INSTRUMENT:
                path = f"payload.instrument.{_INSTRUMENT[key]}"
        elif hist_idx == "":                                  # phase parameter: atoms and the metric tensor
            if key in ("A0", "A1", "A2", "A3", "A4", "A5"):
                continue                                      # the cell comes from the unit-cell report
            phase = _phase_name(model, row, phase_idx)
            if len(parts) >= 4 and key in _SHIFT:
                label = _atom_label(model, phase, row, parts[3])
                atom = phases[phase].atoms[label]
                start = getattr(atom, _SHIFT[key]).value
                value = start + value                         # a shift (ApplyXYZshifts), not a coordinate
                path = f"payload.phases.{phase}.atoms.{label}.{_SHIFT[key]}"
            elif len(parts) >= 4 and key in _ATOM:
                label = _atom_label(model, phase, row, parts[3])
                path = f"payload.phases.{phase}.atoms.{label}.{_ATOM[key]}"
        else:                                                 # histogram-phase parameter
            phase = _phase_name(model, row, phase_idx)
            if key == "Scale":
                path = f"payload.phases.{phase}.scale"
            elif key in _BROADENING:
                path = f"payload.phases.{phase}.peak_broadening.{_BROADENING[key]}"
        if path is None:
            raise UnmappedParameter(f"GSAS-II parameter {name!r} has no recipe path; the output recipe would be "
                                    "wrong without it (teach gateways/gsasii/paths.py the name)")
        refined[path] = (value, esd)

    # the cell: every member from the unit-cell report, when the recipe refines the cell at all
    for phase, cells in (result.get("unit_cell_data") or {}).items():
        if phase not in phases:
            continue
        cell = phases[phase].unit_cell
        if not any(getattr(cell, k).refine_flag for k in _CELL):
            continue
        reported = {str(r["parameter"]): r for r in _records(cells)}
        for k in _CELL:
            r = reported.get(f"cell_{k}")
            if r is not None and _float(r.get("value")) is not None:
                refined[f"payload.phases.{phase}.unit_cell.{k}"] = (_float(r["value"]), _float(r.get("esd")))

    # single peak fitting: the peak table, by index, no ESDs
    peaks = getattr(payload, "single_peaks", None)
    if peaks is not None:
        for i, r in enumerate(_records(result.get("spf_peaks"))):
            for column, field in _SPF_COLUMNS.items():
                v = _float(r.get(column))
                if v is not None and i < len(getattr(peaks, field)):
                    refined[f"payload.single_peaks.{field}.{i}"] = (v, None)

    flagged = {".".join(str(x) for x in loc) for loc in refined_flags(model)}
    not_varied = {p for p in flagged if p not in refined and not p.endswith(".refine_flag")}
    return refined, not_varied, set()
