"""Unit tests for powderline.gateways.topas.conversions (plan §7).

Every function is checked against hand-computed values, including the findings
sanity checks (§C.1): microstrain=1000/eta=1 strain coefficient, sigma=10 cdeg
Gaussian FWHM, beq(Uiso=0.00858).
"""

from __future__ import annotations

import math

import pytest

from powderline.gateways.topas import conversions as cv


# --- constants --------------------------------------------------------------


def test_constants_are_exactly_derived():
    assert cv.SQRT8LN2_OVER_100 == math.sqrt(8.0 * math.log(2.0)) / 100.0
    assert cv.CENTIDEG_TO_DEG == 0.01
    assert cv.SIZE_COEF == pytest.approx(0.018, rel=1e-15)
    assert cv.STRAIN_COEF == pytest.approx(1.8e-4, rel=1e-15)
    assert cv.EIGHT_PI_SQ == 8.0 * math.pi**2
    # literal decimal used in the emitted equations matches the constant
    assert round(cv.SQRT8LN2_OVER_100, 7) == 0.0235482


# --- scalar conversions -----------------------------------------------------


def test_beq_from_uiso_sanity():
    # findings §7: beq(Uiso=0.00858) = 0.6774...
    assert cv.beq_from_uiso(0.00858) == pytest.approx(0.677449, abs=1e-6)
    assert cv.beq_from_uiso(0.0) == 0.0
    assert cv.beq_from_uiso(0.009) == pytest.approx(78.9568352087 * 0.009, rel=1e-12)


def test_bkg_gauss_fwhm_sanity():
    # findings §7: sigma = 10 cdeg -> Gaussian FWHM 0.235482 deg
    assert cv.bkg_gauss_fwhm_deg(10.0) == pytest.approx(0.235482, abs=1e-6)
    # 1000 cdeg (the LaB6 recipe value) scales linearly
    assert cv.bkg_gauss_fwhm_deg(1000.0) == pytest.approx(23.5482, abs=1e-4)


def test_bkg_lor_fwhm_is_centideg_to_deg():
    assert cv.bkg_lor_fwhm_deg(1.0) == 0.01
    assert cv.bkg_lor_fwhm_deg(1e-05) == pytest.approx(1e-07, rel=1e-12)


def test_strain_lor_coef_matches_1e3_radians_in_degrees():
    # findings §C.1: microstrain=1000, eta=1 -> 1e-3 rad * tan(theta), in degrees
    coef = cv.strain_lor_coef_deg(1000.0, 1.0)
    assert coef == pytest.approx(math.degrees(1e-3), rel=1e-12)
    # eta=1 leaves nothing for the Gaussian share
    assert cv.strain_gauss_coef_deg(1000.0, 1.0) == pytest.approx(0.0, abs=1e-15)


def test_strain_split_by_eta():
    total = cv.strain_lor_coef_deg(1000.0, 0.3) + cv.strain_gauss_coef_deg(1000.0, 0.3)
    both = cv.strain_lor_coef_deg(1000.0, 1.0)  # eta=1 => full magnitude on L
    assert total == pytest.approx(both, rel=1e-12)


def test_size_coef_1_over_cos_math():
    # 0.018 * eta * lambda / (pi * size_um), eta=1
    coef = cv.size_lor_coef_deg(1.0, 1.0, 0.1665)
    assert coef == pytest.approx(0.018 * 0.1665 / math.pi, rel=1e-12)
    # eta split conserves the magnitude
    total = cv.size_lor_coef_deg(1.0, 0.4, 0.1665) + cv.size_gauss_coef_deg(1.0, 0.4, 0.1665)
    assert total == pytest.approx(coef, rel=1e-12)


# --- float formatting -------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (90.0, "90"),
        (4.15682, "4.15682"),
        (-0.0, "0"),
        (0.0, "0"),
        (0.001, "0.001"),
        (1.0, "1"),
        (109.96, "109.96"),
    ],
)
def test_fmt(value, expected):
    assert cv.fmt(value) == expected


def test_fmt_is_deterministic_and_roundtrips():
    for v in (0.677449, 4.1813, 1e-05, 18.71740850558368):
        assert cv.fmt(v) == cv.fmt(v)
        assert float(cv.fmt(v)) == pytest.approx(v, rel=1e-9)


# --- name sanitisation ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("LaB6", "LaB6"),
        ("Li4MgWO6_SG12", "Li4MgWO6_SG12"),
        ("2theta pos", "p_2theta_pos"),
        ("La1", "La1"),
        ("P m -3 m", "P_m__3_m"),
        ("", "p_"),
    ],
)
def test_sanitize(raw, expected):
    out = cv.sanitize(raw)
    assert out == expected
    assert out[0].isalpha()
    assert all(ch.isalnum() or ch == "_" for ch in out)


# --- x_calculation_step (D8) ------------------------------------------------


def test_round_down_1sf():
    assert cv.round_down_1sf(0.0018585) == 0.001
    assert cv.round_down_1sf(0.023) == 0.02
    assert cv.round_down_1sf(0.09999) == 0.09
    assert cv.round_down_1sf(7.4) == 7.0
    assert cv.round_down_1sf(0.0) == 0.0


def test_x_calculation_step_demo_like():
    # ~0.00372 deg step -> half is ~0.00186 -> rounded down to 1 sf = 0.001
    tth = [0.6472655703475011 + i * 0.0037167413427411 for i in range(10)]
    assert cv.x_calculation_step(tth) == 0.001


def test_x_calculation_step_needs_two_points():
    with pytest.raises(ValueError):
        cv.x_calculation_step([1.0])


# --- equation-string builders -----------------------------------------------


def test_guard_fwhm_wraps_in_max():
    assert cv.guard_fwhm("0.018 x") == "Max(0.018 x, 1e-9)"
    # composes with the equation builders
    assert cv.guard_fwhm(cv.strain_lor_fwhm_eq("e", "s")) == "Max(1.8e-4 e s Tan(Th) / Pi, 1e-9)"


def test_bkg_equation_strings():
    assert cv.bkg_gauss_fwhm_eq("bkgpk0_sig") == "0.0235482 bkgpk0_sig"
    assert cv.bkg_lor_fwhm_eq("bkgpk0_gam") == "0.01 bkgpk0_gam"


def test_instrument_equation_strings_match_blueprint():
    g = cv.instrument_gauss_fwhm_eq("inst_U", "inst_V", "inst_W")
    assert g == "0.0235482 Sqrt(Max(inst_U Tan(Th)^2 + inst_V Tan(Th) + inst_W, 1e-9))"
    lor = cv.instrument_lor_fwhm_eq("inst_X", "inst_Y", "inst_Z")
    assert lor == "0.01 (inst_X / Cos(Th) + inst_Y Tan(Th) + inst_Z)"


def test_size_strain_equation_strings_match_blueprint():
    assert cv.size_lor_fwhm_eq("LaB6_size_eta", "LaB6_size_um") == (
        "0.018 LaB6_size_eta Lam / (Pi LaB6_size_um Cos(Th))"
    )
    assert cv.size_gauss_fwhm_eq("LaB6_size_eta", "LaB6_size_um") == (
        "0.018 (1 - LaB6_size_eta) Lam / (Pi LaB6_size_um Cos(Th))"
    )
    assert cv.strain_lor_fwhm_eq("LaB6_strain_eta", "LaB6_strain") == (
        "1.8e-4 LaB6_strain_eta LaB6_strain Tan(Th) / Pi"
    )
    assert cv.strain_gauss_fwhm_eq("LaB6_strain_eta", "LaB6_strain") == (
        "1.8e-4 (1 - LaB6_strain_eta) LaB6_strain Tan(Th) / Pi"
    )


# --- peak widths (moved from test_topas_spf, re/07 H) ---------------------------


def test_peak_widths_math():
    fg, fl, fpv, ibg, ibl, ibpv = cv.peak_widths(0.1, 0.05)
    assert fg == pytest.approx(2.0 * math.sqrt(2 * math.log(2)) * 0.1)
    assert fl == pytest.approx(0.1)  # 2 * HWHM
    assert fg < fpv and fl < fpv  # pV FWHM exceeds each component
    assert ibg == pytest.approx(fg * math.sqrt(math.pi / (4 * math.log(2))))
