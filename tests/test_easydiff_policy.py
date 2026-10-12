"""Unsupported-feature policy: refined-unmappable raises, fixed-unmappable warns."""
import copy

import pytest

from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.policy import check_unsupported, param_flag, param_value


def base_recipe():
    return {
        "schema_name": "GSASII_Rietveld",
        "schema_version": "0.25.4",
        "payload": {
            "xrd_data": {"tth": [1, 2], "Itth": [1, 1], "Itth_weights": [1, 1]},
            "fit_range": [1, 15],
            "instrument": {
                "description": "test",
                "initialization": [
                    {"Lam": [0.1665, 0.1665, False], "Zero": [0.0, 0.0, False],
                     "U": [18.7, 18.7, False], "V": [0.6, 0.6, False], "W": [1.1, 1.1, False],
                     "X": [0.28, 0.28, False], "Y": [0.001, 0.001, False], "Z": [0.0, 0.0, False],
                     "SH/L": [0.0, 0.0, False], "Polariz.": [0.99, 0.99, False]},
                    {},
                ],
                "parameterization": {
                    "broadening": {k: [None, False, None, None] for k in "UVWXYZ"},
                    "corrections": {"axial_divergence": None, "zero_shift": None},
                    "polarization": [0.99, False, None, None],
                },
            },
            "phases": {
                "LaB6": {
                    "structure": {"phase_name": "LaB6", "space_group": "P m -3 m",
                                  "unit_cell": {"a": 4.15682, "b": 4.15682, "c": 4.15682,
                                                "alpha": 90.0, "beta": 90.0, "gamma": 90.0},
                                  "atoms": {"La": {"element": "La", "x": 0.0, "y": 0.0, "z": 0.0,
                                                   "occupancy": 1.0, "Uiso": 0.0086, "ADP": "Uiso"}}},
                    "parameterization": {
                        "atoms": {"La": {k: [None, False, None, None]
                                         for k in ("x", "y", "z", "occupancy", "Uiso")}},
                        "scale": [1, True, None, None],
                        "unit_cell": {k: [None, False, None, None]
                                      for k in ("a", "b", "c", "alpha", "beta", "gamma")},
                    },
                }
            },
            "background": {"chebyshev": {"coefficients": [10, 5], "num_coefficients": 2,
                                         "refine_flag": True}},
            "refinement_controls": {"refinement_cycles": 5},
        },
    }


def test_param_flag_and_value():
    assert param_flag([1.0, True, None, None]) is True
    assert param_flag([1.0, False, None, None]) is False
    assert param_flag(None) is False
    assert param_value([2.5, True, None, None]) == 2.5
    assert param_value([None, True, None, None], default=7) == 7
    assert param_value(None, default=7) == 7


def test_clean_recipe_warns_only_about_cycles():
    warnings = check_unsupported(base_recipe())
    assert len(warnings) == 1 and "refinement_cycles" in warnings[0]


def test_spf_schema_rejected():
    r = base_recipe()
    r["schema_name"] = "GSASII_SPF"
    with pytest.raises(EasyDiffractionTranslationError, match="GSASII_Rietveld"):
        check_unsupported(r)


def test_two_wavelength_rejected():
    r = base_recipe()
    r["payload"]["instrument"]["initialization"][0]["Lam1"] = [1.54, 1.54, False]
    with pytest.raises(EasyDiffractionTranslationError, match="Lam1"):
        check_unsupported(r)


@pytest.mark.parametrize("path,match", [
    (("instrument", "parameterization", "broadening", "Z"), "Z"),
    (("instrument", "parameterization", "polarization"), "polarization"),
    (("instrument", "parameterization", "corrections", "axial_divergence"), "axial"),
])
def test_refined_unmappable_instrument_raises(path, match):
    r = base_recipe()
    node = r["payload"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = [None, True, None, None]
    with pytest.raises(EasyDiffractionTranslationError, match=match):
        check_unsupported(r)


def test_refined_background_peaks_raise():
    r = base_recipe()
    r["payload"]["background"]["single_peaks"] = {
        "positions": [[1.5, True, None, None]], "intensities": [[1, False, None, None]]}
    with pytest.raises(EasyDiffractionTranslationError, match="background"):
        check_unsupported(r)


def test_fixed_background_peaks_warn():
    r = base_recipe()
    r["payload"]["background"]["single_peaks"] = {
        "positions": [[1.5, False, None, None]], "intensities": [[1, False, None, None]]}
    warnings = check_unsupported(r)
    assert any("background peaks" in w for w in warnings)


def test_atom_flag_raises():
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["La"]["Uiso"] = [None, True, None, None]
    with pytest.raises(EasyDiffractionTranslationError, match="atom"):
        check_unsupported(r)


def test_fixed_nonzero_Z_and_shl_warn():
    r = base_recipe()
    i1 = r["payload"]["instrument"]["initialization"][0]
    i1["Z"] = [0.3, 0.3, False]
    i1["SH/L"] = [0.0005, 0.0005, False]
    warnings = check_unsupported(r)
    assert any("Z=" in w for w in warnings)
    assert any("SH/L" in w for w in warnings)


def test_peak_broadening_model_string_alone_does_not_warn():
    # The real LaB6 example carries {"model": "isotropic"} with null magnitudes;
    # a bare model string must not trigger the not-mapped warning.
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["peak_broadening"] = {
        "size_broadening": {"model": "isotropic", "isotropic_size": None, "LG_eta": None},
        "strain_broadening": {"model": "isotropic", "isotropic_strain": None, "LG_eta": None},
    }
    assert not any("peak_broadening" in w for w in check_unsupported(r))


def test_peak_broadening_with_value_warns():
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["peak_broadening"] = {
        "size_broadening": {"model": "isotropic", "isotropic_size": [50.0, False, None, None]},
    }
    assert any("peak_broadening" in w for w in check_unsupported(r))


def test_uaniso_flagged_raises():
    # Schema/recipe keys are UPPERCASE (U11..U23) — see schema.py UnitCell docs
    # and examples/example_DRX_33_anisoADP; PR review caught the lowercase drift.
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["La"]["Uaniso"] = {
        "U11": [0.01, True, None, None],
        "U22": [0.01, False, None, None],
        "U33": [0.01, False, None, None],
        "U12": [0.0, False, None, None],
        "U13": [0.0, False, None, None],
        "U23": [0.0, False, None, None],
    }
    with pytest.raises(EasyDiffractionTranslationError, match="anisotropic ADPs.*flagged"):
        check_unsupported(r)


def test_uaniso_fixed_warns():
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["La"]["Uaniso"] = {
        "U11": [0.01, False, None, None],
        "U22": [0.01, False, None, None],
        "U33": [0.01, False, None, None],
        "U12": [0.0, False, None, None],
        "U13": [0.0, False, None, None],
        "U23": [0.0, False, None, None],
    }
    warnings = check_unsupported(r)
    assert any("anisotropic ADPs not mapped" in w and "LaB6/La" in w for w in warnings)


def test_uaniso_case_insensitive():
    # Defensive: lowercase keys (the old assumption) must also be caught.
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["La"]["Uaniso"] = {
        "u11": [0.01, True, None, None],
    }
    with pytest.raises(EasyDiffractionTranslationError, match="anisotropic ADPs.*flagged"):
        check_unsupported(r)


def test_unknown_space_group_raises():
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["structure"]["space_group"] = "NOT A GROUP"
    with pytest.raises(EasyDiffractionTranslationError, match="NOT A GROUP"):
        check_unsupported(r)


def test_rhombohedral_R_setting_rejected():
    # A62: the easydiffraction layer rejects :R itself (core accepts it).
    r = base_recipe()
    r["payload"]["phases"]["LaB6"]["structure"]["space_group"] = "R -3 m :R"
    with pytest.raises(EasyDiffractionTranslationError, match="rhombohedral ':R'"):
        check_unsupported(r)
