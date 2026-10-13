"""Tests for the one CLI, ``powderline.kicker.main`` (re/07, P-06).

The CLI dispatches on the recipe's ``schema_name`` like ``powderline.run``;
these tests drive ``main()`` in-process on ``--validate-only`` (no engine
needed) and check the flag-to-option mapping, the template guard and the exit
statuses. The ``pixi run kicker`` regression suites cover the real runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from powderline import kicker
from subprocess_utils import run_subprocess_utf8

REPO = Path(__file__).resolve().parent.parent
LEGACY_LAB6 = REPO / "examples" / "example_LaB6" / "input.json"
TOPAS_LAB6 = REPO / "tests" / "data" / "topas" / "native" / "lab6_rietveld.json"
TOPAS_SPF = REPO / "tests" / "data" / "topas" / "native" / "lab6_spf.json"
EASY_LAB6 = REPO / "tests" / "data" / "easydiffraction" / "native" / "lab6_crysfml_tch.json"


# --- --validate-only, every engine ------------------------------------------------------------


def test_validate_only_legacy_gsasii_recipe(capsys):
    rc = kicker.main([str(LEGACY_LAB6), "--validate-only", "--no-server"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Recipe validation successful" in out
    assert "Schema name: GSASII_Rietveld" in out
    assert "Schema version: 0.26.0" in out


def test_validate_only_topas_native_recipe(capsys):
    rc = kicker.main([str(TOPAS_LAB6), "--validate-only", "--topas-version", "6"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Schema name: topas.rietveld" in out
    assert "Engine schema version: 1.0.0" in out
    assert "Engine version: 6" in out
    assert "warning(s):" in out and "topas_adp_converted" in out  # the structured warnings are shown


def test_validate_only_easydiffraction_native_recipe(capsys):
    rc = kicker.main([str(EASY_LAB6), "--validate-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Schema name: easydiffraction.rietveld" in out
    assert "Calculator: crysfml" in out


def test_validate_only_writes_nothing(tmp_path):
    out = tmp_path / "out"
    rc = kicker.main([str(TOPAS_LAB6), "--validate-only", "--output", str(out)])
    assert rc == 0
    assert not out.exists()


# --- engine flags ---------------------------------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    ns = kicker.build_parser().parse_args([str(LEGACY_LAB6)])
    for key, value in kw.items():
        setattr(ns, key, value)
    return ns


def test_engine_options_default_is_empty():
    assert kicker.engine_options_from_args(_args(), "gsasii") == {}
    assert kicker.engine_options_from_args(_args(), "topas") == {}


def test_engine_options_gsasii_server_flags():
    assert kicker.engine_options_from_args(_args(no_server=True), "gsasii") == {"execution_mode": "subprocess"}
    assert kicker.engine_options_from_args(_args(use_server=True), "gsasii") == {"execution_mode": "server"}


def test_engine_options_topas_flags():
    opts = kicker.engine_options_from_args(_args(topas_dir=Path("/t"), topas_version="6"), "topas")
    assert opts == {"topas_dir": Path("/t"), "topas_version": "6"}


@pytest.mark.parametrize("flag,gateway", [
    ({"no_server": True}, "topas"), ({"use_server": True}, "easydiffraction"),
    ({"topas_dir": Path("/t")}, "gsasii"), ({"topas_version": "6"}, "gsasii"),
])
def test_engine_options_flag_for_another_engine_is_an_error(flag, gateway):
    with pytest.raises(ValueError, match="applies to"):
        kicker.engine_options_from_args(_args(**flag), gateway)


def test_cli_rejects_gsasii_flag_on_topas_recipe(capsys):
    rc = kicker.main([str(TOPAS_LAB6), "--validate-only", "--no-server"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "--no-server applies to 'gsasii' recipes only" in out and "'topas'" in out


def test_cli_use_server_and_no_server_are_exclusive():
    with pytest.raises(SystemExit):
        kicker.build_parser().parse_args([str(LEGACY_LAB6), "--use-server", "--no-server"])


# --- errors -----------------------------------------------------------------------------------------


def test_cli_missing_recipe_file_exits_1(tmp_path, capsys):
    rc = kicker.main([str(tmp_path / "nope.json")])
    assert rc == 1
    assert "Cannot read recipe" in capsys.readouterr().out


def test_cli_template_path_exits_1(capsys):
    rc = kicker.main([str(REPO / "examples" / "example_template" / "input.json"), "--validate-only"])
    assert rc == 1
    assert "appears to be a template file" in capsys.readouterr().out


def test_cli_reports_validation_errors_as_bullets(tmp_path, capsys):
    recipe = json.loads(TOPAS_LAB6.read_text(encoding="utf-8"))
    recipe["payload"]["refinement_controls"]["iters"] = "many"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(recipe))
    rc = kicker.main([str(bad), "--validate-only"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Recipe validation failed" in out and "Validation errors:" in out
    assert "payload -> refinement_controls -> iters" in out


def test_cli_unknown_engine_exits_1_with_the_registry_message(tmp_path, capsys):
    recipe = json.loads(TOPAS_LAB6.read_text(encoding="utf-8"))
    recipe["schema_name"] = "bogus.rietveld"
    bad = tmp_path / "bogus.json"
    bad.write_text(json.dumps(recipe))
    rc = kicker.main([str(bad), "--validate-only"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no PowderLine gateway named 'bogus'" in out


# --- template guard ------------------------------------------------------------------------------


@pytest.mark.parametrize("schema_name", ["GSASII_Rietveld", "gsasii.rietveld", "topas.rietveld",
                                         "easydiffraction.rietveld"])
def test_is_template_file_rietveld_needs_phases(schema_name):
    payload = {"xrd_data": {}, "instrument": {}, "refinement_controls": {}}
    is_template, reason = kicker.is_template_file({"schema_name": schema_name, "payload": payload}, Path("in.json"))
    assert is_template and "phases" in reason
    payload["phases"] = {}
    assert kicker.is_template_file({"schema_name": schema_name, "payload": payload}, Path("in.json")) == (False, None)


@pytest.mark.parametrize("schema_name", ["GSASII_SPF", "gsasii.spf", "topas.spf"])
def test_is_template_file_spf_needs_single_peaks(schema_name):
    payload = {"xrd_data": {}, "instrument": {}, "refinement_controls": {}}
    is_template, reason = kicker.is_template_file({"schema_name": schema_name, "payload": payload}, Path("in.json"))
    assert is_template and "single_peaks" in reason


def test_is_template_file_real_fixtures_are_not_templates():
    for path in (LEGACY_LAB6, TOPAS_LAB6, TOPAS_SPF, EASY_LAB6):
        assert kicker.is_template_file(json.loads(path.read_text(encoding="utf-8")), path) == (False, None)


# --- engine-free -------------------------------------------------------------------------------------


def test_cli_module_imports_no_engine_and_validates_with_engines_blocked():
    """``powderline.kicker`` imports no engine; ``--validate-only`` works with GSASII/easydiffraction/cryspy blocked."""
    script = f"""
import sys, importlib.abc
BLOCKED = ("GSASII", "easydiffraction", "cryspy")
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in BLOCKED or name.startswith(tuple(b + "." for b in BLOCKED)):
            raise ImportError(name + " blocked")
        return None
sys.meta_path.insert(0, _Block())
from powderline import kicker
for recipe in (r'{LEGACY_LAB6}', r'{TOPAS_LAB6}', r'{EASY_LAB6}'):
    assert kicker.main([recipe, "--validate-only"]) == 0, recipe
for mod in BLOCKED:
    assert mod not in sys.modules, mod
print("OK")
"""
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_legacy_facade_resolves_lazily():
    """``powderline.kicker.<GSAS-II name>`` still resolves (transitional), without the CLI importing GSAS-II itself."""
    from powderline.gateways.gsasii.routing import run_refinement

    assert kicker.run_refinement is run_refinement
    assert kicker.load_recipe_asset is __import__("powderline").load_recipe_asset
    with pytest.raises(AttributeError, match="no attribute 'nonexistent_name'"):
        kicker.nonexistent_name
