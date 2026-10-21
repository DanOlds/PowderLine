"""Shared helper for the ionic-type gate (P-47): per-reflection net peak areas of a calculated pattern."""
from __future__ import annotations

import numpy as np


def peak_areas(two_theta, y_calc, y_bkg, centres, half_width=0.12):
    """Background-subtracted area under ``y_calc`` within ``centres +- half_width`` degrees, per centre.

    The window is narrow on purpose: the ionic gate compares *ratios* of two calculations with the same
    profile, so the tail fraction a window misses cancels exactly within an engine and nearly so between
    engines (both ratios are taken at the same centres)."""
    tth = np.asarray(two_theta, dtype=float)
    net = np.asarray(y_calc, dtype=float) - np.asarray(y_bkg, dtype=float)
    out = []
    for c in centres:
        m = (tth > c - half_width) & (tth < c + half_width)
        out.append(float(np.trapezoid(net[m], tth[m])) if m.any() else float("nan"))
    return np.array(out)


def resolved(centres, min_separation=0.3):
    """Indices of centres whose nearest neighbour is at least ``min_separation`` degrees away."""
    centres = np.asarray(centres, dtype=float)
    keep = []
    for i, c in enumerate(centres):
        others = np.delete(centres, i)
        if others.size == 0 or np.min(np.abs(others - c)) >= min_separation:
            keep.append(i)
    return keep
