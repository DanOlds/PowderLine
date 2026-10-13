"""API-based tests for the powderline public programmatic interface (engine-free half).

Exercises ``powderline.validate()``, ``run(validate_only=True)``,
``load_recipe_asset`` and the ``GSASClient`` HTTP retry logic without any
engine: everything here runs in the core env under the core guard. The tests
that execute a GSAS-II refinement through ``powderline.run()`` / ``GSASClient``
live in ``test_api_gsasii_run.py`` (re/09 split).
"""

import json
import sys
import uuid
from unittest.mock import patch, MagicMock
import pytest

import pandas as pd
from pathlib import Path
from pydantic import ValidationError

import powderline
from powderline.gateways.gsasii.client import GSASClient
from powderline.gateways.gsasii.schema import GsasiiRietveldRecipe
from subprocess_utils import run_subprocess_utf8

pytestmark = pytest.mark.core

REPO = Path(__file__).resolve().parent.parent



# ─── validate() tests ─────────────────────────────────────────────────────────

def test_validate_valid_recipe(recipe_LaB6_dict):
    """validate() accepts a valid dict and returns a ValidationResult holding the gateway's model."""
    result = powderline.validate(recipe_LaB6_dict)
    assert isinstance(result, powderline.ValidationResult)
    assert isinstance(result.recipe, GsasiiRietveldRecipe)
    assert result.recipe.schema_name == "gsasii.rietveld"
    # the example leaves size/strain out: the A107 defaults are reported as structured warnings
    assert [w["code"] for w in result.warnings] == ["gsasii_broadening_default_applied"] * 2


def test_validate_returns_recipemodel_from_model(recipe_LaB6_model):
    """validate() is idempotent — a validated model passes through unchanged (A50)."""
    result = powderline.validate(recipe_LaB6_model)
    assert result.recipe is recipe_LaB6_model


def test_validation_result_is_frozen(recipe_LaB6_dict):
    """ValidationResult is a record of one validation: its attributes cannot be rebound."""
    result = powderline.validate(recipe_LaB6_dict)
    with pytest.raises(AttributeError):
        result.recipe = None


def test_validate_missing_schema_name_is_a_validation_error():
    """A recipe without schema_name fails as an invalid recipe (at schema_name), not as a dispatch error."""
    with pytest.raises(ValidationError) as exc:
        powderline.validate({"payload": {}})
    assert [tuple(e["loc"]) for e in exc.value.errors()] == [("schema_name",)]
    assert exc.value.errors()[0]["type"] == "missing"


def test_validate_schema_name_without_engine_prefix_is_a_validation_error():
    """A schema_name that is not <engine>.<workflow> is reported at schema_name (A133)."""
    with pytest.raises(ValidationError, match="does not name an engine"):
        powderline.validate({"schema_name": "Rietveld", "payload": {}})


def test_validate_unknown_engine_prefix_raises_gateway_not_installed():
    """schema_name 'bogus.rietveld' names a gateway that is not installed; the message lists the installed ones.

    (The "unknown engine lists easydiffraction" assertion of the deleted
    engine= dispatcher tests, now on the schema_name path; re/07 H.)
    """
    with pytest.raises(powderline.GatewayNotInstalledError) as exc:
        powderline.validate({"schema_name": "bogus.rietveld", "payload": {}})
    assert exc.value.gateway == "bogus"
    assert "no PowderLine gateway named 'bogus'" in str(exc.value)
    for name in ("gsasii", "topas", "easydiffraction"):
        assert name in str(exc.value)


def test_validate_native_recipes_dispatch_by_schema_name():
    """validate() routes gsasii.*, topas.* and easydiffraction.* recipes to their gateways and exposes warnings."""
    cases = {
        "gsasii": REPO / "tests" / "data" / "gsasii_native_lab6.json",
        "topas": REPO / "tests" / "data" / "topas" / "native" / "lab6_rietveld.json",
        "easydiffraction": REPO / "tests" / "data" / "easydiffraction" / "native" / "lab6_crysfml_tch.json",
    }
    for name, path in cases.items():
        if not path.exists():
            continue
        result = powderline.validate(json.loads(path.read_text(encoding="utf-8")))
        assert result.recipe.schema_name.startswith(name + ".")
        assert isinstance(result.warnings, list)
        for w in result.warnings:
            assert set(w) == {"code", "message", "field_path"}


def test_validate_is_engine_free():
    """powderline.validate() runs with GSASII, easydiffraction and cryspy blocked, for every gateway.

    The re/08 precondition: the core-only environment validates any recipe.
    """
    legacy = str(REPO / "tests" / "data" / "convert_026" / "example_LaB6" / "input.json")  # the converter's 0.26.0 fixture (P-23)
    gsasii_native = str(REPO / "examples" / "example_LaB6" / "input.json")
    topas_native = str(REPO / "tests" / "data" / "topas" / "native" / "lab6_rietveld.json")
    easy_native = str(REPO / "tests" / "data" / "easydiffraction" / "native" / "lab6_crysfml_tch.json")
    script = f"""
import sys, importlib.abc, json
from pathlib import Path

BLOCKED = ("GSASII", "easydiffraction", "cryspy")

class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in BLOCKED or name.startswith(tuple(b + "." for b in BLOCKED)):
            raise ImportError(name + " blocked for the engine-free validate test")
        return None

sys.meta_path.insert(0, _Block())
import powderline
assert set(powderline.__all__) >= {{"run", "validate", "load_recipe_asset", "support_matrix"}}
assert "powderline.kicker" not in sys.modules, "import powderline must not import the CLI module"

r = powderline.validate(json.loads(Path(r'{gsasii_native}').read_text(encoding="utf-8")))
assert type(r).__name__ == "ValidationResult" and type(r.recipe).__name__ == "GsasiiRietveldRecipe"
r = powderline.validate(json.loads(Path(r'{topas_native}').read_text(encoding="utf-8")))
assert type(r.recipe).__name__ == "TopasRietveldRecipe", type(r.recipe).__name__
r = powderline.validate(json.loads(Path(r'{easy_native}').read_text(encoding="utf-8")))
assert type(r.recipe).__name__ == "EasydiffractionRietveldRecipe", type(r.recipe).__name__
assert isinstance(r.warnings, list)
# a gsasii.* recipe converted from a 0.26.0 recipe validates too (the converter is engine-free)
import importlib.util
spec = importlib.util.spec_from_file_location("convert_recipe_026", r'{REPO / "scripts" / "convert_recipe_026.py"}')
conv = importlib.util.module_from_spec(spec); spec.loader.exec_module(conv)
legacy = json.loads(Path(r'{legacy}').read_text(encoding="utf-8"))
legacy["payload"]["background"]["single_peaks"]["pv_lorentzian_gamma"][0][0] = 0.1
r = powderline.validate(conv.convert(legacy)[0])
assert type(r.recipe).__name__ == "GsasiiRietveldRecipe", type(r.recipe).__name__

for mod in BLOCKED:
    assert mod not in sys.modules, mod + " was imported despite the block"
print("OK")
"""
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_validate_invalid_recipe_raises():
    """validate() raises ValidationError for a recipe missing required fields."""
    bad = {"schema_name": "gsasii.rietveld", "core_schema_version": "1.0.0", "engine_schema_version": "1.0.0"}
    with pytest.raises(ValidationError):
        powderline.validate(bad)


def test_validate_empty_dict_raises():
    """validate() raises ValidationError for an empty dict."""
    with pytest.raises(ValidationError):
        powderline.validate({})


# ─── run() validate_only tests ────────────────────────────────────────────────

def test_run_validate_only_returns_summary(recipe_LaB6_dict, tmp_path):
    """run(validate_only=True) returns a summary dict without executing a refinement."""
    result = powderline.run(recipe_LaB6_dict, tmp_path / "out", validate_only=True)

    assert result["success"] is True
    assert result["method"] == "validate_only"
    assert result["schema_name"] == "gsasii.rietveld"
    assert isinstance(result["phases"], int)
    assert result["rwp"] is None

    # No run was executed, so no run_id should be present.
    assert "run_id" not in result, (
        "validate_only mode must not include run_id — no refinement was executed"
    )

    # No output files should have been written
    gpx = tmp_path / "out" / "dummy.gpx"
    assert not gpx.exists(), "dummy.gpx must not be created in validate_only mode"


def test_run_validate_only_bad_recipe_raises(tmp_path):
    """run(validate_only=True) still raises ValidationError for a bad recipe."""
    with pytest.raises(ValidationError):
        powderline.run({"schema_name": "gsasii.rietveld"}, tmp_path / "out", validate_only=True)


# ─── load_recipe_asset tests ──────────────────────────────────────────────────

def test_load_recipe_asset_json():
    """load_recipe_asset() loads a JSON file and returns a dict."""
    recipe = powderline.load_recipe_asset(Path("examples/example_LaB6/input.json"))
    assert isinstance(recipe, dict)
    assert "schema_name" in recipe
    assert "payload" in recipe


def test_load_recipe_asset_unsupported_format(tmp_path):
    """load_recipe_asset() raises ValueError for unsupported file formats."""
    bad_file = tmp_path / "recipe.xyz"
    bad_file.write_text("{}")
    with pytest.raises(ValueError, match="Unsupported recipe format"):
        powderline.load_recipe_asset(bad_file)


# ─── HTTP retry logic tests ────────────────────────────────────────────────────

def test_submit_to_server_retries_on_connect_error(recipe_LaB6_dict, tmp_path):
    """_submit_to_server() retries up to 3 times on ConnectError before raising."""
    import httpx

    client = GSASClient()
    connect_error = httpx.ConnectError("Simulated connection refused")

    with patch("httpx.post", side_effect=connect_error) as mock_post:
        with pytest.raises(httpx.ConnectError):
            client._submit_to_server(recipe_LaB6_dict, tmp_path / "out")

    assert mock_post.call_count == 3, (
        f"Expected 3 retry attempts on ConnectError, got {mock_post.call_count}"
    )


def test_submit_to_server_does_not_retry_on_http_status_error(
    recipe_LaB6_dict, tmp_path
):
    """_submit_to_server() raises immediately on HTTP status errors (no retry)."""
    import httpx

    client = GSASClient()
    mock_response = MagicMock()
    mock_response.status_code = 500
    http_error = httpx.HTTPStatusError(
        "500 Server Error", request=MagicMock(), response=mock_response
    )

    with patch("httpx.post", side_effect=http_error) as mock_post:
        with pytest.raises(httpx.HTTPStatusError):
            client._submit_to_server(recipe_LaB6_dict, tmp_path / "out")

    assert mock_post.call_count == 1, (
        f"Expected exactly 1 attempt on HTTPStatusError (no retry), got {mock_post.call_count}"
    )


# ─── execution_mode validation tests ─────────────────────────────────────────

def test_run_invalid_execution_mode_raises(recipe_LaB6_dict, tmp_path):
    """run() raises ValueError immediately for an invalid execution_mode."""
    with pytest.raises(ValueError, match="Invalid execution_mode"):
        powderline.run(
            recipe_LaB6_dict,
            tmp_path / "out",
            execution_mode="sever",  # typo — should be 'server'
        )
