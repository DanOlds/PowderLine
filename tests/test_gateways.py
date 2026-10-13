"""Tests for the three built-in gateways: schema layer, version checking, import-block guarantee.

Coverage:
- ENGINE_VERSION_SPEC consistency across all gateways
- validate() for each gateway (LaB6 recipe)
- Version-check behavior (gsasii, topas, easydiffraction)
- Dispatcher routing (registry.get → gateway.run)
- Import-block guarantee: capabilities/validate work with engines unavailable
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from packaging.specifiers import SpecifierSet
from pydantic import ValidationError

import powderline
from powderline import registry, schema
from powderline.exceptions import EngineNotAvailableError, EngineVersionError
from powderline.gateways.gsasii import gateway as gsasii_gateway
from powderline.gateways.topas import gateway as topas_gateway
from powderline.gateways.easydiffraction import gateway as easydiff_gateway
from subprocess_utils import run_subprocess_utf8

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"


def _lab6_recipe():
    """Load the LaB6 example recipe as a dict."""
    return json.loads((EXAMPLES / "example_LaB6" / "input.json").read_text(encoding="utf-8"))


@pytest.fixture
def isolated_registry():
    """Snapshot the registry caches; restore them even if the test fails."""
    saved = (registry._explicit.copy(), registry._instances.copy(), registry._entry_points)
    yield registry
    registry._explicit.clear()
    registry._explicit.update(saved[0])
    registry._instances.clear()
    registry._instances.update(saved[1])
    registry._entry_points = saved[2]


# --- ENGINE_VERSION_SPEC consistency ---


@pytest.mark.parametrize(
    "gateway_module,expected_spec",
    [
        (gsasii_gateway, "==5.7.9"),
        (topas_gateway, ">=7,<8"),
        (easydiff_gateway, "==0.21.1"),
    ],
)
def test_engine_version_spec_parses_and_matches_capabilities(gateway_module, expected_spec):
    """Each gateway's ENGINE_VERSION_SPEC parses and equals capabilities()['engine_version_spec']."""
    spec_str = gateway_module.ENGINE_VERSION_SPEC
    SpecifierSet(spec_str)  # must parse
    assert spec_str == expected_spec
    caps = gateway_module.capabilities()
    assert caps["engine_version_spec"] == expected_spec


@pytest.mark.parametrize(
    "gateway_module",
    [gsasii_gateway, topas_gateway, easydiff_gateway],
)
def test_get_gateway_returns_gateway_with_correct_name(gateway_module):
    """Each gateway module's get_gateway() returns a Gateway whose name == NAME."""
    gw = gateway_module.get_gateway()
    assert gw.name == gateway_module.NAME
    assert isinstance(gw, registry.Gateway)


# --- validate() ---


@pytest.mark.parametrize(
    "gateway_module",
    [gsasii_gateway, topas_gateway],
)
def test_validate_lab6_recipe_dict_returns_recipe_model(gateway_module):
    """Validating the LaB6 recipe dict returns a RecipeModel.

    Note: easydiffraction is excluded because the LaB6 recipe has a Z broadening
    parameter flagged for refinement, which easydiffraction does not support.
    """
    recipe = _lab6_recipe()
    result = gateway_module.validate(recipe)
    assert isinstance(result, schema.RecipeModel)


@pytest.mark.parametrize(
    "gateway_module",
    [gsasii_gateway, topas_gateway],
)
def test_validate_recipe_model_is_idempotent(gateway_module):
    """Passing a RecipeModel to validate() returns a RecipeModel (idempotent).

    Note: easydiffraction is excluded because the LaB6 recipe has a Z broadening
    parameter flagged for refinement, which easydiffraction does not support.
    """
    recipe = _lab6_recipe()
    model = schema.RecipeModel.model_validate(recipe)
    result = gateway_module.validate(model)
    assert isinstance(result, schema.RecipeModel)


@pytest.mark.parametrize(
    "gateway_module,invalid_recipe",
    [
        (gsasii_gateway, {"schema_name": "GSASII_Rietveld"}),
        (topas_gateway, {"schema_name": "GSASII_Rietveld"}),
        (easydiff_gateway, {"schema_name": "GSASII_Rietveld"}),
        (gsasii_gateway, {}),
        (topas_gateway, {}),
        (easydiff_gateway, {}),
    ],
)
def test_validate_invalid_recipe_raises(gateway_module, invalid_recipe):
    """An invalid recipe raises ValidationError (from pydantic or wrapped)."""
    with pytest.raises((ValidationError, Exception)):  # may be wrapped
        gateway_module.validate(invalid_recipe)


def test_easydiff_validate_lab6_raises_translation_error():
    """Easydiffraction validate raises on the LaB6 recipe due to unsupported Z parameter."""
    from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError

    recipe = _lab6_recipe()
    with pytest.raises(EasyDiffractionTranslationError, match="Z is flagged for refinement"):
        easydiff_gateway.validate(recipe)


# --- gsasii version checking ---


@pytest.mark.skipif(
    importlib.util.find_spec("GSASII") is None, reason="GSAS-II not installed"
)
def test_gsasii_installed_engine_version_satisfies_spec():
    """When GSAS-II is installed, installed_engine_version() satisfies ENGINE_VERSION_SPEC."""
    version = gsasii_gateway.installed_engine_version()
    spec = SpecifierSet(gsasii_gateway.ENGINE_VERSION_SPEC)
    assert version in spec, f"installed {version} not in {spec}"


def test_gsasii_version_check_raises_when_version_out_of_range(tmp_path, monkeypatch):
    """When the installed version is out of range, run() raises EngineVersionError."""
    monkeypatch.setattr(gsasii_gateway, "installed_engine_version", lambda: "5.8.0")
    # Mock the in-process executor so nothing actually runs
    fake_run = Mock()
    monkeypatch.setattr(gsasii_gateway, "run_refinement", fake_run)

    with pytest.raises(EngineVersionError):
        gsasii_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False, execution_mode="subprocess")
    assert not fake_run.called, "run_refinement should not be called when version check fails"


def test_gsasii_version_check_bypassed_with_allow_unsupported(tmp_path, monkeypatch):
    """With allow_unsupported_engine_version=True, the version check is bypassed and the run proceeds."""
    monkeypatch.setattr(gsasii_gateway, "installed_engine_version", lambda: "5.8.0")
    fake_run = Mock(return_value={"success": True, "method": "subprocess"})
    monkeypatch.setattr(gsasii_gateway, "run_refinement", fake_run)

    result = gsasii_gateway.run(
        _lab6_recipe(),
        str(tmp_path),
        validate_only=False,
        verbose=True,
        execution_mode="subprocess",
        allow_unsupported_engine_version=True,
    )
    assert fake_run.called
    recipe_arg, output_arg = fake_run.call_args[0]
    assert isinstance(recipe_arg, schema.RecipeModel)  # the gateway validated the dict first
    assert Path(output_arg) == tmp_path
    call_kwargs = fake_run.call_args[1]
    assert call_kwargs["verbose"] is True
    assert call_kwargs["method"] == "subprocess"
    # the gateway's own result normalisation applied
    assert result["error"] is None
    assert result["refined_parameters"].empty


def test_gsasii_validate_only_skips_version_check(tmp_path, monkeypatch):
    """With validate_only=True, the version check is skipped."""
    called = []

    def failing_version_check():
        called.append(True)
        raise AssertionError("version check should not be called for validate_only=True")

    monkeypatch.setattr(gsasii_gateway, "installed_engine_version", failing_version_check)
    fake_run = Mock(return_value={})
    monkeypatch.setattr(gsasii_gateway, "run_refinement", fake_run)

    result = gsasii_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=True)
    assert not called, "installed_engine_version should not be called for validate_only=True"
    assert not fake_run.called
    assert result["method"] == "validate_only" and result["schema_name"] == "GSASII_Rietveld"


# --- topas version checking ---


def test_topas_no_version_configured_skips_check(tmp_path, monkeypatch):
    """When no version is configured, the version check is skipped."""
    from powderline.gateways.topas import runner, engine

    monkeypatch.setattr(runner, "_read_topas_config", lambda: {})
    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    topas_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False)
    assert fake_run.called


def test_topas_version_kwarg_out_of_range_raises(tmp_path, monkeypatch):
    """topas_version=6 (out of range) raises EngineVersionError."""
    from powderline.gateways.topas import engine

    fake_run = Mock()
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    with pytest.raises(EngineVersionError):
        topas_gateway.run(_lab6_recipe(), str(tmp_path), topas_version=6, validate_only=False)
    assert not fake_run.called


def test_topas_version_kwarg_in_range_calls_engine(tmp_path, monkeypatch):
    """topas_version=7 (in range) calls run_topas_recipe."""
    from powderline.gateways.topas import engine

    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    topas_gateway.run(_lab6_recipe(), str(tmp_path), topas_version=7, validate_only=False)
    assert fake_run.called


def test_topas_config_version_out_of_range_raises(tmp_path, monkeypatch):
    """Config {"version": 6} with topas_version=None raises EngineVersionError."""
    from powderline.gateways.topas import runner, engine

    monkeypatch.setattr(runner, "_read_topas_config", lambda: {"version": 6})
    fake_run = Mock()
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    with pytest.raises(EngineVersionError):
        topas_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False)
    assert not fake_run.called


def test_topas_allow_unsupported_bypasses_version_check(tmp_path, monkeypatch):
    """allow_unsupported_engine_version=True bypasses the version check."""
    from powderline.gateways.topas import engine

    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    topas_gateway.run(
        _lab6_recipe(),
        str(tmp_path),
        topas_version=6,
        allow_unsupported_engine_version=True,
        validate_only=False,
    )
    assert fake_run.called


def test_topas_validate_only_skips_version_check(tmp_path, monkeypatch):
    """With validate_only=True, the version check is skipped even with topas_version=6."""
    from powderline.gateways.topas import engine

    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_topas_recipe", fake_run)

    topas_gateway.run(_lab6_recipe(), str(tmp_path), topas_version=6, validate_only=True)
    assert fake_run.called


# --- easydiffraction version checking ---


def test_easydiff_version_out_of_range_raises(tmp_path, monkeypatch):
    """When installed_engine_version() returns '0.20.1' (the previous pin), run() raises EngineVersionError."""
    monkeypatch.setattr(easydiff_gateway, "installed_engine_version", lambda: "0.20.1")
    from powderline.gateways.easydiffraction import engine

    fake_run = Mock()
    monkeypatch.setattr(engine, "run_easydiffraction_recipe", fake_run)

    with pytest.raises(EngineVersionError):
        easydiff_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False)
    assert not fake_run.called


def test_easydiff_version_in_range_calls_engine(tmp_path, monkeypatch):
    """When installed_engine_version() returns '0.21.1', run() calls the engine."""
    monkeypatch.setattr(easydiff_gateway, "installed_engine_version", lambda: "0.21.1")
    from powderline.gateways.easydiffraction import engine

    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_easydiffraction_recipe", fake_run)

    easydiff_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False)
    assert fake_run.called


@pytest.mark.skipif(
    importlib.util.find_spec("easydiffraction") is not None,
    reason="easydiffraction is installed",
)
def test_easydiff_not_installed_raises_engine_not_available_error(tmp_path):
    """When easydiffraction is not installed, run() raises EngineNotAvailableError."""
    with pytest.raises(EngineNotAvailableError) as exc_info:
        easydiff_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=False)
    assert isinstance(exc_info.value, ImportError)
    assert "pixi install -e easydiff" in str(exc_info.value)


@pytest.mark.skipif(
    importlib.util.find_spec("easydiffraction") is not None,
    reason="easydiffraction is installed",
)
def test_easydiff_not_installed_validate_only_does_not_raise(tmp_path, monkeypatch):
    """When easydiffraction is not installed, validate_only=True does not raise."""
    from powderline.gateways.easydiffraction import engine

    fake_run = Mock(return_value={})
    monkeypatch.setattr(engine, "run_easydiffraction_recipe", fake_run)

    # validate_only should not trigger the import that raises EngineNotAvailableError
    easydiff_gateway.run(_lab6_recipe(), str(tmp_path), validate_only=True)
    assert fake_run.called


@pytest.mark.skipif(
    importlib.util.find_spec("easydiffraction") is None,
    reason="easydiffraction not installed",
)
def test_easydiff_installed_version_satisfies_spec():
    """When easydiffraction is installed, its version satisfies ENGINE_VERSION_SPEC.

    easydiffraction (0.20.1, 0.21.1) has no ``__version__``; its own runtime version source
    is ``utils.package_version`` (distribution metadata), which must agree.
    """
    from easydiffraction.utils.utils import package_version

    version = easydiff_gateway.installed_engine_version()
    assert version == package_version("easydiffraction")
    spec = SpecifierSet(easydiff_gateway.ENGINE_VERSION_SPEC)
    assert version in spec


# --- Dispatcher routing (powderline.run dispatches on schema_name, P-06) ---


def _recording_gateways(names, workflows=()):
    """Register fake gateways under ``names`` that record run() kwargs; return the record."""
    calls = {}

    def make_fake_gateway(name):
        def fake_run(recipe, output_dir, **kwargs):
            calls[name] = kwargs
            return {}

        return registry.Gateway(
            name=name,
            capabilities=lambda: {"name": name, "workflows": list(workflows), "engine_version_spec": ">=1"},
            validate=lambda recipe, **opts: recipe,
            run=fake_run,
        )

    for name in names:
        registry.register_gateway(name, lambda n=name: make_fake_gateway(n))
    return calls


def test_dispatcher_routes_by_schema_name_through_registry_get(tmp_path, isolated_registry):
    """powderline.run(recipe) routes to registry.get(<engine of schema_name>).run(...) with the options unchanged."""
    calls = _recording_gateways(["gsasii", "topas", "easydiffraction"])

    powderline.run({"schema_name": "gsasii.rietveld"}, str(tmp_path), verbose=True, validate_only=False,
                   execution_mode="subprocess")
    assert calls == {"gsasii": {"verbose": True, "validate_only": False, "execution_mode": "subprocess"}}

    calls.clear()
    powderline.run({"schema_name": "topas.spf"}, str(tmp_path), verbose=False, validate_only=True,
                   topas_dir="/fake", topas_version=7)
    assert calls == {"topas": {"verbose": False, "validate_only": True, "topas_dir": "/fake", "topas_version": 7}}

    calls.clear()
    powderline.run({"schema_name": "easydiffraction.rietveld"}, str(tmp_path), verbose=True)
    assert calls == {"easydiffraction": {"verbose": True, "validate_only": False}}

    # the 0.26.0 names route to gsasii until the legacy schema is removed
    calls.clear()
    powderline.run({"schema_name": "GSASII_SPF"}, str(tmp_path))
    assert set(calls) == {"gsasii"}


def test_dispatcher_deprecated_engine_must_agree_with_schema_name(tmp_path, isolated_registry):
    """run(engine=...) warns, and is accepted only when that gateway serves the recipe's schema_name."""
    calls = _recording_gateways(["gsasii", "topas"], workflows=("topas.rietveld", "GSASII_Rietveld"))

    with pytest.warns(DeprecationWarning, match="schema_name"):
        powderline.run({"schema_name": "topas.rietveld"}, str(tmp_path), engine="topas")
    assert set(calls) == {"topas"}

    calls.clear()
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="does not serve schema_name"):
        powderline.run({"schema_name": "gsasii.rietveld"}, str(tmp_path), engine="topas")
    assert calls == {}

    # a legacy recipe may still be sent to a translation path that declares the name
    with pytest.warns(DeprecationWarning):
        powderline.run({"schema_name": "GSASII_Rietveld"}, str(tmp_path), engine="topas")
    assert set(calls) == {"topas"}


def test_dispatcher_unknown_engine_raises_value_error():
    """powderline.run(..., engine='bogus') raises ValueError naming the installed gateways."""
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="unknown engine"):
        powderline.run(_lab6_recipe(), "/tmp", engine="bogus")


def test_dispatcher_refuses_allow_unsupported_engine_version(tmp_path, isolated_registry):
    """The gateways' test-only version-check bypass is not a public run() option (A53)."""
    calls = _recording_gateways(["gsasii"])
    with pytest.raises(TypeError, match="allow_unsupported_engine_version"):
        powderline.run({"schema_name": "gsasii.rietveld"}, str(tmp_path), allow_unsupported_engine_version=True)
    assert calls == {}


def test_dispatcher_unknown_option_is_the_gateways_type_error(tmp_path):
    """An option the gateway does not take is a TypeError naming it (A59), from the gateway's signature."""
    with pytest.raises(TypeError, match="topas_dir"):
        powderline.run(_lab6_recipe(), str(tmp_path), validate_only=True, topas_dir="/nowhere")


# --- Import-block guarantee ---


def test_import_block_schema_layer_and_validate():
    """Schema-layer imports (registry, gateways, capabilities, validate) work with engines blocked."""
    recipe_path = str(EXAMPLES / "example_LaB6" / "input.json")
    easydiff_recipe_path = str(EXAMPLES / "example_LaB6_easydiff" / "input.json")
    topas_native_path = str(REPO / "tests" / "data" / "topas" / "native" / "lab6_rietveld.json")
    script = f"""
import sys, importlib.abc, json
from pathlib import Path

# Block GSASII and easydiffraction imports
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in ("GSASII", "easydiffraction") or name.startswith(("GSASII.", "easydiffraction.")):
            raise ImportError(f"{{name}} blocked for import-block test")
        return None

sys.meta_path.insert(0, _Block())

# Import the engine-free core + schema-layer modules
import powderline
import powderline.registry
import powderline.exceptions
import powderline.fitstats
import powderline.reports
import powderline.symmetry
import powderline.schema_core
import powderline.compat
import powderline.gateways.gsasii
import powderline.gateways.gsasii.gateway
import powderline.gateways.gsasii.schema
import powderline.gateways.gsasii.validation
import powderline.gateways.topas.gateway
import powderline.gateways.topas.schema
import powderline.gateways.topas.native_writer
import powderline.gateways.topas.native_run
import powderline.gateways.easydiffraction.gateway
from powderline import registry

# The core phase block and its symmetry rules run engine-free (re/03b)
from powderline.schema_core import Phase, RefinableParameter
from powderline.symmetry import canonical_space_group, cell_tie_groups, coupling_groups
canonical_space_group("F d -3 m:2")
assert [g.members for g in cell_tie_groups("R -3 m:R").groups] == [("a", "b", "c"), ("alpha", "beta", "gamma")]
assert coupling_groups("P -4 21 m", (0.1, 0.6, 0.3)).xyz.groups[0].relations_text() == "y = x + 1/2"
p = lambda v, f=False: [v, f]
phase = Phase[RefinableParameter].model_validate({{
    "space_group": "P 63/m m c",
    "unit_cell": {{"a": p(3.0, True), "b": p(3.0, True), "c": p(5.0), "alpha": p(90.0), "beta": p(90.0),
                  "gamma": p(120.0)}},
    "atoms": {{"C": {{"element": "C", "x": p(1 / 6, True), "y": p(1 / 3, True), "z": p(0.25),
                     "occupancy": p(1.0), "ADP": "Uiso", "Uiso": p(0.01)}}}},
}})
assert phase.site("C").multiplicity == 6 and phase.atoms["C"].x.value == 1 / 6

# The native gsasii schema validates engine-free (re/04, A39)
native = {{
    "schema_name": "gsasii.rietveld", "core_schema_version": "1.0.0", "engine_schema_version": "1.0.0",
    "payload": {{
        "xrd_data": {{"tth": [1.0, 2.0], "Itth": [1.0, 2.0], "Itth_weights": [1.0, 1.0]}},
        "instrument": {{"radiation": {{"type": "PXC", "wavelength": p(0.1665)}},
                        "geometry": {{"bank": 1, "azimuth": 0.0}},
                        "corrections": {{"zero_shift": p(0.0), "polarization": p(0.99),
                                         "axial_divergence": p(0.002)}},
                        "broadening": {{k: p(1.0) for k in "UVWXYZ"}}}},
        "phases": {{"C": {{**phase.model_dump(mode="json"), "scale": p(1.0)}}}},
        "refinement_controls": {{"refinement_cycles": 5}},
    }},
}}
assert type(registry.get("gsasii").validate(native)).__name__ == "GsasiiRietveldRecipe"

# The native topas schema validates and the native writer renders engine-free (re/05)
from powderline.gateways.topas.native_writer import render_native
topas_native = registry.get("topas").validate(json.loads(Path(r'{topas_native_path}').read_text(encoding="utf-8")))
assert type(topas_native).__name__ == "TopasRietveldRecipe"
assert "TCHZ_Peak_Type" in render_native(topas_native, "lab6").inp_text

recipes = {{
    "gsasii": json.loads(Path(r'{recipe_path}').read_text(encoding="utf-8")),
    "topas": json.loads(Path(r'{recipe_path}').read_text(encoding="utf-8")),
    # the GSAS-II LaB6 recipe refines Z, which easydiffraction rejects
    "easydiffraction": json.loads(Path(r'{easydiff_recipe_path}').read_text(encoding="utf-8")),
}}

# The schema-only consumer path, through the registry (entry points)
for name, recipe in recipes.items():
    gw = registry.get(name)
    caps = gw.capabilities()
    assert caps["name"] == name and "engine_version_spec" in caps
    assert type(gw.validate(recipe)).__name__ == "RecipeModel"

# The support matrix loads every gateway factory (A65)
assert {{row["gateway"] for row in powderline.support_matrix()}} >= set(recipes)

# Assert GSASII and easydiffraction are NOT in sys.modules
assert "GSASII" not in sys.modules, "GSASII was imported despite the block"
assert "easydiffraction" not in sys.modules, "easydiffraction was imported despite the block"

print("OK")
"""
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_native_package_imports_are_legacy_free():
    """Importing the topas/easydiffraction packages and the native TOPAS runner loads no 0.26.0 module (re/07 B).

    The legacy translation modules are deleted later in re/07; the native paths
    must not depend on them at import time. The legacy branch of each gateway
    imports them lazily, by name, where it runs.
    """
    script = """
import sys
import powderline.gateways.topas
import powderline.gateways.topas.native_run
import powderline.gateways.easydiffraction
legacy = [m for m in sys.modules if m in (
    "powderline.gateways.topas.writer", "powderline.gateways.topas.roundtrip",
    "powderline.gateways.topas.engine", "powderline.gateways.topas.kicker",
    "powderline.gateways.easydiffraction.engine", "powderline.gateways.easydiffraction.builder",
    "powderline.gateways.easydiffraction.policy", "powderline.gateways.easydiffraction.conversions",
)]
assert not legacy, f"legacy modules loaded at package import: {legacy}"
from powderline.gateways.topas import TopasTranslationError
from powderline.gateways.easydiffraction import EasyDiffractionTranslationError
print("OK")
"""
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_import_block_run_raises_engine_not_available_error(tmp_path):
    """With GSASII blocked, gsasii gateway run() raises EngineNotAvailableError."""
    recipe_path = str(EXAMPLES / "example_LaB6" / "input.json")
    output_path = str(tmp_path)
    script = f"""
import sys, importlib.abc, json
from pathlib import Path

# Block GSASII import
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "GSASII" or name.startswith("GSASII."):
            raise ImportError("GSASII blocked for import-block test")
        return None

sys.meta_path.insert(0, _Block())

from powderline.gateways.gsasii import gateway as gsasii_gw
from powderline.exceptions import EngineNotAvailableError

recipe = json.loads(Path(r'{recipe_path}').read_text(encoding="utf-8"))

try:
    gsasii_gw.run(recipe, r'{output_path}', validate_only=False)
    print("ERROR: should have raised EngineNotAvailableError")
    sys.exit(1)
except EngineNotAvailableError as e:
    assert isinstance(e, ImportError)
    print("OK")
"""
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


# --- Uniform validate() contract (A59) ---


@pytest.mark.parametrize("gateway_module", [gsasii_gateway, topas_gateway, easydiff_gateway],
                         ids=["gsasii", "topas", "easydiffraction"])
def test_validate_signature_is_uniform(gateway_module):
    import inspect

    params = inspect.signature(gateway_module.validate).parameters
    assert list(params) == ["recipe", "verbose"]
    assert params["verbose"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["verbose"].default is False


@pytest.mark.parametrize("name", ["gsasii", "topas", "easydiffraction"])
def test_validate_accepts_verbose_through_the_registry(name):
    example = "example_LaB6_easydiff" if name == "easydiffraction" else "example_LaB6"
    recipe = json.loads((EXAMPLES / example / "input.json").read_text(encoding="utf-8"))
    assert isinstance(registry.get(name).validate(recipe, verbose=True), schema.RecipeModel)
