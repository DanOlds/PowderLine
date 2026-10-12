"""Unit-convention conversions for the easydiffraction engine (GSAS-II-free)."""
import math

import numpy as np
import pytest

from powderline.gateways.easydiffraction.conversions import (
    crop_and_sigma,
    datablock_slug,
    gauss_broadening_to_ed,
    gauss_broadening_to_gsas,
    lorentz_broadening_to_ed,
    lorentz_broadening_to_gsas,
    q_and_d,
    rwp_fraction_to_percent,
    zero_to_ed,
    zero_to_gsas,
)


def test_gauss_uvw_centidegsq_sigma_to_degsq_fwhm():
    # GSAS-II U (sigma^2, centideg^2) -> easydiffraction Caglioti U (FWHM^2, deg^2)
    # LaB6 example value, cross-checked in the API spike:
    assert gauss_broadening_to_ed(18.71740850558368) == pytest.approx(0.010379135146427253)
    assert gauss_broadening_to_ed(0.0) == 0.0


def test_gauss_roundtrip():
    assert gauss_broadening_to_gsas(gauss_broadening_to_ed(1.147)) == pytest.approx(1.147)


def test_lorentz_and_zero_are_centideg_to_deg():
    assert lorentz_broadening_to_ed(0.28143034323339766) == pytest.approx(0.0028143034323339766)
    assert lorentz_broadening_to_gsas(0.01) == pytest.approx(1.0)
    assert zero_to_ed(2.0) == pytest.approx(0.02)
    assert zero_to_gsas(zero_to_ed(-3.7)) == pytest.approx(-3.7)


def test_rwp_fraction_to_percent():
    assert rwp_fraction_to_percent(0.19769747424440315) == pytest.approx(19.769747424440315)
    assert rwp_fraction_to_percent(None) is None


def test_datablock_slug_lowercases_and_sanitizes():
    # easydiffraction rejects uppercase datablock names ("Use 'lab6' instead")
    assert datablock_slug("LaB6") == "lab6"
    assert datablock_slug("Li4MgWO6 SG12!") == "li4mgwo6_sg12_"
    assert datablock_slug("") == "phase"


def test_crop_and_sigma_masks_range_and_bad_weights():
    tth = np.array([0.5, 1.0, 2.0, 14.0, 15.5])
    itth = np.ones(5)
    w = np.array([4.0, 4.0, 0.0, 25.0, 4.0])
    mask, sigma = crop_and_sigma(tth, itth, w, [1.0, 15.0])
    assert mask.tolist() == [False, True, False, True, False]  # range + w<=0 dropped
    assert sigma == pytest.approx([0.5, 0.2])  # 1/sqrt(w)


def test_crop_and_sigma_none_range_keeps_all_positive_weight_points():
    tth = np.array([1.0, 2.0])
    mask, sigma = crop_and_sigma(tth, np.ones(2), np.array([1.0, 1.0]), None)
    assert mask.all() and sigma == pytest.approx([1.0, 1.0])


def test_q_and_d_against_gsas_reference_row():
    # First row of examples/example_LaB6/output/fit_profile.txt:
    # two_theta=0.64726557, q=0.42630772, d=14.73861496 at lam=0.1665
    q, d = q_and_d(np.array([0.64726557]), 0.1665)
    assert q[0] == pytest.approx(0.42630772, abs=1e-6)
    assert d[0] == pytest.approx(14.73861496, abs=1e-4)
