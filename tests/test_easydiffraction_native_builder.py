"""Native easydiffraction builder (re/06 Phase 3): mapping, data, site check, refinement flags.

The pure parts run in the core-only environment; tests that build an
easydiffraction ``Project`` are skipped where easydiffraction is not installed.
"""

from __future__ import annotations

import copy
import importlib.util

import numpy as np
import pytest

from powderline.gateways.easydiffraction import native_builder as nb
from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.schema import EasydiffractionRecipe
from powderline.schema_core import fit_limits_on_data

_HAS_ED = importlib.util.find_spec("easydiffraction") is not None
needs_ed = pytest.mark.skipif(not _HAS_ED, reason="easydiffraction not installed")


@pytest.fixture(autouse=True)
def _declare_schema_versions(monkeypatch):
    """Until the gateway adoption commit adds ``schema.DECLARATIONS`` to ``gateway.capabilities()``,
    a full native recipe cannot pass its version check (A65); declare them here so the
    builder tests validate complete recipes."""
    from powderline.gateways.easydiffraction import gateway, schema

    if "supported_engine_schemas" not in gateway.capabilities():
        base = gateway.capabilities
        monkeypatch.setattr(gateway, "capabilities", lambda: {**base(), **schema.DECLARATIONS})


def P(v, f=False, lo=None, hi=None):
    return [v, f, lo, hi]


def _lab6_phase(scale_flag=False):
    return {
        "space_group": "P m -3 m",
        "unit_cell": {"a": P(4.1568), "b": P(4.1568), "c": P(4.1568),
                      "alpha": P(90.0), "beta": P(90.0), "gamma": P(90.0)},
        "atoms": {
            "La1": {"element": "La", "x": P(0.0), "y": P(0.0), "z": P(0.0), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.005)},
            "B1": {"element": "B", "x": P(0.1997), "y": P(0.5), "z": P(0.5), "occupancy": P(1.0),
                   "ADP": "Uiso", "Uiso": P(0.006)},
        },
        "scale": P(1.0, scale_flag, 0.0, None),
        "background": {"num_coefficients": 2, "coefficients": [100.0, 0.0], "refine_flag": False},
    }


def _recipe(**phases):
    """A small native recipe: 551 points 5..60 deg, window [10, 55], LaB6 unless phases are given."""
    tth = np.linspace(5.0, 60.0, 551)
    itth = 100.0 + 50.0 * np.exp(-((tth - 30.0) ** 2))
    weights = np.full(551, 0.01)
    weights[20] = 0.0  # an excluded point inside the data span
    return {
        "core_schema_version": "1.0.0", "engine_schema_version": "1.0.0",
        "schema_name": "easydiffraction.rietveld",
        "payload": {
            "xrd_data": {"tth": tth.tolist(), "Itth": itth.tolist(), "Itth_weights": weights.tolist()},
            "fit_range": [10.0, 55.0],
            "phases": phases or {"LaB6": _lab6_phase()},
            "instrument": {
                "radiation": {"wavelength": P(1.5406)},
                "corrections": {"zero": P(0.0), "polarization": P(0.5)},
                "broadening": {"u": P(0.01), "v": P(-0.001), "w": P(0.002), "x": P(0.01), "y": P(0.0)},
            },
            "refinement_controls": {"calculator": "cryspy", "peak_type": "pseudo-voigt"},
        },
    }


def _validated(recipe=None, **phases) -> EasydiffractionRecipe:
    return EasydiffractionRecipe.model_validate(recipe if recipe is not None else _recipe(**phases))


# ---------------------------------------------------------------------------
# 3.1 space-group mapping, names, data
# ---------------------------------------------------------------------------


def test_resolve_space_group_maps_to_easydiffraction_symbol_and_code():
    assert nb.resolve_space_group("P m -3 m", "cryspy") == ("P m -3 m", "")
    assert nb.resolve_space_group("C 1 2/m 1", "crysfml") == ("C 2/m", "b1")
    assert nb.resolve_space_group("R -3 c:H", "cryspy") == ("R -3 c", "h")


def test_resolve_space_group_unsupported_names_calculator_and_remedy():
    with pytest.raises(EasyDiffractionTranslationError) as exc:
        nb.resolve_space_group("P 1 21/c 1", "crysfml")
    message = str(exc.value)
    assert "'crysfml'" in message and "'P 1 21/c 1'" in message
    assert "calculator 'cryspy'" in message
    # an origin-choice-1 setting refused for CrysPy suggests the :2 origin (A174)
    with pytest.raises(EasyDiffractionTranslationError) as exc:
        nb.resolve_space_group("F d -3 m:1", "cryspy")
    assert "'F d -3 m:2'" in str(exc.value) and "'crysfml'" in str(exc.value)


def test_datablock_name_is_phase_name_lowercased():
    assert nb.datablock_name("LaB6") == "lab6"
    assert nb.datablock_name("Al2O3_corundum") == "al2o3_corundum"


def test_window_sigma_weights_window_only():
    tth = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    w = np.array([4.0, 0.0, 4.0, 16.0, 4.0])
    mask, sigma = nb.window_sigma(tth, w, 2.0, 4.0)
    assert mask.tolist() == [False, False, True, True, False]
    assert sigma[2] == pytest.approx(0.5) and sigma[3] == pytest.approx(0.25)
    assert sigma[0] == sigma[1] == sigma[4] == nb.SIGMA_WEIGHTLESS
    assert len(sigma) == 5  # every point is handed (A158)


def test_prepare_data_hands_every_point_and_masks_core_window():
    model = _validated()
    tth, itth, sigma, mask = nb.prepare_data(model.payload)
    payload = model.payload
    assert len(tth) == len(payload.xrd_data.tth) == len(sigma) == len(mask)
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    expected = (tth >= lo) & (tth <= hi) & (np.asarray(payload.xrd_data.Itth_weights) > 0)
    assert mask.tolist() == expected.tolist()
    assert mask[20] is np.False_  # zero-weight point inside the window is weightless
    assert sigma[~mask].min() == nb.SIGMA_WEIGHTLESS
    assert sigma[mask] == pytest.approx(10.0)


@pytest.mark.parametrize("value", [12.345678901234567, 5e-05, 1e-7, 0.1 + 0.2, 123456789012345.67, 1.0, 0.0])
def test_cif_number_round_trips_full_precision_without_exponent(value):
    text = nb.cif_number(value)
    assert float(text) == value
    assert "e" not in text.lower()  # EB-89: the loop reader cannot parse exponents


def test_data_cif_has_experiment_type_and_full_precision_loop():
    tth = np.array([2.0 + 1.234567890123e-7, 2.5])
    cif = nb.data_cif(tth, np.array([10.0, 5e-5]), np.array([1.0, nb.SIGMA_WEIGHTLESS]))
    lines = cif.splitlines()
    assert lines[0] == "data_powderline"
    assert "_experiment_type.beam_mode \"constant wavelength\"" in lines
    assert lines[-2:] == [f"{float(tth[0])!r} 10 1", "2.5 0.00005 100000000"]
    assert float(lines[-2].split()[0]) == tth[0]


def test_background_phase_single_or_none_or_error():
    model = _validated()
    assert nb.background_phase(model.payload) == "LaB6"
    two = _recipe()
    two["payload"]["phases"]["Second"] = copy.deepcopy(two["payload"]["phases"]["LaB6"])
    with pytest.raises(EasyDiffractionTranslationError, match="one background per experiment"):
        nb.background_phase(_validated(two).payload)
    two["payload"]["phases"]["Second"].pop("background")
    assert nb.background_phase(_validated(two).payload) == "LaB6"


def test_atom_order_hands_anisotropic_atoms_first():
    recipe = _recipe()
    atoms = recipe["payload"]["phases"]["LaB6"]["atoms"]
    atoms["B1"] = {**atoms["B1"], "ADP": "Uaniso",
                   "Uaniso": {"U11": P(0.006), "U22": P(0.008), "U33": P(0.008),
                              "U12": P(0.0), "U13": P(0.0), "U23": P(0.0)}}
    atoms["B1"].pop("Uiso")
    phase = _validated(recipe).payload.phases["LaB6"]
    assert [label for label, _ in nb.atom_order(phase)] == ["B1", "La1"]


def test_module_imports_no_engine():
    import sys
    assert "easydiffraction" not in sys.modules or _HAS_ED  # the import is lazy
    assert "GSASII" not in sys.modules or "powderline.kicker" in sys.modules


@needs_ed
def test_build_project_values_only(tmp_path):
    model = _validated()
    result = nb.build_project(model, tmp_path)
    assert result.datablock_names == {"LaB6": "lab6"}
    assert (tmp_path / nb.DATA_CIF_NAME).exists()
    structure = result.project.structures["lab6"]
    assert structure.cell.length_a.value == pytest.approx(4.1568)
    assert structure.cell.length_b.value == pytest.approx(4.1568)
    assert structure.atom_sites["B1"].fract_x.value == pytest.approx(0.1997)
    assert structure.atom_sites["B1"].adp_iso.value == pytest.approx(0.006)
    expt = result.project.experiments["powderline"]
    assert len(expt.data.x) == 551
    assert expt.calculator.type == "cryspy"
    assert expt.peak.type == "cwl-pseudo-voigt"
    assert expt.instrument.setup_wavelength.value == pytest.approx(1.5406)
    assert result.window_mask.sum() == int(((np.asarray(model.payload.xrd_data.tth) >= 10.0)
                                            & (np.asarray(model.payload.xrd_data.tth) <= 55.0)).sum()) - 1


# ---------------------------------------------------------------------------
# 3.2 site check and special positions (pure, with a duck-typed structure)
# ---------------------------------------------------------------------------


class _Value:
    def __init__(self, value):
        self.value = value


class _FakeSite:
    def __init__(self, xyz, multiplicity, letter="f"):
        self.fract_x, self.fract_y, self.fract_z = (_Value(v) for v in xyz)
        self.multiplicity = _Value(multiplicity)
        self.wyckoff_letter = _Value(letter)


class _FakeStructure:
    """What ``site_problems`` reads of an easydiffraction structure: ``atom_sites[label]``."""

    def __init__(self, sites: dict):
        self.atom_sites = sites


def _hex_phase(x=0.137, y=0.863):
    """P 63/m m c with Zn on 6h (x, -x, 1/4), stated with y = 1 - x (in [0, 1))."""
    return {
        "space_group": "P 63/m m c",
        "unit_cell": {"a": P(5.2), "b": P(5.2), "c": P(8.6), "alpha": P(90.0), "beta": P(90.0), "gamma": P(120.0)},
        "atoms": {
            "Mg1": {"element": "Mg", "x": P(0.333333), "y": P(0.666667), "z": P(0.0625), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.01)},
            "Zn1": {"element": "Zn", "x": P(x), "y": P(y), "z": P(0.25), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.01)},
        },
        "scale": P(1.0),
    }


def test_compare_site_same_translated_moved_multiplicity():
    assert nb._compare_site((0.1997, 0.5, 0.5), (0.1997, 0.5, 0.5), 6, 6) == nb.SiteComparison("same")
    found = nb._compare_site((0.137, 0.863, 0.25), (0.137, -0.137, 0.25), 6, 6)
    assert found.status == "translated" and found.shift == (0, -1, 0)
    assert nb._compare_site((0.137, 0.863, 0.25), (0.137, 0.863 + 2e-3, 0.25), 6, 6).status == "moved"
    assert nb._compare_site((0.137, 0.863, 0.25), (0.5, 0.863, 0.25), 6, 6).status == "moved"
    assert nb._compare_site((0.47, 0.5, 0.5), (0.47, 0.5, 0.5), 6, 24).status == "multiplicity"
    # float noise below POSITION_TOL is the same position
    assert nb._compare_site((1 / 3, 2 / 3, 0.25), (0.33333333333, 0.66666666667, 0.25), 2, 2).status == "same"


def test_check_sites_accepts_lattice_translation_with_warning():
    phase = _validated(Zn=_hex_phase()).payload.phases["Zn"]
    structure = _FakeStructure({"Mg1": _FakeSite((1 / 3, 2 / 3, 0.0625), 4, "f"),
                                "Zn1": _FakeSite((0.137, -0.137, 0.25), 6, "h")})
    warnings = nb.check_sites("Zn", phase, "crysfml", structure)
    assert [w["code"] for w in warnings] == ["easydiffraction_position_translated"]
    assert warnings[0]["field_path"] == "payload.phases.Zn.atoms.Zn1"
    assert "(0, -1, 0)" in warnings[0]["message"] and "(0.137, -0.137, 0.25)" in warnings[0]["message"]


def test_check_sites_rejects_move_that_is_not_a_translation():
    phase = _validated(Zn=_hex_phase()).payload.phases["Zn"]
    structure = _FakeStructure({"Mg1": _FakeSite((1 / 3, 2 / 3, 0.0625), 4),
                                "Zn1": _FakeSite((0.137, 0.137, 0.25), 6)})
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("Zn", phase, "crysfml", structure)
    assert "payload.phases.Zn.atoms.Zn1 at (0.137, 0.863, 0.25)" in str(exc.value)
    assert "holds it at (0.137, 0.137, 0.25)" in str(exc.value)
    assert isinstance(exc.value, (EasyDiffractionTranslationError, ValueError))


def test_check_sites_rejects_multiplicity_mismatch_naming_both():
    phase = _validated().payload.phases["LaB6"]
    structure = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                "B1": _FakeSite((0.1997, 0.5, 0.5), 24, "l")})
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("LaB6", phase, "crysfml", structure)
    message = str(exc.value)
    assert "B1" in message and "multiplicity 24" in message and "multiplicity in 'P m -3 m' is 6" in message
    assert "equivalent positions" in message and "(0.5, 0.1997, 0.5)" in message
    assert "La1" not in message.split("- ")[1]  # only the problem atom is listed


def test_origin1_refused_for_cryspy_only():
    from powderline.symmetry import analyze_site

    special = analyze_site("F d -3 m:1", (0.137, 0.25, 0.25))  # 48f
    general = analyze_site("F d -3 m:1", (0.137, 0.271, 0.389))
    why = nb.origin1_problem("F d -3 m:1", "cryspy", special)
    assert why is not None and "'F d -3 m:2'" in why and "crysfml" in why
    assert nb.origin1_problem("F d -3 m:1", "crysfml", special) is None
    assert nb.origin1_problem("F d -3 m:1", "cryspy", general) is None
    assert nb.origin1_problem("F d -3 m:2", "cryspy", analyze_site("F d -3 m:2", (0.137, 0.125, 0.125))) is None
    assert nb.origin1_problem("P m -3 m", "cryspy", analyze_site("P m -3 m", (0.1997, 0.5, 0.5))) is None


def test_check_sites_origin1_error_before_reading_the_structure(monkeypatch):
    """The origin-1 refusal needs no easydiffraction reading of the atom (the setting is the problem)."""
    from powderline.gateways.easydiffraction import space_group_support as sgs

    monkeypatch.setitem(sgs.CRYSPY_SETTINGS, "F d -3 m:1", {"short_name": "F d -3 m", "coord_system_code": "1"})
    phase = _validated(Spinel={
        "space_group": "F d -3 m:1",
        "unit_cell": {k: P(8.08) for k in "abc"} | {k: P(90.0) for k in ("alpha", "beta", "gamma")},
        "atoms": {"O1": {"element": "O", "x": P(0.137), "y": P(0.25), "z": P(0.25), "occupancy": P(1.0),
                         "ADP": "Uiso", "Uiso": P(0.01)}},
        "scale": P(1.0)}).payload.phases["Spinel"]
    with pytest.raises(nb.SiteError, match="origin-choice-1"):
        nb.check_sites("Spinel", phase, "cryspy", _FakeStructure({}))


@pytest.mark.parametrize("template, xyz, expected", [
    ("(x,1/2,1/2)", (0.1997, 0.5, 0.5), True),
    ("(x,1/2,1/2)", (0.5, 0.5, 0.1997), False),      # another image of the orbit (EB-77)
    ("(x,-x,z)", (0.137, 0.863, 0.25), True),         # modulo 1 (EB-74)
    ("(x,-x,z)", (0.137, -0.137, 0.25), True),
    ("(x,2x,1/4)", (0.863, 0.726, 0.25), True),
    ("(x,2x,1/4)", (0.274, 0.137, 0.25), False),
    ("(1/4,1/4,z)", (0.25, 0.25, 0.389), True),
    ("(1/4,1/4,z)", (0.75, 0.25, 0.389), False),
    ("(x,1/2-x,z)", (0.137, 0.363, 0.3), True),
    ("(2x,x,z)", (0.274, 0.137, 0.3), True),
])
def test_matches_template(template, xyz, expected):
    assert nb._matches_template(template, xyz) is expected


def test_check_sites_cryspy_template_rule_names_the_position_to_write(monkeypatch):
    """CrysPy: B on 6f written as (1/2, 1/2, x) is refused with (x, 1/2, 1/2) suggested (A164)."""
    recipe = _recipe()
    b1 = recipe["payload"]["phases"]["LaB6"]["atoms"]["B1"]
    b1["x"], b1["y"], b1["z"] = P(0.5), P(0.5), P(0.1997)
    phase = _validated(recipe).payload.phases["LaB6"]
    structure = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                "B1": _FakeSite((0.5, 0.5, 0.1997), 6, "f")})
    monkeypatch.setattr(nb, "_ed_wyckoff_template", lambda name, code, letter: {"f": "(x,1/2,1/2)"}.get(letter))
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("LaB6", phase, "cryspy", structure)
    assert "template (x,1/2,1/2)" in str(exc.value) and "(0.1997, 0.5, 0.5)" in str(exc.value)
    # CrysFML has no such rule, and CrysPy accepts the template's own pattern
    assert nb.check_sites("LaB6", phase, "crysfml", structure) == []
    phase_ok = _validated().payload.phases["LaB6"]
    structure_ok = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                   "B1": _FakeSite((0.1997, 0.5, 0.5), 6, "f")})
    assert nb.check_sites("LaB6", phase_ok, "cryspy", structure_ok) == []


@needs_ed
def test_build_project_runs_site_check(tmp_path):
    result = nb.build_project(_validated(Zn=_hex_phase()), tmp_path)
    assert [w["code"] for w in result.warnings] in ([], ["easydiffraction_position_translated"])
    zn = result.project.structures["zn"].atom_sites["Zn1"]
    assert int(zn.multiplicity.value) == 6
