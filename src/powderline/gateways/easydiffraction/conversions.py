"""GSAS-II <-> easydiffraction unit/convention conversions.

Every numeric convention decision for the easydiffraction engine lives here:
- GSAS-II CW Gaussian U/V/W are variances (sigma^2) in centidegrees^2;
  easydiffraction ``broad_gauss_u/v/w`` are Caglioti FWHM^2 terms in deg^2:
  FWHM^2 = 8 ln2 * sigma^2 and 1 centideg^2 = 1e-4 deg^2.
- GSAS-II Lorentzian X/Y and Zero are centidegrees; easydiffraction uses degrees.
- easydiffraction reports Rwp/Rexp as fractions; the run() contract uses percent.
- easydiffraction datablock names must be lowercase [a-z0-9_].
"""

from __future__ import annotations

import math
import re

import numpy as np

GAUSS_CDEG2_TO_DEG2 = 8.0 * math.log(2.0) * 1e-4
CDEG_TO_DEG = 0.01


def gauss_broadening_to_ed(v: float) -> float:
    return v * GAUSS_CDEG2_TO_DEG2


def gauss_broadening_to_gsas(v: float) -> float:
    return v / GAUSS_CDEG2_TO_DEG2


def lorentz_broadening_to_ed(v: float) -> float:
    return v * CDEG_TO_DEG


def lorentz_broadening_to_gsas(v: float) -> float:
    return v / CDEG_TO_DEG


def zero_to_ed(v: float) -> float:
    return v * CDEG_TO_DEG


def zero_to_gsas(v: float) -> float:
    return v / CDEG_TO_DEG


def rwp_fraction_to_percent(v):
    return None if v is None else 100.0 * float(v)


def datablock_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", str(name).lower())
    return slug or "phase"


def crop_and_sigma(tth, itth, weights, fit_range):
    """Mask points to fit_range with positive weights; sigma = 1/sqrt(w)."""
    tth = np.asarray(tth, dtype=float)
    w = np.asarray(weights, dtype=float)
    lo, hi = -np.inf, np.inf
    if fit_range:
        if fit_range[0] is not None:
            lo = float(fit_range[0])
        if len(fit_range) > 1 and fit_range[1] is not None:
            hi = float(fit_range[1])
    mask = (tth >= lo) & (tth <= hi) & (w > 0)
    sigma = 1.0 / np.sqrt(w[mask])
    return mask, sigma


def q_and_d(tth_deg, wavelength: float):
    theta = np.radians(np.asarray(tth_deg, dtype=float) / 2.0)
    s = np.sin(theta)
    q = 4.0 * math.pi * s / wavelength
    with np.errstate(divide="ignore"):
        d = np.where(s > 0, wavelength / (2.0 * s), np.inf)
    return q, d
